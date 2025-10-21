# bot/helpers_events.py (новый модуль в боте)
import re
import discord
from collections import Counter

# юникод-эмодзи — очень большой диапазон; упростим счётчик (по \p{Emoji} лучше через regex-модуль), тут — эвристика
EMOJI_UNICODE_RE = re.compile(
    "["                       # грубая маска известных диапазонов
    "\U0001F300-\U0001F6FF"   # Misc Symbols and Pictographs
    "\U0001F700-\U0001F77F"   # Alchemical Symbols
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE
)

URL_RE = re.compile(r"https?://", re.I)
CUSTOM_EMOJI_RE = re.compile(r"<(a?):([A-Za-z0-9_~\-]+):([0-9]+)>")  # <:name:id> или <a:name:id>

def extract_custom_emoji_keys(text: str) -> list[str]:
    keys = []
    for m in CUSTOM_EMOJI_RE.finditer(text or ""):
        animated, name, id_ = m.groups()
        if animated:
            keys.append(f"a:{name}:{id_}")
        else:
            keys.append(f"{name}:{id_}")
    return keys

def count_unicode_emojis(text: str) -> int:
    if not text:
        return 0
    # просто считаем количество символов из диапазона
    return sum(1 for _ in EMOJI_UNICODE_RE.finditer(text))

def extract_emoji_usage(text: str) -> Counter:
    cnt = Counter()
    # кастомные
    for key in extract_custom_emoji_keys(text):
        cnt[f"<{key}>"] += 1
    # юникод — считаем каждый символ
    for m in EMOJI_UNICODE_RE.finditer(text or ""):
        cnt[m.group(0)] += 1
    return cnt

def message_basic_counters(msg: discord.Message) -> dict:
    text = msg.content or ""
    return {
        "words": len(text.split()),
        "chars": len(text),
        "emoji_count": count_unicode_emojis(text) + len(extract_custom_emoji_keys(text)),
        "link_count": len(URL_RE.findall(text)),
        "mention_cnt": len(msg.mentions),
        "has_attach": bool(msg.attachments),
        "has_sticker": bool(msg.stickers),
        "is_reply": bool(msg.reference),
    }
