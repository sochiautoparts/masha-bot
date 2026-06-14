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
import re
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
from bot.bmw_knowledge import is_bmw_topic


# ── Extended BMW relevance keywords for news.json filtering ─────────────────────
# These supplement is_bmw_topic() to catch items that mention BMW models/tech
# but might not use the exact phrases in BMW_AUTO_KEYWORDS_EN/RU.
_EXTRA_BMW_KEYWORDS: list[str] = [
    # Neue Klasse platform
    "neue klasse", "neueklasse",
    # Electric BMW models
    "ix3", "ix1", "ix2", "i4", "i5", "i7", "i3", "i8", "im3",
    # BMW Group brands (MINI, Rolls-Royce) — still BMW Group
    "mini cooper", "mini countryman", "rolls-royce",
    # BMW-specific terms
    "kidney grille", "hofmeister kink", "angel eyes",
    "m sport", "m performance", "competition package",
    # BMW racing
    "bmw m motorsport", "bmw m team", "bmw m hybrid",
    "bmw m4 gt3", "bmw m4 gt4", "bmw m2 csr",
    # Common model codes
    "g20", "g80", "g82", "g87", "g60", "g70", "g65",
    "f90", "f80", "f82", "f87", "f30", "f10", "e46", "e39", "e30", "e36",
    # Russian BMW terms
    "баварский моторный", "баварец", "бимер",
    # BMW sub-brands
    "alpina", "diniz", "ac schnitzer",
    # BMW concepts / events
    "concept neue klasse", "le mans bmw", "bmw art car",
]


def _is_bmw_relevant(text: str) -> bool:
    """Check if text is relevant to BMW — used as HARD filter on news.json.

    Two-stage check:
    1. is_bmw_topic() from bmw_knowledge (BMW_AUTO_KEYWORDS_EN + RU)
    2. Extended keyword list for models/tech not in the main list

    If EITHER matches, the item is considered BMW-relevant.
    """
    # Stage 1: Main BMW topic check
    if is_bmw_topic(text):
        return True

    # Stage 2: Extended keywords
    text_lower = text.lower()
    for kw in _EXTRA_BMW_KEYWORDS:
        if kw in text_lower:
            return True

    return False


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

            skipped_blocklist = 0
            skipped_not_bmw = 0
            total_raw = len(data)

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

                # ── HARD BMW-RELEVANCE FILTER ──
                # The source news.json contains ~23% non-BMW articles
                # (Chery, Nissan, Ferrari, Reddit community posts, etc.)
                # We ONLY allow BMW-relevant content into the pipeline.
                combined = f"{title} {summary}"

                # 1) Blocklist — hard block for junk brands
                combined_lower = combined.lower()
                if any(bl in combined_lower for bl in BMW_BLOCKLIST):
                    skipped_blocklist += 1
                    logger.debug(f"Blocked by blocklist: {title[:60]}")
                    continue

                # 2) BMW relevance — MUST contain BMW-related keywords
                #    Uses is_bmw_topic() from bmw_knowledge + extra keywords
                if not _is_bmw_relevant(combined):
                    skipped_not_bmw += 1
                    logger.debug(f"Skipped (not BMW-relevant): {title[:60]}")
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
                "Loaded %d/%d items from news.json (%d with images) — blocked: %d, not-BMW: %d",
                len(items), total_raw,
                sum(1 for i in items if i.get("image_urls")),
                skipped_blocklist, skipped_not_bmw,
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
    seen_google_thumb = False  # Track Google News generic thumbnails

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

        # ── Google News generic thumbnail ──
        # Google News articles all use the SAME generic icon:
        #   https://lh3.googleusercontent.com/...=s0-w300-rw
        # This is NOT an article image — it's just the Google News logo.
        # Block ALL googleusercontent images with size markers like =s0-w300-rw
        if 'googleusercontent.com' in url_lower:
            # Size markers in Google image URLs: =s0-w300-rw, =w300, =h200, etc.
            if re.search(r'=[sw]\d+', url_lower) or re.search(r'-w\d+-rw', url_lower):
                continue
            # Also block if it has query params with small dimensions
            if re.search(r'[?&]w=\d+', url_lower) or re.search(r'[?&]h=\d+', url_lower):
                continue

        # ── Motorsport.com wrong-car images ──
        # motorsport.com images often show wrong cars in filename
        # e.g., "91-manthey-dk-engineering-pors.jpg" for a BMW article
        # If filename contains non-BMW brand identifiers, skip it
        if 'motorsport.com' in url_lower:
            filename = url_lower.split('/')[-1]
            wrong_car_markers = ['pors', 'ferrari', 'toyota', 'mercedes', 'audi',
                                 'honda', 'nissan', 'renault', 'ford', 'chevrolet']
            if any(marker in filename for marker in wrong_car_markers):
                logger.debug(f"Skipping motorsport.com wrong-car image: {filename[:60]}")
                continue

        # Check for junk patterns
        if _is_junk_url(url):
            continue

        # Check for thumbnail patterns
        if _is_thumbnail_url(url):
            continue

        # ── Reddit URL fix: preview.redd.it → i.redd.it ──
        # preview.redd.it returns 403 Forbidden for bots
        # i.redd.it works fine and serves the full-size image
        # external-preview.redd.it can't be converted (different domain structure)
        # — skip those entirely as they're cached previews of external images
        if 'external-preview.redd.it' in url_lower:
            # Can't convert these — skip them
            continue

        if 'preview.redd.it' in url_lower:
            import re as _re
            # Convert preview.redd.it → i.redd.it
            url = url.replace('preview.redd.it', 'i.redd.it')
            url_lower = url.lower()

            # Check for small width/height query params
            w_match = _re.search(r'width=(\d+)', url_lower)
            h_match = _re.search(r'height=(\d+)', url_lower)
            if w_match and h_match:
                w, h = int(w_match.group(1)), int(h_match.group(1))
                if w < 400 or h < 400:
                    # Small Reddit thumbnail — use base URL without query params
                    url = url.split('?')[0]
                    url_lower = url.lower()
            elif w_match:
                w = int(w_match.group(1))
                if w < 400:
                    url = url.split('?')[0]
                    url_lower = url.lower()
            else:
                # No size params — strip query params anyway for cleaner URL
                url = url.split('?')[0]
                url_lower = url.lower()

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
