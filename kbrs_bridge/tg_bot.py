# tg_bot.py
import os, asyncio, traceback, threading, time
from typing import Iterable, Optional, List
from dotenv import load_dotenv
import telebot
from telebot import types

from telebot import apihelper

from kbrs_bridge.db import (
    CAP_MINUTES,
    init_db,
    start_or_reset_torch, cancel_torch_for,
    list_active_upcoming, list_active_all,
    due_reminders, mark_reminder_fired, get_torch,
    sync_known_players, list_known_players
)

load_dotenv()
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = int(os.getenv("TG_CHAT_ID", "0"))
TG_TOPIC_FACTS = int(os.getenv("TG_TOPIC_FACTS", "2"))

WATCHER_USERNAMES = [u.strip() for u in os.getenv("TG_WATCHER_USERNAMES", "Twokndos,Billy_Nagamy").split(",") if u.strip()]

# --- основной список
KNOWN_PLAYERS = [
    "Deim0sAA", "s33y9", "styxay", "Sima89r", "trntt", "Twokndos", "Billy_Nagamy",
    "trntt + Sima89r",   # отдельный аккаунт
    "trntt - цея",       # новый отдельный аккаунт
]

# --- указываем, на кого должен ссылаться тег
MENTION_ALIAS = {
    "trntt + Sima89r": "trntt",  # тег -> @trntt
    "trntt - цея":     "trntt",  # тег -> @trntt
}

if not TG_BOT_TOKEN or TG_CHAT_ID == 0 or TG_TOPIC_FACTS == 0:
    raise SystemExit("TG_BOT_TOKEN/TG_CHAT_ID/TG_TOPIC_FACTS не заданы в .env")

init_db()
sync_known_players(KNOWN_PLAYERS)

bot = telebot.TeleBot(TG_BOT_TOKEN, parse_mode=None)  # для polling и хендлеров
bot_sender = telebot.TeleBot(TG_BOT_TOKEN, parse_mode=None)  # ОТДЕЛЬНЫЙ экземпляр для отправки из Discord

_SCHEDULER_STARTED = False

_POLLING_STARTED = False

def start_polling_in_thread():
    global _POLLING_STARTED
    if _POLLING_STARTED:
        print("[tg] polling already started; skip")
        return

    def _runner():
        print("[tg] polling started")
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                # на всякий случай снимем вебхук, если он когда-то ставился
                try:
                    print("[tg] removing webhook...")
                    bot.remove_webhook()
                    time.sleep(1)  # даем Telegram время обработать удаление
                except Exception as e:
                    print(f"[tg] webhook removal failed (non-critical): {e}")

                print("[tg] starting infinity_polling...")
                bot.infinity_polling(skip_pending=True, allowed_updates=["message", "callback_query"])
                break  # если polling завершился нормально, выходим

            except apihelper.ApiTelegramException as e:
                if _is_409(e):
                    retry_count += 1
                    print(f"[tg] 409 Conflict detected (attempt {retry_count}/{max_retries}). Another bot instance may be running.")
                    if retry_count < max_retries:
                        print(f"[tg] Waiting 5 seconds before retry...")
                        time.sleep(5)
                    else:
                        print("[tg] Max retries reached. Giving up on polling.")
                        print("[tg] Make sure no other bot instances are running (local/server).")
                        break
                else:
                    print(f"[tg] polling crashed with API error: {e}")
                    break
            except Exception as e:
                print(f"[tg] polling crashed with unexpected error: {e}")
                break

        print("[tg] polling stopped")

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    _POLLING_STARTED = True
    return t

def _is_409(e: Exception) -> bool:
    try:
        return (getattr(e, "result", None) and getattr(e.result, "status_code", None) == 409) or ("409" in str(e))
    except Exception:
        return False
# --------------- УТИЛИТЫ ОТПРАВКИ ---------------

# --- Постоянная клавиатура (reply) ---
# --- Постоянная клавиатура (reply) ---
MAIN_BTN_SPENT   = "🕯 Отметить «все потрачены»"
MAIN_BTN_UPC     = "⏱ Ближайшие капы"
MAIN_BTN_LIST    = "🗒️ Все таймеры"
MAIN_BTN_CANCEL  = "❌ Отменить мой таймер"

# Набор текстов кнопок для фильтра в лог-хендлере
TOPIC_BUTTONS = {
    MAIN_BTN_SPENT,
    MAIN_BTN_UPC,
    MAIN_BTN_LIST,
    MAIN_BTN_CANCEL,
}

def main_reply_kb() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, selective=False)
    kb.row(MAIN_BTN_SPENT)
    kb.row(MAIN_BTN_UPC, MAIN_BTN_LIST)
    kb.row(MAIN_BTN_CANCEL)
    return kb

def start_facts_scheduler():
    """Запускает фоновый луп напоминаний, можно вызывать многократно — безопасно."""
    global _SCHEDULER_STARTED
    if _SCHEDULER_STARTED:
        return
    threading.Thread(target=_scheduler_loop, daemon=True).start()
    _SCHEDULER_STARTED = True

# def start_polling_in_thread():
#     """Запускает TeleBot.infinity_polling() в отдельном daemon-потоке."""
#     def _runner():
#         print("[tg] polling started")
#         bot.infinity_polling(skip_pending=True, allowed_updates=["message", "callback_query"])
#         print("[tg] polling stopped")

#     t = threading.Thread(target=_runner, daemon=True)
#     t.start()
#     return t

def _chunks(text: str, limit: int = 4096):
    if not text:
        return
    if len(text) <= limit:
        yield text; return
    cur, cur_len = [], 0
    for line in text.splitlines(True):
        if cur_len + len(line) > limit:
            yield "".join(cur); cur, cur_len = [line], len(line)
        else:
            cur.append(line); cur_len += len(line)
    if cur: yield "".join(cur)

def _send_sync(text: str, chat_id: Optional[int] = None, thread_id: Optional[int] = None):
    if not text:
        return
    for chunk in _chunks(text):
        try:
            bot_sender.send_message(chat_id if chat_id is not None else TG_CHAT_ID, chunk, message_thread_id=thread_id)
        except apihelper.ApiTelegramException as e:
            if _is_409(e) and thread_id is not None:
                bot_sender.send_message(chat_id if chat_id is not None else TG_CHAT_ID, chunk)  # без треда
            else:
                raise

def _send_photo_sync(photo_url: str, caption: Optional[str] = None, chat_id: Optional[int] = None, thread_id: Optional[int] = None):
    try:
        bot_sender.send_photo(chat_id if chat_id else TG_CHAT_ID, photo=photo_url, caption=caption, message_thread_id=thread_id)
    except apihelper.ApiTelegramException as e:
        if _is_409(e) and thread_id is not None:
            bot_sender.send_photo(chat_id if chat_id else TG_CHAT_ID, photo=photo_url, caption=caption)
        else:
            raise

def _send_document_sync(file_url: str, caption: Optional[str] = None, chat_id: Optional[int] = None, thread_id: Optional[int] = None):
    try:
        bot_sender.send_document(chat_id if chat_id else TG_CHAT_ID, document=file_url, caption=caption, message_thread_id=thread_id)
    except apihelper.ApiTelegramException as e:
        if _is_409(e) and thread_id is not None:
            bot_sender.send_document(chat_id if chat_id else TG_CHAT_ID, document=file_url, caption=caption)
        else:
            raise

async def send_text(text: str, retries: int = 3):
    await send_text_to(None, text, thread_id=None, retries=retries)

async def send_text_to(chat_id: Optional[int], text: str, thread_id: Optional[int] = None, retries: int = 3):
    for chunk in _chunks(text):
        attempt = 0
        while True:
            try:
                await asyncio.to_thread(_send_sync, chunk, chat_id, thread_id)
                break
            except Exception as e:
                attempt += 1
                print(f"[tg] send failed (attempt {attempt}/{retries}) chat_id={chat_id}, thread_id={thread_id}: {e}")
                traceback.print_exc()
                if attempt >= retries:
                    break
                await asyncio.sleep(1.0)

async def send_photo(photo_url: str, caption: Optional[str] = None, retries: int = 3):
    await send_photo_to(None, photo_url, caption, thread_id=None, retries=retries)

async def send_photo_to(chat_id: Optional[int], photo_url: str, caption: Optional[str] = None, thread_id: Optional[int] = None, retries: int = 3):
    attempt = 0
    while True:
        try:
            await asyncio.to_thread(_send_photo_sync, photo_url, caption, chat_id, thread_id)
            break
        except Exception as e:
            attempt += 1
            print(f"[tg] send_photo failed (attempt {attempt}/{retries}) chat_id={chat_id}, thread_id={thread_id}: {e}")
            traceback.print_exc()
            if attempt >= retries:
                break
            await asyncio.sleep(1.0)

async def send_document_to(chat_id: Optional[int], file_url: str, caption: Optional[str] = None, thread_id: Optional[int] = None, retries: int = 3):
    attempt = 0
    while True:
        try:
            await asyncio.to_thread(_send_document_sync, file_url, caption, chat_id, thread_id)
            break
        except Exception as e:
            attempt += 1
            print(f"[tg] send_document failed (attempt {attempt}/{retries}) chat_id={chat_id}, thread_id={thread_id}: {e}")
            traceback.print_exc()
            if attempt >= retries:
                break
            await asyncio.sleep(1.0)

def _send_media_group_sync(photo_urls: list[str], chat_id: Optional[int] = None, thread_id: Optional[int] = None):
    media = [types.InputMediaPhoto(url) for url in photo_urls]
    try:
        bot_sender.send_media_group(chat_id if chat_id else TG_CHAT_ID, media=media, message_thread_id=thread_id)
    except apihelper.ApiTelegramException as e:
        if _is_409(e) and thread_id is not None:
            bot_sender.send_media_group(chat_id if chat_id else TG_CHAT_ID, media=media)  # целиком без треда
        else:
            raise

async def push_to_tg(payload: dict, default_chat_id: Optional[int] = None, default_thread_id: Optional[int] = None, retries: int = 3):
    kind = (payload.get("kind") or "").lower()
    chat_id = payload.get("chat_id", default_chat_id)
    thread_id = payload.get("thread_id", default_thread_id)

    if kind == "text":
        text = payload.get("text", "")
        await _push_text(text, chat_id, thread_id, retries); return
    if kind == "photo":
        url = payload.get("url"); caption = payload.get("caption")
        await _push_photo(url, caption, chat_id, thread_id, retries); return
    if kind == "album":
        urls = payload.get("urls") or []
        await _push_album(urls, chat_id, thread_id, retries); return
    if kind == "document":
        url = payload.get("url"); caption = payload.get("caption")
        await _push_document(url, caption, chat_id, thread_id, retries); return
    raise ValueError(f"[tg] unsupported payload kind={kind!r}")

async def _push_text(text, chat_id, thread_id, retries):
    for chunk in _chunks(text):
        attempt = 0
        while True:
            try:
                await asyncio.to_thread(_send_sync, chunk, chat_id, thread_id); break
            except apihelper.ApiTelegramException as e:
                attempt += 1
                if _is_409(e) and thread_id is not None:
                    try:
                        await asyncio.to_thread(_send_sync, chunk, chat_id, None); break
                    except Exception:
                        if attempt >= retries: raise
                        await asyncio.sleep(1.0)
                else:
                    if attempt >= retries: raise
                    await asyncio.sleep(1.0)

async def _push_photo(url, caption, chat_id, thread_id, retries):
    attempt = 0
    while True:
        try:
            await asyncio.to_thread(_send_photo_sync, url, caption, chat_id, thread_id); return
        except apihelper.ApiTelegramException as e:
            attempt += 1
            if _is_409(e) and thread_id is not None:
                try:
                    await asyncio.to_thread(_send_photo_sync, url, caption, chat_id, None); return
                except Exception:
                    if attempt >= retries: raise
                    await asyncio.sleep(1.0)
            else:
                if attempt >= retries: raise
                await asyncio.sleep(1.0)

async def _push_album(urls, chat_id, thread_id, retries):
    attempt = 0
    while True:
        try:
            await asyncio.to_thread(_send_media_group_sync, urls, chat_id, thread_id); return
        except apihelper.ApiTelegramException as e:
            attempt += 1
            if _is_409(e) and thread_id is not None:
                try:
                    await asyncio.to_thread(_send_media_group_sync, urls, chat_id, None); return
                except Exception:
                    for u in urls: await _push_photo(u, None, chat_id, None, retries=1)
                    return
            else:
                if attempt >= retries:
                    for u in urls: await _push_photo(u, None, chat_id, thread_id, retries=1)
                    return
                await asyncio.sleep(1.0)

async def _push_document(url, caption, chat_id, thread_id, retries):
    attempt = 0
    while True:
        try:
            await asyncio.to_thread(_send_document_sync, url, caption, chat_id, thread_id); return
        except apihelper.ApiTelegramException as e:
            attempt += 1
            if _is_409(e) and thread_id is not None:
                try:
                    await asyncio.to_thread(_send_document_sync, url, caption, chat_id, None); return
                except Exception:
                    if attempt >= retries: raise
                    await asyncio.sleep(1.0)
            else:
                if attempt >= retries: raise
                await asyncio.sleep(1.0)

async def send_media_group_to(chat_id: Optional[int], photo_urls: list[str], thread_id: Optional[int] = None, retries: int = 3):
    if not photo_urls:
        return
    for i in range(0, len(photo_urls), 10):
        chunk = photo_urls[i:i+10]
        attempt = 0
        while True:
            try:
                await asyncio.to_thread(_send_media_group_sync, chunk, chat_id, thread_id)
                break
            except Exception as e:
                attempt += 1
                print(f"[tg] send_media_group failed (attempt {attempt}/{retries}) thread_id={thread_id}: {e}")
                traceback.print_exc()
                if attempt >= retries:
                    break
                await asyncio.sleep(1.0)

# --- Вспомогалки для текста ---
def _ctx_ids(src: types.Message | types.CallbackQuery) -> tuple[int, int | None]:
    m = src.message if isinstance(src, types.CallbackQuery) else src
    cid = int(m.chat.id)
    tid = int(m.message_thread_id) if getattr(m, "is_topic_message", False) else None
    return cid, tid

def post_to_facts_thread(text: str, *, src: types.Message | types.CallbackQuery | None = None, thread_id: int | None = None):
    """
    Пишем служебное сообщение в НУЖНЫЙ тред внутри группы:
    - если src из темы этой группы -> берем её thread_id
    - иначе -> берем thread_id аргумент или TG_TOPIC_FACTS
    """
    tid = None
    if src is not None:
        cid, maybe_tid = _ctx_ids(src)
        if cid == TG_CHAT_ID and maybe_tid is not None:
            tid = maybe_tid
    if tid is None:
        tid = thread_id if thread_id is not None else TG_TOPIC_FACTS

    # ВСЕГДА шлём в TG_CHAT_ID!
    _send_sync(text, chat_id=TG_CHAT_ID, thread_id=tid)

def reply_in_context(src: types.Message | types.CallbackQuery, text: str, *, reply_markup=None):
    """
    Отвечаем пользователю там, где он нажал:
    - если это тема в группе -> укажем message_thread_id, чтобы ответ лёг в тему
    - если ЛС/обычный чат -> без thread_id
    """
    m = src.message if isinstance(src, types.CallbackQuery) else src
    cid = int(m.chat.id)
    if getattr(m, "is_topic_message", False):
        bot.send_message(cid, text, reply_markup=reply_markup, message_thread_id=m.message_thread_id)
    else:
        bot.send_message(cid, text, reply_markup=reply_markup)

def _fmt_hhmm(seconds: int) -> str:
    m = max(0, seconds) // 60
    h, m = divmod(m, 60)
    return f"{h:01d}ч {m:02d}м"

def _fmt_hhmm_from_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m = seconds // 60
    h, m = divmod(m, 60)
    return f"{h:01d}ч {m:02d}м"

_ORD_RU = {1: "1-го", 2: "2-го", 3: "3-го", 4: "4-го", 5: "5-го", 6: "6-го"}

def _next_torch_info(*, started_at: int, cap_minutes: int, now_ts: int) -> tuple[int, int, int]:
    """
    Считает:
      - номер ближайшего факела N (1..6; при полном капе возвращаем 1),
      - секунды до N-го факела,
      - секунды до полного капа.
    Без обращения к БД, только по started_at/cap_minutes/now_ts.
    """
    total = cap_minutes * 60
    one = total // 6
    cap_at = started_at + total
    rem_full = cap_at - now_ts
    if rem_full <= 0:
        # полный кап
        return (1, 0, 0)
    # сколько факелов НЕ восстановилось (0..6)
    missing = (rem_full + one - 1) // one  # ceil(rem_full / one)
    # уже доступно:
    available = 6 - missing
    # номер ближайшего, который вот-вот восстановится:
    next_idx = min(available + 1, 6)
    # время до следующего «щелчка»
    # rem_full в ((missing-1)*one, missing*one], значит:
    t_next = rem_full - (missing - 1) * one
    return (next_idx, max(0, t_next), max(0, rem_full))

def _watchers_suffix(*, exclude: Iterable[str] = ()) -> str:
    """Возвращает строку с тегами наблюдателей, исключая указанных."""
    ex = {u.lstrip("@") for u in (exclude or [])}
    names = [u for u in WATCHER_USERNAMES if u and u not in ex]
    at = " ".join(f"@{u}" for u in names) if names else ""
    return f" {at}".rstrip()

# --------------- КЛАВИАТУРЫ ---------------

def main_menu_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🕯 Отметить «все потрачены»", callback_data="torch_spent"),
    )
    kb.row(
        types.InlineKeyboardButton("⏱ Ближайшие капы", callback_data="torch_upcoming"),
        types.InlineKeyboardButton("📋 Все таймеры", callback_data="torch_list"),
    )
    kb.row(
        types.InlineKeyboardButton("❌ Отменить мой таймер", callback_data="torch_cancel_me"),
    )
    return kb

def who_menu_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("Это я", callback_data="torch_me"),
        types.InlineKeyboardButton("Другой игрок…", callback_data="torch_other"),
    )
    kb.row(types.InlineKeyboardButton("📖 Выбрать из списка", callback_data="torch_pick_list"))
    return kb

# выбор из списка
@bot.callback_query_handler(func=lambda c: c.data == "torch_pick_list")
def cb_torch_pick_list(call: types.CallbackQuery):
    # всегда отвечаем коллбэку, чтобы кнопка не «висела»
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    # определяем контекст (чат/тред)
    m = call.message
    cid = m.chat.id
    tid = m.message_thread_id if getattr(m, "is_topic_message", False) else None

    # тянем список игроков
    try:
        players = list_known_players()
    except Exception as e:
        print("[torch_pick_list] list_known_players error:", e)
        players = []

    if not players:
        # отправим отдельным сообщением в контекст
        if tid is not None:
            bot.send_message(cid, "Список игроков пуст. Добавь через «Другой игрок…».", message_thread_id=tid)
        else:
            bot.send_message(cid, "Список игроков пуст. Добавь через «Другой игрок…».")
        return

    # собираем инлайн-клавиатуру
    kb = types.InlineKeyboardMarkup(row_width=3)
    buttons = [types.InlineKeyboardButton(f"@{p}", callback_data=f"torch_pick:{p}") for p in players]
    # разложим рядами по 3
    for i in range(0, len(buttons), 3):
        kb.row(*buttons[i:i+3])
    # кнопка «назад» — возвращает к выбору «Это я / Другой игрок»
    kb.row(types.InlineKeyboardButton("↩ Назад", callback_data="torch_spent"))

    # вместо редактирования — отправляем НОВОЕ сообщение с клавой в ТОТ ЖЕ тред
    if tid is not None:
        bot.send_message(cid, "Кого отметить?", reply_markup=kb, message_thread_id=tid)
    else:
        bot.send_message(cid, "Кого отметить?", reply_markup=kb)

    print(f"[torch_pick_list] shown in chat={cid} thread={tid}")


@bot.callback_query_handler(func=lambda c: c.data.startswith("torch_pick:"))
def cb_torch_pick(call: types.CallbackQuery):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    # кого выбрали
    try:
        username = call.data.split(":", 1)[1]
    except Exception:
        return

    # контекст вызова
    m = call.message
    cid = m.chat.id
    tid = m.message_thread_id if getattr(m, "is_topic_message", False) else None

    # где хранить/куда слать напоминания: если нажали в теме этой группы — используем её; иначе дефолт из .env
    thread_for_db = tid if (cid == TG_CHAT_ID and tid is not None) else TG_TOPIC_FACTS

    try:
        info = start_or_reset_torch(
            username,
            reported_by=call.from_user.id,
            thread_id=thread_for_db,
            chat_id=TG_CHAT_ID  # ВСЕГДА анонсы/напоминания в группу с темами
        )
    except Exception as e:
        print("[torch_pick] start_or_reset_torch error:", e)
        if tid is not None:
            bot.send_message(cid, "Ошибка запуска таймера. Попробуй ещё раз.", message_thread_id=tid)
        else:
            bot.send_message(cid, "Ошибка запуска таймера. Попробуй ещё раз.")
        return

    eta = info["cap_at"] - int(time.time())

    # Анонс в НУЖНЫЙ тред группы
    _send_sync(
        f"🕯 У {username} потрачены все факела — таймер перезапущен.\nДо полного капа: {_fmt_hhmm(eta)}.{_watchers_suffix()}",
        chat_id=TG_CHAT_ID,
        thread_id=thread_for_db
    )

    # Короткий ответ туда, где пользователь нажал кнопку (ЛС/тема)
    if tid is not None:
        bot.send_message(cid, f"Готово! {username} — до капа {_fmt_hhmm(eta)}.", message_thread_id=tid, reply_markup=main_reply_kb())
    else:
        bot.send_message(cid, f"Готово! {username} — до капа {_fmt_hhmm(eta)}.", reply_markup=main_reply_kb())

    print(f"[torch_pick] @{username} set from chat={cid} thread={tid} -> thread_db={thread_for_db}")

# @bot.callback_query_handler(func=lambda c: c.data.startswith("torch_pick:"))
# def cb_torch_pick(call: types.CallbackQuery):
#     bot.answer_callback_query(call.id)
#     username = call.data.split(":", 1)[1]
#     info = start_or_reset_torch(username, reported_by=call.from_user.id, thread_id=TG_TOPIC_FACTS)
#     eta = info["cap_at"] - int(time.time())
#     _send_sync(
#         f"🕯 У @{username} потрачены все факела — таймер перезапущен.\nДо полного капа: {_fmt_hhmm(eta)}.{_watchers_suffix()}",
#         chat_id=TG_CHAT_ID, thread_id=TG_TOPIC_FACTS
#     )
#     bot.edit_message_text(
#         f"Готово! @{username} — до капа {_fmt_hhmm(eta)}.",
#         chat_id=call.message.chat.id,
#         message_id=call.message.message_id,
#         reply_markup=main_menu_kb()
#     )

# --------------- ХЕНДЛЕРЫ ---------------
def _facts_thread_id_for(msg: types.Message | types.CallbackQuery) -> int:
    """Если вызвано из темы – вернуть её id; иначе – TG_TOPIC_FACTS из .env."""
    m = msg.message if isinstance(msg, types.CallbackQuery) else msg
    if getattr(m, "is_topic_message", False) and m.chat.id == TG_CHAT_ID:
        return int(m.message_thread_id)
    return int(TG_TOPIC_FACTS)

def _is_facts_topic_message(m: types.Message) -> bool:
    # можно принимать и в ЛС, и в любой чат, и обязательно — в заданной ветке
    if getattr(m, "is_topic_message", False):
        return (m.chat.id == TG_CHAT_ID) and (m.message_thread_id == TG_TOPIC_FACTS)
    return True  # ЛС и обычные группы тоже ок

@bot.message_handler(func=lambda m: _is_facts_topic_message(m) and (m.text == MAIN_BTN_SPENT))
def on_btn_spent(m: types.Message):
    kw = _thread_kwargs_from(m)
    bot.send_message(m.chat.id, "У кого потрачены все 6 факелов?", reply_markup=who_menu_kb(), **kw)

@bot.message_handler(func=lambda m: _is_facts_topic_message(m) and (m.text == MAIN_BTN_UPC))
def on_btn_upcoming(m: types.Message):
    kw = _thread_kwargs_from(m)
    rows = list_active_upcoming(limit=10)
    text = "Пока нет активных таймеров." if not rows else \
        "⏱ Ближайшие капы:\n" + "\n".join(
            f"• {r['username']} — через {_fmt_hhmm(r['cap_at'] - int(time.time()))}" for r in rows)
    bot.send_message(m.chat.id, text, reply_markup=main_reply_kb(), **kw)

@bot.message_handler(func=lambda m: _is_facts_topic_message(m) and (m.text == MAIN_BTN_LIST))
def on_btn_list(m: types.Message):
    kw = _thread_kwargs_from(m)
    rows = list_active_all()
    if not rows:
        bot.send_message(m.chat.id, "Активных таймеров нет.", reply_markup=main_reply_kb(), **kw)
        return

    now = int(time.time())
    lines = ["🗒️ Активные таймеры:"]
    for r in rows:
        # ожидаем, что в row есть started_at и cap_at (или хотя бы started_at)
        # Если list_active_all уже отдаёт started_at и cap_at — используем их.
        # Если отдаёт только cap_at, можно восстановить started_at = cap_at - CAP_MINUTES*60
        started_at = int(r.get("started_at") or (int(r["cap_at"]) - CAP_MINUTES * 60))
        next_idx, sec_next, sec_cap = _next_torch_info(
            started_at=started_at,
            cap_minutes=CAP_MINUTES,
            now_ts=now
        )
        lines.append(
            f"• {r['username']} — до {_ORD_RU[next_idx]} факела: {_fmt_hhmm_from_seconds(sec_next)}, "
            f"до полного капа: {_fmt_hhmm_from_seconds(sec_cap)}"
        )

    bot.send_message(m.chat.id, "\n".join(lines), reply_markup=main_reply_kb(), **kw)

# отмена таймера по reply-кнопке
@bot.message_handler(func=lambda m: _is_facts_topic_message(m) and (m.text == MAIN_BTN_CANCEL))
def on_btn_cancel(m: types.Message):
    username = (m.from_user.username or "").strip()
    if not username:
        reply_in_context(m, "У тебя нет @username в Telegram, отменять нечего.", reply_markup=main_reply_kb())
        return

    cid, tid = _ctx_ids(m)
    thread_for_db = tid if (cid == TG_CHAT_ID and tid is not None) else TG_TOPIC_FACTS
    ok = cancel_torch_for(username)
    if ok:
        post_to_facts_thread(f"🔕 Таймер @{username} отменён.", thread_id=thread_for_db)
        reply_in_context(m, f"Окей, @{username}, таймер снят.", reply_markup=main_reply_kb())
    else:
        reply_in_context(m, "Активного таймера не найдено.", reply_markup=main_reply_kb())


@bot.message_handler(commands=['facts','torches','факелы'])
def cmd_facts(message: types.Message):
    kw = _thread_kwargs_from(message)
    bot.send_message(message.chat.id, "🔥 Напоминалка капа факелов. Выбери действие:",
                     reply_markup=main_reply_kb(), **kw)
    bot.send_message(message.chat.id, "Меню:", reply_markup=main_menu_kb(), **kw)

@bot.callback_query_handler(func=lambda c: c.data == "torch_spent")
def cb_torch_spent(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "У кого потрачены все 6 факелов?",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=who_menu_kb()
    )

# torch_me
@bot.callback_query_handler(func=lambda c: c.data == "torch_me")
def cb_torch_me(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    username = (call.from_user.username or "").strip()
    if not username:
        reply_in_context(call, "У тебя нет @username в Telegram. Укажи через кнопку «Другой игрок…».", reply_markup=main_reply_kb())
        return

    # для хранения в БД: chat_id/thread_id того контекста, где действие инициировано
    cid, tid = _ctx_ids(call)
    thread_for_db = tid if (cid == TG_CHAT_ID and tid is not None) else TG_TOPIC_FACTS
    info = start_or_reset_torch(username, reported_by=call.from_user.id, thread_id=thread_for_db, chat_id=TG_CHAT_ID)
    eta = info["cap_at"] - int(time.time())

    # анонс в НУЖНЫЙ тред (всегда в TG_CHAT_ID)
    post_to_facts_thread(
        f"🕯 У {username} потрачены все факела — таймер перезапущен.\nДо полного капа: {_fmt_hhmm(eta)}.{_watchers_suffix()}",
        src=call
    )
    # подтверждение пользователю — туда, где он нажал
    reply_in_context(call, f"Готово! {username} — до капа {_fmt_hhmm(eta)}.", reply_markup=main_reply_kb())


@bot.callback_query_handler(func=lambda c: c.data == "torch_other")
def cb_torch_other(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    m = call.message
    if getattr(m, "is_topic_message", False):
        msg = bot.send_message(m.chat.id, "Введи @username игрока. Пример: `@Dirka`", parse_mode="Markdown", message_thread_id=m.message_thread_id)
    else:
        msg = bot.send_message(m.chat.id, "Введи @username игрока. Пример: `@Dirka`", parse_mode="Markdown")
    bot.register_next_step_handler(msg, _on_other_username, call.from_user.id, m.chat.id, (m.message_thread_id if m.is_topic_message else None))


def _on_other_username(message: types.Message, reporter_id: int, src_chat_id: int, src_thread_id: int | None):
    text = (message.text or "").strip()
    if not text.startswith("@") or len(text) < 2:
        if src_thread_id is not None:
            bot.reply_to(message, "Нужен @username, например: @Dirka", message_thread_id=src_thread_id)
        else:
            bot.reply_to(message, "Нужен @username, например: @Dirka")
        return

    username = text.lstrip("@").strip()
    thread_for_db = src_thread_id if (src_chat_id == TG_CHAT_ID and src_thread_id is not None) else TG_TOPIC_FACTS
    info = start_or_reset_torch(username, reported_by=reporter_id, thread_id=thread_for_db, chat_id=TG_CHAT_ID)
    eta = info["cap_at"] - int(time.time())

    # анонс в нужный тред группы
    post_to_facts_thread(
        f"🕯 У {username} потрачены все факела — таймер перезапущен (отметил @{message.from_user.username or message.from_user.id}).\nДо полного капа: {_fmt_hhmm(eta)}.{_watchers_suffix()}",
        thread_id=thread_for_db
    )
    # ответ пользователю
    if src_thread_id is not None:
        bot.reply_to(message, f"Готово! {username} — до капа {_fmt_hhmm(eta)}.", message_thread_id=src_thread_id)
    else:
        bot.reply_to(message, f"Готово! {username} — до капа {_fmt_hhmm(eta)}.")

@bot.callback_query_handler(func=lambda c: c.data == "torch_upcoming")
def cb_torch_upcoming(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    rows = list_active_upcoming(limit=10)
    if not rows:
        bot.send_message(call.message.chat.id, "Пока нет активных таймеров.")
        return
    lines = ["⏱ Ближайшие капы:"]
    now = int(time.time())
    for r in rows:
        eta = r["cap_at"] - now
        lines.append(f"• {r['username']} — через {_fmt_hhmm(eta)}")
    bot.send_message(call.message.chat.id, "\n".join(lines))

@bot.callback_query_handler(func=lambda c: c.data == "torch_list")
def cb_torch_list(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    rows = list_active_all()
    if not rows:
        bot.send_message(call.message.chat.id, "Активных таймеров нет.")
        return

    now = int(time.time())
    lines = ["🗒️ Активные таймеры:"]
    for r in rows:
        started_at = int(r.get("started_at") or (int(r["cap_at"]) - CAP_MINUTES * 60))
        next_idx, sec_next, sec_cap = _next_torch_info(
            started_at=started_at,
            cap_minutes=CAP_MINUTES,
            now_ts=now
        )
        lines.append(
            f"• {r['username']} — до {_ORD_RU[next_idx]} факела: {_fmt_hhmm_from_seconds(sec_next)}, "
            f"до полного капа: {_fmt_hhmm_from_seconds(sec_cap)}"
        )

    bot.send_message(call.message.chat.id, "\n".join(lines))

@bot.callback_query_handler(func=lambda c: c.data == "torch_cancel_me")
def cb_torch_cancel_me(call: types.CallbackQuery):
    bot.answer_callback_query(call.id)
    username = (call.from_user.username or "").strip()
    if not username:
        bot.send_message(call.message.chat.id, "У тебя нет @username в Telegram, отменять нечего.")
        return
    ok = cancel_torch_for(username)
    if ok:
        _send_sync(f"🔕 Таймер @{username} отменён.", chat_id=TG_CHAT_ID, thread_id=TG_TOPIC_FACTS)
        bot.send_message(call.message.chat.id, f"Окей, @{username}, таймер снят.")
    else:
        bot.send_message(call.message.chat.id, "Активного таймера не найдено.")

# Опционально: показать ID темы
@bot.message_handler(commands=['topic_id'])
def get_topic_id(message: types.Message):
    if message.is_topic_message:
        bot.reply_to(message, f"🧩 ID этой темы: `{message.message_thread_id}`", parse_mode="Markdown")
    else:
        bot.reply_to(message, "⚠️ Это сообщение не в теме (форуме).")


@bot.message_handler(func=lambda m: (
    getattr(m, "is_topic_message", False)
    and not ( (m.text or "").startswith("/") or (m.text or "") in TOPIC_BUTTONS )
))
def echo_topic_info(message: types.Message):
    # чисто лог, не мешаем остальным хендлерам
    try:
        print(f"Сообщение в теме ID={message.message_thread_id} | чат={message.chat.id}")
    except Exception:
        pass

def _thread_kwargs_from(msg: types.Message) -> dict:
    if getattr(msg, "is_topic_message", False):
        return {"message_thread_id": msg.message_thread_id}
    return {}
# --------------- ФОНОВЫЙ ПЛАНИРОВЩИК ---------------

def _scheduler_loop():
    """Фоновый оповещатель: раз в 20с проверяет due-напоминания и шлёт их
       в ТОТ ЖЕ чат/тред, где был запущен таймер (берёт из БД chat_id/thread_id)."""
    print("[scheduler] started")
    while True:
        try:
            now = int(time.time())
            # due_reminders возвращает список (rem_id, torch_id, due_at, kind)
            due = due_reminders(now_ts=now, limit=500)

            if not due:
                time.sleep(20)
                continue

            # Группируем по torch_id, чтобы решить конфликт pre vs cap
            grouped = {}  # torch_id -> {"cap": (rem_id, due_at) | None, "pres": [(rem_id, due_at), ...]}
            for rem_id, tid, due_at, kind in due:
                g = grouped.setdefault(tid, {"cap": None, "pres": []})
                if kind == "cap":
                    g["cap"] = (rem_id, due_at)
                else:
                    g["pres"].append((rem_id, due_at))

            for torch_id, g in grouped.items():
                rec = get_torch(torch_id)
                # Структура: id, tg_username, started_at, reported_by, thread_id, active, chat_id
                if not rec:
                    # Помечаем всё fired, чтобы не зацикливаться
                    if g["cap"]:
                        mark_reminder_fired(g["cap"][0])
                    for rid, _ in g["pres"]:
                        mark_reminder_fired(rid)
                    continue

                _tid, username, started_at, reported_by, thread_id, active, chat_id = rec
                if not active:
                    if g["cap"]:
                        mark_reminder_fired(g["cap"][0])
                    for rid, _ in g["pres"]:
                        mark_reminder_fired(rid)
                    continue

                # Куда слать: используем сохранённые chat_id/thread_id; если их нет — дефолты из .env
                target_chat = int(chat_id) if chat_id else TG_CHAT_ID
                target_thread = int(thread_id) if thread_id else TG_TOPIC_FACTS

                # подставляем имя для упоминания
                mention_name = MENTION_ALIAS.get(username, username)

                # === 🔥 Полный кап ===
                if g["cap"]:
                    _send_sync(
                        f"🔥 @{mention_name} — ПОЛНЫЙ КАП ФАКЕЛОВ! Пора заходить.{_watchers_suffix(exclude=[mention_name])}",
                        chat_id=target_chat,
                        thread_id=target_thread
                    )
                    mark_reminder_fired(g["cap"][0])
                    for rid, _ in g["pres"]:
                        mark_reminder_fired(rid)
                    continue

                # === ⏳ Промежуточные напоминания ===
                cap_at = int(started_at) + CAP_MINUTES * 60
                for rid, _ in sorted(g["pres"], key=lambda x: x[1]):
                    mins_left = max(0, (cap_at - now) // 60)
                    msg_left = f"{mins_left//60}ч {mins_left%60:02d}м" if mins_left >= 60 else f"{mins_left}м"

                    urgent = mins_left <= 10  # теги только на 10 мин
                    name = f"@{mention_name}" if urgent else username
                    suffix = _watchers_suffix(exclude=[mention_name]) if urgent else ""

                    _send_sync(
                        f"⏳ У {name} полный кап через {msg_left}!{suffix}",
                        chat_id=target_chat,
                        thread_id=target_thread
                    )
                    mark_reminder_fired(rid)

        except Exception as e:
            print("[scheduler] error:", e)
            traceback.print_exc()

        time.sleep(20)

# Стартуем планировщик в отдельном потоке
# threading.Thread(target=_scheduler_loop, daemon=True).start()

@bot.message_handler(commands=['start'])
def cmd_start(message: types.Message):
    kw = _thread_kwargs_from(message)
    username = message.from_user.username or message.from_user.first_name or "игрок"
    bot.send_message(message.chat.id,
                     f"Привет, @{username}!\n\nЯ — напоминалка капа факелов 🔥\nВыбери действие 👇",
                     reply_markup=main_reply_kb(), **kw)
    bot.send_message(message.chat.id, "Меню:", reply_markup=main_menu_kb(), **kw)

if __name__ == "__main__":
    print("Bot is running. Topic (facts) =", TG_TOPIC_FACTS)
    bot.infinity_polling(skip_pending=True, allowed_updates=["message", "callback_query"])
