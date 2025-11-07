"""
Async database layer with connection pooling.

Features:
- aiosqlite for async operations
- Connection pooling
- Transaction management
- Query builders
- Migration support
"""

import asyncio
import aiosqlite
from typing import Any
from pathlib import Path
from contextlib import asynccontextmanager

from core.config import settings
from core.logger import get_logger
from core.exceptions import DatabaseError

logger = get_logger(__name__)


class AsyncDatabase:
    """
    Async SQLite database manager with connection pooling.

    Features:
    - Connection pool management
    - WAL mode for concurrent access
    - Transaction support
    - Query logging
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or settings.database.path
        self._pool: list[aiosqlite.Connection] = []
        self._pool_size = settings.database.max_connections
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize database schema and connection pool."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            # Ensure database file exists
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

            # Create initial connection to set up schema
            async with aiosqlite.connect(self.db_path) as db:
                # Enable WAL mode for concurrent access
                if settings.database.wal_mode:
                    await db.execute("PRAGMA journal_mode=WAL")
                    logger.info("WAL mode enabled for database")

                # Create schema
                await self._create_schema(db)
                await db.commit()

            # Pre-warm connection pool
            for _ in range(min(3, self._pool_size)):
                conn = await self._create_connection()
                self._pool.append(conn)

            self._initialized = True
            logger.info(f"Database initialized at {self.db_path}")

    async def _create_connection(self) -> aiosqlite.Connection:
        """Create a new database connection."""
        conn = await aiosqlite.connect(
            self.db_path,
            timeout=settings.database.timeout,
        )
        conn.row_factory = aiosqlite.Row
        return conn

    @asynccontextmanager
    async def connection(self):
        """
        Get a connection from the pool.

        Usage:
            async with db.connection() as conn:
                cursor = await conn.execute("SELECT * FROM users")
                rows = await cursor.fetchall()
        """
        if not self._initialized:
            await self.initialize()

        conn = None

        # Try to get from pool
        async with self._lock:
            if self._pool:
                conn = self._pool.pop()

        # Create new if pool is empty
        if conn is None:
            conn = await self._create_connection()

        try:
            yield conn
        finally:
            # Return to pool if not full
            async with self._lock:
                if len(self._pool) < self._pool_size:
                    self._pool.append(conn)
                else:
                    await conn.close()

    @asynccontextmanager
    async def transaction(self):
        """
        Execute operations in a transaction.

        Usage:
            async with db.transaction() as conn:
                await conn.execute("INSERT INTO ...")
                await conn.execute("UPDATE ...")
                # Auto-commit on success, rollback on exception
        """
        async with self.connection() as conn:
            await conn.execute("BEGIN")
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def close(self) -> None:
        """Close all pooled connections."""
        async with self._lock:
            for conn in self._pool:
                await conn.close()
            self._pool.clear()
        logger.info("Database connections closed")

    async def _create_schema(self, db: aiosqlite.Connection) -> None:
        """Create database schema."""
        schema = """
        -- User mappings (Discord ↔ Telegram)
        CREATE TABLE IF NOT EXISTS user_map (
            discord_user_id TEXT PRIMARY KEY,
            telegram_chat_id INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_map_handle (
            discord_user_id TEXT PRIMARY KEY,
            telegram_username TEXT NOT NULL
        );

        -- Torch timer system
        CREATE TABLE IF NOT EXISTS torches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_username TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            reported_by INTEGER,
            thread_id INTEGER NOT NULL,
            chat_id INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            torch_id INTEGER NOT NULL,
            due_at INTEGER NOT NULL,
            kind TEXT NOT NULL,
            fired INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            FOREIGN KEY(torch_id) REFERENCES torches(id) ON DELETE CASCADE
        );

        -- Known players list
        CREATE TABLE IF NOT EXISTS known_players (
            tg_username TEXT PRIMARY KEY,
            added_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );

        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at, fired);
        CREATE INDEX IF NOT EXISTS idx_torches_active ON torches(active, started_at);
        CREATE INDEX IF NOT EXISTS idx_torches_username ON torches(tg_username, active);
        """

        await db.executescript(schema)
        logger.debug("Database schema created/verified")

    # ========================================================================
    # User mapping operations
    # ========================================================================

    async def upsert_user_mapping(self, discord_user_id: int, telegram_chat_id: int) -> None:
        """Map Discord user to Telegram chat."""
        async with self.connection() as conn:
            await conn.execute(
                """
                INSERT INTO user_map(discord_user_id, telegram_chat_id)
                VALUES(?, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET telegram_chat_id=excluded.telegram_chat_id
                """,
                (str(discord_user_id), telegram_chat_id),
            )
            await conn.commit()

    async def get_telegram_chat_id(self, discord_user_id: int) -> int | None:
        """Get Telegram chat ID for Discord user."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT telegram_chat_id FROM user_map WHERE discord_user_id = ?",
                (str(discord_user_id),),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else None

    async def upsert_user_handle(self, discord_user_id: int, telegram_username: str) -> None:
        """Map Discord user to Telegram username."""
        handle = telegram_username.lstrip("@").strip()
        if not handle:
            return

        async with self.connection() as conn:
            await conn.execute(
                """
                INSERT INTO user_map_handle(discord_user_id, telegram_username)
                VALUES(?, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET telegram_username=excluded.telegram_username
                """,
                (str(discord_user_id), handle),
            )
            await conn.commit()

    async def get_telegram_handle(self, discord_user_id: int) -> str | None:
        """Get Telegram username for Discord user."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT telegram_username FROM user_map_handle WHERE discord_user_id = ?",
                (str(discord_user_id),),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    # ========================================================================
    # Known players operations
    # ========================================================================

    async def sync_known_players(self, usernames: list[str]) -> None:
        """Sync known players list (idempotent)."""
        if not usernames:
            return

        clean = [u.strip().lstrip("@") for u in usernames if u.strip().lstrip("@")]
        if not clean:
            return

        async with self.connection() as conn:
            await conn.executemany(
                "INSERT OR IGNORE INTO known_players(tg_username) VALUES(?)",
                [(u,) for u in clean],
            )
            await conn.commit()

    async def list_known_players(self) -> list[str]:
        """Get all known players."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                "SELECT tg_username FROM known_players ORDER BY tg_username COLLATE NOCASE"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    # ========================================================================
    # Torch operations
    # ========================================================================

    async def get_active_torch(self, username: str) -> dict[str, Any] | None:
        """Get active torch for username."""
        username = username.strip().lstrip("@")

        async with self.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, tg_username, started_at, reported_by, thread_id, chat_id, active
                FROM torches
                WHERE tg_username = ? AND active = 1
                LIMIT 1
                """,
                (username,),
            )
            row = await cursor.fetchone()
            if not row:
                return None

            return {
                "id": row[0],
                "username": row[1],
                "started_at": row[2],
                "reported_by": row[3],
                "thread_id": row[4],
                "chat_id": row[5],
                "active": row[6],
            }

    async def start_or_reset_torch(
        self,
        username: str,
        started_at: int,
        reported_by: int | None,
        thread_id: int,
        chat_id: int,
        cap_minutes: int = 600,
        pre_alerts_min: list[int] | None = None,
    ) -> dict[str, Any]:
        """Start or reset torch timer."""
        username = username.strip().lstrip("@")
        pre_alerts_min = pre_alerts_min or [120, 60, 30, 10]

        async with self.transaction() as conn:
            # Check if torch exists
            existing = await self.get_active_torch(username)

            if existing:
                # Update existing
                torch_id = existing["id"]
                await conn.execute(
                    """
                    UPDATE torches
                    SET started_at = ?, reported_by = ?, thread_id = ?, chat_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (started_at, reported_by, thread_id, chat_id, started_at, torch_id),
                )
                # Clear old reminders
                await conn.execute("DELETE FROM reminders WHERE torch_id = ?", (torch_id,))
            else:
                # Insert new
                cursor = await conn.execute(
                    """
                    INSERT INTO torches(tg_username, started_at, reported_by, thread_id, chat_id, active)
                    VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (username, started_at, reported_by, thread_id, chat_id),
                )
                torch_id = cursor.lastrowid

            # Create reminders
            cap_at = started_at + cap_minutes * 60
            for mins in pre_alerts_min:
                await conn.execute(
                    "INSERT INTO reminders(torch_id, due_at, kind, fired) VALUES(?, ?, 'pre', 0)",
                    (torch_id, cap_at - mins * 60),
                )

            # Final cap reminder
            await conn.execute(
                "INSERT INTO reminders(torch_id, due_at, kind, fired) VALUES(?, ?, 'cap', 0)",
                (torch_id, cap_at),
            )

        return {"torch_id": torch_id, "started_at": started_at, "cap_at": cap_at}

    async def cancel_torch(self, username: str) -> bool:
        """Cancel active torch for username."""
        torch = await self.get_active_torch(username)
        if not torch:
            return False

        async with self.transaction() as conn:
            await conn.execute("UPDATE torches SET active = 0 WHERE id = ?", (torch["id"],))
            await conn.execute("DELETE FROM reminders WHERE torch_id = ?", (torch["id"],))

        return True

    async def get_due_reminders(self, now_ts: int, limit: int = 20) -> list[dict[str, Any]]:
        """Get reminders that are due."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, torch_id, due_at, kind
                FROM reminders
                WHERE fired = 0 AND due_at <= ?
                ORDER BY due_at ASC
                LIMIT ?
                """,
                (now_ts, limit),
            )
            rows = await cursor.fetchall()

            return [
                {
                    "id": row[0],
                    "torch_id": row[1],
                    "due_at": row[2],
                    "kind": row[3],
                }
                for row in rows
            ]

    async def mark_reminder_fired(self, reminder_id: int) -> None:
        """Mark reminder as fired."""
        async with self.connection() as conn:
            await conn.execute(
                "UPDATE reminders SET fired = 1 WHERE id = ?",
                (reminder_id,),
            )
            await conn.commit()

    async def get_torch(self, torch_id: int) -> dict[str, Any] | None:
        """Get torch by ID."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, tg_username, started_at, reported_by, thread_id, active, chat_id
                FROM torches
                WHERE id = ?
                """,
                (torch_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None

            return {
                "id": row[0],
                "username": row[1],
                "started_at": row[2],
                "reported_by": row[3],
                "thread_id": row[4],
                "active": row[5],
                "chat_id": row[6],
            }

    async def list_active_torches(self) -> list[dict[str, Any]]:
        """List all active torches."""
        async with self.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, tg_username, started_at
                FROM torches
                WHERE active = 1
                ORDER BY started_at ASC
                """
            )
            rows = await cursor.fetchall()

            return [
                {
                    "torch_id": row[0],
                    "username": row[1],
                    "started_at": row[2],
                }
                for row in rows
            ]


# Singleton instance
_db: AsyncDatabase | None = None


def get_database() -> AsyncDatabase:
    """Get or create the singleton database instance."""
    global _db
    if _db is None:
        _db = AsyncDatabase()
    return _db


async def close_database():
    """Close the singleton database."""
    global _db
    if _db:
        await _db.close()
        _db = None
