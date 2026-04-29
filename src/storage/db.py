from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass
class AuthorizedUser:
    telegram_user_id: int
    username: str | None
    expires_at: datetime


class Database:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS authorized_users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    granted_by INTEGER NOT NULL,
                    granted_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS news_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS price_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    price REAL,
                    bid REAL,
                    ask REAL,
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS custom_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER NOT NULL,
                    source_name TEXT,
                    source_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(owner_user_id, source_url)
                );

                CREATE TABLE IF NOT EXISTS broadcast_channels (
                    channel_id INTEGER PRIMARY KEY,
                    channel_name TEXT,
                    added_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def add_or_extend_user(
        self, telegram_user_id: int, username: str | None, expires_at: datetime, granted_by: int
    ) -> None:
        now = self._now_iso()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO authorized_users(
                    telegram_user_id, username, granted_by, granted_at, expires_at, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    username=excluded.username,
                    granted_by=excluded.granted_by,
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at
                """,
                (telegram_user_id, username, granted_by, now, expires_at.isoformat(), now, now),
            )

    def remove_user(self, telegram_user_id: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "DELETE FROM authorized_users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )

    def is_user_authorized(self, telegram_user_id: int) -> bool:
        self.purge_expired_users()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM authorized_users WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
            return bool(row)

    def list_authorized_users(self) -> list[AuthorizedUser]:
        self.purge_expired_users()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT telegram_user_id, username, expires_at FROM authorized_users ORDER BY expires_at ASC"
            ).fetchall()
        return [
            AuthorizedUser(
                telegram_user_id=row["telegram_user_id"],
                username=row["username"],
                expires_at=datetime.fromisoformat(row["expires_at"]),
            )
            for row in rows
        ]

    def purge_expired_users(self) -> None:
        now = self._now_iso()
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM authorized_users WHERE expires_at <= ?", (now,))

    def set_setting(self, key: str, value: str) -> None:
        now = self._now_iso()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO bot_settings(setting_key, setting_value, updated_at)
                VALUES(?,?,?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value=excluded.setting_value,
                    updated_at=excluded.updated_at
                """,
                (key, value, now),
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = ?",
                (key,),
            ).fetchone()
            if not row:
                return default
            return str(row["setting_value"])

    def get_last_broadcast_at(self) -> str | None:
        return self.get_setting("last_broadcast_at")

    def set_last_broadcast_at(self, value: str) -> None:
        self.set_setting("last_broadcast_at", value)

    def get_last_alert_state(self) -> dict[str, str | None]:
        return {
            "hash": self.get_setting("last_alert_hash"),
            "signal": self.get_setting("last_alert_signal"),
            "confidence": self.get_setting("last_alert_confidence"),
            "sent_at": self.get_setting("last_alert_sent_at"),
            "price": self.get_setting("last_alert_price"),
            "headlines_hash": self.get_setting("last_alert_headlines_hash"),
        }

    def set_last_alert_state(
        self,
        signal_hash: str,
        signal: str,
        confidence: str,
        sent_at: str,
        price: str | None = None,
        headlines_hash: str | None = None,
    ) -> None:
        self.set_setting("last_alert_hash", signal_hash)
        self.set_setting("last_alert_signal", signal)
        self.set_setting("last_alert_confidence", confidence)
        self.set_setting("last_alert_sent_at", sent_at)
        if price is not None:
            self.set_setting("last_alert_price", price)
        if headlines_hash is not None:
            self.set_setting("last_alert_headlines_hash", headlines_hash)

    def get_watch_state(self) -> dict[str, str | None]:
        return {
            "last_checked_at": self.get_setting("watch_last_checked_at"),
            "last_signal": self.get_setting("watch_last_signal"),
            "last_confidence": self.get_setting("watch_last_confidence"),
            "last_price": self.get_setting("watch_last_price"),
            "last_headlines_hash": self.get_setting("watch_last_headlines_hash"),
            "last_sent_at": self.get_setting("last_alert_sent_at"),
            "last_sent_signal": self.get_setting("last_alert_signal"),
            "last_sent_confidence": self.get_setting("last_alert_confidence"),
        }

    def set_watch_state(
        self,
        checked_at: str,
        signal: str,
        confidence: str,
        price: str,
        headlines_hash: str,
    ) -> None:
        self.set_setting("watch_last_checked_at", checked_at)
        self.set_setting("watch_last_signal", signal)
        self.set_setting("watch_last_confidence", confidence)
        self.set_setting("watch_last_price", price)
        self.set_setting("watch_last_headlines_hash", headlines_hash)

    def add_news_items(self, items: Iterable[dict[str, str | None]]) -> None:
        now = self._now_iso()
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                """
                INSERT INTO news_cache(source, title, url, published_at, created_at)
                VALUES(?,?,?,?,?)
                """,
                [
                    (item["source"], item["title"], item["url"], item.get("published_at"), now)
                    for item in items
                ],
            )
            conn.execute(
                """
                DELETE FROM news_cache
                WHERE id NOT IN (
                    SELECT id FROM news_cache ORDER BY id DESC LIMIT 300
                )
                """
            )

    def get_recent_news(self, limit: int = 8) -> list[dict[str, str | None]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT source, title, url, published_at
                FROM news_cache
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_news_since(self, since_iso: str, limit: int = 200) -> list[dict[str, str | None]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT source, title, url, published_at, created_at
                FROM news_cache
                WHERE created_at >= ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (since_iso, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_latest_price(
        self, provider: str, currency: str, price: float | None, bid: float | None, ask: float | None
    ) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO price_cache(provider, currency, price, bid, ask, fetched_at)
                VALUES(?,?,?,?,?,?)
                """,
                (provider, currency, price, bid, ask, self._now_iso()),
            )
            conn.execute(
                "DELETE FROM price_cache WHERE id NOT IN (SELECT id FROM price_cache ORDER BY id DESC LIMIT 50)"
            )

    def get_latest_price(self) -> dict[str, str | float | None] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT provider, currency, price, bid, ask, fetched_at
                FROM price_cache
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def add_audit_log(self, actor_id: int, action: str, details: str | None = None) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO audit_log(actor_id, action, details, created_at) VALUES(?,?,?,?)",
                (actor_id, action, details, self._now_iso()),
            )

    def add_custom_source(self, owner_user_id: int, source_url: str, source_name: str | None = None) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO custom_sources(owner_user_id, source_name, source_url, created_at)
                VALUES(?,?,?,?)
                ON CONFLICT(owner_user_id, source_url) DO UPDATE SET
                    source_name=excluded.source_name
                """,
                (owner_user_id, source_name, source_url, self._now_iso()),
            )

    def remove_custom_source(self, owner_user_id: int, source_url: str) -> bool:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "DELETE FROM custom_sources WHERE owner_user_id = ? AND source_url = ?",
                (owner_user_id, source_url),
            )
            return cursor.rowcount > 0

    def list_custom_sources(self, owner_user_id: int | None = None) -> list[dict[str, str | int | None]]:
        with closing(self._connect()) as conn:
            if owner_user_id is None:
                rows = conn.execute(
                    """
                    SELECT owner_user_id, source_name, source_url, created_at
                    FROM custom_sources
                    ORDER BY id DESC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT owner_user_id, source_name, source_url, created_at
                    FROM custom_sources
                    WHERE owner_user_id = ?
                    ORDER BY id DESC
                    """,
                    (owner_user_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def add_broadcast_channel(self, channel_id: int, added_by: int, channel_name: str | None = None) -> None:
        now = self._now_iso()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO broadcast_channels(channel_id, channel_name, added_by, created_at, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    channel_name=excluded.channel_name,
                    added_by=excluded.added_by,
                    updated_at=excluded.updated_at
                """,
                (channel_id, channel_name, added_by, now, now),
            )

    def remove_broadcast_channel(self, channel_id: int) -> bool:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute("DELETE FROM broadcast_channels WHERE channel_id = ?", (channel_id,))
            return cursor.rowcount > 0

    def list_broadcast_channels(self) -> list[dict[str, str | int | None]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT channel_id, channel_name, added_by, created_at, updated_at
                FROM broadcast_channels
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]
