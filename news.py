"""News fetching, filtering, and dedup for masha-bot.

v5.0: Uses BMWRSSFetcher for proper concurrent fetching with Reddit stagger.
Saves image_urls to database for use by channel posting pipeline.
- Removed: BimmerPost (timeout), Motor1 (404), Reuters (401) — all broken
- Added: BimmerFile, Google News BMW EN+RU, Autocar, AutoExpress, Reddit r/BMWMotorrad
- Image URLs are now extracted and saved for every news item
- CarScoops replaced with MotorAuthority (CarScoops returns 403)
- Reddit feeds use old.reddit.com with 5-8s stagger to avoid 429
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
import feedparser

logger = logging.getLogger(__name__)


# Reuse the same sources as rss_fetcher.py — single source of truth
from bot.sources.rss_fetcher import (
    BMW_RSS_SOURCES,
    BMW_RELEVANCE_KEYWORDS,
    BMW_BLOCKLIST,
    URGENT_BMW_KEYWORDS,
)

from bot.sources.image_fetcher import extract_rss_images


async def fetch_bmw_news(
    db: Any = None,
    limit: int = 20,
    urgent_only: bool = False,
) -> list[dict[str, Any]]:
    """Fetch BMW news from RSS sources.

    v5.0: Uses BMWRSSFetcher for proper Reddit rate limiting (staggered delays)
    instead of sequential fetching which caused 429 errors.
    Returns items with image_urls extracted from RSS entries.

    Args:
        db: Optional database instance for dedup
        limit: Maximum number of items to return
        urgent_only: Only return urgent news

    Returns:
        List of news items with title, url, summary, source, image_urls, etc.
    """
    all_items: list[dict[str, Any]] = []

    # Use BMWRSSFetcher for proper concurrent fetching with Reddit stagger
    try:
        from bot.sources.rss_fetcher import BMWRSSFetcher
        fetcher_db = db or Database()
        fetcher = BMWRSSFetcher(fetcher_db)
        try:
            all_items = await fetcher._fetch_all_sources()
        finally:
            await fetcher.close()
    except Exception as exc:
        logger.warning("BMWRSSFetcher failed, falling back to sequential: %s", exc)
        # Fallback to sequential (old behavior) if BMWRSSFetcher fails
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
        ) as session:
            for source in BMW_RSS_SOURCES:
                try:
                    items = await _fetch_rss_source(session, source)
                    all_items.extend(items)
                except Exception as exc2:
                    logger.warning("Failed to fetch %s: %s", source["name"], exc2)

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
        if urgent_only and not any(kw in text for kw in URGENT_BMW_KEYWORDS):
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

    # Google News feeds have more entries
    entry_limit = 30 if "news.google.com" in url else 15

    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.warning("RSS %s returned status %d", name, resp.status)
                return []
            content = await resp.text()

        feed = feedparser.parse(content)
        items = []

        for entry in feed.entries[:entry_limit]:
            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            link = getattr(entry, "link", "")
            published = getattr(entry, "published", "")

            # Extract image URLs from RSS entry using the dedicated image_fetcher
            image_urls = extract_rss_images(entry)

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

    except Exception as exc:
        logger.warning("RSS fetch error for %s: %s", name, exc)
        return items


def is_urgent(text: str) -> bool:
    """Check if text contains urgent BMW news."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in URGENT_BMW_KEYWORDS)


async def run_news_cycle() -> int:
    """Run one news fetch cycle — fetch from RSS, store to DB.

    Now saves image_urls for each news item so the posting pipeline
    can use them for photo posts.

    Returns the number of new items added.
    """
    try:
        from bot.database import _get_db
        db = _get_db()

        items = await fetch_bmw_news(limit=20)
        new_count = 0

        for item in items:
            try:
                # Extract image_urls — crucial for photo posts
                image_urls = item.get("image_urls", [])

                added = await db.add_news_item(
                    source=item.get("source", "rss"),
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    summary=item.get("summary", ""),
                    published_at=item.get("published", ""),
                    is_urgent=is_urgent(item.get("title", "") + " " + item.get("summary", "")),
                    content_type=item.get("category", "auto"),
                    image_urls=image_urls,
                )
                if added:
                    new_count += 1
                    if image_urls:
                        logger.info(
                            "News item added with %d images: %s",
                            len(image_urls), item.get("title", "")[:50]
                        )
            except Exception as exc:
                logger.debug("Failed to store news item: %s", exc)

        if new_count > 0:
            logger.info("News cycle: %d new items out of %d fetched", new_count, len(items))

        return new_count

    except Exception as exc:
        logger.error("News cycle error: %s", exc)
        return 0
