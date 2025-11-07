"""
Async API client with connection pooling and retry logic.

Features:
- aiohttp for async requests
- Automatic token refresh
- Exponential backoff retry
- Connection pooling
- Request/response logging
- Type-safe responses
"""

import asyncio
import time
from typing import Any
from contextlib import asynccontextmanager

import aiohttp
from aiohttp import ClientSession, ClientTimeout, TCPConnector

from core.config import settings
from core.logger import get_logger
from core.exceptions import APIError, AuthenticationError, RateLimitError

logger = get_logger(__name__)


class APIClient:
    """
    Async HTTP client for backend API communication.

    Handles:
    - JWT authentication with auto-refresh
    - Exponential backoff retry
    - Connection pooling
    - Request timeout
    """

    def __init__(self):
        self._session: ClientSession | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: float = 0
        self._lock = asyncio.Lock()

        # Configuration
        self._base_url = settings.api.base_url.rstrip("/")
        self._username = settings.api.username
        self._password = settings.api.password
        self._timeout = settings.api.timeout
        self._max_retries = settings.api.max_retries
        self._retry_delay = settings.api.retry_delay

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _ensure_session(self) -> None:
        """Create aiohttp session with connection pooling if not exists."""
        if self._session is None or self._session.closed:
            connector = TCPConnector(
                limit=100,  # Max connections
                limit_per_host=30,
                ttl_dns_cache=300,
            )
            timeout = ClientTimeout(total=self._timeout)
            self._session = ClientSession(
                connector=connector,
                timeout=timeout,
                raise_for_status=False,
            )
            logger.debug("HTTP session created with connection pooling")

    async def close(self) -> None:
        """Close HTTP session and cleanup resources."""
        if self._session and not self._session.closed:
            await self._session.close()
            await asyncio.sleep(0.25)  # Allow connections to close
            logger.debug("HTTP session closed")

    def _is_token_expired(self) -> bool:
        """Check if access token is expired or will expire soon."""
        return time.time() >= (self._token_expires_at - 60)  # 1min buffer

    async def _login(self) -> None:
        """Authenticate and obtain access/refresh tokens."""
        url = f"{self._base_url}/auth/token/"
        payload = {"username": self._username, "password": self._password}

        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status == 401:
                    raise AuthenticationError(
                        "Invalid credentials",
                        status_code=401,
                    )
                if resp.status >= 400:
                    text = await resp.text()
                    raise APIError(
                        f"Login failed: {resp.status}",
                        status_code=resp.status,
                        response_body=text,
                    )

                data = await resp.json()
                self._access_token = data["access"]
                self._refresh_token = data["refresh"]
                self._token_expires_at = time.time() + settings.api.token_lifetime

                logger.info("Successfully authenticated with API")

        except aiohttp.ClientError as e:
            raise APIError(f"Network error during login: {e}")

    async def _refresh_access_token(self) -> None:
        """Refresh access token using refresh token."""
        if not self._refresh_token:
            await self._login()
            return

        url = f"{self._base_url}/auth/refresh/"
        payload = {"refresh": self._refresh_token}

        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status >= 400:
                    # Refresh failed, do full login
                    logger.warning("Token refresh failed, performing full login")
                    await self._login()
                    return

                data = await resp.json()
                self._access_token = data["access"]
                self._token_expires_at = time.time() + settings.api.token_lifetime

                logger.debug("Access token refreshed")

        except aiohttp.ClientError as e:
            logger.warning(f"Token refresh network error: {e}, performing full login")
            await self._login()

    async def ensure_token(self) -> None:
        """Ensure we have a valid access token."""
        async with self._lock:
            if not self._access_token or self._is_token_expired():
                if self._refresh_token:
                    await self._refresh_access_token()
                else:
                    await self._login()

    def _auth_headers(self) -> dict[str, str]:
        """Get authorization headers."""
        if not self._access_token:
            return {}
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Make HTTP request with exponential backoff retry.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API endpoint path
            **kwargs: Additional arguments for aiohttp request

        Returns:
            Parsed JSON response

        Raises:
            APIError: On request failure after retries
        """
        await self._ensure_session()
        await self.ensure_token()

        url = f"{self._base_url}{path}"
        headers = {**self._auth_headers(), **kwargs.pop("headers", {})}

        last_exception = None

        for attempt in range(self._max_retries + 1):
            try:
                async with self._session.request(
                    method, url, headers=headers, **kwargs
                ) as resp:
                    # Rate limit handling
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", 5))
                        logger.warning(f"Rate limited, retrying after {retry_after}s")
                        raise RateLimitError(
                            "API rate limit exceeded",
                            retry_after=retry_after,
                        )

                    # Auth error - try token refresh
                    if resp.status == 401 and attempt < self._max_retries:
                        logger.warning("Got 401, refreshing token and retrying")
                        await self._refresh_access_token()
                        headers = self._auth_headers()
                        continue

                    # Client/server errors
                    if resp.status >= 400:
                        text = await resp.text()
                        raise APIError(
                            f"HTTP {resp.status}: {text[:200]}",
                            status_code=resp.status,
                            response_body=text,
                        )

                    # Success
                    if resp.status == 204:  # No content
                        return {}

                    data = await resp.json()
                    return data

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exception = APIError(f"Network error: {e}")
                if attempt < self._max_retries:
                    delay = self._retry_delay * (2 ** attempt)
                    logger.warning(
                        f"Request failed (attempt {attempt + 1}/{self._max_retries + 1}), "
                        f"retrying in {delay:.1f}s: {e}"
                    )
                    await asyncio.sleep(delay)
                    continue

            except RateLimitError as e:
                if attempt < self._max_retries:
                    await asyncio.sleep(e.retry_after or 5)
                    continue
                raise

        # All retries exhausted
        raise last_exception or APIError("Request failed after all retries")

    # ========================================================================
    # XP endpoints
    # ========================================================================

    async def add_xp(
        self,
        discord_id: int,
        username: str,
        amount: int,
        source: str,
        guild_id: int | None = None,
    ) -> dict[str, Any]:
        """Award XP to a user."""
        payload = {
            "discord_id": discord_id,
            "username": username,
            "amount": amount,
            "source": source,
        }
        if guild_id is not None:
            payload["guild_id"] = guild_id

        return await self._request_with_retry("POST", "/discord/add_xp/", json=payload)

    async def get_rank(self, discord_id: int) -> dict[str, Any] | None:
        """Get user rank and level."""
        try:
            return await self._request_with_retry("GET", f"/discord/rank/{discord_id}/")
        except APIError as e:
            if e.status_code == 404:
                return None
            raise

    async def get_top(self, limit: int = 10) -> dict[str, Any]:
        """Get leaderboard."""
        return await self._request_with_retry("GET", f"/discord/top/?limit={limit}")

    async def sync_members(self, members: list[dict]) -> dict[str, Any]:
        """Bulk register members."""
        return await self._request_with_retry(
            "POST",
            "/discord/sync_members/",
            json={"members": members},
        )

    # ========================================================================
    # Stats endpoints
    # ========================================================================

    async def get_user_stats(
        self,
        discord_id: int,
        range: str = "all",
        tz: str | None = None,
    ) -> dict[str, Any]:
        """Get user activity statistics."""
        params = {"discord_id": discord_id, "range": range}
        if tz:
            params["tz"] = tz

        return await self._request_with_retry("GET", "/stats/user/", params=params)

    async def get_server_highlights(
        self,
        guild_id: int,
        range: str = "day",
        tz: str | None = None,
    ) -> dict[str, Any]:
        """Get server highlights."""
        params = {"guild_id": guild_id, "range": range}
        if tz:
            params["tz"] = tz

        return await self._request_with_retry("GET", "/stats/highlights/", params=params)

    # ========================================================================
    # Birthday endpoints
    # ========================================================================

    async def get_birthdays_upcoming(
        self,
        discord_ids: list[int],
        limit: int = 5,
        tz: str | None = None,
    ) -> dict[str, Any]:
        """Get upcoming birthdays."""
        payload = {"discord_ids": discord_ids, "limit": limit}
        if tz:
            payload["tz"] = tz

        return await self._request_with_retry("POST", "/birthdays/upcoming/", json=payload)

    async def set_birthday(
        self,
        discord_id: int,
        date_str: str,
        tz: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Set user birthday."""
        payload = {"discord_id": discord_id, "date": date_str, "enabled": enabled}
        if tz:
            payload["tz"] = tz

        return await self._request_with_retry("POST", "/birthdays/set/", json=payload)

    # ========================================================================
    # Bulk event endpoints
    # ========================================================================

    async def bulk_messages(self, events: list[dict]) -> dict[str, Any]:
        """Send bulk message events."""
        if not events:
            return {}
        return await self._request_with_retry(
            "POST",
            "/events/messages/bulk",
            json={"events": events},
        )

    async def bulk_reactions(self, events: list[dict]) -> dict[str, Any]:
        """Send bulk reaction events."""
        if not events:
            return {}
        return await self._request_with_retry(
            "POST",
            "/events/reactions/bulk",
            json={"events": events},
        )

    async def bulk_emoji(self, events: list[dict]) -> dict[str, Any]:
        """Send bulk emoji usage events."""
        if not events:
            return {}
        return await self._request_with_retry(
            "POST",
            "/events/emoji_usage/bulk",
            json={"events": events},
        )

    async def bulk_xp(self, events: list[dict]) -> dict[str, Any]:
        """Send bulk XP events."""
        if not events:
            return {}
        return await self._request_with_retry(
            "POST",
            "/events/xp/bulk",
            json={"events": events},
        )


# Singleton instance
_api_client: APIClient | None = None


@asynccontextmanager
async def get_api_client():
    """
    Get or create the singleton API client instance.

    Usage:
        async with get_api_client() as api:
            data = await api.get_rank(user_id)
    """
    global _api_client
    if _api_client is None:
        _api_client = APIClient()

    try:
        await _api_client._ensure_session()
        yield _api_client
    finally:
        # Don't close the singleton session
        pass


async def close_api_client():
    """Close the singleton API client."""
    global _api_client
    if _api_client:
        await _api_client.close()
        _api_client = None
