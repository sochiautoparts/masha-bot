"""Image URL extraction from RSS feeds and article pages for masha-bot.

v3.0 — RADICALLY SIMPLIFIED:
  - Only extracts image URLs from article pages and RSS entries
  - No web search, no image search, no AI generation, no caching
  - Actual downloading and validation is done by channel.py
  - This module only finds the URLs

KEY FUNCTIONS:
  - extract_rss_images(): Extract image URLs from a feedparser entry
  - fetch_article_image_urls(): Extract image URLs from an article page HTML
"""

from __future__ import annotations

import logging
import re
from typing import Any, List
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("masha.image_fetcher")

# ── Minimal junk filter — only catches obvious non-content ──────────────────

JUNK_KEYWORDS = frozenset([
    'favicon', '1x1', 'pixel', 'spacer', 'blank.gif',
    'gravatar', 'analytics', 'tracker', 'beacon',
    'doubleclick', 'adservice', 'googlesyndication',
])


def _is_junk_url(url: str) -> bool:
    """Check if a URL is obvious junk (tracking pixel, favicon, analytics)."""
    url_lower = url.lower()
    return any(kw in url_lower for kw in JUNK_KEYWORDS)


# ── RSS image extraction ───────────────────────────────────────────────────

def extract_rss_images(entry: Any, max_images: int = 10) -> List[str]:
    """Extract image URLs from a feedparser entry.

    Checks:
    1. <enclosure> with image type
    2. <media:content> with image type
    3. <media:thumbnail>
    4. <content:encoded> <img> tags
    5. <summary>/<description> <img> tags
    """
    image_urls: List[str] = []

    # 1. <enclosure type="image/...">
    enclosures = getattr(entry, "enclosures", []) or []
    for enc in enclosures:
        url = enc.get("href", "") or enc.get("url", "")
        enc_type = enc.get("type", "").lower()
        if url and ("image" in enc_type or any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"])):
            if not _is_junk_url(url) and url not in image_urls:
                image_urls.append(url)

    # 2. <media:content medium="image">
    media_content = getattr(entry, "media_content", []) or []
    for mc in media_content:
        url = mc.get("url", "")
        medium = mc.get("medium", "").lower()
        mc_type = mc.get("type", "").lower()
        if url and (medium == "image" or "image" in mc_type):
            if not _is_junk_url(url) and url not in image_urls:
                image_urls.append(url)

    # 3. <media:thumbnail>
    media_thumbnail = getattr(entry, "media_thumbnail", []) or []
    for mt in media_thumbnail:
        url = mt.get("url", "")
        if url and not _is_junk_url(url) and url not in image_urls:
            image_urls.append(url)

    # 4. <content:encoded> or <summary> with <img>
    for field_name in ("content", "summary", "description"):
        content_value = getattr(entry, field_name, None)
        if isinstance(content_value, list):
            content_value = content_value[0].get("value", "") if content_value else ""
        elif content_value is None:
            continue

        img_matches = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', str(content_value), re.IGNORECASE)
        for img_url in img_matches:
            img_url = img_url.replace("&amp;", "&")
            if not _is_junk_url(img_url) and img_url not in image_urls:
                image_urls.append(img_url)

    return image_urls[:max_images]


# ── Article page image URL extraction ──────────────────────────────────────

async def fetch_article_image_urls(url: str, max_images: int = 10) -> List[str]:
    """Extract image URLs from an article page.

    Extracts in priority order:
    1. og:image meta tags
    2. twitter:image meta tags
    3. JSON-LD structured data
    4. <img> tags from article body (src + data-src)
    5. <picture>/<source srcset> elements

    Returns deduplicated list of image URLs (not downloaded).
    """
    image_urls: List[str] = []
    seen = set()

    if not url or not url.startswith(("http://", "https://")):
        return image_urls

    def _add(img_url: str):
        if not img_url or len(img_url) < 10 or img_url in seen:
            return
        if img_url.startswith("//"):
            img_url = "https:" + img_url
        if _is_junk_url(img_url):
            return
        img_url = img_url.replace("&amp;", "&")
        seen.add(img_url)
        image_urls.append(img_url)

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
            },
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return image_urls

            html = resp.text

            # 1. og:image
            for match in re.findall(r'<meta[^>]+property=["\x27]og:image(?::url|:secure_url)?["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE):
                _add(match)
            for match in re.findall(r'<meta[^>]+content=["\x27]([^"\x27]+)["\x27][^>]+property=["\x27]og:image(?::url|:secure_url)?["\x27]', html, re.IGNORECASE):
                _add(match)

            # 2. twitter:image
            for match in re.findall(r'<meta[^>]+name=["\x27]twitter:image["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE):
                _add(match)
            for match in re.findall(r'<meta[^>]+content=["\x27]([^"\x27]+)["\x27][^>]+name=["\x27]twitter:image["\x27]', html, re.IGNORECASE):
                _add(match)

            # 3. JSON-LD
            import json
            for block in re.findall(r'<script[^>]+type=["\x27]application/ld\+json["\x27][^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL):
                try:
                    data = json.loads(block)
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        img_field = item.get("image") or item.get("images")
                        if not img_field:
                            continue
                        if isinstance(img_field, str):
                            _add(img_field)
                        elif isinstance(img_field, dict):
                            _add(img_field.get("url") or img_field.get("contentUrl") or img_field.get("@id", ""))
                        elif isinstance(img_field, list):
                            for img_item in img_field:
                                if isinstance(img_item, str):
                                    _add(img_item)
                                elif isinstance(img_item, dict):
                                    _add(img_item.get("url") or img_item.get("contentUrl") or img_item.get("@id", ""))
                except (json.JSONDecodeError, Exception):
                    continue

            # 4. <img> from article body
            article_html = ""
            for pattern in [r'<article[^>]*>(.*?)</article>',
                            r'<main[^>]*>(.*?)</main>',
                            r'<div[^>]+class=["\x27][^"\x27]*(?:content|article|post|entry|gallery)[^"\x27]*["\x27][^>]*>(.*?)</div>']:
                for match in re.findall(pattern, html, re.IGNORECASE | re.DOTALL):
                    article_html += match + "\n"

            search_html = article_html if article_html else html

            # Lazy-loaded images first (often higher quality)
            for match in re.findall(r'<img[^>]+data-src=["\x27]([^"\x27]+)["\x27]', search_html, re.IGNORECASE):
                _add(match)
            for match in re.findall(r'<img[^>]+data-lazy-src=["\x27]([^"\x27]+)["\x27]', search_html, re.IGNORECASE):
                _add(match)
            # Regular src
            for match in re.findall(r'<img[^>]+src=["\x27]([^"\x27]+)["\x27]', search_html, re.IGNORECASE):
                _add(match)

            # 5. <picture> srcset
            for block in re.findall(r'<picture[^>]*>(.*?)</picture>', html, re.IGNORECASE | re.DOTALL):
                for srcset in re.findall(r'srcset=["\x27]([^"\x27]+)["\x27]', block, re.IGNORECASE):
                    for part in srcset.split(','):
                        img_url = part.strip().split()[0] if part.strip() else ''
                        _add(img_url)

    except Exception as e:
        logger.debug(f"Article image URL extraction failed for {url}: {e}")

    return image_urls[:max_images]
