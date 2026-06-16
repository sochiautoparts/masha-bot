"""News fetching from curated news.json source for masha-bot.

v9.0: MULTI-SOURCE — BMW News from sochiautoparts/nws repo, then falls back to
  RSS feeds and web search when news.json is unavailable (404/down).

Source priority:
  1. Curated bmw-news.json from sochiautoparts/nws (preferred — BMW-filtered, with images)
  2. RSS fallback (15+ BMW/automotive RSS feeds via BMWRSSFetcher)
  3. Web search fallback (Google News RSS + DDG + SearXNG)

v8.0 was: Single source from creastudioai-beep/nebm (repo went 404).
v9.0: Switched to sochiautoparts/nws — hourly-updated BMW news with better filtering.
      Handles new JSON format: {kind, items: [{id, title, summary, url, image, source, source_url, published}]}
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
NEWS_JSON_URL = "https://raw.githubusercontent.com/sochiautoparts/nws/main/data/bmw-news.json"

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
    "bmw ix",  # BMW iX SUV — use "bmw ix" instead of bare "ix" to avoid false positives ("fix", "mix", etc.)
    # BMW Group brands (MINI, Rolls-Royce) — still BMW Group
    "mini cooper", "mini countryman", "rolls-royce",
    # BMW-specific terms
    "kidney grille", "hofmeister kink", "angel eyes",
    "m sport", "m performance", "competition package",
    # BMW racing
    "bmw m motorsport", "bmw m team", "bmw m hybrid",
    "bmw m4 gt3", "bmw m4 gt4", "bmw m2 csr",
    # Common BMW model names (1er-8er, X-series, Z-series)
    "m140i", "m135i", "m240i", "m340i", "m440i", "m550i", "m760i",
    "118i", "320i", "330i", "330e", "520i", "530i", "540i", "740i", "750i",
    "s1000rr", "s1k",  # BMW motorcycle
    # Common model codes
    "g20", "g80", "g82", "g87", "g60", "g70", "g65",
    "f90", "f80", "f82", "f87", "f30", "f10", "e46", "e39", "e30", "e36",
    "g01", "g05", "g07", "g15", "g29",  # X-series, Z4, 8er codes
    # Russian BMW terms
    "баварский моторный", "баварец", "бимер",
    # BMW sub-brands
    "alpina", "diniz", "ac schnitzer", "bovensiepen",
    # BMW concepts / events
    "concept neue klasse", "le mans bmw", "bmw art car",
    # Reddit community BMW-specific terms
    "bimmer", "bimmerpost",
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


async def fetch_news_json(limit: int = 500) -> list[dict[str, Any]]:
    """Fetch news from the curated bmw-news.json file (sochiautoparts/nws repo).

    Returns items with the same field names used throughout the codebase:
      title, url, summary, source, published, category, image_urls, lang, etc.

    The bmw-news.json format (v9.0) is:
      {
        "kind": "bmw",
        "generated_at": "ISO timestamp",
        "total_items": int,
        "sources_used": [...],
        "items": [
          {
            "id": "sha256[:16]",
            "title": "...",
            "summary": "...",
            "url": "...",
            "image": "single_url_or_empty",   ← single string, not list!
            "source": "BMW Blog",
            "source_url": "https://bmwblog.com",
            "published": "ISO 8601"
          }
        ]
      }

    Also supports the old flat-array format from creastudioai-beep/nebm for
    backward compatibility.

    Returns empty list if news.json is unavailable (404, timeout, etc.).
    Callers should fall back to fetch_news_rss_fallback() or
    fetch_news_multi_source() for automatic fallback.
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
                logger.warning(
                    "news.json returned status %d — will need fallback",
                    response.status_code,
                )
                return items

            data = response.json()

            # ── Detect JSON format ──
            # New format (sochiautoparts/nws): {"kind": "bmw", "items": [...]}
            # Old format (creastudioai-beep/nebm): [...] (flat array)
            if isinstance(data, dict) and "items" in data:
                news_list = data["items"]
                meta_info = (
                    f"kind={data.get('kind', '?')}, "
                    f"total_items={data.get('total_items', '?')}, "
                    f"generated_at={data.get('generated_at_human', data.get('generated_at', '?'))}"
                )
                logger.info("Parsed bmw-news.json (%s)", meta_info)
            elif isinstance(data, list):
                # Old format — flat array
                news_list = data
                logger.info("Parsed legacy news.json (flat array, %d items)", len(news_list))
            else:
                logger.warning("news.json has unexpected format: %s", type(data))
                return items

            skipped_blocklist = 0
            skipped_not_bmw = 0
            total_raw = len(news_list)

            for entry in news_list:
                title = entry.get("title", "")
                url = entry.get("url", "")
                summary = entry.get("summary", "")
                published = entry.get("published", "")
                source_name = entry.get("source", "news_json")

                # ── Handle image field differences ──
                # New format: "image" (single string or empty)
                # Old format: "images" (list of URLs)
                image_field = entry.get("image", "")
                images_field = entry.get("images", [])

                if image_field and isinstance(image_field, str) and image_field.startswith("http"):
                    # New format: single image URL → convert to list
                    images = [image_field]
                elif isinstance(images_field, list) and images_field:
                    # Old format: list of image URLs
                    images = images_field
                else:
                    images = []

                # ── Detect language ──
                # New format has no "lang" field — detect from content
                lang = entry.get("lang", "")
                if not lang:
                    # Detect Cyrillic characters in title or summary
                    text_for_lang = f"{title} {summary}"
                    lang = "ru" if any(
                        0x0400 <= ord(c) <= 0x04FF for c in text_for_lang
                    ) else "en"

                # Skip empty entries
                if not title or not url:
                    continue

                # ── HARD BMW-RELEVANCE FILTER ──
                # Even though sochiautoparts/nws pre-filters for BMW content,
                # some items may slip through (comparative articles, etc.)
                # We keep the filter as a safety net.
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
                # Prefer the item's "id" field if available (sochiautoparts/nws format)
                item_id = entry.get("id", "")
                if item_id:
                    fingerprint = item_id
                else:
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
                    # Store source_url from new format for reference
                    "_source_url": entry.get("source_url", ""),
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

        # ── Google News images via googleusercontent.com ──
        # Google News articles use googleusercontent.com as a CDN proxy for
        # article images. The URL format is:
        #   https://lh3.googleusercontent.com/...=s0-w300-rw
        # The size suffix (=s0-w300-rw, =w300, etc.) controls the served size,
        # but the ORIGINAL image may be much larger.
        # Strategy: Instead of blocking, STRIP the size suffix to get the
        # full-resolution version. Then the image passes through to download.
        if 'googleusercontent.com' in url_lower:
            # Strip Google size parameters to get full-resolution image
            # =s0-w300-rw → remove to get original
            # =w300 → remove to get original
            # =s640 → remove to get original
            url = re.sub(r'=[sw]\d+[-\w]*$', '', url)
            url_lower = url.lower()
            # Also strip from middle of URL (less common but possible)
            url = re.sub(r'-w\d+-rw', '', url)
            url_lower = url.lower()
            # Strip query params with dimensions
            url = re.sub(r'[?&]w=\d+', '', url)
            url = re.sub(r'[?&]h=\d+', '', url)
            url_lower = url.lower()
            # If URL is now just the base googleusercontent URL without params,
            # it should serve the original full-size image

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
            # external-preview.redd.it works with proper User-Agent header
            # These are cached previews of external images hosted by Reddit CDN
            # Strip small-width query params to get full size
            if '?width=' in url_lower:
                # Small width thumbnail — strip to get full size
                url = url.split('?')[0]
                url_lower = url.lower()

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

    Supports:
      - ISO 8601 format (primary, from sochiautoparts/nws):
          "2026-06-16T12:50:20+00:00"
          "2026-06-16T12:50:20Z"
      - RFC 2822 format (legacy, from old creastudioai-beep/nebm):
          "Sat, 13 Jun 2026 16:50:40 +0000"
    """
    if not published:
        return 0
    # Try ISO 8601 first (primary format from sochiautoparts/nws)
    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        pass
    # Try RFC 2822 as fallback (legacy format)
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(published)
        return dt.timestamp()
    except Exception:
        pass
    return 0


def is_urgent(text: str) -> bool:
    """Check if text contains urgent BMW news."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in URGENT_BMW_KEYWORDS)


async def fetch_news_rss_fallback(limit: int = 200) -> list[dict[str, Any]]:
    """Fetch news from RSS feeds as a fallback when news.json is unavailable.

    Uses the existing BMWRSSFetcher class with 15+ BMW/automotive RSS sources.
    Converts RSS items to the same format as news.json items so downstream
    pipeline code works without modification.
    """
    items: list[dict[str, Any]] = []

    try:
        from bot.database import _get_db
        from bot.sources.rss_fetcher import BMWRSSFetcher

        db = _get_db()
        fetcher = BMWRSSFetcher(db)
        try:
            rss_items = await fetcher._fetch_all_sources()
        finally:
            await fetcher.close()

        for entry in rss_items:
            title = entry.get("title", "")
            url = entry.get("url", "")
            summary = entry.get("summary", "")
            published = entry.get("published", "")
            source_name = entry.get("source", "rss")
            image_urls = entry.get("image_urls", []) or []

            # Skip empty entries
            if not title or not url:
                continue

            # ── BMW-RELEVANCE FILTER (same as news.json path) ──
            combined = f"{title} {summary}"
            combined_lower = combined.lower()

            if any(bl in combined_lower for bl in BMW_BLOCKLIST):
                logger.debug(f"RSS blocked by blocklist: {title[:60]}")
                continue

            if not _is_bmw_relevant(combined):
                logger.debug(f"RSS skipped (not BMW-relevant): {title[:60]}")
                continue

            # Ensure image_urls is always a list
            if not isinstance(image_urls, list):
                image_urls = []

            # Compute fingerprint (same method as news.json items)
            fingerprint = hashlib.sha256(
                (title + url).encode()
            ).hexdigest()[:16]

            # Check urgency (same method as news.json items)
            is_urgent_flag = any(kw.lower() in combined for kw in URGENT_BMW_KEYWORDS)

            # Parse published time (same method as news.json items)
            published_time = _parse_published_time(str(published) if published else "")

            # Determine language from source
            lang = "ru" if any(
                ru_marker in source_name.lower()
                for ru_marker in ["ru", "russian", "русск"]
            ) else "en"

            items.append({
                "source": f"rss:{source_name}",
                "title": title,
                "summary": summary[:500] if summary else "",
                "url": url,
                "published": str(published) if published else "",
                "published_time": published_time,
                "category": "auto",
                "content_type": "news+reaction",
                "fingerprint": fingerprint,
                "image_urls": image_urls,
                "lang": lang,
                "is_urgent": is_urgent_flag,
            })

        # Sort by published date (newest first)
        items.sort(key=lambda x: x.get("published_time", 0), reverse=True)

        logger.info(
            "RSS fallback: loaded %d items from %d RSS sources",
            len(items), len(rss_items) if rss_items else 0,
        )

    except Exception as exc:
        logger.error("RSS fallback error: %s", exc)

    return items[:limit]


async def fetch_news_web_search_fallback(limit: int = 100) -> list[dict[str, Any]]:
    """Fetch news from web search as a last-resort fallback.

    Uses Google News RSS, DDG, and SearXNG from bot.web_search.
    Converts search results to the same format as news.json items.
    """
    items: list[dict[str, Any]] = []

    try:
        from bot.web_search import search_news, SearchResult

        # Search both English and Russian BMW news
        queries = [
            "BMW news latest",
            "BMW M Power news",
            "BMW новости",
            "BMW M новости",
        ]

        seen_urls: set[str] = set()

        for query in queries:
            try:
                results: list[SearchResult] = await search_news(query, max_results=10)
                for r in results:
                    if not r.title or not r.url:
                        continue
                    # Dedup by URL
                    if r.url in seen_urls:
                        continue
                    seen_urls.add(r.url)

                    combined = f"{r.title} {r.snippet}"
                    combined_lower = combined.lower()

                    # BMW relevance check
                    if any(bl in combined_lower for bl in BMW_BLOCKLIST):
                        continue
                    if not _is_bmw_relevant(combined):
                        continue

                    fingerprint = hashlib.sha256(
                        (r.title + r.url).encode()
                    ).hexdigest()[:16]

                    is_urgent_flag = any(kw.lower() in combined for kw in URGENT_BMW_KEYWORDS)

                    # Determine language from query
                    lang = "ru" if any(
                        ord(c) > 0x0400 for c in r.title  # Cyrillic chars
                    ) else "en"

                    items.append({
                        "source": f"web:{r.source}",
                        "title": r.title,
                        "summary": r.snippet[:500] if r.snippet else "",
                        "url": r.url,
                        "published": "",
                        "published_time": time.time(),  # Fresh from web search
                        "category": "auto",
                        "content_type": "news+reaction",
                        "fingerprint": fingerprint,
                        "image_urls": [],  # No images from web search
                        "lang": lang,
                        "is_urgent": is_urgent_flag,
                    })
            except Exception as exc:
                logger.debug("Web search query '%s' failed: %s", query, exc)
                continue

        # Sort newest first (web search results have same timestamp)
        items.sort(key=lambda x: x.get("published_time", 0), reverse=True)

        logger.info("Web search fallback: loaded %d items", len(items))

    except Exception as exc:
        logger.error("Web search fallback error: %s", exc)

    return items[:limit]


async def fetch_news_multi_source(limit: int = 500) -> list[dict[str, Any]]:
    """Fetch news with multi-source fallback chain.

    Priority:
      1. Curated bmw-news.json from sochiautoparts/nws (preferred — BMW-filtered)
      2. RSS feeds (15+ BMW/automotive sources)
      3. Web search (Google News RSS + DDG + SearXNG)

    Returns items in the same format as fetch_news_json().
    """
    # Source 1: Curated bmw-news.json
    items = await fetch_news_json(limit=limit)
    if items:
        logger.info("Multi-source: using bmw-news.json (%d items)", len(items))
        return items

    logger.warning("bmw-news.json returned 0 items — trying RSS fallback")

    # Source 2: RSS feeds
    items = await fetch_news_rss_fallback(limit=limit)
    if items:
        logger.info("Multi-source: using RSS fallback (%d items)", len(items))
        return items

    logger.warning("RSS fallback returned 0 items — trying web search fallback")

    # Source 3: Web search
    items = await fetch_news_web_search_fallback(limit=limit)
    if items:
        logger.info("Multi-source: using web search fallback (%d items)", len(items))
        return items

    logger.error("All news sources failed — no items available")
    return []


async def run_news_cycle() -> int:
    """Run one news fetch cycle — load from multiple sources, store to DB.

    Uses multi-source fallback: news.json → RSS → web search.
    Returns the number of new items added.
    """
    try:
        from bot.database import _get_db
        db = _get_db()

        items = await fetch_news_multi_source(limit=500)
        new_count = 0

        for item in items:
            try:
                image_urls = item.get("image_urls", []) or []

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
            logger.info("News cycle: %d new items out of %d", new_count, len(items))
        else:
            logger.info("News cycle: 0 new items (all already in DB)")

        return new_count

    except Exception as exc:
        logger.error("News cycle error: %s", exc)
        return 0
