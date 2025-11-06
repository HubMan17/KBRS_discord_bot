import os
import io
import re
import asyncio
import datetime as dt
from typing import List, Tuple, Optional, Dict, Callable, Awaitable

import discord
from discord.ext import commands
from dotenv import load_dotenv
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .translator import translate_to_force

load_dotenv()

# --- порядок отправки внутри одного сообщения ---
RELAY_SEND_TEXT_FIRST = os.getenv("RELAY_SEND_TEXT_FIRST", "1").strip().lower() in {"1", "true", "yes"}

# ---------- конфиг из .env ----------
def _to_int(name: str) -> int:
    v = os.getenv(name, "").strip()
    try:
        return int(v) if v else 0
    except Exception:
        return 0

ANNOUNCMENT_ID  = _to_int("ANNOUNCMENT")
ENGLISH_CHAT_ID = _to_int("ENGLISH_CHAT")
TAIWAN_CHAT_ID  = _to_int("TAIWAN_CHAT")      # zh-CN
JAPANESE_CHAT_ID= _to_int("JAPANESE_CHAT")    # ja
RUSSIAN_CHAT_ID = _to_int("RUSSIAN_CHAT")     # ru
KOREAN_CHAT_ID  = _to_int("KOREAN_CHAT")      # ko

ENABLE_RELAY       = os.getenv("ENABLE_DISCORD_RELAY", "1").strip().lower() in {"1", "true", "yes"}
ENABLE_TRANSLATION = os.getenv("ENABLE_TRANSLATION", "1").strip().lower() in {"1", "true", "yes"}

# Поведение при сбое перевода конкретного языка:
#  - STRICT: не шлём ничего в этот канал, ставим ретрай в фоне
#  - BEST_EFFORT: шлём временный EN, а перевод досылаем ретраем
RELAY_MODE = os.getenv("RELAY_MODE", "STRICT").strip().upper()  # STRICT | BEST_EFFORT

# Тайминги ретраев перевода
RELAY_ATTEMPTS      = int(os.getenv("RELAY_ATTEMPTS", "3"))   # фоновые попытки после первичной неудачи
RELAY_RETRY_LAG_SEC = float(os.getenv("RELAY_RETRY_LAG_SEC", "10"))

# Базовые таймауты на API (можно сделать выше для zh-CN)
BASE_TIMEOUT_EN = int(os.getenv("RELAY_BASE_TIMEOUT_EN", "15"))
BASE_TIMEOUT_ZH = int(os.getenv("RELAY_BASE_TIMEOUT_ZH", "25"))
BASE_TIMEOUT_JA = int(os.getenv("RELAY_BASE_TIMEOUT_JA", "18"))
BASE_TIMEOUT_RU = int(os.getenv("RELAY_BASE_TIMEOUT_RU", "18"))
BASE_TIMEOUT_KO = int(os.getenv("RELAY_BASE_TIMEOUT_KO", "18"))

# таймзона для шапки
def _safe_zoneinfo():
    tz_name = os.getenv("TZ", "Europe/Moscow").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")
TZ = _safe_zoneinfo()

# простой детектор «похоже на английский»
CYR = re.compile(r"[\u0400-\u04FF]")
CJK = re.compile(r"[\u3040-\u30FF\u4E00-\u9FFF\u3400-\u4DBF\u31F0-\u31FF]")
def _is_probably_english(text: str) -> bool:
    if not text:
        return True
    t = text.strip()
    if CYR.search(t) or CJK.search(t):
        return False
    letters = sum(ch.isalpha() for ch in t)
    ascii_letters_spaces = sum((ch.isascii() and (ch.isalpha() or ch.isspace())) for ch in t)
    if letters == 0:
        return True
    return (ascii_letters_spaces / max(1, len(t))) > 0.6

def _fmt_time_local(d: dt.datetime) -> str:
    return d.astimezone(TZ).strftime("%H:%M %d.%m.%y")

async def _collect_attachments_bytes(message: discord.Message) -> List[Tuple[str, bytes]]:
    out: List[Tuple[str, bytes]] = []
    for a in message.attachments:
        try:
            data = await a.read()
            out.append((a.filename, data))
        except Exception as e:
            print("[relay] attachment read error:", a.filename, e)
    return out

# --- отправка с гарантированным порядком внутри одного «пакета» ---
async def _send_text_then_attachments(
    ch: discord.abc.Messageable,
    content: str,
    atts: List[Tuple[str, bytes]],
):
    """1) текст (если есть) -> 2) вложения одним сообщением"""
    if content:
        await ch.send(content=content)
    if atts:
        files = [discord.File(io.BytesIO(data), filename=fn) for fn, data in atts]
        await ch.send(files=files)

async def _send_combined(
    ch: discord.abc.Messageable,
    content: str,
    atts: List[Tuple[str, bytes]],
):
    """Старое поведение: текст + вложения в одном сообщении."""
    files = [discord.File(io.BytesIO(data), filename=fn) for fn, data in atts] if atts else None
    await ch.send(content=content, files=files)

async def _send_with_order(ch: discord.abc.Messageable, content: str, atts: List[Tuple[str, bytes]]):
    if RELAY_SEND_TEXT_FIRST:
        await _send_text_then_attachments(ch, content, atts)
    else:
        await _send_combined(ch, content, atts)

# --- infra ---
async def _fetch_channel(bot: discord.Client | commands.Bot, channel_id: int) -> Optional[discord.abc.Messageable]:
    ch = bot.get_channel(channel_id)
    if ch is None:
        try:
            ch = await bot.fetch_channel(channel_id)
        except Exception as e:
            print(f"[relay] fetch_channel {channel_id} failed:", e)
            return None
    return ch

async def _translate_safe(text: str, lang: str, base_timeout: int) -> str:
    return await translate_to_force(text, target_lang=lang, attempts=4, base_timeout=base_timeout)

async def _retry_translate_and_send(
    bot: discord.Client | commands.Bot,
    channel_id: int,
    header: str,
    original_text: str,
    atts: List[Tuple[str, bytes]],
    lang: str,
    base_timeout: int,
    attempts: int,
    lag_sec: float,
):
    """Фоновый ретрай: добиваем перевод и отправляем, не блокируя основную очередь канала."""
    for i in range(attempts):
        try:
            text = await _translate_safe(original_text, lang, base_timeout=base_timeout + i*6)
            ch = await _fetch_channel(bot, channel_id)
            if ch:
                content = f"{header}\n\n{text}" if text else header
                await _send_with_order(ch, content, atts)
                print(f"[relay] retry OK -> ch={channel_id} lang={lang}")
            return
        except Exception as e:
            print(f"[relay] retry {i+1}/{attempts} failed lang={lang}: {e}")
            await asyncio.sleep(lag_sec)
    print(f"[relay] give up lang={lang} after retries")

# --- основной сервис с очередями по каналам ---
class DiscordRelayService:
    """
    Ретрансляция: ANNOUNCMENT -> EN, zh-CN, ja, ru.
    Порядок сообщений гарантируется очередями по целевым каналам:
    каждое исходное сообщение ставится в очередь с приоритетом по message.id.
    """

    def __init__(self) -> None:
        self._bot: discord.Client | commands.Bot | None = None
        self._queues: Dict[int, "asyncio.PriorityQueue"] = {}
        self._seq_counter: int = 0  # для стабильной сортировки при равном приоритете

    def attach(self, bot: discord.Client | commands.Bot):
        self._bot = bot
        bot.add_listener(self._on_ready, "on_ready")
        bot.add_listener(self._on_message, "on_message")

    # --- очередь/воркер по каналу ---
    def _get_queue(self, channel_id: int) -> "asyncio.PriorityQueue":
        q = self._queues.get(channel_id)
        if q is None:
            q = asyncio.PriorityQueue()
            self._queues[channel_id] = q
            asyncio.create_task(self._worker(channel_id, q))
        return q

    async def _worker(self, channel_id: int, q: "asyncio.PriorityQueue"):
        while True:
            priority, seq, job = await q.get()
            try:
                await job()
            except Exception as e:
                print(f"[relay] worker error ch={channel_id}: {e}")
            finally:
                q.task_done()

    def _enqueue(self, channel_id: int, priority: int, job: Callable[[], Awaitable[None]]):
        q = self._get_queue(channel_id)
        self._seq_counter += 1
        q.put_nowait((priority, self._seq_counter, job))

    # --- discord events ---
    async def _on_ready(self):
        if ENABLE_RELAY:
            print(f"[relay] enabled. ANNOUNCMENT={ANNOUNCMENT_ID} → "
                  f"EN={ENGLISH_CHAT_ID}, ZH_CN={TAIWAN_CHAT_ID}, JA={JAPANESE_CHAT_ID}, RU={RUSSIAN_CHAT_ID}, KO={KOREAN_CHAT_ID} "
                  f"| MODE={RELAY_MODE} | TEXT_FIRST={RELAY_SEND_TEXT_FIRST} | QUEUED=YES")
        else:
            print("[relay] disabled via ENABLE_DISCORD_RELAY")

    async def _on_message(self, message: discord.Message):
        if not ENABLE_RELAY or not self._bot:
            return
        if message.author == getattr(self._bot, "user", None):
            return
        if message.channel.id != ANNOUNCMENT_ID:
            return

        original_text = (message.content or "").strip()
        header = f"#announcement | {message.author.display_name} • {_fmt_time_local(message.created_at)}"
        atts = await _collect_attachments_bytes(message)
        priority = message.id  # snowflake -> монотонно растёт

        # --- EN job (оригинал или перевод в EN) ---
        if ENGLISH_CHAT_ID:
            async def en_job():
                ch = await _fetch_channel(self._bot, ENGLISH_CHAT_ID)
                if not ch:
                    return
                try:
                    if original_text:
                        if _is_probably_english(original_text):
                            text = original_text
                        else:
                            text = await _translate_safe(original_text, "en", BASE_TIMEOUT_EN)
                        await _send_with_order(ch, f"{header}\n\n{text}", atts)
                    else:
                        await _send_with_order(ch, header, atts)
                except Exception as e:
                    print("[relay] send EN failed:", e)
            self._enqueue(ENGLISH_CHAT_ID, priority, en_job)

        # --- Generic job per language/channel ---
        async def make_lang_job(channel_id: int, lang: str, base_timeout: int):
            async def _job():
                ch = await _fetch_channel(self._bot, channel_id)
                if not ch:
                    return

                if not original_text or not ENABLE_TRANSLATION:
                    try:
                        await _send_with_order(ch, header, atts)
                    except Exception as e:
                        print(f"[relay] send {lang} failed (no text):", e)
                    return

                try:
                    text = await _translate_safe(original_text, lang, base_timeout)
                    await _send_with_order(ch, f"{header}\n\n{text}", atts)
                except Exception as e:
                    print(f"[relay] translate/send failed lang={lang}: {e}")
                    if RELAY_MODE == "BEST_EFFORT":
                        try:
                            tmp = original_text if _is_probably_english(original_text) else \
                                  await _translate_safe(original_text, "en", BASE_TIMEOUT_EN)
                            await _send_with_order(ch, f"{header}\n\n{tmp}", atts)
                            print(f"[relay] temporary EN fallback sent to ch={channel_id}")
                        except Exception as e2:
                            print(f"[relay] fallback EN failed ch={channel_id}: {e2}")
                    # планируем фоновый ретрай (не блокирует очередь)
                    asyncio.create_task(_retry_translate_and_send(
                        self._bot, channel_id, header, original_text, atts,
                        lang, base_timeout, RELAY_ATTEMPTS, RELAY_RETRY_LAG_SEC
                    ))
            return _job

        if TAIWAN_CHAT_ID:
            self._enqueue(TAIWAN_CHAT_ID, priority, await make_lang_job(TAIWAN_CHAT_ID, "zh-CN", BASE_TIMEOUT_ZH))
        if JAPANESE_CHAT_ID:
            self._enqueue(JAPANESE_CHAT_ID, priority, await make_lang_job(JAPANESE_CHAT_ID, "ja", BASE_TIMEOUT_JA))
        if RUSSIAN_CHAT_ID:
            self._enqueue(RUSSIAN_CHAT_ID, priority, await make_lang_job(RUSSIAN_CHAT_ID, "ru", BASE_TIMEOUT_RU))
        if KOREAN_CHAT_ID:
            self._enqueue(KOREAN_CHAT_ID, priority, await make_lang_job(KOREAN_CHAT_ID, "ko", BASE_TIMEOUT_KO))


def setup_discord_relay(bot: discord.Client | commands.Bot) -> DiscordRelayService:
    svc = DiscordRelayService()
    svc.attach(bot)
    return svc
