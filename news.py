"""News fetching from curated news.json source for masha-bot.

v7.0: SINGLE SOURCE — loads news ONLY from the curated news.json file:
  https://raw.githubusercontent.com/creastudioai-beep/nebm/refs/heads/main/data/news.json

This file is regularly updated with fresh BMW/automotive news, each item
includes title, url, summary, images[], source, lang, published date.

BENEFITS over RSS:
  - Images are pre-curated (no more junk/thumbnail/logo photos!)
  - No broken RSS feeds, no 404s, no rate limits
  - Consistent data format
  - Direct article URLs (no Google News redirects)
  - Language detection included

v6.0 was: Replaced Reddit feeds with TopGear. Saved image_urls to DB.
v7.0: Complete rewrite — single curated source replaces all RSS + web search.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ── The ONLY news source ──────────────────────────────────────────────────────
NEWS_JSON_URL = "https://raw.githubusercontent.com/creastudioai-beep/nebm/refs/heads/main/data/news.json"

# Reuse BMW relevance/blocklist keywords from rss_fetcher for any filtering
from bot.sources.rss_fetcher import (
    BMW_RELEVANCE_KEYWORDS,
    BMW_BLOCKLIST,
    URGENT_BMW_KEYWORDS,
)


async def fetch_news_json(limit: int = 100) -> list[dict[str, Any]]:
    """Fetch news from the curated news.json file.

    Returns items with the same field names used throughout the codebase:
      title, url, summary, source, published, category, image_urls, lang, etc.

    Each news.json item has:
      - title: article headline
      - url: direct article URL
      - summary: article excerpt
      - published: RFC 2822 date string
      - source: source name (e.g. "BMW Blog")
      - lang: language code ("en" or "ru")
      - images: list of direct image URLs — PRE-CURATED, no junk!
      - fetched_at: ISO timestamp when the JSON was generated
    """
    items: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "MashaBot/10.0 (+https://t.me/asmasha_bot)",
                "Accept": "application/json",
            },
        ) as client:
            response = await client.get(NEWS_JSON_URL)
            if response.status_code != 200:
                logger.warning("news.json returned status %d", response.status_code)
                return items

            data = response.json()
            if not isinstance(data, list):
                logger.warning("news.json is not a list: %s", type(data))
                return items

            for entry in data:
                title = entry.get("title", "")
                url = entry.get("url", "")
                summary = entry.get("summary", "")
                published = entry.get("published", "")
                source_name = entry.get("source", "news_json")
                lang = entry.get("lang", "en")
                images = entry.get("images", [])

                # Skip empty entries
                if not title or not url:
                    continue

                # Basic relevance check — still filter for BMW/auto content
                combined = f"{title} {summary}".lower()
                if any(bl in combined for bl in BMW_BLOCKLIST):
                    continue

                # Convert images list to image_urls (the field name used in the pipeline)
                # Filter out obvious thumbnail/small images from the curated list
                image_urls = _filter_curated_images(images)

                # Compute fingerprint for dedup
                fingerprint = hashlib.sha256(
                    (title + url).encode()
                ).hexdigest()[:16]

                # Check if urgent
                is_urgent = any(kw.lower() in combined for kw in URGENT_BMW_KEYWORDS)

                # Parse published time for scoring
                published_time = _parse_published_time(published)

                items.append({
                    "source": f"news_json:{source_name}",
                    "title": title,
                    "summary": summary[:500] if summary else "",
                    "url": url,
                    "published": published,
                    "published_time": published_time,
                    "category": "auto",
                    "content_type": "news+reaction",
                    "fingerprint": fingerprint,
                    "image_urls": image_urls,
                    "lang": lang,
                    "is_urgent": is_urgent,
                    # Keep the raw images list too for reference
                    "_raw_images": images,
                })

            # Sort by published date (newest first)
            items.sort(key=lambda x: x.get("published_time", 0), reverse=True)

            logger.info(
                "Loaded %d items from news.json (%d with images)",
                len(items),
                sum(1 for i in items if i.get("image_urls")),
            )

    except httpx.TimeoutException:
        logger.warning("news.json fetch timed out")
    except Exception as exc:
        logger.error("news.json fetch error: %s", exc)

    return items[:limit]


def _filter_curated_images(images: list[str]) -> list[str]:
    """Filter pre-curated image URLs from news.json.

    Even though images are curated, some entries may still contain
    thumbnail-sized variants or sidebar images. We filter based on:
    1. URL dimension patterns (e.g., 120x120 = thumbnail)
    2. Known junk keywords (favicon, logo, icon, etc.)
    3. SVG format (always icons/logos)
    4. Reddit preview thumbnails with small width/height params

    We prefer LARGER images — if both 830x467 and full-size versions
    exist for the same base image, keep the larger one.
    """
    from bot.sources.image_fetcher import _is_junk_url, _is_thumbnail_url

    filtered = []
    seen_base = set()  # Track base filenames to dedup resized versions

    for url in images:
        if not url or len(url) < 10:
            continue

        # Normalize
        url = url.replace("&amp;", "&")
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith(("http://", "https://")):
            continue

        url_lower = url.lower()

        # Skip SVG
        if url_lower.endswith('.svg') or '.svg?' in url_lower:
            continue

        # Skip data: URIs
        if url.startswith('data:'):
            continue

        # Check for junk patterns
        if _is_junk_url(url):
            continue

        # Check for thumbnail patterns
        if _is_thumbnail_url(url):
            continue

        # ── Reddit preview URL filtering ──
        # Reddit URLs like: preview.redd.it/xxx.jpg?width=140&height=140&crop=1:1,smart
        # These are small thumbnails. We want the larger versions.
        # Strategy: if width or height is < 400, it's a thumbnail
        if 'preview.redd.it' in url_lower or 'external-preview.redd.it' in url_lower:
            import re as _re
            # Check for width/height params
            w_match = _re.search(r'width=(\d+)', url_lower)
            h_match = _re.search(r'height=(\d+)', url_lower)
            if w_match and h_match:
                w, h = int(w_match.group(1)), int(h_match.group(1))
                if w < 400 or h < 400:
                    # This is a small Reddit thumbnail — skip it
                    # But try to get the full-size URL by removing size params
                    base_reddit_url = url.split('?')[0]
                    if base_reddit_url not in seen_base:
                        # Use the full-size URL instead
                        seen_base.add(base_reddit_url)
                        filtered.append(base_reddit_url)
                    continue
            elif w_match:
                w = int(w_match.group(1))
                if w < 400:
                    base_reddit_url = url.split('?')[0]
                    if base_reddit_url not in seen_base:
                        seen_base.add(base_reddit_url)
                        filtered.append(base_reddit_url)
                    continue

        # Dedup by base filename — keep the larger version
        # e.g., "bmw-m3-eletric-touring-02-830x467.jpg" and
        #        "bmw-m3-eletric-touring-02.jpg" — keep the latter (larger)
        base = _extract_image_base(url)
        if base in seen_base:
            # We already have this image — check if current URL is the larger version
            # Replace if current is larger (no dimension suffix = full size)
            existing_idx = None
            for idx, f in enumerate(filtered):
                if _extract_image_base(f) == base:
                    existing_idx = idx
                    break
            if existing_idx is not None:
                # If current URL has no dimension suffix and existing does, replace
                existing_has_dim = _has_dimension_suffix(filtered[existing_idx])
                current_has_dim = _has_dimension_suffix(url)
                if existing_has_dim and not current_has_dim:
                    filtered[existing_idx] = url  # Replace with full-size version
            continue

        seen_base.add(base)
        filtered.append(url)

    return filtered[:10]  # Max 10 images per article


def _extract_image_base(url: str) -> str:
    """Extract the base filename from an image URL for dedup.

    Examples:
      bmw-m3-eletric-touring-02-830x467.jpg → bmw-m3-eletric-touring-02
      bmw-m3-eletric-touring-02.jpg → bmw-m3-eletric-touring-02
      bmw-m-concept-neue-klasse-le-mans-38.jpg → bmw-m-concept-neue-klasse-le-mans-38
    """
    import re
    # Remove query string
    path = url.split("?")[0]
    # Get filename
    filename = path.rsplit("/", 1)[-1] if "/" in path else path
    # Remove extension
    name = re.sub(r'\.(jpg|jpeg|png|webp|gif)$', '', filename, flags=re.IGNORECASE)
    # Remove dimension suffix like -830x467
    name = re.sub(r'-\d{2,4}x\d{2,4}$', '', name)
    return name.lower()


def _has_dimension_suffix(url: str) -> bool:
    """Check if URL has a dimension suffix like -830x467."""
    import re
    path = url.split("?")[0]
    filename = path.rsplit("/", 1)[-1] if "/" in path else path
    return bool(re.search(r'-\d{2,4}x\d{2,4}\.(jpg|jpeg|png|webp|gif)$', filename, re.IGNORECASE))


def _parse_published_time(published: str) -> float:
    """Parse a published date string to Unix timestamp.

    Supports RFC 2822 format from news.json:
      "Sat, 13 Jun 2026 16:50:40 +0000"
    """
    if not published:
        return 0
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(published)
        return dt.timestamp()
    except Exception:
        pass
    try:
        # Try ISO format as fallback
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0


def is_urgent(text: str) -> bool:
    """Check if text contains urgent BMW news."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in URGENT_BMW_KEYWORDS)


async def run_news_cycle() -> int:
    """Run one news fetch cycle — load from news.json, store to DB.

    Returns the number of new items added.
    """
    try:
        from bot.database import _get_db
        db = _get_db()

        items = await fetch_news_json(limit=100)
        new_count = 0

        for item in items:
            try:
                image_urls = item.get("image_urls", [])

                added = await db.add_news_item(
                    source=item.get("source", "news_json"),
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    summary=item.get("summary", ""),
                    published_at=item.get("published", ""),
                    is_urgent=item.get("is_urgent", False),
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
            logger.info("News cycle: %d new items out of %d from news.json", new_count, len(items))
        else:
            logger.info("News cycle: 0 new items (all already in DB)")

        return new_count

    except Exception as exc:
        logger.error("News cycle error: %s", exc)
        return 0
