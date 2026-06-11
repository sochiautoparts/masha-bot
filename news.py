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
    {"name": "BimmerPost", "url": "https://bimmerpost.com/feed/", "category": "bmw",
     "alt_url": "https://bimmerpost.com/wp/feed/", "headers": {"User-Agent": "Mozilla/5.0 (compatible; Feedfetcher-Google)"}},
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
    """Fetch items from a single RSS source with image extraction."""
    url = source["url"]
    name = source["name"]
    alt_url = source.get("alt_url", "")
    custom_headers = source.get("headers", {})

    # Try primary URL first, then alt URL if it fails
    for feed_url in [url, alt_url]:
        if not feed_url:
            continue
        try:
            req_headers = {"User-Agent": "masha-bot/1.0 (BMW News Fetcher)"}
            req_headers.update(custom_headers)
            async with session.get(feed_url, headers=req_headers) as resp:
                if resp.status != 200:
                    if feed_url == url and alt_url:
                        logger.debug(f"RSS {name}: primary URL failed ({resp.status}), trying alt URL")
                        continue
                    return []
                content = await resp.text()
                if not content or len(content) < 100:
                    continue
                break
        except Exception as exc:
            if feed_url == url and alt_url:
                logger.debug(f"RSS {name}: primary URL error ({exc}), trying alt URL")
                continue
            return []
    else:
        return []

    feed = feedparser.parse(content)
    items = []

    for entry in feed.entries[:15]:
        title = getattr(entry, "title", "")
        summary = getattr(entry, "summary", "")
        link = getattr(entry, "link", "")
        published = getattr(entry, "published", "")

        # ── Extract image URLs from RSS entry ──
        image_urls = _extract_rss_images(entry)

        items.append({
            "source": name,
            "title": title,
            "summary": summary[:500],
            "url": link,
            "published": published,
            "category": source.get("category", ""),
            "fingerprint": hashlib.sha256((title + link).encode()).hexdigest()[:16],
            "image_urls": image_urls,
        })

    return items


def _extract_rss_images(entry) -> list[str]:
    """Extract image URLs from a feedparser RSS entry.

    Checks multiple sources in priority order:
    1. media_content (Media RSS — most reliable, includes dimensions)
    2. enclosures (RSS 2.0 standard)
    3. links with rel=enclosure
    4. media_thumbnail (lower quality but still useful)
    5. <image> tag from feed entry
    6. <img> tags embedded in summary/content HTML
    """
    image_urls = []
    seen = set()

    def _add(url: str):
        """Add URL if valid and not already seen."""
        if not url or len(url) < 15 or url in seen:
            return
        # Normalize protocol-relative URLs
        if url.startswith("//"):
            url = "https:" + url
        # Skip obvious non-content URLs
        url_lower = url.lower()
        junk = ["icon", "logo", "favicon", "avatar", "badge", "button", "banner",
                "pixel", "tracker", "1x1", "spacer", "blank", "transparent",
                "ad.", "ads/", "advert", "social", "share", "emoji", "rss",
                "feed", "subscribe", "newsletter"]
        if any(kw in url_lower for kw in junk):
            return
        seen.add(url)
        image_urls.append(url)

    # 1. media_content — Media RSS extension (most reliable)
    #    Usually has medium="image" and width/height attributes
    for mc in getattr(entry, "media_content", []):
        url = mc.get("url", "")
        medium = mc.get("medium", "")
        if url and (not medium or medium == "image"):
            # Prefer larger images — check width/height if available
            try:
                w = int(mc.get("width", 0))
                h = int(mc.get("height", 0))
                if w > 0 and h > 0 and w < 100 and h < 100:
                    continue  # Too small, skip
            except (ValueError, TypeError):
                pass
            _add(url)

    # 2. enclosures — RSS 2.0 standard
    for enc in getattr(entry, "enclosures", []):
        url = enc.get("href", "") or enc.get("url", "")
        enc_type = enc.get("type", "")
        if url and ("image" in enc_type or not enc_type):
            _add(url)

    # 3. links with rel="enclosure"
    for link_item in getattr(entry, "links", []):
        if link_item.get("rel") == "enclosure":
            url = link_item.get("href", "")
            enc_type = link_item.get("type", "")
            if url and ("image" in enc_type or not enc_type):
                _add(url)

    # 4. media_thumbnail — lower quality but still real images
    for mt in getattr(entry, "media_thumbnail", []):
        url = mt.get("url", "")
        if url:
            _add(url)

    # 5. Direct image property (some feeds have entry.image)
    img_obj = getattr(entry, "image", None)
    if isinstance(img_obj, dict):
        _add(img_obj.get("href", "") or img_obj.get("url", ""))
    elif isinstance(img_obj, str) and img_obj.startswith("http"):
        _add(img_obj)

    # 6. Extract <img> from summary/content HTML (last resort)
    for html_field in ["summary", "summary_detail", "content", "value"]:
        html = ""
        val = getattr(entry, html_field, None)
        if isinstance(val, dict):
            html = val.get("value", "")
        elif isinstance(val, str):
            html = val
        if html and "<img" in html:
            import re
            img_srcs = re.findall(
                r'<img[^>]+src=["\x27]([^"\x27]+)["\x27]',
                html, re.IGNORECASE
            )
            for src in img_srcs:
                _add(src)
            # Also check data-src for lazy-loaded images
            data_srcs = re.findall(
                r'<img[^>]+data-src=["\x27]([^"\x27]+)["\x27]',
                html, re.IGNORECASE
            )
            for src in data_srcs:
                _add(src)

    return image_urls


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


async def run_news_cycle() -> int:
    """Run one news fetch cycle — fetch from RSS, store to DB.

    Returns the number of new items added.
    """
    try:
        from bot.database import _get_db
        db = _get_db()

        items = await fetch_bmw_news(limit=20)
        new_count = 0

        for item in items:
            try:
                added = await db.add_news_item(
                    source=item.get("source", "rss"),
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    summary=item.get("summary", ""),
                    published_at=item.get("published", ""),
                    is_urgent=is_urgent(item.get("title", "") + " " + item.get("summary", "")),
                    content_type=item.get("category", "auto"),
                )
                if added:
                    new_count += 1
            except Exception as exc:
                logger.debug("Failed to store news item: %s", exc)

        if new_count > 0:
            logger.info("News cycle: %d new items out of %d fetched", new_count, len(items))

        return new_count

    except Exception as exc:
        logger.error("News cycle error: %s", exc)
        return 0
