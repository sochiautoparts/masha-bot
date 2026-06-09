"""SQLite database with aiosqlite for masha-bot.

Tables: users, chat_history, news_items, channel_posts, ai_cache,
        partner_posts, post_fingerprints, topic_registry, evergreen_used
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).parent / "data"
DB_PATH = os.getenv("DB_PATH", str(DB_DIR / "masha_bot.db"))


class Database:
    """Async SQLite database for masha-bot."""

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        """Initialize database and create tables."""
        # Ensure directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._create_tables()
        logger.info("Database initialized at %s", self.db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _create_tables(self) -> None:
        """Create all required tables."""
        assert self._conn is not None

        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                joined_at TEXT DEFAULT (datetime('now')),
                is_subscriber INTEGER DEFAULT 0,
                questions_asked INTEGER DEFAULT 0,
                last_interaction TEXT
            );

            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                summary TEXT,
                published_at TEXT,
                fetched_at TEXT DEFAULT (datetime('now')),
                is_urgent INTEGER DEFAULT 0,
                is_used INTEGER DEFAULT 0,
                content_type TEXT,
                fingerprint TEXT,
                UNIQUE(url)
            );

            CREATE TABLE IF NOT EXISTS channel_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                text TEXT NOT NULL,
                content_type TEXT,
                source TEXT,
                character_mix TEXT,
                has_image INTEGER DEFAULT 0,
                image_url TEXT,
                posted_at TEXT DEFAULT (datetime('now')),
                views INTEGER DEFAULT 0,
                reactions TEXT,
                fingerprint TEXT
            );

            CREATE TABLE IF NOT EXISTS ai_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key TEXT UNIQUE NOT NULL,
                prompt_hash TEXT,
                response_text TEXT,
                model TEXT,
                provider TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT
            );

            CREATE TABLE IF NOT EXISTS partner_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_name TEXT NOT NULL,
                post_text TEXT,
                post_url TEXT,
                posted_at TEXT DEFAULT (datetime('now')),
                message_id INTEGER,
                clicks INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS post_fingerprints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE NOT NULL,
                post_id INTEGER,
                text_hash TEXT,
                semantic_hash TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (post_id) REFERENCES channel_posts(id)
            );

            CREATE TABLE IF NOT EXISTS topic_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                theme_day TEXT,
                content_type TEXT,
                used_at TEXT,
                use_count INTEGER DEFAULT 0,
                last_used TEXT,
                UNIQUE(topic)
            );

            CREATE TABLE IF NOT EXISTS evergreen_used (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evergreen_id TEXT NOT NULL,
                used_at TEXT DEFAULT (datetime('now')),
                post_id INTEGER,
                UNIQUE(evergreen_id)
            );

            CREATE INDEX IF NOT EXISTS idx_news_fingerprint ON news_items(fingerprint);
            CREATE INDEX IF NOT EXISTS idx_news_is_used ON news_items(is_used);
            CREATE INDEX IF NOT EXISTS idx_channel_posts_posted_at ON channel_posts(posted_at);
            CREATE INDEX IF NOT EXISTS idx_ai_cache_key ON ai_cache(cache_key);
            CREATE INDEX IF NOT EXISTS idx_post_fingerprints_fingerprint ON post_fingerprints(fingerprint);
            CREATE INDEX IF NOT EXISTS idx_topic_registry_last_used ON topic_registry(last_used);
        """)
        await self._conn.commit()

    # ── Users ─────────────────────────────────────────────────────────────

    async def add_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> None:
        """Add or update a user."""
        assert self._conn is not None
        await self._conn.execute(
            """INSERT INTO users (user_id, username, first_name, last_name, last_interaction)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   username = COALESCE(excluded.username, users.username),
                   first_name = COALESCE(excluded.first_name, users.first_name),
                   last_name = COALESCE(excluded.last_name, users.last_name),
                   last_interaction = excluded.last_interaction""",
            (user_id, username, first_name, last_name, datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        """Get a user by ID."""
        assert self._conn is not None
        async with self._conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # ── Chat History ──────────────────────────────────────────────────────

    async def add_chat_message(
        self,
        user_id: int,
        chat_id: int,
        role: str,
        content: str,
    ) -> None:
        """Add a chat message to history."""
        assert self._conn is not None
        await self._conn.execute(
            "INSERT INTO chat_history (user_id, chat_id, role, content) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, role, content),
        )
        await self._conn.commit()

    async def get_chat_history(
        self,
        chat_id: int,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get recent chat history for a chat."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in reversed(rows)]

    # ── News Items ────────────────────────────────────────────────────────

    async def add_news_item(
        self,
        source: str,
        title: str,
        url: str | None = None,
        summary: str | None = None,
        published_at: str | None = None,
        is_urgent: bool = False,
        content_type: str | None = None,
    ) -> bool:
        """Add a news item. Returns True if added, False if duplicate."""
        assert self._conn is not None
        fingerprint = hashlib.sha256(
            (title + (url or "")).encode()
        ).hexdigest()[:16]

        try:
            await self._conn.execute(
                """INSERT INTO news_items (source, title, url, summary, published_at, is_urgent, content_type, fingerprint)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (source, title, url, summary, published_at, int(is_urgent), content_type, fingerprint),
            )
            await self._conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def get_unused_news(
        self,
        limit: int = 10,
        urgent_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Get unused news items."""
        assert self._conn is not None
        query = "SELECT * FROM news_items WHERE is_used = 0"
        if urgent_only:
            query += " AND is_urgent = 1"
        query += " ORDER BY fetched_at DESC LIMIT ?"

        async with self._conn.execute(query, (limit,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def mark_news_used(self, news_id: int) -> None:
        """Mark a news item as used."""
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE news_items SET is_used = 1 WHERE id = ?", (news_id,)
        )
        await self._conn.commit()

    # ── Channel Posts ─────────────────────────────────────────────────────

    async def add_channel_post(
        self,
        message_id: int,
        text: str,
        content_type: str | None = None,
        source: str | None = None,
        character_mix: str | None = None,
        has_image: bool = False,
        image_url: str | None = None,
        fingerprint: str | None = None,
    ) -> int:
        """Add a channel post record. Returns the database ID."""
        assert self._conn is not None
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        cur = await self._conn.execute(
            """INSERT INTO channel_posts
               (message_id, text, content_type, source, character_mix, has_image, image_url, fingerprint)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, text, content_type, source, character_mix, int(has_image), image_url, fingerprint or text_hash),
        )
        await self._conn.commit()
        return cur.lastrowid or 0

    async def get_posts_today_count(self) -> int:
        """Get number of posts made today."""
        assert self._conn is not None
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM channel_posts WHERE date(posted_at) = ?",
            (today,),
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    async def get_recent_posts(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent channel posts."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM channel_posts ORDER BY posted_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def is_duplicate_post(self, text: str, threshold: float = 0.75) -> bool:
        """Check if a post is semantically similar to recent posts."""
        assert self._conn is not None
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

        # Check exact hash first
        async with self._conn.execute(
            "SELECT id FROM post_fingerprints WHERE text_hash = ?",
            (text_hash,),
        ) as cur:
            if await cur.fetchone():
                return True

        # Check fingerprint
        async with self._conn.execute(
            "SELECT id FROM post_fingerprints WHERE fingerprint = ?",
            (text_hash,),
        ) as cur:
            if await cur.fetchone():
                return True

        return False

    # ── Post Fingerprints ─────────────────────────────────────────────────

    async def add_fingerprint(
        self,
        fingerprint: str,
        post_id: int | None = None,
        text_hash: str | None = None,
        semantic_hash: str | None = None,
    ) -> None:
        """Add a post fingerprint for dedup."""
        assert self._conn is not None
        try:
            await self._conn.execute(
                "INSERT INTO post_fingerprints (fingerprint, post_id, text_hash, semantic_hash) VALUES (?, ?, ?, ?)",
                (fingerprint, post_id, text_hash, semantic_hash),
            )
            await self._conn.commit()
        except aiosqlite.IntegrityError:
            pass

    # ── AI Cache ──────────────────────────────────────────────────────────

    async def get_cached_response(self, cache_key: str) -> dict[str, Any] | None:
        """Get a cached AI response."""
        assert self._conn is not None
        now = datetime.now(timezone.utc).isoformat()
        async with self._conn.execute(
            "SELECT * FROM ai_cache WHERE cache_key = ? AND (expires_at IS NULL OR expires_at > ?)",
            (cache_key, now),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_cached_response(
        self,
        cache_key: str,
        response_text: str,
        model: str | None = None,
        provider: str | None = None,
        prompt_hash: str | None = None,
        ttl_hours: int = 24,
    ) -> None:
        """Cache an AI response."""
        assert self._conn is not None
        expires = (
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
        )
        try:
            await self._conn.execute(
                """INSERT INTO ai_cache (cache_key, prompt_hash, response_text, model, provider, expires_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now', '+' || ? || ' hours'))""",
                (cache_key, prompt_hash, response_text, model, provider, str(ttl_hours)),
            )
            await self._conn.commit()
        except aiosqlite.IntegrityError:
            pass

    # ── Topic Registry ────────────────────────────────────────────────────

    async def register_topic(
        self,
        topic: str,
        theme_day: str | None = None,
        content_type: str | None = None,
    ) -> None:
        """Register a topic as used."""
        assert self._conn is not None
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """INSERT INTO topic_registry (topic, theme_day, content_type, use_count, last_used)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(topic) DO UPDATE SET
                   use_count = use_count + 1,
                   last_used = excluded.last_used""",
            (topic, theme_day, content_type, now),
        )
        await self._conn.commit()

    async def get_unused_topics(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get topics not used recently."""
        assert self._conn is not None
        async with self._conn.execute(
            """SELECT * FROM topic_registry
               WHERE last_used IS NULL OR last_used < datetime('now', '-7 days')
               ORDER BY last_used ASC NULLS FIRST LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # ── Evergreen Used ────────────────────────────────────────────────────

    async def mark_evergreen_used(self, evergreen_id: str, post_id: int | None = None) -> None:
        """Mark an evergreen content item as used."""
        assert self._conn is not None
        try:
            await self._conn.execute(
                "INSERT INTO evergreen_used (evergreen_id, post_id) VALUES (?, ?)",
                (evergreen_id, post_id),
            )
            await self._conn.commit()
        except aiosqlite.IntegrityError:
            pass

    async def is_evergreen_used(self, evergreen_id: str) -> bool:
        """Check if an evergreen item has been used."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT id FROM evergreen_used WHERE evergreen_id = ?", (evergreen_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def get_unused_evergreen_count(self) -> int:
        """Count evergreen items not yet used."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM evergreen_used WHERE used_at > datetime('now', '-30 days')"
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    # ── Partner Posts ─────────────────────────────────────────────────────

    async def add_partner_post(
        self,
        program_name: str,
        post_text: str,
        post_url: str | None = None,
        message_id: int | None = None,
    ) -> int:
        """Record a partner post."""
        assert self._conn is not None
        cur = await self._conn.execute(
            "INSERT INTO partner_posts (program_name, post_text, post_url, message_id) VALUES (?, ?, ?, ?)",
            (program_name, post_text, post_url, message_id),
        )
        await self._conn.commit()
        return cur.lastrowid or 0

    async def get_partner_posts_today(self) -> int:
        """Count partner posts today."""
        assert self._conn is not None
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM partner_posts WHERE date(posted_at) = ?",
            (today,),
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    # ── Analytics ─────────────────────────────────────────────────────────

    async def get_posts_stats(self, days: int = 7) -> dict[str, Any]:
        """Get posting statistics for the last N days."""
        assert self._conn is not None
        async with self._conn.execute(
            """SELECT
                date(posted_at) as date,
                COUNT(*) as post_count,
                COUNT(CASE WHEN has_image = 1 THEN 1 END) as with_image,
                COUNT(DISTINCT content_type) as content_types
               FROM channel_posts
               WHERE posted_at > datetime('now', '-' || ? || ' days')
               GROUP BY date(posted_at)
               ORDER BY date DESC""",
            (str(days),),
        ) as cur:
            rows = await cur.fetchall()
            return {"daily_stats": [dict(r) for r in rows], "period_days": days}

    async def cleanup_old_data(self, days: int = 30) -> int:
        """Remove old data to keep database size manageable."""
        assert self._conn is not None
        count = 0

        # Clean old AI cache
        cur = await self._conn.execute(
            "DELETE FROM ai_cache WHERE expires_at < datetime('now')"
        )
        count += cur.rowcount

        # Clean old news items (used, older than N days)
        cur = await self._conn.execute(
            "DELETE FROM news_items WHERE is_used = 1 AND fetched_at < datetime('now', '-' || ? || ' days')",
            (str(days),),
        )
        count += cur.rowcount

        # Clean old chat history
        cur = await self._conn.execute(
            "DELETE FROM chat_history WHERE created_at < datetime('now', '-' || ? || ' days')",
            (str(days * 2),),
        )
        count += cur.rowcount

        await self._conn.commit()
        return count
