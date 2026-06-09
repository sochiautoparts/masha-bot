"""News fetching, filtering, and dedup for masha-bot.

BMW-specific RSS sources, search queries, blocklist,
and auto-relevance keywords.
"""

from __future__ import annotations

import hashlib
import logging
import random
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
import feedparser

logger = logging.getLogger(__name__)

# ── BMW-specific RSS sources ──────────────────────────────────────────────────

BMW_RSS_SOURCES: list[dict[str, str]] = [
    {"name": "BMW Blog", "url": "https://bmwblog.com/feed/", "category": "bmw"},
    {"name": "BimmerPost", "url": "https://bimmerpost.com/feed/", "category": "bmw"},
    {"name": "Motor1", "url": "https://www.motor1.com/rss/feed/", "category": "general"},
    {"name": "Reuters Auto", "url": "https://www.reuters.com/rssFeed/automobilesNews", "category": "news"},
    {"name": "Electrek", "url": "https://electrek.co/feed/", "category": "electric"},
    {"name": "InsideEVs", "url": "https://insideevs.com/rss/feed/", "category": "electric"},
    {"name": "Reddit r/BMW", "url": "https://www.reddit.com/r/BMW/.rss", "category": "reddit"},
    {"name": "Reddit r/cars", "url": "https://www.reddit.com/r/cars/.rss", "category": "reddit"},
]

# ── BMW search queries ────────────────────────────────────────────────────────

BMW_SEARCH_QUERIES: list[str] = [
    "BMW новости",
    "BMW M-Power новости",
    "BMW новая модель",
    "BMW M3 M4 M5 новости",
    "BMW iX i4 i5 электромобиль",
    "BMW M Performance тюнинг",
    "BMW N55 B58 S58 S63 двигатель",
    "BMW X5 X7 X3 новинка",
    "BMW 3 серии G20 новости",
    "BMW Motorrad новости",
    "BMW альпина B3 B4 Alpina",
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

# ── Blocklist ─────────────────────────────────────────────────────────────────

BMW_BLOCKLIST: list[str] = [
    "lada", "лада", "уаз", "uaz", "газ", "volga",
    "kia", "hyundai", "daewoo",
    "трактор", "комбайн",
]

# ── Urgent news keywords ─────────────────────────────────────────────────────

URGENT_KEYWORDS: list[str] = [
    "recall", "отзыв", "проблема безопасности",
    "new model", "новая модель", "премьера",
    "nürburgring record", "рекорд нюрбургринга",
    "m5 g90", "новый m5",
    "i5 m60", "i7 m70", "ix m60",
]


async def fetch_bmw_news(
    db: Any = None,
    limit: int = 10,
    urgent_only: bool = False,
) -> list[dict[str, Any]]:
    """Fetch BMW news from RSS sources.

    Args:
        db: Optional database instance for dedup
        limit: Maximum number of items to return
        urgent_only: Only return urgent news

    Returns:
        List of news items with title, url, summary, source, etc.
    """
    all_items: list[dict[str, Any]] = []

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        headers={"User-Agent": "masha-bot/1.0 (BMW News Fetcher)"},
    ) as session:
        for source in BMW_RSS_SOURCES:
            try:
                items = await _fetch_rss_source(session, source)
                all_items.extend(items)
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", source["name"], exc)

    # Filter for BMW relevance
    filtered = []
    for item in all_items:
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()

        # Skip blocked content
        if any(bl in text for bl in BMW_BLOCKLIST):
            continue

        # Must be BMW-relevant
        if not any(kw in text for kw in BMW_RELEVANCE_KEYWORDS):
            continue

        # Filter urgent if requested
        if urgent_only and not any(kw in text for kw in URGENT_KEYWORDS):
            continue

        filtered.append(item)

    # Sort by date
    filtered.sort(key=lambda x: x.get("published", ""), reverse=True)

    return filtered[:limit]


async def _fetch_rss_source(
    session: aiohttp.ClientSession,
    source: dict[str, str],
) -> list[dict[str, Any]]:
    """Fetch items from a single RSS source."""
    url = source["url"]
    name = source["name"]

    async with session.get(url) as resp:
        if resp.status != 200:
            return []
        content = await resp.text()

    feed = feedparser.parse(content)
    items = []

    for entry in feed.entries[:15]:
        title = getattr(entry, "title", "")
        summary = getattr(entry, "summary", "")
        link = getattr(entry, "link", "")
        published = getattr(entry, "published", "")

        items.append({
            "source": name,
            "title": title,
            "summary": summary[:500],
            "url": link,
            "published": published,
            "category": source.get("category", ""),
            "fingerprint": hashlib.sha256((title + link).encode()).hexdigest()[:16],
        })

    return items


def is_bmw_relevant(text: str) -> bool:
    """Check if text is relevant to BMW."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in BMW_RELEVANCE_KEYWORDS)


def is_blocked(text: str) -> bool:
    """Check if text should be blocked."""
    text_lower = text.lower()
    return any(bl in text_lower for bl in BMW_BLOCKLIST)


def is_urgent(text: str) -> bool:
    """Check if text contains urgent BMW news."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in URGENT_KEYWORDS)


def get_random_search_queries(count: int = 3) -> list[str]:
    """Get random BMW search queries."""
    return random.sample(BMW_SEARCH_QUERIES, min(count, len(BMW_SEARCH_QUERIES)))
