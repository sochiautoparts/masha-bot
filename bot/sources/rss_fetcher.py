"""BMW-specific RSS and web search fetcher for masha-bot.

BMW-focused RSS sources and search queries for content sourcing.
"""

from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
import feedparser

from ..database import Database

logger = logging.getLogger(__name__)

# ── BMW-focused RSS sources ───────────────────────────────────────────────────

BMW_RSS_SOURCES: list[dict[str, str]] = [
    # BMW-specific
    {"name": "BMW Blog", "url": "https://bmwblog.com/feed/", "category": "bmw_official"},
    {"name": "BimmerPost", "url": "https://bimmerpost.com/feed/", "category": "bmw_community"},
    {"name": "BMW Motorrad", "url": "https://www.bmw-motorrad.com/en/rss/feed.xml", "category": "bmw_motorrad"},
    # General auto with BMW coverage
    {"name": "Motor1", "url": "https://www.motor1.com/rss/feed/", "category": "general_auto"},
    {"name": "Reuters Auto", "url": "https://www.reuters.com/rssFeed/automobilesNews", "category": "news"},
    {"name": "Electrek", "url": "https://electrek.co/feed/", "category": "electric"},
    {"name": "InsideEVs", "url": "https://insideevs.com/rss/feed/", "category": "electric"},
    # Reddit
    {"name": "Reddit r/BMW", "url": "https://www.reddit.com/r/BMW/.rss", "category": "reddit"},
    {"name": "Reddit r/cars", "url": "https://www.reddit.com/r/cars/.rss", "category": "reddit"},
    {"name": "Reddit r/MotorSport", "url": "https://www.reddit.com/r/MotorSport/.rss", "category": "reddit"},
]

# ── BMW-focused search queries ────────────────────────────────────────────────

BMW_SEARCH_QUERIES_RU: list[str] = [
    "BMW новости",
    "BMW M-Power новости",
    "BMW новая модель",
    "BMW M3 M4 M5 новости",
    "BMW iX i4 i5 электромобиль",
    "BMW M Performance тюнинг",
    "BMW N55 B58 S58 S63 двигатель",
    "BMW VANOS Valvetronic",
    "BMW X5 X7 X3 новинка",
    "BMW 3 серии G20 новости",
    "BMW Motorrad новости",
    "BMW альпина B3 B4 Alpina",
]

BMW_SEARCH_QUERIES_EN: list[str] = [
    "BMW news latest",
    "BMW M Power news",
    "BMW new model 2026",
    "BMW engine B58 S58",
    "BMW M3 G80 news",
    "BMW M5 F90 news",
    "BMW iX M60",
    "BMW X5 M Competition",
    "BMW recall",
    "BMW tuning",
    "BMW Nürburgring lap record",
    "BMW Alpina news",
]

# ── Auto-relevance keywords ───────────────────────────────────────────────────

BMW_RELEVANCE_KEYWORDS: list[str] = [
    "bmw", "bimmer", "beemer", "бмв", "бавар",
    "m-power", "mpower", "///m", "m-division",
    "xdrive", "valvetronic", "vanos",
    "n55", "b58", "s58", "s63", "b48", "b46", "b38", "s68",
    "m3", "m4", "m5", "m2", "m8", "xm",
    "x5", "x7", "x3", "x4", "x1", "x2", "x6",
    "3 series", "5 series", "7 series", "4 series",
    "g20", "g80", "g82", "f90", "g60", "g70",
    "ix", "i4", "i5", "i7", "ix1", "ix2", "ix3",
    "alpina", "m performance",
    "nürburgring", "nurburgring",
    "interlagos", "individual",
    "bimmercode", "ista", "carly",
]

# ── Blocklist — exclude non-BMW content ───────────────────────────────────────

BMW_BLOCKLIST: list[str] = [
    "lada", "лада", "уаз", "uaz", "газ", "volga",
    "kia", "hyundai", "daewoo",
    "трактор", "комбайн",
]

# ── Urgent BMW news keywords ─────────────────────────────────────────────────

URGENT_BMW_KEYWORDS: list[str] = [
    "recall", "отзыв", "проблема безопасности",
    "new model", "новая модель", "премьера",
    "nürburgring record", "рекорд нюрбургринга",
    "m5 g90", "новый m5",
    "i5 m60", "i7 m70", "ix m60",
    "bmw recall", "bmw отзывная",
]


class BMWRSSFetcher:
    """Fetches BMW-specific news from RSS and web search."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._session: Optional[aiohttp.ClientSession] = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "masha-bot/1.0 (BMW News Fetcher)"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def fetch_urgent(self) -> dict[str, Any] | None:
        """Fetch urgent BMW news (recalls, new models, records)."""
        all_items = await self._fetch_all_sources()

        for item in all_items:
            title = item.get("title", "")
            if self._is_urgent(title):
                # Check if already used
                if not await self._is_known(item):
                    item["content_type"] = "news+reaction"
                    return item

        return None

    async def fetch_latest(self) -> dict[str, Any] | None:
        """Fetch the latest BMW news item."""
        all_items = await self._fetch_all_sources()

        for item in all_items:
            if self._is_bmw_relevant(item.get("title", "") + " " + item.get("summary", "")):
                if not await self._is_known(item):
                    item["content_type"] = "news+reaction"
                    return item

        return None

    async def fetch_for_theme(self, theme: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch news items relevant to a specific theme."""
        all_items = await self._fetch_all_sources()
        theme_keywords = self._get_theme_keywords(theme.get("name", ""))

        relevant = []
        for item in all_items:
            text = (item.get("title", "") + " " + item.get("summary", "")).lower()
            if any(kw in text for kw in theme_keywords):
                if not await self._is_known(item):
                    relevant.append(item)

        return relevant

    async def _fetch_all_sources(self) -> list[dict[str, Any]]:
        """Fetch items from all RSS sources."""
        all_items: list[dict[str, Any]] = []

        for source in BMW_RSS_SOURCES:
            try:
                items = await self._fetch_rss(source)
                all_items.extend(items)
            except Exception as exc:
                logger.warning("Failed to fetch from %s: %s", source["name"], exc)

        # Sort by date (newest first)
        all_items.sort(
            key=lambda x: x.get("published", ""), reverse=True
        )
        return all_items

    async def _fetch_rss(self, source: dict[str, str]) -> list[dict[str, Any]]:
        """Fetch and parse an RSS feed."""
        url = source["url"]
        name = source["name"]

        try:
            session = self._get_session()
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("RSS %s returned status %d", name, resp.status)
                    return []

                content = await resp.text()

            feed = feedparser.parse(content)
            items: list[dict[str, Any]] = []

            for entry in feed.entries[:15]:  # Limit entries per source
                title = getattr(entry, "title", "")
                summary = getattr(entry, "summary", "")
                link = getattr(entry, "link", "")
                published = getattr(entry, "published", "")

                # Filter for BMW relevance
                combined = f"{title} {summary}".lower()
                if not self._is_bmw_relevant(combined):
                    continue

                if self._is_blocked(combined):
                    continue

                items.append({
                    "source": name,
                    "title": title,
                    "summary": summary[:500],
                    "url": link,
                    "published": published,
                    "category": source.get("category", ""),
                })

            return items

        except Exception as exc:
            logger.warning("RSS fetch error for %s: %s", name, exc)
            return []

    async def _is_known(self, item: dict[str, Any]) -> bool:
        """Check if a news item is already in the database."""
        title = item.get("title", "")
        url = item.get("url", "")
        fingerprint = hashlib.sha256((title + url).encode()).hexdigest()[:16]

        # Check database for this item
        news_items = await self.db.get_unused_news()
        for existing in news_items:
            if existing.get("fingerprint") == fingerprint:
                return True

        return False

    def _is_bmw_relevant(self, text: str) -> bool:
        """Check if text is relevant to BMW."""
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in BMW_RELEVANCE_KEYWORDS)

    def _is_blocked(self, text: str) -> bool:
        """Check if text should be blocked."""
        text_lower = text.lower()
        return any(bl.lower() in text_lower for bl in BMW_BLOCKLIST)

    def _is_urgent(self, title: str) -> bool:
        """Check if a title contains urgent BMW news keywords."""
        title_lower = title.lower()
        return any(kw.lower() in title_lower for kw in URGENT_BMW_KEYWORDS)

    def _get_theme_keywords(self, theme_name: str) -> list[str]:
        """Get keywords relevant to a theme day."""
        theme_keywords: dict[str, list[str]] = {
            "M-Monday": ["m3", "m4", "m5", "m2", "m8", "xm", "m-power", "mpower", "///m", "competition"],
            "Tech Tuesday": ["engine", "motor", "v8", "i6", "b58", "s58", "s63", "n55", "vanos", "valvetronic", "turbо"],
            "Workshop Wednesday": ["diy", "maintenance", "service", "repair", "oil", "brake", "filter", "istа", "bimmercode"],
            "Throwback Thursday": ["classic", "e30", "e39", "e46", "e28", "e36", "history", "heritage", "vintage"],
            "Freaky Friday": ["tuning", "custom", "alpina", "ac schnitzer", "individual", "modified", "widebody", "stage"],
            "Spotlight Saturday": ["review", "test", "drive", "first look", "comparison", "vs"],
            "Sunday Drive": ["nürburgring", "nurburgring", "track", "lap", "road trip", "driving", "experience"],
        }
        return theme_keywords.get(theme_name, ["bmw"])
