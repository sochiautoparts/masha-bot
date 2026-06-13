"""BMW-specific RSS and web search fetcher for masha-bot.

BMW-focused RSS sources and search queries for content sourcing.

v3.0: Replaced all broken/404/timeout RSS sources with verified working feeds.
- Removed: BimmerPost (timeout), BMW Motorrad (timeout), BMW Group Press (404),
  TopSpeed BMW (404), Motor1 (404), Reuters Auto (401)
- Fixed: CarScoops (/category/bmw/ → /feed/), InsideEVs (/rss/feed/ → /feed/)
- Added: BimmerFile, Google News BMW (EN+RU), Autocar, AutoExpress,
  Reddit r/BMWMotorrad
v2.0: Now extracts image URLs from RSS enclosures, media:content,
and article <img> tags for original-first image sourcing.
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
    # ── BMW-specific (highest priority) ─────────────────────────────────────
    {"name": "BMW Blog", "url": "https://bmwblog.com/feed/", "category": "bmw_official"},
    {"name": "BimmerFile", "url": "https://bimmerfile.com/feed/", "category": "bmw_community"},
    {"name": "Google News BMW", "url": "https://news.google.com/rss/search?q=BMW+when:7d&hl=en-US&gl=US&ceid=US:en", "category": "bmw_news"},
    {"name": "Google News BMW RU", "url": "https://news.google.com/rss/search?q=%D0%91%D0%9C%D0%92+%D0%BD%D0%BE%D0%B2%D0%BE%D1%81%D1%82%D0%B8&hl=ru&gl=RU&ceid=RU:ru", "category": "bmw_news"},
    # ── General auto with BMW coverage ──────────────────────────────────────
    {"name": "CarScoops", "url": "https://www.carscoops.com/feed/", "category": "general_auto"},
    {"name": "CarAndDriver", "url": "https://www.caranddriver.com/rss/all.xml", "category": "general_auto"},
    {"name": "Autocar", "url": "https://www.autocar.co.uk/rss", "category": "general_auto"},
    {"name": "AutoExpress", "url": "https://www.autoexpress.co.uk/rss", "category": "general_auto"},
    {"name": "CarExpert", "url": "https://carexpert.com.au/feed/", "category": "general_auto"},
    # ── Electric / EV ──────────────────────────────────────────────────────
    {"name": "Electrek", "url": "https://electrek.co/feed/", "category": "electric"},
    {"name": "InsideEVs", "url": "https://insideevs.com/feed/", "category": "electric"},
    # ── Replacing Reddit (429 errors) with working sources ────────────────
    # v5.0: Reddit r/BMW and r/cars consistently return 429 — replaced
    {"name": "Jalopnik", "url": "https://jalopnik.com/rss", "category": "general_auto"},
    {"name": "Google News BMW M", "url": "https://news.google.com/rss/search?q=BMW+M+Power+when:7d&hl=en-US&gl=US&ceid=US:en", "category": "bmw_news"},
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
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                },
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
        """Fetch items from all RSS sources concurrently."""
        import asyncio

        all_items: list[dict[str, Any]] = []

        async def _safe_fetch(source: dict[str, str]) -> list[dict[str, Any]]:
            try:
                return await self._fetch_rss(source)
            except Exception as exc:
                logger.warning("Failed to fetch from %s: %s", source["name"], exc)
                return []

        # Fetch all sources concurrently
        results = await asyncio.gather(*[_safe_fetch(s) for s in BMW_RSS_SOURCES])
        for items in results:
            all_items.extend(items)

        # Sort by date (newest first)
        all_items.sort(
            key=lambda x: x.get("published", ""), reverse=True
        )
        return all_items

    async def _fetch_rss(self, source: dict[str, str]) -> list[dict[str, Any]]:
        """Fetch and parse an RSS feed."""
        url = source["url"]
        name = source["name"]

        # Google News feeds have 100 entries, allow more
        entry_limit = 30 if "news.google.com" in url else 15

        try:
            session = self._get_session()
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("RSS %s returned status %d", name, resp.status)
                    return []

                content = await resp.text()

            feed = feedparser.parse(content)
            items: list[dict[str, Any]] = []

            for entry in feed.entries[:entry_limit]:
                title = getattr(entry, "title", "")
                summary = getattr(entry, "summary", "")
                link = getattr(entry, "link", "")
                published = getattr(entry, "published", "")

                # v4.0: Extract full content from RSS if available
                # Many feeds include <content:encoded> with much more text than <summary>
                content_raw = getattr(entry, "content", None)
                full_content = ""
                if content_raw:
                    if isinstance(content_raw, list) and content_raw:
                        full_content = content_raw[0].get("value", "")
                    elif isinstance(content_raw, str):
                        full_content = content_raw

                # Strip HTML from full_content for text analysis
                import re as _re
                full_content_clean = _re.sub(r'<[^>]+>', '', full_content) if full_content else ""

                # Use the best available text for BMW relevance check
                combined = f"{title} {full_content_clean or summary}".lower()
                if not self._is_bmw_relevant(combined):
                    continue

                if self._is_blocked(combined):
                    continue

                # Extract image URLs from RSS enclosures and media:content
                image_urls = self._extract_entry_images(entry)

                items.append({
                    "source": name,
                    "title": title,
                    "summary": summary[:500],
                    "full_content": full_content_clean[:3000] if full_content_clean else "",  # v4.0: full article from RSS
                    "url": link,
                    "published": published,
                    "category": source.get("category", ""),
                    "image_urls": image_urls,  # Original images from RSS
                    "rss_entry": entry,         # Raw entry for image_fetcher
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

    def _extract_entry_images(self, entry: Any) -> list[str]:
        """Extract image URLs from a feedparser entry.

        Checks enclosures, media:content, media:thumbnail,
        and <img> tags in content/summary.
        """
        from .image_fetcher import extract_rss_images, _is_junk_url
        return extract_rss_images(entry)

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
