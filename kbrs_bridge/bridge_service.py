import os
import asyncio
import re
import datetime as dt
from collections import deque

import discord
from discord.ext import commands
from dotenv import load_dotenv
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# --- наши модули ---
from . import tg_bot
from .tg_bot import send_text_to, send_photo_to, send_document_to, send_media_group_to, push_to_tg
from .translator import translate_en_to_ru_force

# --- загрузка .env ---
load_dotenv()

# === НАСТРОЙКИ ===
ENABLE_TRANSLATION = os.getenv("ENABLE_TRANSLATION", "1").strip().lower() in {"1", "true", "yes"}
DISABLE_TG_SEND = os.getenv("DISABLE_TG_SEND", "0").strip().lower() in {"1", "true", "yes"}

START_TG_POLLING = os.getenv("START_TG_POLLING", "1").strip().lower() in {"1","true","yes"}

# --- безопасная таймзона ---
def _safe_zoneinfo():
    tz_name = os.getenv("TZ", "Europe/Moscow").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")

TZ = _safe_zoneinfo()

IMG_RX = re.compile(r"\.(png|jpe?g|gif|webp|bmp|tiff?)$", re.I)

# --- чтение ID из env ---
def _to_int(name: str) -> int:
    v = os.getenv(name, "").strip()
    try:
        return int(v) if v else 0
    except Exception:
        return 0

DS_THREAD_ID = _to_int("DS_THREAD_ID")
DS_TOPIC_HW = _to_int("DS_TOPIC_HW")
TG_THREAD_ID = _to_int("TG_THREAD_ID")
TG_TOPIC_HW = _to_int("TG_TOPIC_HW")

# --- карта дискорд → телеграм ---
DS_TO_TG_MAP: dict[int, int] = {}
if DS_THREAD_ID and TG_THREAD_ID:
    DS_TO_TG_MAP[DS_THREAD_ID] = TG_THREAD_ID
if DS_TOPIC_HW and TG_TOPIC_HW:
    DS_TO_TG_MAP[DS_TOPIC_HW] = TG_TOPIC_HW

if not DS_TO_TG_MAP:
    print("[bridge][warn] DS_TO_TG_MAP is empty. "
          "Проверь пары DS_THREAD_ID↔TG_THREAD_ID и DS_TOPIC_HW↔TG_TOPIC_HW в .env")

# --- утилиты ---
def _fmt_time_local(d: dt.datetime) -> str:
    return d.astimezone(TZ).strftime("%H:%M %d.%m.%y")

def _is_image(att: discord.Attachment) -> bool:
    return (att.content_type and att.content_type.startswith("image/")) or bool(IMG_RX.search(att.filename))

def _channel_label(ch: discord.abc.Messageable) -> str:
    if isinstance(ch, discord.DMChannel):
        return "dm"
    if isinstance(ch, discord.GroupChannel):
        return "group-dm"
    return getattr(ch, "name", "unknown")

def _select_tg_thread_id(message: discord.Message) -> int | None:
    ch_id = message.channel.id
    if ch_id in DS_TO_TG_MAP:
        return DS_TO_TG_MAP[ch_id]
    parent_id = getattr(message.channel, "parent_id", 0)
    if parent_id in DS_TO_TG_MAP:
        return DS_TO_TG_MAP[parent_id]
    return None


# === ОСНОВНОЙ КЛАСС ===
class BridgeService:
    """
    Встраиваемый «плагин».
    - Стартует тг-бот (планировщик + поллинг) в отдельном треде.
    - Навешивает on_message/on_ready на уже существующий Discord Bot.
    - Поддерживает флаг DISABLE_TG_SEND для временного отключения отправки в TG.
    """

    _backgrounds_started = False

    def __init__(self) -> None:
        self._seen_ids: deque[int] = deque(maxlen=5000)
        self._seen_set: set[int] = set()
        self._lock = asyncio.Lock()
        self.disable_tg_send = DISABLE_TG_SEND  # локальный флаг (можно менять в рантайме)

    # --- фоновые задачи ---
    def start_backgrounds(self):
        if BridgeService._backgrounds_started:
            print("[bridge] backgrounds already started, skip")
            return

        BridgeService._backgrounds_started = True
        tg_bot.start_facts_scheduler()
        if START_TG_POLLING:
            tg_bot.start_polling_in_thread()
        else:
            print("[bridge] START_TG_POLLING=0 → skip polling, send-only mode")

    # --- системные обработчики ---
    async def _on_ready(self):
        print(f"[bridge] DS_TO_TG_MAP = {DS_TO_TG_MAP}")
        if self.disable_tg_send:
            print("[bridge] ⚠️ Telegram sending is DISABLED via .env")

    async def _on_message(self, message: discord.Message):
        if message.author == getattr(self, "_bot_user", None):
            return

        tg_thread = _select_tg_thread_id(message)
        if not tg_thread:
            return

        async with self._lock:
            if message.id in self._seen_set:
                return

            images = [a for a in message.attachments if _is_image(a)]
            docs = [a for a in message.attachments if a not in images]
            original_text = (message.content or "").strip()

            print(f"[bridge] got msg id={message.id} ch={message.channel.id} -> tg_topic={tg_thread} "
                  f"text_len={len(original_text)} imgs={len(images)} docs={len(docs)}")

            # === Проверка отключения TG ===
            if self.disable_tg_send:
                print(f"[bridge] ⚠️ TG send disabled (message {message.id}), skipping.")
                return

            # текст
            if original_text and ENABLE_TRANSLATION:
                header = f"#{_channel_label(message.channel)} | {message.author.display_name} • {_fmt_time_local(message.created_at)}"
                try:
                    ru = await translate_en_to_ru_force(original_text, attempts=4, base_timeout=15)
                    await push_to_tg({"kind": "text", "text": f"{header}\n\n{ru}", "thread_id": tg_thread})
                except Exception as e:
                    print("[bridge] HARD translation failure, not sending text:", e)

            # фото
            if images:
                urls = [a.url for a in images]
                try:
                    if len(urls) == 1:
                        await push_to_tg({"kind": "photo", "url": urls[0], "thread_id": tg_thread})
                    else:
                        await push_to_tg({"kind": "album", "urls": urls, "thread_id": tg_thread})
                except Exception as e:
                    print("[bridge] photo send error:", e)

            # файлы
            for a in docs:
                try:
                    await push_to_tg({"kind": "document", "url": a.url, "caption": a.filename, "thread_id": tg_thread})
                except Exception as e:
                    print("[bridge] doc send error:", a.filename, e)

            # === дедуп ===
            if original_text or images or docs:
                self._seen_set.add(message.id)
                self._seen_ids.append(message.id)
                while len(self._seen_set) > self._seen_ids.maxlen:
                    old = self._seen_ids.popleft()
                    self._seen_set.discard(old)

    # --- подключение к Discord Bot ---
    def attach(self, bot: discord.Client | commands.Bot):
        bot.add_listener(self._on_ready, "on_ready")
        bot.add_listener(self._on_message, "on_message")
        self._bot_user = getattr(bot, "user", None)

    # --- runtime переключатель ---
    def toggle_tg(self, state: bool):
        """Позволяет включать/выключать отправку в TG без перезапуска."""
        self.disable_tg_send = not state
        print(f"[bridge] Telegram sending {'ENABLED' if state else 'DISABLED'} at runtime.")


# === ФУНКЦИЯ ИНИЦИАЛИЗАЦИИ ===
def setup_bridge(bot: discord.Client | commands.Bot) -> BridgeService:
    svc = BridgeService()
    svc.start_backgrounds()
    svc.attach(bot)
    print(f"[bridge] DS_TO_TG_MAP = {DS_TO_TG_MAP} (from env)")
    if svc.disable_tg_send:
        print("[bridge] ⚠️ Telegram sending is DISABLED via .env")
    return svc
