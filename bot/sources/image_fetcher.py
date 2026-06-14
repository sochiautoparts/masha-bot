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
    # v5.0: Added more junk patterns that appear in RSS feeds
    'logo', 'icon', 'avatar', 'badge', 'button',
    'banner', 'social', 'share', 'follow', 'subscribe',
    '/assets/images/', '/assets/img/', '/themes/',
    '/widgets/', '/plugins/',
])

# v4.0: Thumbnail size patterns in URLs — these indicate small images
# CAREFUL: We only match SQUARE or nearly-square patterns (NxN) because
# real article images are rectangular (e.g., 830x553, 1600x1066).
# We don't want to block legitimate images with dimensions in their URL.
THUMBNAIL_PATTERNS = [
    # Square thumbnails: 50x50, 80x80, 100x100, 150x150, 300x300
    r'[-_]\d{2,3}x\d{2,3}(?:[-_.]|$)',  # Will be filtered by dimension check below
    # Specific known thumbnail paths
    '/thumb/', '/thumbnail/', '/thumbs/', '/tiny/',
    # Size indicators
    '_thumb.', '-thumb.', '_tiny.', '-tiny.',
    # Autocar uses car_review_image_190 (190px wide — too small)
    'car_review_image_190',
    # Generic tiny markers
    '?resize=', '&w=50&', '&w=100&', '&h=50&', '&h=100&',
]


def _is_thumbnail_url(url: str) -> bool:
    """Check if a URL likely points to a small/thumbnail image.

    v4.0: Matches NxN (square) patterns like 150x150, 300x300 in URLs.
    Does NOT block rectangular patterns like 830x553 (real article images).
    """
    import re as _re
    url_lower = url.lower()

    # Simple string patterns (not regex — just substring checks)
    simple_patterns = [
        '/thumb/', '/thumbnail/', '/thumbs/', '/tiny/',
        '_thumb.', '-thumb.', '_tiny.', '-tiny.',
        '_thumb-', '-thumb-',  # v7.1: Also catch -thumb- and _thumb-
        'car_review_image_190',
    ]
    for pattern in simple_patterns:
        if pattern in url_lower:
            return True

    # Regex patterns for dimension-based checks
    regex_patterns = [
        r'[-_]\d{2,3}x\d{2,3}(?:[-_.]|$)',  # NxN dimension patterns
    ]
    for pattern in regex_patterns:
        if _re.search(pattern, url_lower):
            # Found a dimension pattern — check if it's square/small
            dim_match = _re.search(r'[-_](\d+)x(\d+)(?:[-_.]|$)', url_lower)
            if dim_match:
                w, h = int(dim_match.group(1)), int(dim_match.group(2))
                # Both dimensions under 400 = thumbnail
                if w < 400 and h < 400:
                    return True
                # One dimension under 200 = probably thumbnail
                if w < 200 or h < 200:
                    return True

    # Query string size markers (using simple string check, not regex)
    tiny_size_markers = ['&w=50&', '&w=100&', '&h=50&', '&h=100&']
    for marker in tiny_size_markers:
        if marker in url_lower:
            return True

    # Check for ?resize= with small size
    resize_match = _re.search(r'\?resize=[^&]*?(\d+),(\d+)', url_lower)
    if resize_match:
        w, h = int(resize_match.group(1)), int(resize_match.group(2))
        if w < 400 and h < 400:
            return True

    return False


def _is_junk_url(url: str) -> bool:
    """Check if a URL is obvious junk (tracking pixel, favicon, analytics)
    or a thumbnail-sized image that would look bad in a post.
    v4.0: Added thumbnail URL pattern filtering.
    """
    url_lower = url.lower()
    if any(kw in url_lower for kw in JUNK_KEYWORDS):
        return True
    # v4.0: Check for thumbnail patterns
    if _is_thumbnail_url(url):
        return True
    return False


# Domains that NEVER contain real article photos
_JUNK_IMAGE_DOMAINS = frozenset([
    'gravatar.com', 'google.com', 'googlesyndication.com',
    'facebook.com', 'twitter.com', 'instagram.com', 'youtube.com',
    'doubleclick.net', 'adservice.google.com',
])


# ── RSS image extraction ───────────────────────────────────────────────────

def extract_rss_images(entry: Any, max_images: int = 10) -> List[str]:
    """Extract image URLs from a feedparser entry.

    v5.0: Added domain-level filtering (gravatar, social media, etc.)
    Checks:
    1. <enclosure> with image type
    2. <media:content> with image type
    3. <media:thumbnail>
    4. <content:encoded> <img> tags
    5. <summary>/<description> <img> tags
    """
    from urllib.parse import urlparse
    image_urls: List[str] = []

    def _is_domain_junk(url: str) -> bool:
        """Check if URL is from a junk domain."""
        try:
            domain = urlparse(url).netloc.lower()
            return any(jd in domain for jd in _JUNK_IMAGE_DOMAINS)
        except Exception:
            return False

    # 1. <enclosure type="image/...">
    enclosures = getattr(entry, "enclosures", []) or []
    for enc in enclosures:
        url = enc.get("href", "") or enc.get("url", "")
        enc_type = enc.get("type", "").lower()
        if url and ("image" in enc_type or any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"])):
            if not _is_junk_url(url) and not _is_domain_junk(url) and url not in image_urls:
                image_urls.append(url)

    # 2. <media:content medium="image">
    media_content = getattr(entry, "media_content", []) or []
    for mc in media_content:
        url = mc.get("url", "")
        medium = mc.get("medium", "").lower()
        mc_type = mc.get("type", "").lower()
        if url and (medium == "image" or "image" in mc_type):
            if not _is_junk_url(url) and not _is_domain_junk(url) and url not in image_urls:
                image_urls.append(url)

    # 3. <media:thumbnail>
    media_thumbnail = getattr(entry, "media_thumbnail", []) or []
    for mt in media_thumbnail:
        url = mt.get("url", "")
        if url and not _is_junk_url(url) and not _is_domain_junk(url) and url not in image_urls:
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
            if not _is_junk_url(img_url) and not _is_domain_junk(img_url) and img_url not in image_urls:
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


# ── Article text extraction ────────────────────────────────────────────────

async def fetch_article_text(url: str, max_chars: int = 3000) -> str:
    """Extract the main text content from an article page.

    v4.0: RSS feeds give truncated summaries. This function scrapes
    the FULL article text so the AI can write a unique post based on
    complete facts rather than a 2-line teaser.

    Strategy:
    1. Try <article>, <main>, or content divs for article body
    2. Strip all HTML tags
    3. Clean up whitespace and artifacts
    4. Return up to max_chars of clean text

    Returns empty string on failure (caller falls back to RSS summary).
    """
    if not url or not url.startswith(("http://", "https://")):
        return ""

    _SCRAPE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
    }

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            max_redirects=5,
            headers=_SCRAPE_HEADERS,
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return ""

            html = resp.text

            # Try to extract article body HTML
            article_html = ""

            # Strategy 1: <article> tag (most reliable)
            for match in re.findall(r'<article[^>]*>(.*?)</article>', html, re.IGNORECASE | re.DOTALL):
                article_html += match + "\n"

            # Strategy 2: <main> tag
            if not article_html:
                for match in re.findall(r'<main[^>]*>(.*?)</main>', html, re.IGNORECASE | re.DOTALL):
                    article_html += match + "\n"

            # Strategy 3: Content divs (common patterns)
            if not article_html:
                for pattern in [
                    r'<div[^>]+class=["\x27][^"\x27]*(?:article-body|post-content|entry-content|article-content|story-body|main-content|content-body|post-body|article__body|news-body)[^"\x27]*["\x27][^>]*>(.*?)</div>',
                    r'<div[^>]+id=["\x27][^"\x27]*(?:article|content|story|post|entry)[^"\x27]*["\x27][^>]*>(.*?)</div>',
                ]:
                    matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
                    for match in matches:
                        article_html += match + "\n"
                    if article_html:
                        break

            # Strategy 4: Fallback — use the whole page but skip header/footer/nav
            if not article_html:
                # Remove navigation, header, footer, sidebar, comments
                for tag in ['nav', 'header', 'footer', 'aside', 'noscript']:
                    html = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', html, flags=re.IGNORECASE | re.DOTALL)
                article_html = html

            # Strip HTML tags — keep only text
            # Remove script/style content first
            article_html = re.sub(r'<script[^>]*>.*?</script>', '', article_html, flags=re.IGNORECASE | re.DOTALL)
            article_html = re.sub(r'<style[^>]*>.*?</style>', '', article_html, flags=re.IGNORECASE | re.DOTALL)
            article_html = re.sub(r'<noscript[^>]*>.*?</noscript>', '', article_html, flags=re.IGNORECASE | re.DOTALL)

            # Convert block elements to newlines
            for tag in ['p', 'br', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr']:
                article_html = re.sub(rf'</?{tag}[^>]*>', '\n', article_html, flags=re.IGNORECASE)

            # Remove all remaining HTML tags
            article_html = re.sub(r'<[^>]+>', '', article_html)

            # Decode HTML entities
            article_html = article_html.replace('&nbsp;', ' ')
            article_html = article_html.replace('&amp;', '&')
            article_html = article_html.replace('&lt;', '<')
            article_html = article_html.replace('&gt;', '>')
            article_html = article_html.replace('&#8217;', "'")
            article_html = article_html.replace('&#8216;', "'")
            article_html = article_html.replace('&#8220;', '"')
            article_html = article_html.replace('&#8221;', '"')
            article_html = article_html.replace('&#8211;', '\u2013')
            article_html = article_html.replace('&#8212;', '\u2014')

            # Clean up whitespace
            article_html = re.sub(r'[ \t]+', ' ', article_html)
            article_html = re.sub(r'\n\s*\n+', '\n\n', article_html)

            # Remove common junk lines (social media, newsletter signup, etc.)
            junk_lines = [
                'share this', 'follow us', 'subscribe', 'sign up', 'newsletter',
                'click here', 'read more', 'continue reading', 'related articles',
                'you may also like', 'recommended', 'advertisement', 'cookie',
                'privacy policy', 'terms of use', 'comments', 'add comment',
                '\u043f\u043e\u0434\u0435\u043b\u0438\u0442\u044c\u0441\u044f', '\u043f\u043e\u0434\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f', '\u0447\u0438\u0442\u0430\u0442\u044c \u0434\u0430\u043b\u0435\u0435', '\u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0435\u043d\u0438\u0435',
                '\u0440\u0435\u043a\u043b\u0430\u043c\u0430', '\u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0438', '\u043e\u0441\u0442\u0430\u0432\u0438\u0442\u044c \u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0439',
            ]
            lines = []
            for line in article_html.split('\n'):
                line = line.strip()
                if not line or len(line) < 15:
                    continue
                line_lower = line.lower()
                if any(junk in line_lower for junk in junk_lines):
                    continue
                lines.append(line)

            text = '\n'.join(lines)

            if len(text) > max_chars:
                text = text[:max_chars].rsplit('\n', 1)[0] + '...'

            return text.strip()

    except Exception as e:
        logger.debug(f"Article text extraction failed for {url}: {e}")
        return ""
