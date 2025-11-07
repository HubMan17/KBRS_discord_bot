"""
AI-powered translation service with caching and retry logic.

Features:
- DeepSeek R1 model for high-quality translations
- In-memory LRU cache for repeated translations
- Exponential backoff retry
- Language-specific validation
- Rate limiting protection
"""

import re
import asyncio
import hashlib
from functools import lru_cache
from typing import Literal
from datetime import datetime, timedelta

import aiohttp

from core.config import settings
from core.logger import get_logger
from core.exceptions import TranslationError, RateLimitError

logger = get_logger(__name__)

# Language detection patterns
CYRILLIC_RX = re.compile(r"[\u0400-\u04FF]")
CJK_RX = re.compile(r"[\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF\uAC00-\uD7AF]")

# Cache configuration
CACHE_SIZE = 1000
CACHE_TTL = timedelta(hours=24)

# Rate limiting
_last_request_time: float = 0
_min_request_interval: float = 0.5  # 500ms between requests


class TranslationCache:
    """LRU cache for translations with TTL."""

    def __init__(self, max_size: int = CACHE_SIZE, ttl: timedelta = CACHE_TTL):
        self._cache: dict[str, tuple[dict, datetime]] = {}
        self._max_size = max_size
        self._ttl = ttl

    def _make_key(self, text: str, lang: str) -> str:
        """Generate cache key from text and language."""
        content = f"{lang}:{text}".encode("utf-8")
        return hashlib.md5(content).hexdigest()

    def get(self, text: str, lang: str) -> dict | None:
        """Get cached translation if exists and not expired."""
        key = self._make_key(text, lang)
        if key in self._cache:
            result, timestamp = self._cache[key]
            if datetime.now() - timestamp < self._ttl:
                logger.debug(f"Translation cache hit for {lang}")
                return result
            else:
                # Expired, remove
                del self._cache[key]
        return None

    def set(self, text: str, lang: str, result: dict) -> None:
        """Cache translation result."""
        # Evict oldest if cache is full
        if len(self._cache) >= self._max_size:
            oldest_key = min(self._cache.items(), key=lambda x: x[1][1])[0]
            del self._cache[oldest_key]

        key = self._make_key(text, lang)
        self._cache[key] = (result, datetime.now())
        logger.debug(f"Cached translation for {lang}")

    def clear(self) -> None:
        """Clear all cached translations."""
        self._cache.clear()
        logger.info("Translation cache cleared")


class Translator:
    """
    AI-powered translator using DeepSeek R1 model.

    Supports: English (en), Russian (ru), Japanese (ja), Korean (ko), Chinese (zh)
    """

    def __init__(self):
        self.cache = TranslationCache()
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> None:
        """Create HTTP session if not exists."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            await asyncio.sleep(0.25)

    async def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        global _last_request_time
        now = asyncio.get_event_loop().time()
        elapsed = now - _last_request_time

        if elapsed < _min_request_interval:
            wait_time = _min_request_interval - elapsed
            await asyncio.sleep(wait_time)

        _last_request_time = asyncio.get_event_loop().time()

    def _build_prompt(self, text: str, target_lang: str) -> str:
        """Build translation prompt for AI model."""
        prompts = {
            "ru": (
                f"Переведи на русский язык:\n{text}\n\n"
                "Верни ТОЛЬКО перевод в формате XML: <ru>перевод</ru>"
            ),
            "en": (
                f"Translate to English:\n{text}\n\n"
                "Return ONLY the translation in XML format: <out>translation</out>"
            ),
            "ja": (
                f"日本語に翻訳してください:\n{text}\n\n"
                "XMLフォーマットで翻訳のみを返してください: <out>翻訳</out>"
            ),
            "zh": (
                f"翻译成中文:\n{text}\n\n"
                "只返回XML格式的翻译: <out>翻译</out>"
            ),
            "ko": (
                f"한국어로 번역:\n{text}\n\n"
                "Adapt translation for Korean cultural context. Use appropriate honorifics and formality.\n"
                "Return ONLY the translation in XML format: <out>번역</out>"
            ),
        }
        return prompts.get(target_lang, prompts["en"])

    def _extract_translation(self, response_text: str, lang: str) -> str:
        """Extract translation from AI response."""
        # Remove <think> tags if present
        response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL)

        # Try language-specific tag
        tag = "ru" if lang == "ru" else "out"
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, response_text, re.DOTALL)

        if match:
            return match.group(1).strip()

        # Fallback: try any XML-like tags
        match = re.search(r"<\w+>(.*?)</\w+>", response_text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Last resort: return cleaned response
        cleaned = response_text.strip()
        if cleaned:
            logger.warning(f"No XML tags found, returning raw response for {lang}")
            return cleaned

        raise TranslationError(f"Could not extract translation from response: {response_text[:200]}")

    def _validate_translation(self, text: str, lang: str) -> None:
        """Validate that translation contains expected characters."""
        if lang == "ru" and not CYRILLIC_RX.search(text):
            raise TranslationError("Russian translation missing Cyrillic characters")

        if lang in ("zh", "ja", "ko"):
            if not CJK_RX.search(text):
                # Allow some flexibility - might contain English terms
                logger.warning(f"Translation for {lang} has few CJK characters")

    async def _translate_with_retry(
        self,
        text: str,
        target_lang: Literal["ru", "en", "zh", "ja", "ko"],
    ) -> str:
        """
        Translate text with exponential backoff retry.

        Args:
            text: Source text to translate
            target_lang: Target language code

        Returns:
            Translated text

        Raises:
            TranslationError: If translation fails after retries
        """
        if not settings.translation.enabled:
            raise TranslationError("Translation service is disabled")

        if not settings.translation.url or not settings.translation.key:
            raise TranslationError("Translation service not configured")

        # Check cache first
        cached = self.cache.get(text, target_lang)
        if cached:
            return cached["translation"]

        await self._ensure_session()

        # Build request
        prompt = self._build_prompt(text, target_lang)
        payload = {
            "model": settings.translation.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2000,
            "temperature": 0.3,
        }

        headers = {
            "Authorization": f"Bearer {settings.translation.key}",
            "Content-Type": "application/json",
        }

        # Get language-specific timeout
        timeout_map = {
            "en": settings.translation.timeout_en,
            "ru": settings.translation.timeout_ru,
            "zh": settings.translation.timeout_zh,
            "ja": settings.translation.timeout_ja,
            "ko": settings.translation.timeout_ko,
        }
        timeout = aiohttp.ClientTimeout(total=timeout_map.get(target_lang, 30))

        # Retry logic
        last_exception = None

        for attempt in range(settings.translation.max_attempts):
            try:
                # Rate limiting
                await self._rate_limit()

                async with self._session.post(
                    settings.translation.url,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                ) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 5))
                        logger.warning(f"Translation rate limited, waiting {retry_after}s")
                        await asyncio.sleep(retry_after)
                        continue

                    if resp.status >= 400:
                        text = await resp.text()
                        raise TranslationError(
                            f"Translation API error {resp.status}",
                            details={"response": text[:200]},
                        )

                    data = await resp.json()

                    # Extract translation
                    content = data["choices"][0]["message"]["content"]
                    translation = self._extract_translation(content, target_lang)

                    # Validate
                    self._validate_translation(translation, target_lang)

                    # Cache result
                    result = {"translation": translation, "lang": target_lang}
                    self.cache.set(text, target_lang, result)

                    logger.info(f"Translated to {target_lang}: {len(text)} → {len(translation)} chars")
                    return translation

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exception = TranslationError(
                    f"Network error during translation: {e}",
                    details={"attempt": attempt + 1, "lang": target_lang},
                )

                if attempt < settings.translation.max_attempts - 1:
                    delay = settings.translation.base_delay * (2 ** attempt)
                    logger.warning(
                        f"Translation failed (attempt {attempt + 1}/{settings.translation.max_attempts}), "
                        f"retrying in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue

            except TranslationError as e:
                # Don't retry validation/extraction errors
                logger.error(f"Translation error: {e}")
                raise

        # All retries exhausted
        raise last_exception or TranslationError("Translation failed after all retries")

    async def translate(
        self,
        text: str,
        target_lang: Literal["ru", "en", "zh", "ja", "ko"],
    ) -> str:
        """
        Translate text to target language.

        Args:
            text: Source text
            target_lang: Target language code

        Returns:
            Translated text
        """
        if not text or not text.strip():
            return ""

        try:
            return await self._translate_with_retry(text, target_lang)
        except TranslationError:
            # Log error but don't crash
            logger.exception(f"Translation to {target_lang} failed")
            # Fallback to original text
            return text

    async def translate_multilang(
        self,
        text: str,
        langs: list[Literal["ru", "en", "zh", "ja", "ko"]],
    ) -> dict[str, str]:
        """
        Translate text to multiple languages concurrently.

        Args:
            text: Source text
            langs: List of target language codes

        Returns:
            Dictionary mapping language codes to translations
        """
        tasks = {lang: self.translate(text, lang) for lang in langs}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        translations = {}
        for lang, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"Translation to {lang} failed: {result}")
                translations[lang] = text  # Fallback to original
            else:
                translations[lang] = result

        return translations


# Singleton instance
_translator: Translator | None = None


def get_translator() -> Translator:
    """Get or create the singleton translator instance."""
    global _translator
    if _translator is None:
        _translator = Translator()
    return _translator


async def close_translator():
    """Close the singleton translator."""
    global _translator
    if _translator:
        await _translator.close()
        _translator = None
