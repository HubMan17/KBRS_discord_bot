# db.py
import os, sqlite3, time
from typing import Optional, List, Tuple, Dict, Any

DB_PATH = os.getenv("BRIDGE_DB", "bridge.db")

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS user_map (
    discord_user_id TEXT PRIMARY KEY,
    telegram_chat_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_map_handle (
    discord_user_id TEXT PRIMARY KEY,
    telegram_username TEXT NOT NULL
);

/* --- ФАКЕЛА --- */
CREATE TABLE IF NOT EXISTS torches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_username TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    reported_by INTEGER,
    thread_id INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    torch_id INTEGER NOT NULL,
    due_at INTEGER NOT NULL,
    kind TEXT NOT NULL,
    fired INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(torch_id) REFERENCES torches(id) ON DELETE CASCADE
);

/* --- СПИСОК ИЗВЕСТНЫХ ИГРОКОВ --- */
CREATE TABLE IF NOT EXISTS known_players (
    tg_username TEXT PRIMARY KEY  -- без '@'
);

CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at, fired);
CREATE INDEX IF NOT EXISTS idx_torches_active ON torches(active, started_at);
"""

def sync_known_players(usernames: list[str]):
    """Идёмпотентно синкает список известных игроков (вставляет, не удаляет лишних)."""
    if not usernames:
        return
    clean = [u.strip().lstrip('@') for u in usernames if u.strip().lstrip('@')]
    if not clean:
        return
    with _conn() as c:
        c.executemany("INSERT OR IGNORE INTO known_players(tg_username) VALUES(?)", [(u,) for u in clean])

def list_known_players() -> list[str]:
    with _conn() as c:
        cur = c.execute("SELECT tg_username FROM known_players ORDER BY tg_username COLLATE NOCASE")
        return [r[0] for r in cur.fetchall()]

def _conn():
    # для фонового потока
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    with _conn() as c:
        c.executescript(_SCHEMA)
        # лёгкая миграция: добавить chat_id к torches
        try:
            c.execute("ALTER TABLE torches ADD COLUMN chat_id INTEGER")
        except Exception:
            pass  # уже есть

# ----- Старые функции (оставляем) -----
def upsert_mapping(discord_user_id: int, telegram_chat_id: int):
    with _conn() as c:
        c.execute(
            "INSERT INTO user_map(discord_user_id, telegram_chat_id) VALUES(?, ?) "
            "ON CONFLICT(discord_user_id) DO UPDATE SET telegram_chat_id=excluded.telegram_chat_id",
            (str(discord_user_id), int(telegram_chat_id)),
        )

def get_tg_chat_id(discord_user_id: int) -> Optional[int]:
    with _conn() as c:
        cur = c.execute("SELECT telegram_chat_id FROM user_map WHERE discord_user_id = ?", (str(discord_user_id),))
        row = cur.fetchone()
        return int(row[0]) if row else None

def upsert_handle(discord_user_id: int, telegram_username: str):
    handle = telegram_username.lstrip('@').strip()
    if not handle:
        return
    with _conn() as c:
        c.execute(
            "INSERT INTO user_map_handle(discord_user_id, telegram_username) VALUES(?, ?) "
            "ON CONFLICT(discord_user_id) DO UPDATE SET telegram_username=excluded.telegram_username",
            (str(discord_user_id), handle),
        )

def get_tg_handle(discord_user_id: int) -> Optional[str]:
    with _conn() as c:
        cur = c.execute("SELECT telegram_username FROM user_map_handle WHERE discord_user_id = ?", (str(discord_user_id),))
        row = cur.fetchone()
        return row[0] if row else None

def get_handles_for_users(discord_user_ids: List[int]) -> List[str]:
    if not discord_user_ids:
        return []
    ids = [str(x) for x in discord_user_ids]
    q = f"SELECT telegram_username FROM user_map_handle WHERE discord_user_id IN ({','.join(['?']*len(ids))})"
    with _conn() as c:
        cur = c.execute(q, ids)
        return [r[0] for r in cur.fetchall() if r and r[0]]

# ----- Функционал факелов -----

TORCH_COUNT = 6
TORCH_REGEN_MIN = 100
CAP_MINUTES = TORCH_COUNT * TORCH_REGEN_MIN          # 600
PRE_ALERTS_MIN = [120, 60, 30, 10]   # 2ч, 1ч, 30м, 10м

def _now() -> int:
    return int(time.time())

def _norm_username(u: str) -> str:
    return u.strip().lstrip('@')

def _get_active_torch_for(username: str) -> Optional[Tuple]:
    with _conn() as c:
        cur = c.execute(
            "SELECT id, tg_username, started_at, reported_by, thread_id, active "
            "FROM torches WHERE tg_username = ? AND active = 1 LIMIT 1",
            (_norm_username(username),),
        )
        return cur.fetchone()

def _insert_torch(username: str, started_at: int, reported_by: Optional[int], thread_id: int, chat_id: int) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO torches(tg_username, started_at, reported_by, thread_id, active, chat_id) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (_norm_username(username), started_at, reported_by, thread_id, chat_id),
        )
        return int(cur.lastrowid)

def _deactivate_torch(torch_id: int):
    with _conn() as c:
        c.execute("UPDATE torches SET active = 0 WHERE id = ?", (torch_id,))

def _clear_reminders(torch_id: int):
    with _conn() as c:
        c.execute("DELETE FROM reminders WHERE torch_id = ?", (torch_id,))

def _insert_reminder(torch_id: int, due_at: int, kind: str):
    with _conn() as c:
        c.execute(
            "INSERT INTO reminders(torch_id, due_at, kind, fired) VALUES(?, ?, ?, 0)",
            (torch_id, due_at, kind),
        )

def start_or_reset_torch(username: str, reported_by: Optional[int], thread_id: int, chat_id: int) -> Dict[str, Any]:
    now = _now()
    exist = _get_active_torch_for(username)
    if exist:
        torch_id = int(exist[0])
        with _conn() as c:
            c.execute("UPDATE torches SET started_at = ?, reported_by = ?, thread_id = ?, chat_id = ? WHERE id = ?",
                      (now, reported_by, thread_id, chat_id, torch_id))
        _clear_reminders(torch_id)
    else:
        torch_id = _insert_torch(username, now, reported_by, thread_id, chat_id)

    cap_at = now + CAP_MINUTES * 60
    for mins in PRE_ALERTS_MIN:
        _insert_reminder(torch_id, cap_at - mins * 60, "pre")
    _insert_reminder(torch_id, cap_at, "cap")
    return {"torch_id": torch_id, "started_at": now, "cap_at": cap_at}

def cancel_torch_for(username: str):
    exist = _get_active_torch_for(username)
    if not exist:
        return False
    torch_id = int(exist[0])
    _deactivate_torch(torch_id)
    _clear_reminders(torch_id)
    return True

def due_reminders(now_ts: Optional[int] = None, limit: int = 20) -> List[Tuple]:
    """Список (id, torch_id, due_at, kind) напоминаний, которые пора отправить."""
    now_ts = now_ts or _now()
    with _conn() as c:
        cur = c.execute(
            "SELECT id, torch_id, due_at, kind FROM reminders "
            "WHERE fired = 0 AND due_at <= ? ORDER BY due_at ASC LIMIT ?",
            (now_ts, limit),
        )
        return cur.fetchall()

def mark_reminder_fired(reminder_id: int):
    with _conn() as c:
        c.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))

def get_torch(torch_id: int):
    """Возвращает запись таймера:
       (id, tg_username, started_at, reported_by, thread_id, active, chat_id)"""
    with _conn() as c:
        cur = c.execute(
            "SELECT id, tg_username, started_at, reported_by, thread_id, active, chat_id "
            "FROM torches WHERE id = ?",
            (torch_id,),
        )
        return cur.fetchone()

def list_active_upcoming(limit: int = 10) -> List[Dict[str, Any]]:
    """Ближайшие по времени полного капа."""
    now = _now()
    with _conn() as c:
        cur = c.execute(
            "SELECT id, tg_username, started_at FROM torches WHERE active = 1 ORDER BY started_at ASC"
        )
        rows = cur.fetchall()
    # считаем cap_at = started_at + 600м, сортируем по времени ДО капа
    out = []
    for (tid, username, started_at) in rows:
        cap_at = int(started_at) + CAP_MINUTES * 60
        eta = max(0, cap_at - now)
        out.append({"torch_id": int(tid), "username": username, "cap_at": cap_at, "eta": eta})
    out.sort(key=lambda x: x["eta"])
    return out[:limit]

def list_active_all() -> List[Dict[str, Any]]:
    now = _now()
    with _conn() as c:
        cur = c.execute("SELECT id, tg_username, started_at FROM torches WHERE active = 1")
        rows = cur.fetchall()
    out = []
    for (tid, username, started_at) in rows:
        cap_at = int(started_at) + CAP_MINUTES * 60
        first_torch_at = int(started_at) + 1 * TORCH_REGEN_MIN * 60
        eta_cap = max(0, cap_at - now)
        eta_first = max(0, first_torch_at - now)
        out.append({
            "torch_id": int(tid),
            "username": username,
            "started_at": int(started_at),
            "cap_at": cap_at,
            "eta_cap": eta_cap,
            "eta_first": eta_first,
        })
    out.sort(key=lambda x: x["eta_cap"])
    return out
