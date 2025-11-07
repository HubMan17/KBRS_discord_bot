import os, time, requests
from dotenv import load_dotenv

load_dotenv()
API_BASE = os.getenv("API_BASE_URL", "").rstrip("/")
API_USERNAME = os.getenv("API_USERNAME", "")
API_PASSWORD = os.getenv("API_PASSWORD", "")

class ApiClient:
    def __init__(self):
        self.access = None
        self.refresh = None
        self.exp_ts = 0
        self.session = requests.Session()

    def _auth_headers(self):
        if not self.access:
            return {}
        return {"Authorization": f"Bearer {self.access}"}

    def login(self):
        url = f"{API_BASE}/auth/token/"
        r = requests.post(url, json={"username": API_USERNAME, "password": API_PASSWORD}, timeout=10)
        r.raise_for_status()
        data = r.json()
        self.access = data["access"]
        self.refresh = data["refresh"]
        # простая эвристика: 25 минут «жизни» access токена
        self.exp_ts = time.time() + 25 * 60

    def ensure_token(self):
        if not self.access or time.time() >= self.exp_ts:
            if self.refresh:
                try:
                    r = requests.post(f"{API_BASE}/auth/refresh/", json={"refresh": self.refresh}, timeout=10)
                    r.raise_for_status()
                    self.access = r.json()["access"]
                    self.exp_ts = time.time() + 25 * 60
                    return
                except Exception:
                    pass
            self.login()

    # --- API calls ---
    def add_xp(self, discord_id: int, username: str, amount: int, source: str, guild_id: int | None = None):
        self.ensure_token()
        payload = {"discord_id": discord_id, "username": username, "amount": amount, "source": source}
        if guild_id is not None:
            payload["guild_id"] = guild_id
        r = requests.post(f"{API_BASE}/discord/add_xp/", json=payload,
                        headers=self._auth_headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def rank(self, discord_id: int):
        self.ensure_token()
        r = requests.get(f"{API_BASE}/discord/rank/{discord_id}/", headers=self._auth_headers(), timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def top(self, limit=10):
        self.ensure_token()
        r = requests.get(f"{API_BASE}/discord/top/?limit={limit}", headers=self._auth_headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def sync_members(self, members: list[dict]):
        self.ensure_token()
        r = requests.post(f"{API_BASE}/discord/sync_members/",
                          json={"members": members},
                          headers=self._auth_headers(),
                          timeout=15)
        r.raise_for_status()
        return r.json()
    
    def user_stats(self, discord_id: int, range: str = "all", tz: str | None = None):
        self.ensure_token()
        params = {"discord_id": discord_id, "range": range}
        if tz:
            params["tz"] = tz
        r = requests.get(f"{API_BASE}/stats/user/", params=params, headers=self._auth_headers(), timeout=15)
        r.raise_for_status()
        return r.json()
    
    def server_highlights(self, guild_id: int, range: str = "day", tz: str | None = None):
        self.ensure_token()
        params = {"guild_id": guild_id, "range": range}
        if tz:
            params["tz"] = tz
        r = requests.get(
            f"{API_BASE}/stats/highlights/",
            params=params,
            headers=self._auth_headers(),
            timeout=15
        )
        r.raise_for_status()
        return r.json()
    
    def birthdays_upcoming(self, discord_ids: list[int], limit: int = 5, tz: str | None = None):
        self.ensure_token()
        payload = {"discord_ids": discord_ids, "limit": limit}
        if tz:
            payload["tz"] = tz
        r = self.session.post(f"{API_BASE}/birthdays/upcoming/", json=payload, headers=self._auth_headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def set_birthday(self, discord_id: int, date_str: str, tz: str | None = None, enabled: bool = True):
        self.ensure_token()
        payload = {"discord_id": discord_id, "date": date_str, "enabled": enabled}
        if tz:
            payload["tz"] = tz
        r = self.session.post(f"{API_BASE}/birthdays/set/", json=payload, headers=self._auth_headers(), timeout=15)
        r.raise_for_status()
        return r.json()
    
    def send_bulk(self, path: str, events: list[dict]) -> dict:
        self.ensure_token()
        url = f"{API_BASE}{path}"
        r = self.session.post(url, json={"events": events}, headers=self._auth_headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def bulk_messages(self, events: list[dict]):   return self.send_bulk("/events/messages/bulk", events)
    def bulk_reactions(self, events: list[dict]):  return self.send_bulk("/events/reactions/bulk", events)
    def bulk_emoji(self, events: list[dict]):      return self.send_bulk("/events/emoji_usage/bulk", events)
    def bulk_xp(self, events: list[dict]):         return self.send_bulk("/events/xp/bulk", events)

    def check_achievements(self, discord_id: int, trigger_event: str | None = None):
        """Trigger achievement check for a user."""
        self.ensure_token()
        payload = {"discord_id": discord_id}
        if trigger_event:
            payload["trigger_event"] = trigger_event
        r = self.session.post(
            f"{API_BASE}/achievements/check/",
            json=payload,
            headers=self._auth_headers(),
            timeout=15
        )
        r.raise_for_status()
        return r.json()

    def get_user_achievements(self, discord_id: int, category: str | None = None, unlocked_only: bool = False):
        """Get user's achievements with progress."""
        self.ensure_token()
        payload = {"discord_id": discord_id, "unlocked_only": unlocked_only}
        if category:
            payload["category"] = category
        r = self.session.post(
            f"{API_BASE}/achievements/user/",
            json=payload,
            headers=self._auth_headers(),
            timeout=15
        )
        r.raise_for_status()
        return r.json()

    def get_achievement_stats(self, discord_id: int):
        """Get detailed achievement statistics for a user."""
        self.ensure_token()
        payload = {"discord_id": discord_id}
        r = self.session.post(
            f"{API_BASE}/achievements/stats/",
            json=payload,
            headers=self._auth_headers(),
            timeout=15
        )
        r.raise_for_status()
        return r.json()

api = ApiClient()
