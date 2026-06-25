"""SQLite database with aiosqlite for masha-bot.

Tables: users, chat_history, news_items, channel_posts, ai_cache,
        partner_posts, post_fingerprints, topic_registry, evergreen_used,
        user_cars, chat_modes

Module-level async functions wrap a singleton Database instance,
providing a simple functional API used across the codebase.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
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
        await self._run_migrations()
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
                language_code TEXT DEFAULT 'ru',
                joined_at TEXT DEFAULT (datetime('now')),
                is_subscriber INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
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
                image_urls TEXT DEFAULT '',
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
                first_seen REAL,
                last_posted REAL,
                post_count INTEGER DEFAULT 0,
                titles TEXT,
                UNIQUE(topic)
            );

            CREATE TABLE IF NOT EXISTS evergreen_used (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evergreen_id TEXT NOT NULL,
                used_at TEXT DEFAULT (datetime('now')),
                post_id INTEGER,
                UNIQUE(evergreen_id)
            );

            CREATE TABLE IF NOT EXISTS user_cars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                brand TEXT DEFAULT '',
                model TEXT DEFAULT '',
                year INTEGER DEFAULT 0,
                engine TEXT DEFAULT '',
                mileage INTEGER DEFAULT 0,
                vin TEXT DEFAULT '',
                added_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS chat_modes (
                user_id INTEGER PRIMARY KEY,
                mode TEXT DEFAULT 'normal',
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            -- v16: posted_urls — TTL-based URL dedup (aligned with asya-bot).
            -- Unlike channel_posts.source (which is checked with NO time bound
            -- and permanently blocks re-posting), this table uses a 48h TTL
            -- so articles can be re-posted after 48h when the news pool runs low.
            -- Cleanup prunes entries older than 30 days (see cleanup_posted_urls).
            CREATE TABLE IF NOT EXISTS posted_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                url_fingerprint TEXT NOT NULL,
                title TEXT DEFAULT '',
                posted_at REAL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_posted_urls_fingerprint ON posted_urls(url_fingerprint);
            CREATE INDEX IF NOT EXISTS idx_posted_urls_time ON posted_urls(posted_at);

            CREATE INDEX IF NOT EXISTS idx_news_fingerprint ON news_items(fingerprint);
            CREATE INDEX IF NOT EXISTS idx_news_is_used ON news_items(is_used);
            CREATE INDEX IF NOT EXISTS idx_channel_posts_posted_at ON channel_posts(posted_at);
            CREATE INDEX IF NOT EXISTS idx_ai_cache_key ON ai_cache(cache_key);
            CREATE INDEX IF NOT EXISTS idx_post_fingerprints_fingerprint ON post_fingerprints(fingerprint);
            CREATE INDEX IF NOT EXISTS idx_topic_registry_last_used ON topic_registry(last_used);
            CREATE INDEX IF NOT EXISTS idx_user_cars_user_id ON user_cars(user_id);
            CREATE INDEX IF NOT EXISTS idx_chat_modes_user_id ON chat_modes(user_id);
        """)

        # Migrate: add columns if they don't exist (for existing databases)
        alter_statements = [
            "ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN language_code TEXT DEFAULT 'ru'",
            "ALTER TABLE topic_registry ADD COLUMN first_seen REAL",
            "ALTER TABLE topic_registry ADD COLUMN last_posted REAL",
            "ALTER TABLE topic_registry ADD COLUMN post_count INTEGER DEFAULT 0",
            "ALTER TABLE topic_registry ADD COLUMN titles TEXT",
            "ALTER TABLE news_items ADD COLUMN image_urls TEXT DEFAULT ''",
        ]
        for stmt in alter_statements:
            try:
                await self._conn.execute(stmt)
            except Exception:
                pass  # Column already exists

        await self._conn.commit()

    async def _run_migrations(self) -> None:
        """Run data migrations for existing databases."""
        assert self._conn is not None

        # Ensure image_urls column exists in news_items
        try:
            await self._conn.execute("SELECT image_urls FROM news_items LIMIT 1")
        except Exception:
            try:
                await self._conn.execute("ALTER TABLE news_items ADD COLUMN image_urls TEXT DEFAULT ''")
                await self._conn.commit()
                logger.info("Migration: added image_urls column to news_items")
            except Exception:
                pass

        # v18: add author_name column to chat_history so group conversations
        # can record WHO said each message — Маша uses this to follow the thread.
        try:
            await self._conn.execute("SELECT author_name FROM chat_history LIMIT 1")
        except Exception:
            try:
                await self._conn.execute(
                    "ALTER TABLE chat_history ADD COLUMN author_name TEXT DEFAULT ''"
                )
                await self._conn.commit()
                logger.info("Migration: added author_name column to chat_history")
            except Exception:
                pass

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
        author_name: str = "",
    ) -> None:
        """Add a chat message to history.

        v18: `author_name` records WHO said the message (display name of the
        group participant, or "Маша" for the bot). Used to build a readable
        conversation transcript for the AI when Маша replies in groups.
        """
        assert self._conn is not None
        await self._conn.execute(
            "INSERT INTO chat_history (user_id, chat_id, role, content, author_name) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, role, content, author_name),
        )
        await self._conn.commit()

    async def get_chat_history(
        self,
        chat_id: int,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get recent chat history for a chat (oldest-first)."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in reversed(rows)]

    async def clear_chat_history_for_user(self, user_id: int) -> None:
        """Clear chat history for a user."""
        assert self._conn is not None
        await self._conn.execute(
            "DELETE FROM chat_history WHERE user_id = ? OR chat_id = ?",
            (user_id, user_id),
        )
        await self._conn.commit()

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
        image_urls: list[str] | None = None,
    ) -> bool:
        """Add a news item. Returns True if added, False if duplicate."""
        assert self._conn is not None
        fingerprint = hashlib.sha256(
            (title + (url or "")).encode()
        ).hexdigest()[:16]

        # Serialize image_urls to JSON string for storage
        image_urls_json = json.dumps(image_urls, ensure_ascii=False) if image_urls else ""

        try:
            await self._conn.execute(
                """INSERT INTO news_items (source, title, url, summary, published_at, is_urgent, content_type, fingerprint, image_urls)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source, title, url, summary, published_at, int(is_urgent), content_type, fingerprint, image_urls_json),
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
            items = [dict(r) for r in rows]
            # Parse image_urls from JSON
            for item in items:
                if "image_urls" in item and isinstance(item["image_urls"], str) and item["image_urls"]:
                    try:
                        item["image_urls"] = json.loads(item["image_urls"])
                    except (json.JSONDecodeError, TypeError):
                        item["image_urls"] = []
                elif "image_urls" not in item or not item.get("image_urls"):
                    # Empty string "" or missing → empty list
                    item["image_urls"] = []
            return items

    async def mark_news_used(self, news_id: int) -> None:
        """Mark a news item as used."""
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE news_items SET is_used = 1 WHERE id = ?", (news_id,)
        )
        await self._conn.commit()

    async def mark_news_used_by_url(self, url: str) -> None:
        """Mark a news item as used by its URL."""
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE news_items SET is_used = 1 WHERE url = ?", (url,)
        )
        await self._conn.commit()

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize a URL for dedup comparison.

        Canonicalization:
          - lowercase scheme + host
          - strip leading "www." from host
          - force https scheme (so http:// and https:// are treated as equal)
          - strip trailing slash (except root "/")
          - drop tracking query params (utm_*, ref, source, ref_src, ref_url,
            fbclid, gclid, etc.)

        This prevents the same article from being posted twice when the URL has
        trivial differences (http vs https, www vs non-www, trailing slash,
        tracking params).
        """
        if not url:
            return ""
        u = url.strip()
        try:
            from urllib.parse import urlsplit, urlunsplit
            parts = urlsplit(u)
            scheme = "https"
            host = (parts.hostname or "").lower()
            if host.startswith("www."):
                host = host[4:]
            path = parts.path or ""
            # Strip trailing slash (but keep root "/")
            if len(path) > 1 and path.endswith("/"):
                path = path.rstrip("/")
            # Drop tracking query params
            kept = []
            if parts.query:
                for kv in parts.query.split("&"):
                    if "=" in kv:
                        k, _, _v = kv.partition("=")
                        k = k.lower()
                        if k.startswith("utm_") or k in (
                            "ref", "source", "ref_src", "ref_url",
                            "fbclid", "gclid", "mc_cid", "mc_eid",
                        ):
                            continue
                        kept.append(kv)
                    else:
                        kept.append(kv)
            query = "&".join(kept)
            u = urlunsplit((scheme, host, path, query, ""))
        except Exception:
            # Fallback to the pre-v10 simple normalization if urlsplit fails
            if u.endswith("/") and u.count("/") > 3:
                u = u.rstrip("/")
            if "?" in u:
                base, _, query = u.partition("?")
                kept = []
                for kv in query.split("&"):
                    if "=" in kv:
                        k, _, _v = kv.partition("=")
                        k = k.lower()
                        if k.startswith("utm_") or k in ("ref", "source", "ref_src", "ref_url"):
                            continue
                        kept.append(kv)
                    else:
                        kept.append(kv)
                u = base + ("?" + "&".join(kept) if kept else "")
        return u

    @staticmethod
    def _url_dedup_variants(url: str) -> list[str]:
        """Build a list of URL variants to check against the DB.

        The DB stores RAW source URLs in channel_posts.source and news_items.url.
        Because historical posts used the old (looser) normalization, we check
        several plausible forms of the incoming URL so that an article posted
        as http://www.example.com/a/, https://example.com/a, etc. is recognized
        as already-posted.
        """
        if not url:
            return []
        raw = url.strip()
        normalized = Database._normalize_url(raw)
        variants = set()
        variants.add(raw)
        variants.add(normalized)
        # with/without trailing slash
        if normalized.endswith("/"):
            variants.add(normalized.rstrip("/"))
        else:
            variants.add(normalized + "/")
        # http vs https of the normalized form
        if normalized.startswith("https://"):
            variants.add("http://" + normalized[len("https://"):])
        elif normalized.startswith("http://"):
            variants.add("https://" + normalized[len("http://"):])
        # www vs non-www (both schemes)
        for v in list(variants):
            if "://www." in v:
                variants.add(v.replace("://www.", "://", 1))
            else:
                m = re.match(r"(https?://)([^/]+)", v)
                if m:
                    variants.add(m.group(1) + "www." + m.group(2) + v[len(m.group(0)):])
        # Order: most-likely first, drop empties
        ordered = [v for v in [raw, normalized] if v]
        for v in variants:
            if v not in ordered:
                ordered.append(v)
        return ordered

    async def is_source_url_posted(self, source_url: str) -> bool:
        """Check if a source URL has already been posted to the channel.

        This is the PRIMARY dedup mechanism — if the same article URL was
        already posted, we skip it regardless of what text the AI generates.
        Prevents the same news being posted multiple times with different text.

        v10.0: Generates http/https, www/non-www, slash/no-slash variants so
        trivially-different URLs are treated as duplicates.
        """
        if not source_url:
            return False
        assert self._conn is not None

        candidates = self._url_dedup_variants(source_url)

        # Check channel_posts table for any URL variant
        for url_variant in candidates:
            async with self._conn.execute(
                "SELECT id FROM channel_posts WHERE source = ? LIMIT 1",
                (url_variant,)
            ) as cur:
                if await cur.fetchone():
                    return True

        # Also check news_items table for is_used flag
        for url_variant in candidates:
            async with self._conn.execute(
                "SELECT id FROM news_items WHERE url = ? AND is_used = 1 LIMIT 1",
                (url_variant,)
            ) as cur:
                if await cur.fetchone():
                    return True

        return False

    # ── v16: posted_urls — TTL-based URL dedup (aligned with asya-bot) ────
    # Unlike is_source_url_posted() which checks channel_posts.source with NO
    # time bound (permanent block), these methods use a 48h TTL so articles
    # can be re-posted after 48h when the news pool runs low.

    @staticmethod
    def _url_fingerprint(url: str) -> str:
        """Stable fingerprint for a URL — same as _normalize_url but hashed.

        Uses the same normalization as _url_dedup_variants (strip www, force
        https, drop trailing slash, strip tracking params) so that trivially-
        different URLs map to the same fingerprint.
        """
        normalized = Database._normalize_url(url or "")
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    async def is_url_recently_posted(self, url: str, hours: int = 48) -> bool:
        """Check if a URL was posted within the last `hours` (default 48h).

        This is the v16 TTL-based dedup. Articles posted MORE than 48h ago
        are considered re-postable (the news pool may be low and the audience
        has likely rotated). Returns True only if the URL was posted recently.
        """
        if not url:
            return False
        assert self._conn is not None
        fp = self._url_fingerprint(url)
        cutoff = time.time() - (hours * 3600)
        async with self._conn.execute(
            "SELECT id FROM posted_urls WHERE url_fingerprint = ? AND posted_at >= ? LIMIT 1",
            (fp, cutoff),
        ) as cur:
            return bool(await cur.fetchone())

    async def save_posted_url(self, url: str, title: str = "") -> None:
        """Record that a URL was just posted (UPSERT — updates posted_at if exists)."""
        if not url:
            return
        assert self._conn is not None
        fp = self._url_fingerprint(url)
        now = time.time()
        await self._conn.execute(
            """INSERT INTO posted_urls (url, url_fingerprint, title, posted_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(url_fingerprint) DO UPDATE SET
                   posted_at = excluded.posted_at,
                   title = excluded.title""",
            (url, fp, title[:200], now),
        )
        await self._conn.commit()

    async def cleanup_posted_urls(self, max_age_days: int = 30) -> int:
        """Delete posted_urls entries older than max_age_days. Returns count deleted."""
        assert self._conn is not None
        cutoff = time.time() - (max_age_days * 86400)
        cur = await self._conn.execute(
            "DELETE FROM posted_urls WHERE posted_at < ?", (cutoff,)
        )
        await self._conn.commit()
        return cur.rowcount or 0

    async def migrate_channel_posts_to_posted_urls(self) -> int:
        """One-time migration: copy existing channel_posts.source URLs into
        posted_urls so they're subject to the TTL (and eventually pruned).

        Uses posted_at = posted_at timestamp of the channel_post (so articles
        posted >48h ago become immediately re-postable).
        """
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT source, text, posted_at FROM channel_posts WHERE source LIKE 'http%'"
        ) as cur:
            rows = await cur.fetchall()
        migrated = 0
        for row in rows:
            url = row["source"]
            title = (row["text"] or "")[:200]
            # Parse posted_at (text like "2026-06-21 04:03:28") to epoch
            try:
                from datetime import datetime
                dt = datetime.strptime(row["posted_at"], "%Y-%m-%d %H:%M:%S")
                posted_at = dt.timestamp()
            except Exception:
                posted_at = time.time()
            fp = self._url_fingerprint(url)
            try:
                await self._conn.execute(
                    """INSERT OR IGNORE INTO posted_urls (url, url_fingerprint, title, posted_at)
                       VALUES (?, ?, ?, ?)""",
                    (url, fp, title, posted_at),
                )
                migrated += 1
            except Exception:
                pass
        await self._conn.commit()
        return migrated

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

    async def get_posts_hourly_count(self) -> int:
        """Get number of posts in the current hour."""
        assert self._conn is not None
        now = datetime.now(timezone.utc)
        hour_start = now.replace(minute=0, second=0, microsecond=0).isoformat()
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM channel_posts WHERE posted_at >= ?",
            (hour_start,),
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0

    async def get_posts_recent_count(self, hours: int = 3) -> int:
        """Get number of posts in the last N hours (rolling window).

        v16: Used for cap checks instead of get_posts_today_count() (which
        counts since midnight UTC). The rolling window ensures old evergreen
        spam from earlier today doesn't permanently block news posts for the
        rest of the day. With a 3h window matching the posting cycle, old
        posts naturally fall off and the bot can resume posting.
        """
        assert self._conn is not None
        cutoff = (datetime.now(timezone.utc).timestamp()) - (hours * 3600)
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM channel_posts WHERE posted_at >= ?",
            (cutoff_dt,),
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

    async def get_recent_post_titles(self, hours: int = 72, limit: int = 50) -> list[str]:
        """Get recent post titles/texts for semantic dedup."""
        assert self._conn is not None
        async with self._conn.execute(
            """SELECT text FROM channel_posts
               WHERE posted_at > datetime('now', '-' || ? || ' hours')
               ORDER BY posted_at DESC LIMIT ?""",
            (str(hours), limit),
        ) as cur:
            rows = await cur.fetchall()
            return [r["text"][:200] for r in rows if r["text"]]

    async def is_duplicate_post(self, text: str, threshold: float = 0.75) -> bool:
        """Check if a post is semantically similar to recent posts.

        NOTE: This checks the `post_fingerprints` table. The active channel.py
        pipeline populates `channel_posts.fingerprint` (not post_fingerprints),
        so prefer is_post_text_duplicate() for real text-dedup. Kept for
        backward compatibility with the legacy publishing module.
        """
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

    async def is_post_text_duplicate(self, text: str, hours: int = 48) -> bool:
        """Check if a post with the same text fingerprint was published recently.

        Uses the `fingerprint` column of `channel_posts` (sha256(text)[:16]),
        which is populated by add_channel_post(). This is the functional
        text-based dedup layer for the active pipeline — it catches exact text
        repeats regardless of source URL.
        """
        assert self._conn is not None
        if not text:
            return False
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        async with self._conn.execute(
            "SELECT id FROM channel_posts WHERE fingerprint = ? "
            "AND posted_at > datetime('now', ?) LIMIT 1",
            (text_hash, f"-{int(hours)} hours"),
        ) as cur:
            return await cur.fetchone() is not None

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

    async def save_topic_registry_entry(
        self,
        entity_key: str,
        first_seen: float,
        last_posted: float,
        post_count: int,
        titles: list[str],
    ) -> None:
        """Save a topic registry entry with full metadata."""
        assert self._conn is not None
        titles_json = json.dumps(titles[-20:], ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """INSERT INTO topic_registry (topic, first_seen, last_posted, post_count, titles, last_used, use_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(topic) DO UPDATE SET
                   first_seen = COALESCE(topic_registry.first_seen, excluded.first_seen),
                   last_posted = excluded.last_posted,
                   post_count = excluded.post_count,
                   titles = excluded.titles,
                   last_used = excluded.last_used,
                   use_count = excluded.use_count""",
            (entity_key, first_seen, last_posted, post_count, titles_json, now, post_count),
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

    async def load_all_topics(self) -> dict[str, dict]:
        """Load all topic registry entries for in-memory dedup."""
        assert self._conn is not None
        result = {}
        async with self._conn.execute("SELECT * FROM topic_registry") as cur:
            rows = await cur.fetchall()
            for r in rows:
                entry = dict(r)
                titles = entry.get("titles", "[]")
                if isinstance(titles, str):
                    try:
                        titles = json.loads(titles)
                    except (json.JSONDecodeError, TypeError):
                        titles = []
                first_seen = entry.get("first_seen") or entry.get("last_used")
                if isinstance(first_seen, str):
                    try:
                        from datetime import datetime as dt
                        first_seen = dt.fromisoformat(first_seen).timestamp()
                    except (ValueError, TypeError):
                        first_seen = time.time()
                last_posted = entry.get("last_posted") or entry.get("last_used")
                if isinstance(last_posted, str):
                    try:
                        from datetime import datetime as dt
                        last_posted = dt.fromisoformat(last_posted).timestamp()
                    except (ValueError, TypeError):
                        last_posted = time.time()
                result[entry["topic"]] = {
                    "first_seen": first_seen if isinstance(first_seen, (int, float)) else time.time(),
                    "last_posted": last_posted if isinstance(last_posted, (int, float)) else time.time(),
                    "post_count": entry.get("post_count", 1) or 1,
                    "titles": titles if isinstance(titles, list) else [],
                }
        return result

    # ── Evergreen Used ────────────────────────────────────────────────────

    async def mark_evergreen_used(self, evergreen_id: str, post_id: int | None = None) -> None:
        """Mark an evergreen content item as used.

        UPSERT semantics: if the evergreen_id was already recorded, UPDATE
        used_at to NOW (and post_id). This is critical because evergreen items
        are intentionally re-used over time — without the update, the UNIQUE
        constraint would silently drop the second use and `used_at` would stay
        frozen at the first use, breaking time-window dedup.
        """
        assert self._conn is not None
        await self._conn.execute(
            """INSERT INTO evergreen_used (evergreen_id, post_id, used_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(evergreen_id) DO UPDATE SET
                   used_at = datetime('now'),
                   post_id = excluded.post_id""",
            (evergreen_id, post_id),
        )
        await self._conn.commit()

    async def is_evergreen_used(self, evergreen_id: str) -> bool:
        """Check if an evergreen item has EVER been used."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT id FROM evergreen_used WHERE evergreen_id = ?", (evergreen_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def is_evergreen_used_recently(self, evergreen_id: str, hours: int = 2) -> bool:
        """Check if an evergreen item was used within the last `hours` hours.

        This is the time-window dedup used by the evergreen fallback to prevent
        the same evergreen topic being reposted in quick succession.
        """
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT id FROM evergreen_used WHERE evergreen_id = ? AND used_at > datetime('now', ?)",
            (evergreen_id, f"-{int(hours)} hours"),
        ) as cur:
            return await cur.fetchone() is not None

    async def get_recent_evergreen_ids(self, hours: int = 2) -> set[str]:
        """Return the set of evergreen IDs used within the last `hours` hours."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT evergreen_id FROM evergreen_used WHERE used_at > datetime('now', ?)",
            (f"-{int(hours)} hours",),
        ) as cur:
            rows = await cur.fetchall()
            return {r["evergreen_id"] for r in rows}

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

    # ── User Cars ─────────────────────────────────────────────────────────

    async def add_user_car(
        self,
        user_id: int,
        brand: str = "",
        model: str = "",
        year: int = 0,
        engine: str = "",
        mileage: int = 0,
        vin: str = "",
    ) -> int:
        """Add a car to user's profile."""
        assert self._conn is not None
        cur = await self._conn.execute(
            """INSERT INTO user_cars (user_id, brand, model, year, engine, mileage, vin)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, brand, model, year, engine, mileage, vin),
        )
        await self._conn.commit()
        return cur.lastrowid or 0

    async def get_user_cars(self, user_id: int) -> list[dict[str, Any]]:
        """Get all cars for a user."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM user_cars WHERE user_id = ? ORDER BY added_at DESC",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def delete_user_car(self, car_id: int, user_id: int) -> bool:
        """Delete a car from user's profile. Returns True if deleted."""
        assert self._conn is not None
        cur = await self._conn.execute(
            "DELETE FROM user_cars WHERE id = ? AND user_id = ?",
            (car_id, user_id),
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def update_car_mileage(self, car_id: int, user_id: int, mileage: int) -> bool:
        """Update mileage for a car. Returns True if updated."""
        assert self._conn is not None
        cur = await self._conn.execute(
            "UPDATE user_cars SET mileage = ? WHERE id = ? AND user_id = ?",
            (mileage, car_id, user_id),
        )
        await self._conn.commit()
        return cur.rowcount > 0

    # ── Chat Modes ────────────────────────────────────────────────────────

    async def get_chat_mode(self, user_id: int) -> str:
        """Get the current chat mode for a user. Defaults to 'normal'."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT mode FROM chat_modes WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return row["mode"] if row else "normal"

    async def set_chat_mode(self, user_id: int, mode: str) -> None:
        """Set the chat mode for a user."""
        assert self._conn is not None
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """INSERT INTO chat_modes (user_id, mode, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET mode = excluded.mode, updated_at = excluded.updated_at""",
            (user_id, mode, now),
        )
        await self._conn.commit()

    # ── Admin / Block ─────────────────────────────────────────────────────

    async def is_user_admin(self, user_id: int) -> bool:
        """Check if user is admin."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT is_admin FROM users WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row["is_admin"])

    async def set_user_admin(self, user_id: int, is_admin: bool) -> None:
        """Set admin status for a user."""
        assert self._conn is not None
        # Ensure user exists first
        await self._conn.execute(
            """INSERT INTO users (user_id, is_admin) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET is_admin = excluded.is_admin""",
            (user_id, int(is_admin)),
        )
        await self._conn.commit()

    async def is_user_blocked(self, user_id: int) -> bool:
        """Check if user is blocked."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT is_blocked FROM users WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row["is_blocked"])

    async def block_user(self, user_id: int, is_blocked: bool) -> None:
        """Block or unblock a user."""
        assert self._conn is not None
        # Ensure user exists first
        await self._conn.execute(
            """INSERT INTO users (user_id, is_blocked) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET is_blocked = excluded.is_blocked""",
            (user_id, int(is_blocked)),
        )
        await self._conn.commit()

    # ── Get or Create User ────────────────────────────────────────────────

    async def get_or_create_user(
        self,
        user_id: int,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
        language_code: str = "ru",
    ) -> dict[str, Any]:
        """Get user or create if not exists."""
        assert self._conn is not None
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """INSERT INTO users (user_id, username, first_name, last_name, language_code, last_interaction, questions_asked)
               VALUES (?, ?, ?, ?, ?, ?, 0)
               ON CONFLICT(user_id) DO UPDATE SET
                   username = COALESCE(NULLIF(excluded.username, ''), users.username),
                   first_name = COALESCE(NULLIF(excluded.first_name, ''), users.first_name),
                   last_name = COALESCE(NULLIF(excluded.last_name, ''), users.last_name),
                   last_interaction = excluded.last_interaction,
                   questions_asked = users.questions_asked + 1""",
            (user_id, username, first_name, last_name, language_code, now),
        )
        await self._conn.commit()

        async with self._conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {"user_id": user_id}

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

    async def get_comprehensive_stats(self) -> dict[str, Any]:
        """Get comprehensive bot statistics."""
        assert self._conn is not None
        stats = {}

        async with self._conn.execute("SELECT COUNT(*) as cnt FROM users") as cur:
            row = await cur.fetchone()
            stats["total_users"] = row["cnt"] if row else 0

        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE last_interaction > datetime('now', '-7 days')"
        ) as cur:
            row = await cur.fetchone()
            stats["active_users"] = row["cnt"] if row else 0

        async with self._conn.execute("SELECT COUNT(*) as cnt FROM news_items") as cur:
            row = await cur.fetchone()
            stats["total_news"] = row["cnt"] if row else 0

        async with self._conn.execute("SELECT COUNT(*) as cnt FROM news_items WHERE is_used = 0") as cur:
            row = await cur.fetchone()
            stats["unposted_news"] = row["cnt"] if row else 0

        async with self._conn.execute("SELECT COUNT(*) as cnt FROM channel_posts") as cur:
            row = await cur.fetchone()
            stats["total_posts"] = row["cnt"] if row else 0

        async with self._conn.execute("SELECT COUNT(*) as cnt FROM partner_posts") as cur:
            row = await cur.fetchone()
            stats["partner_posts"] = row["cnt"] if row else 0

        async with self._conn.execute("SELECT COUNT(*) as cnt FROM ai_cache") as cur:
            row = await cur.fetchone()
            stats["cached_queries"] = row["cnt"] if row else 0

        return stats

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

        # Clean old post fingerprints
        cur = await self._conn.execute(
            "DELETE FROM post_fingerprints WHERE created_at < datetime('now', '-' || ? || ' days')",
            (str(days),),
        )
        count += cur.rowcount

        await self._conn.commit()
        return count


# ══════════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL SINGLETON + FUNCTIONAL API
# ══════════════════════════════════════════════════════════════════════════════

_db: Database | None = None


def _get_db() -> Database:
    """Get the singleton database instance."""
    global _db
    if _db is None:
        _db = Database()
    return _db


# ── Initialization ────────────────────────────────────────────────────────

async def init_db() -> None:
    """Initialize the database singleton and create tables.

    v16: Also runs one-time migration to populate posted_urls from existing
    channel_posts, so the 48h TTL dedup has historical data from day one.
    """
    db = _get_db()
    await db.init()
    # v16: One-time migration — populate posted_urls from channel_posts
    try:
        migrated = await db.migrate_channel_posts_to_posted_urls()
        if migrated > 0:
            logger.info(f"v16 migration: {migrated} URLs copied to posted_urls (48h TTL dedup)")
            # Prune entries older than 30 days immediately
            pruned = await db.cleanup_posted_urls(max_age_days=30)
            if pruned > 0:
                logger.info(f"v16 cleanup: pruned {pruned} posted_urls entries older than 30 days")
    except Exception as e:
        logger.warning(f"v16 posted_urls migration failed (non-critical): {e}")


# ── Chat History ──────────────────────────────────────────────────────────

async def add_chat_message(
    user_id: int,
    role: str,
    content: str,
    chat_id: int | None = None,
    author_name: str = "",
) -> None:
    """Add a chat message.

    Backwards-compatible simplified API. Defaults chat_id to user_id (private
    chat). v18: pass chat_id + author_name explicitly for GROUP conversations
    so Маша can load the conversation thread when she replies.
    """
    db = _get_db()
    cid = chat_id if chat_id is not None else user_id
    await db.add_chat_message(
        user_id=user_id, chat_id=cid, role=role,
        content=content, author_name=author_name,
    )


async def get_chat_history(chat_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """Get recent chat history for a chat (oldest-first).

    Module-level API for handlers. Each row dict has keys:
    id, user_id, chat_id, role, content, created_at, author_name.
    Used by the group-chat handler to build a conversation transcript
    so Маша replies with full context of the ongoing discussion.
    """
    db = _get_db()
    return await db.get_chat_history(chat_id=chat_id, limit=limit)


async def clear_chat_history(user_id: int) -> None:
    """Clear all chat history for a user."""
    db = _get_db()
    await db.clear_chat_history_for_user(user_id)


# ── Users ─────────────────────────────────────────────────────────────────

async def get_or_create_user(
    user_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
    language_code: str = "ru",
) -> dict[str, Any]:
    """Get or create a user."""
    db = _get_db()
    return await db.get_or_create_user(
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        language_code=language_code,
    )


async def is_user_admin(user_id: int) -> bool:
    """Check if user is admin."""
    db = _get_db()
    return await db.is_user_admin(user_id)


async def set_user_admin(user_id: int, is_admin: bool) -> None:
    """Set admin status for a user."""
    db = _get_db()
    await db.set_user_admin(user_id, is_admin)


async def is_user_blocked(user_id: int) -> bool:
    """Check if user is blocked."""
    db = _get_db()
    return await db.is_user_blocked(user_id)


async def block_user(user_id: int, is_blocked: bool) -> None:
    """Block or unblock a user."""
    db = _get_db()
    await db.block_user(user_id, is_blocked)


# ── Chat Modes ────────────────────────────────────────────────────────────

async def get_chat_mode(user_id: int) -> str:
    """Get current chat mode for a user."""
    db = _get_db()
    return await db.get_chat_mode(user_id)


async def set_chat_mode(user_id: int, mode: str) -> None:
    """Set chat mode for a user."""
    db = _get_db()
    await db.set_chat_mode(user_id, mode)


# ── User Cars ─────────────────────────────────────────────────────────────

async def add_user_car(
    user_id: int,
    brand: str = "",
    model: str = "",
    year: int = 0,
    engine: str = "",
    mileage: int = 0,
) -> int:
    """Add a car to user's profile. Returns car ID."""
    db = _get_db()
    return await db.add_user_car(
        user_id=user_id, brand=brand, model=model,
        year=year, engine=engine, mileage=mileage,
    )


async def get_user_cars(user_id: int) -> list[dict[str, Any]]:
    """Get all cars for a user."""
    db = _get_db()
    return await db.get_user_cars(user_id)


async def delete_user_car(car_id: int, user_id: int) -> bool:
    """Delete a car. Returns True if deleted."""
    db = _get_db()
    return await db.delete_user_car(car_id, user_id)


async def update_car_mileage(car_id: int, user_id: int, km: int) -> bool:
    """Update car mileage. Returns True if updated."""
    db = _get_db()
    return await db.update_car_mileage(car_id, user_id, km)


# ── News ──────────────────────────────────────────────────────────────────

async def get_unposted_news(limit: int = 10) -> list[dict[str, Any]]:
    """Get unposted news items. Maps DB columns to expected API keys."""
    db = _get_db()
    items = await db.get_unused_news(limit=limit)
    # Map 'content_type' -> 'category' for admin.py compatibility
    for item in items:
        if "category" not in item and "content_type" in item:
            item["category"] = item["content_type"] or "auto"
        if "category" not in item:
            item["category"] = "auto"
        # Parse image_urls from JSON string stored in DB
        if "image_urls" in item and isinstance(item["image_urls"], str) and item["image_urls"]:
            try:
                item["image_urls"] = json.loads(item["image_urls"])
            except (json.JSONDecodeError, TypeError):
                item["image_urls"] = []
        elif "image_urls" not in item or not item.get("image_urls"):
            # Empty string "" or missing → empty list
            item["image_urls"] = []
    return items


async def mark_news_posted(url: str) -> None:
    """Mark a news item as posted by URL."""
    db = _get_db()
    await db.mark_news_used_by_url(url)


# ── Channel Posts ─────────────────────────────────────────────────────────

async def add_channel_post(
    content: str,
    message_id: int,
    post_type: str | None = None,
    source_url: str | None = None,
    character_mix: str | None = None,
    has_image: bool = False,
    image_url: str | None = None,
    fingerprint: str | None = None,
) -> int:
    """Add a channel post. Maps API kwargs to DB columns."""
    db = _get_db()
    return await db.add_channel_post(
        message_id=message_id,
        text=content,
        content_type=post_type,
        source=source_url,
        character_mix=character_mix,
        has_image=has_image,
        image_url=image_url,
        fingerprint=fingerprint,
    )


async def get_today_post_count() -> int:
    """Get number of posts made today."""
    db = _get_db()
    return await db.get_posts_today_count()


async def get_hourly_post_count() -> int:
    """Get number of posts in the current hour."""
    db = _get_db()
    return await db.get_posts_hourly_count()


async def get_recent_post_count(hours: int = 3) -> int:
    """Get number of posts in the last N hours (rolling window).

    v16: Used for cap checks so old evergreen spam doesn't block news posts.
    """
    db = _get_db()
    return await db.get_posts_recent_count(hours=hours)


# ── Partner Posts ─────────────────────────────────────────────────────────

async def add_partner_post(
    program_name: str = "",
    post_content: str = "",
    program_id: str = "",
    category: str = "general",
    affiliate_url: str | None = None,
    message_id: int | None = None,
    **kwargs,
) -> int:
    """Add a partner post record. Accepts flexible kwargs for compatibility."""
    db = _get_db()
    return await db.add_partner_post(
        program_name=program_name,
        post_text=post_content,
        post_url=affiliate_url,
        message_id=message_id,
    )


async def get_today_partner_post_count() -> int:
    """Get number of partner posts made today."""
    db = _get_db()
    return await db.get_partner_posts_today()


# ── Dedup / Fingerprints ─────────────────────────────────────────────────

async def is_duplicate_post(text: str, hours: int = 48, **kwargs) -> bool:
    """Check if a post is a duplicate."""
    db = _get_db()
    return await db.is_duplicate_post(text)


async def add_post_fingerprint(
    fingerprint: str,
    post_id: int | None = None,
    text_hash: str | None = None,
    semantic_hash: str | None = None,
) -> None:
    """Add a post fingerprint for dedup."""
    db = _get_db()
    await db.add_fingerprint(fingerprint, post_id, text_hash, semantic_hash)


async def cleanup_old_fingerprints(max_age_days: int = 7) -> int:
    """Clean up old fingerprints and data. Returns number of rows deleted."""
    db = _get_db()
    return await db.cleanup_old_data(days=max_age_days)


async def is_source_url_posted(source_url: str) -> bool:
    """Check if a source URL has already been posted to the channel.

    PRIMARY dedup: same article URL = skip regardless of AI-generated text.
    """
    db = _get_db()
    return await db.is_source_url_posted(source_url)


# ── v16: TTL-based URL dedup wrappers (aligned with asya-bot) ────────────────
# These complement is_source_url_posted() (which is permanent) with a TTL
# version that allows re-posting after 48h. channel.py uses is_url_recently_posted
# as the PRIMARY check so the news pool isn't permanently blocked.

async def is_url_recently_posted(url: str, hours: int = 48) -> bool:
    """Check if a URL was posted within the last `hours` (default 48h)."""
    db = _get_db()
    return await db.is_url_recently_posted(url, hours=hours)


async def save_posted_url(url: str, title: str = "") -> None:
    """Record that a URL was just posted (UPSERT)."""
    db = _get_db()
    await db.save_posted_url(url, title=title)


async def cleanup_posted_urls(max_age_days: int = 30) -> int:
    """Delete posted_urls entries older than max_age_days."""
    db = _get_db()
    return await db.cleanup_posted_urls(max_age_days=max_age_days)


async def migrate_channel_posts_to_posted_urls() -> int:
    """One-time migration: copy channel_posts.source into posted_urls."""
    db = _get_db()
    return await db.migrate_channel_posts_to_posted_urls()


async def is_post_text_duplicate(text: str, hours: int = 48) -> bool:
    """Check if a post with the same text fingerprint was published recently.

    Defense-in-depth dedup layer: catches exact/near-exact text repeats even
    when the source URL differs (e.g. same story syndicated across sites, or
    AI regenerating identical text). Uses the `fingerprint` column already
    populated by add_channel_post (sha256(text)[:16]).
    """
    if not text:
        return False
    db = _get_db()
    return await db.is_post_text_duplicate(text, hours=hours)


async def is_evergreen_used_recently(evergreen_id: str, hours: int = 2) -> bool:
    """Check if an evergreen item was used within the last `hours` hours."""
    db = _get_db()
    return await db.is_evergreen_used_recently(evergreen_id, hours)


async def get_recent_evergreen_ids(hours: int = 2) -> set[str]:
    """Return the set of evergreen IDs used within the last `hours` hours."""
    db = _get_db()
    return await db.get_recent_evergreen_ids(hours)


async def mark_evergreen_used(evergreen_id: str, post_id: int | None = None) -> None:
    """Mark an evergreen content item as used (UPSERT — updates used_at)."""
    db = _get_db()
    await db.mark_evergreen_used(evergreen_id, post_id)


# ── Topic Registry ───────────────────────────────────────────────────────

async def load_topic_registry() -> dict[str, dict]:
    """Load all topic registry entries from DB."""
    db = _get_db()
    return await db.load_all_topics()


async def save_topic_to_registry(
    entity_key: str,
    first_seen: float,
    last_posted: float,
    post_count: int,
    titles: list[str],
) -> None:
    """Save a topic registry entry."""
    db = _get_db()
    await db.save_topic_registry_entry(
        entity_key=entity_key,
        first_seen=first_seen,
        last_posted=last_posted,
        post_count=post_count,
        titles=titles,
    )


# ── Recent Post Titles ───────────────────────────────────────────────────

async def get_recent_post_titles(hours: int = 72, limit: int = 50) -> list[str]:
    """Get recent post titles for semantic dedup."""
    db = _get_db()
    return await db.get_recent_post_titles(hours=hours, limit=limit)


# ── Stats ─────────────────────────────────────────────────────────────────

async def get_stats() -> dict[str, Any]:
    """Get comprehensive bot statistics."""
    db = _get_db()
    return await db.get_comprehensive_stats()


# ── Rate Limiting (in-memory, NOT DB) ────────────────────────────────────

_rate_limits: dict[int, list[float]] = {}
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 10     # messages per window


def check_rate_limit(user_id: int) -> bool:
    """Check if user is within rate limits. Sync function!

    Returns True if user CAN send a message, False if rate-limited.
    """
    now = time.time()
    if user_id not in _rate_limits:
        _rate_limits[user_id] = [now]
        return True

    # Remove old timestamps outside the window
    _rate_limits[user_id] = [
        t for t in _rate_limits[user_id] if now - t < _RATE_LIMIT_WINDOW
    ]

    if len(_rate_limits[user_id]) >= _RATE_LIMIT_MAX:
        return False

    _rate_limits[user_id].append(now)
    return True
