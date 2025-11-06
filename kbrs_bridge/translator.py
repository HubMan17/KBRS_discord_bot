# translator.py
import os
import json
import asyncio
import re
import requests

# --- Настройки окружения ---
DEEP_URL = os.getenv("DEEP_URL")
DEEP_KEY = os.getenv("DEEP_KEY")
DEEP_MODEL = os.getenv("DEEP_MODEL", "deepseek-ai/DeepSeek-R1-0528")

HEADERS = {
    "Authorization": f"Bearer {DEEP_KEY}",
    "Content-Type": "application/json",
}

# --- Общие regex-помощники ---
CYRILLIC_RX = re.compile(r"[\u0400-\u04FF]")
CJK_RX = re.compile(r"[\u3040-\u30FF\u4E00-\u9FFF\u3400-\u4DBF\u31F0-\u31FF]")

THINK_SPLIT_RX = re.compile(r"</think>\s*", re.IGNORECASE)
TAG_BLOCK_RX = lambda tag: re.compile(rf"<{tag}>(.*?)</{tag}>", re.IGNORECASE | re.DOTALL)


# =======================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =======================
def _strip_think(s: str) -> str:
    """Удаляем внутренние рассуждения до последнего </think>."""
    if not s:
        return ""
    parts = THINK_SPLIT_RX.split(s)
    return parts[-1].strip() if parts else s.strip()


def _extract_block(s: str, tag: str) -> str:
    """Извлекает содержимое тега <tag>...</tag>."""
    if not s:
        return ""
    m = TAG_BLOCK_RX(tag).search(s)
    return (m.group(1).strip() if m else "").strip()


def _fallback_ru_only(s: str) -> str:
    """
    Если <ru>...</ru> отсутствует — пытаемся вытащить последние русские строки.
    """
    if not s:
        return ""
    lines = [ln.rstrip() for ln in s.splitlines()]
    if len(lines) > 20:
        lines = lines[-20:]
    kept = []
    for ln in lines:
        ls = ln.strip()
        if ls == "":
            kept.append(ln)
            continue
        low = ls.lower()
        if low.startswith(("мы имеем", "ключевые элементы", "особенности перевода", "структура", "объяснение", "анализ")):
            continue
        if low.startswith(("-", "*", "— ")):
            continue
        if CYRILLIC_RX.search(ls):
            kept.append(ln)
            continue
        if any(tok in ls for tok in ("http://", "https://", "<#", "<@", "@everyone", "@here")):
            kept.append(ln)
            continue
    result = "\n".join(kept).strip()
    return result


# =======================
# ВЫЗОВ МОДЕЛИ (универсальный)
# =======================
def _call_deepseek(prompt: str, system_prompt: str, timeout: int = 20) -> str:
    if not (DEEP_URL and DEEP_KEY):
        raise RuntimeError("DEEP_URL/DEEP_KEY are not set")

    payload = {
        "model": DEEP_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 1200,
    }
    r = requests.post(DEEP_URL, headers=HEADERS, data=json.dumps(payload), timeout=timeout)
    r.raise_for_status()
    js = r.json()
    try:
        out = js["choices"][0]["message"]["content"]
    except Exception:
        out = js.get("text") or ""
    return _strip_think(out)


# =====================================================
# 1️⃣  СПЕЦИАЛЬНЫЙ: EN→RU с жёстким контролем <ru>...</ru>
# =====================================================
SYSTEM_PROMPT_RU = (
    "Ты профессиональный переводчик. Твоя задача — перевести входной текст на РУССКИЙ ЯЗЫК.\n"
    "ОТВЕЧАЙ СТРОГО ТОЛЬКО в одном блоке <ru>...</ru> и НИЧЕГО СНАРУЖИ.\n"
    "Запрещены комментарии, размышления, списки, объяснения. Сохраняй эмодзи, упоминания (@...), "
    "ссылки и Discord-разметку (<#...>, <@...>). Если в исходнике несколько языков — выдай один цельный русский вариант."
)


def _call_deepseek_ru(prompt: str, timeout: int = 20) -> str:
    return _call_deepseek(
        prompt=(
            "Переведи на русский. Верни ответ СТРОГО в формате: <ru>...только перевод на русском...</ru>\n\n" + prompt
        ),
        system_prompt=SYSTEM_PROMPT_RU,
        timeout=timeout,
    )


async def translate_en_to_ru_force(text: str, attempts: int = 4, base_timeout: int = 15) -> str:
    """
    Строгий перевод в RU с ретраями. Возвращает чистый текст (из <ru>...</ru>).
    """
    if not text:
        return ""

    templates = [
        "Только перевод. Ответ строго в <ru>…</ru>:\n\n{t}",
        "Верни строго <ru>…</ru> без пояснений:\n{t}",
        "{t}\n\nФормат ответа: <ru>…</ru> (без всего лишнего)",
    ]

    for i in range(attempts):
        tpl = templates[min(i, len(templates) - 1)]
        prompt = tpl.format(t=text)
        per_call_timeout = base_timeout + i * 8
        try:
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(None, _call_deepseek_ru, prompt, per_call_timeout)
            ru = _extract_block(raw, "ru").strip()
            if not ru:
                ru = _fallback_ru_only(raw)
            if not ru or not CYRILLIC_RX.search(ru):
                raise RuntimeError("no cyrillic in result")
            return ru
        except Exception as e:
            wait = 0.6 * (2 ** i)
            print(f"[translator] RU attempt {i+1}/{attempts} failed: {e}; retry in {wait:.1f}s")
            await asyncio.sleep(wait)

    raise RuntimeError("translation failed after retries")


# =====================================================
# 2️⃣  УНИВЕРСАЛЬНЫЙ: перевод в любой язык (для Discord Relay)
# =====================================================
async def translate_to_force(text: str, target_lang: str, attempts: int = 4, base_timeout: int = 15) -> str:
    """
    Гарантированный перевод в целевой язык (ISO-like: 'en', 'ru', 'ja', 'zh-CN', ...).
    Ответ ожидаем строго внутри <out>...</out>.
    """
    if not text:
        return ""

    system_prompt = (
        "Ты профессиональный переводчик. Переведи входной текст на указанный язык.\n"
        "ОТВЕЧАЙ СТРОГО ТОЛЬКО в одном блоке <out>...</out> и НИЧЕГО СНАРУЖИ.\n"
        "Без комментариев, размышлений, списков, объяснений. Сохраняй эмодзи, упоминания (@...), "
        "ссылки и Discord-разметку (<#...>, <@...>)."
    )

    user_templates = [
        "Целевой язык: {lang}\nВерни ответ строго как <out>...перевод...</out>\n\n{t}",
        "Язык: {lang}\nФормат ответа: <out>…</out>\n{t}",
        "{t}\n\n-> {lang}\n<out>…</out>",
    ]

    for i in range(attempts):
        tpl = user_templates[min(i, len(user_templates) - 1)]
        prompt = tpl.format(t=text, lang=target_lang)
        per_call_timeout = base_timeout + i * 8
        try:
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(None, _call_deepseek, prompt, system_prompt, per_call_timeout)
            out = _extract_block(raw, "out").strip()
            if not out:
                raise RuntimeError("empty <out> block")

            # простая валидация для отдельных языков (при желании можно расширить)
            if target_lang.lower().startswith("ru") and not CYRILLIC_RX.search(out):
                raise RuntimeError("expected Cyrillic for RU target")
            if target_lang.lower().startswith(("zh", "ja")) and not CJK_RX.search(out):
                raise RuntimeError("expected CJK characters for target")

            return out
        except Exception as e:
            wait = 0.6 * (2 ** i)
            print(f"[translator] to={target_lang} attempt {i+1}/{attempts} failed: {e}; retry in {wait:.1f}s")
            await asyncio.sleep(wait)

    raise RuntimeError(f"translation to {target_lang} failed after retries")
