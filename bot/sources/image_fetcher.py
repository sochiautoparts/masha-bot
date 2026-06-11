"""Smart Image Fetcher v2.0 — Original-first image sourcing for masha-bot.

PRIORITY PIPELINE:
  1. Article images — og:image / twitter:image / <img> from source URL
  2. RSS enclosures — <enclosure> / <media:content> from RSS feed
  3. Image search — SearXNG images / Google Images
  4. AI generation — Pollinations (LAST RESORT ONLY, handled by caller)

KEY FEATURES:
  - Extracts original photos from article pages (og:image, twitter:image)
  - Parses RSS enclosures and media:content
  - Validates images: min size, content-type, magic bytes
  - Caches images by topic/entity with 7-day TTL
  - Blacklist of junk image domains and patterns
  - Returns base64-encoded images ready for Telegram
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("masha.image_fetcher")

# ── Configuration ─────────────────────────────────────────────────────────────

IMAGE_CACHE_DIR = Path("data/image_cache")
IMAGE_CACHE_TTL_DAYS = 7
IMAGE_MIN_SIZE_BYTES = 10_000        # 10 KB — filter out tracking pixels, icons
IMAGE_MAX_SIZE_BYTES = 10_485_760    # 10 MB — Telegram limit
IMAGE_MIN_WIDTH = 400
IMAGE_MIN_HEIGHT = 300
IMAGE_FETCH_TIMEOUT = 12.0
ARTICLE_FETCH_TIMEOUT = 15.0
MAX_IMAGES_PER_SOURCE = 3

# ── Blacklist — junk image URLs that should never be used ─────────────────────

JUNK_DOMAINS = {
    # Tracking / analytics
    "pixel", "tracker", "analytics", "counter", "beacon",
    "mc.yandex.ru", "mc.yandex.com", "google-analytics.com",
    "facebook.com/tr", "connect.facebook.net",
    # Icons / UI elements
    "gravatar.com", "wp.com/mu-plugins/",
    # Ad networks
    "doubleclick.net", "adservice.google.com",
    "pagead2.googlesyndication.com", "ad.doubleclick.net",
    # Social media UI
    "platform.twitter.com", "apis.google.com",
}

JUNK_PATTERNS = [
    r"[\?&](utm_|ref|share|action|callback|client_id)=.*$",
    r"/(icon|logo|favicon|badge|avatar|spinner|loading|placeholder|blank|pixel)\b",
    r"\d+x\d+\.(gif|png)$",              # e.g. 16x16.gif, 32x32.png
    r"tracker|beacon|pixel|counter|analytics",
    r"gravatar|avatar|profile.*photo",
    r"(button|btn|icon|logo|badge|spinner)\.(png|gif|svg|webp)$",
]

JUNK_EXTENSIONS = {".gif", ".svg"}  # Too often icons/buttons; very rarely real photos


# ── Image Cache ───────────────────────────────────────────────────────────────

class ImageCache:
    """File-based image cache with TTL. Key = entity/topic, Value = image data."""

    def __init__(self, cache_dir: Path = IMAGE_CACHE_DIR, ttl_days: int = IMAGE_CACHE_TTL_DAYS):
        self.cache_dir = cache_dir
        self.ttl_days = ttl_days
        self._index_path = cache_dir / "index.json"
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load_index()

    def _load_index(self) -> None:
        """Load cache index from disk."""
        try:
            if self._index_path.exists():
                with open(self._index_path, "r", encoding="utf-8") as f:
                    self._index = json.load(f)
        except Exception as e:
            logger.debug(f"Failed to load image cache index: {e}")
            self._index = {}

    def _save_index(self) -> None:
        """Save cache index to disk."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._index_path, "w", encoding="utf-8") as f:
                json.dump(self._index, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"Failed to save image cache index: {e}")

    def _cache_key(self, topic: str) -> str:
        """Generate cache key from topic."""
        return hashlib.md5(topic.lower().strip().encode()).hexdigest()

    def get(self, topic: str) -> Optional[Dict[str, Any]]:
        """Get cached image data for a topic."""
        key = self._cache_key(topic)
        entry = self._index.get(key)
        if not entry:
            return None

        # Check TTL
        cached_at = entry.get("cached_at", 0)
        age_days = (time.time() - cached_at) / 86400
        if age_days > self.ttl_days:
            self.delete(topic)
            return None

        # Check file exists
        file_path = self.cache_dir / entry.get("filename", "")
        if not file_path.exists():
            self.delete(topic)
            return None

        try:
            with open(file_path, "rb") as f:
                img_bytes = f.read()
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            return {
                "image_b64": img_b64,
                "source": entry.get("source", "cache"),
                "url": entry.get("url", ""),
            }
        except Exception as e:
            logger.debug(f"Failed to read cached image: {e}")
            return None

    def put(self, topic: str, img_b64: str, source: str, url: str = "") -> None:
        """Store image in cache."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            key = self._cache_key(topic)
            filename = f"{key}.jpg"

            # Decode and save raw bytes
            img_bytes = base64.b64decode(img_b64)
            file_path = self.cache_dir / filename
            with open(file_path, "wb") as f:
                f.write(img_bytes)

            self._index[key] = {
                "topic": topic[:100],
                "filename": filename,
                "source": source,
                "url": url[:500],
                "cached_at": time.time(),
            }
            self._save_index()
        except Exception as e:
            logger.debug(f"Failed to cache image: {e}")

    def delete(self, topic: str) -> None:
        """Remove a topic from cache."""
        key = self._cache_key(topic)
        entry = self._index.pop(key, None)
        if entry:
            try:
                file_path = self.cache_dir / entry.get("filename", "")
                if file_path.exists():
                    file_path.unlink()
            except Exception:
                pass
            self._save_index()


# ── Validation ────────────────────────────────────────────────────────────────

def _is_junk_url(url: str) -> bool:
    """Check if a URL is a junk/tracking/icon image."""
    url_lower = url.lower()
    parsed = urlparse(url_lower)

    # Check junk domains
    hostname = parsed.hostname or ""
    for junk in JUNK_DOMAINS:
        if junk in hostname:
            return True

    # Check junk patterns
    for pattern in JUNK_PATTERNS:
        if re.search(pattern, url_lower):
            return True

    # Check junk extensions
    path = parsed.path.lower()
    for ext in JUNK_EXTENSIONS:
        if path.endswith(ext):
            return True

    return False


async def _validate_image(
    client: httpx.AsyncClient,
    url: str,
    max_size: int = IMAGE_MAX_SIZE_BYTES,
) -> Optional[Dict[str, Any]]:
    """Download and validate an image URL. Returns dict with b64 data or None."""
    try:
        # Quick HEAD request to check content-type and size
        try:
            head_resp = await client.head(url, timeout=6.0, follow_redirects=True)
            content_type = head_resp.headers.get("content-type", "").lower()
            content_length = int(head_resp.headers.get("content-length", "0"))

            # Only allow image types
            if not any(ct in content_type for ct in ["image/jpeg", "image/png", "image/webp", "image/jpg"]):
                # Some servers don't return proper content-type on HEAD, so continue anyway
                if content_type and not content_type.startswith("image/"):
                    return None

            # Skip obviously too small files
            if 0 < content_length < IMAGE_MIN_SIZE_BYTES:
                return None

            # Skip obviously too large files
            if content_length > max_size:
                return None
        except Exception:
            pass  # HEAD failed, try GET anyway

        # Full GET request
        resp = await client.get(url, timeout=IMAGE_FETCH_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            return None

        img_bytes = resp.content

        # Size checks
        if len(img_bytes) < IMAGE_MIN_SIZE_BYTES:
            return None
        if len(img_bytes) > max_size:
            return None

        # Content-type check from GET response
        content_type = resp.headers.get("content-type", "").lower()
        if content_type and not any(ct in content_type for ct in [
            "image/jpeg", "image/png", "image/webp", "image/jpg", "application/octet-stream",
        ]):
            return None

        # Basic JPEG/PNG/WebP magic bytes check
        is_valid_format = (
            img_bytes[:2] == b'\xff\xd8'  # JPEG
            or img_bytes[:8] == b'\x89PNG\r\n\x1a\n'  # PNG
            or img_bytes[:4] == b'RIFF'  # WebP (RIFF container)
        )
        if not is_valid_format:
            return None

        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        return {
            "image_b64": img_b64,
            "image_url": str(resp.url),
            "source": "fetched",
            "size_bytes": len(img_bytes),
        }

    except Exception as e:
        logger.debug(f"Image validation failed for {url}: {e}")
        return None


# ── Strategy 1: Article page image extraction (og:image, twitter:image) ──────

async def fetch_article_images(url: str) -> List[str]:
    """Fetch original images from an article page.

    Extracts images in priority order:
    1. og:image meta tag
    2. twitter:image meta tag
    3. <link rel="image_src">
    4. First large <img> in the article content area
    """
    image_urls: List[str] = []

    if not url or not url.startswith(("http://", "https://")):
        return image_urls

    try:
        async with httpx.AsyncClient(
            timeout=ARTICLE_FETCH_TIMEOUT,
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
            og_match = re.search(
                r'<meta\s+(?:property|name)=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
                html, re.IGNORECASE,
            )
            if not og_match:
                og_match = re.search(
                    r'<meta\s+content=["\']([^"\']+)["\']\s+(?:property|name)=["\']og:image["\']',
                    html, re.IGNORECASE,
                )
            if og_match:
                img_url = og_match.group(1).replace("&amp;", "&")
                if not _is_junk_url(img_url):
                    image_urls.append(img_url)

            # 2. twitter:image
            tw_match = re.search(
                r'<meta\s+(?:property|name)=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']',
                html, re.IGNORECASE,
            )
            if not tw_match:
                tw_match = re.search(
                    r'<meta\s+content=["\']([^"\']+)["\']\s+(?:property|name)=["\']twitter:image["\']',
                    html, re.IGNORECASE,
                )
            if tw_match:
                img_url = tw_match.group(1).replace("&amp;", "&")
                if not _is_junk_url(img_url) and img_url not in image_urls:
                    image_urls.append(img_url)

            # 3. <link rel="image_src">
            link_match = re.search(
                r'<link\s+[^>]*rel=["\']image_src["\'][^>]*href=["\']([^"\']+)["\']',
                html, re.IGNORECASE,
            )
            if not link_match:
                link_match = re.search(
                    r'<link\s+[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']image_src["\']',
                    html, re.IGNORECASE,
                )
            if link_match:
                img_url = link_match.group(1).replace("&amp;", "&")
                if not _is_junk_url(img_url) and img_url not in image_urls:
                    image_urls.append(img_url)

            # 4. First large <img> in article content area
            article_blocks = re.findall(
                r'<(?:article|main)[^>]*>(.*?)</(?:article|main)>',
                html, re.DOTALL | re.IGNORECASE,
            )
            if not article_blocks:
                article_blocks = re.findall(
                    r'<div[^>]*class=["\'][^"\']*(?:content|post|entry|article)[^"\']*["\'][^>]*>(.*?)</div>',
                    html, re.DOTALL | re.IGNORECASE,
                )

            for block in article_blocks[:2]:  # Check first 2 article blocks
                img_matches = re.findall(
                    r'<img[^>]+src=["\']([^"\']+)["\']',
                    block, re.IGNORECASE,
                )
                for img_url in img_matches:
                    img_url = img_url.replace("&amp;", "&")
                    if not _is_junk_url(img_url) and img_url not in image_urls:
                        image_urls.append(img_url)

            # 5. Fallback: first <img> with width/height attributes suggesting large image
            if not image_urls:
                sized_imgs = re.findall(
                    r'<img[^>]+src=["\']([^"\']+)["\'][^>]*(?:width|height)\s*=\s*["\'](\d+)["\']',
                    html, re.IGNORECASE,
                )
                for img_url, dim in sized_imgs:
                    if int(dim) >= IMAGE_MIN_WIDTH and not _is_junk_url(img_url) and img_url not in image_urls:
                        image_urls.append(img_url)

    except Exception as e:
        logger.debug(f"Article image extraction failed for {url}: {e}")

    return image_urls[:MAX_IMAGES_PER_SOURCE]


# ── Strategy 2: RSS enclosure / media:content extraction ─────────────────────

def extract_rss_images(entry: Any) -> List[str]:
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

    return image_urls[:MAX_IMAGES_PER_SOURCE]


# ── Strategy 3: Image search ─────────────────────────────────────────────────

async def search_images(topic: str, max_images: int = 3) -> List[str]:
    """Search for images related to a topic using SearXNG image search."""
    image_urls: List[str] = []

    try:
        from bot.web_search import search_searxng

        # Clean up topic for search
        clean_topic = re.sub(r'[^\w\s]', '', topic)[:80]

        # Try SearXNG image search
        results = await search_searxng(
            f"{clean_topic} BMW",
            max_results=8,
            language="ru",
            categories="images",
        )

        for r in results:
            if r.url:
                url_lower = r.url.lower()
                # Direct image URLs
                if any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                    if not _is_junk_url(r.url):
                        image_urls.append(r.url)
                # Image service URLs (imgur, etc.)
                elif any(domain in url_lower for domain in ['imgur.com', 'flickr.com', 'unsplash.com']):
                    if not _is_junk_url(r.url):
                        image_urls.append(r.url)

        # Fallback: regular web search with image keywords
        if not image_urls:
            from bot.web_search import web_search
            results = await web_search(f"{clean_topic} BMW photo image", max_results=5)
            for r in results:
                if r.url and not _is_junk_url(r.url):
                    image_urls.append(r.url)

    except Exception as e:
        logger.debug(f"Image search failed for '{topic}': {e}")

    return image_urls[:max_images]


# ── Main fetcher class ───────────────────────────────────────────────────────

class ImageFetcher:
    """Smart image fetcher with original-first priority pipeline.

    Usage:
        fetcher = ImageFetcher()
        result = await fetcher.fetch(
            topic="BMW M5 G90 debut",
            article_url="https://bmwblog.com/...",
            rss_entry=feed_entry,
        )
        # result = {"image_b64": ..., "source": "og:image", "url": ...}
    """

    def __init__(self) -> None:
        self.cache = ImageCache()
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=IMAGE_FETCH_TIMEOUT,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                },
            )
        return self._client

    async def fetch(
        self,
        topic: str,
        article_url: str = "",
        rss_entry: Any = None,
        content_type: str = "news+reaction",
    ) -> Optional[Dict[str, Any]]:
        """Fetch an image using the priority pipeline.

        Returns dict with keys: image_b64, source, url
        Or None if no suitable image found (caller should fall back to AI generation).
        """
        # ── Step 0: Check cache ───────────────────────────────────────────
        cached = self.cache.get(topic)
        if cached:
            logger.info(f"Image cache HIT for '{topic[:50]}'")
            cached["source"] = f"cache:{cached.get('source', 'unknown')}"
            return cached

        client = self._get_client()
        all_candidates: List[Tuple[str, str]] = []  # (url, source_label)

        # ── Step 1: RSS enclosures / media:content ────────────────────────
        if rss_entry is not None:
            rss_urls = extract_rss_images(rss_entry)
            for url in rss_urls:
                all_candidates.append((url, "rss_enclosure"))
            if rss_urls:
                logger.info(f"RSS images: {len(rss_urls)} found for '{topic[:50]}'")

        # ── Step 2: Article page og:image / twitter:image ─────────────────
        if article_url:
            article_urls = await fetch_article_images(article_url)
            for url in article_urls:
                if url not in [u for u, _ in all_candidates]:
                    all_candidates.append((url, "og:image"))
            if article_urls:
                logger.info(f"Article images: {len(article_urls)} found for '{article_url[:60]}'")

        # ── Step 3: Image search ──────────────────────────────────────────
        if not all_candidates:
            search_urls = await search_images(topic)
            for url in search_urls:
                if url not in [u for u, _ in all_candidates]:
                    all_candidates.append((url, "image_search"))
            if search_urls:
                logger.info(f"Search images: {len(search_urls)} found for '{topic[:50]}'")

        # ── Step 4: Validate and pick the best ────────────────────────────
        for url, source_label in all_candidates:
            result = await _validate_image(client, url)
            if result:
                result["source"] = source_label
                # Cache the result
                self.cache.put(topic, result["image_b64"], source=source_label, url=url)
                logger.info(f"Image fetched via {source_label}: {url[:80]} ({result.get('size_bytes', 0)} bytes)")
                return result

        # ── All strategies failed ─────────────────────────────────────────
        logger.info(f"No real image found for '{topic[:50]}' — AI generation will be used as fallback")
        return None

    async def fetch_multiple(
        self,
        topic: str,
        article_url: str = "",
        rss_entry: Any = None,
        max_images: int = 3,
    ) -> List[Dict[str, Any]]:
        """Fetch multiple images for a post (e.g., for mediagroup).

        Returns list of dicts with image_b64, source, url.
        """
        client = self._get_client()
        all_candidates: List[Tuple[str, str]] = []

        # Collect all candidate URLs from all sources
        if rss_entry is not None:
            for url in extract_rss_images(rss_entry):
                all_candidates.append((url, "rss_enclosure"))

        if article_url:
            for url in await fetch_article_images(article_url):
                if url not in [u for u, _ in all_candidates]:
                    all_candidates.append((url, "og:image"))

        if len(all_candidates) < max_images:
            for url in await search_images(topic, max_images=max_images):
                if url not in [u for u, _ in all_candidates]:
                    all_candidates.append((url, "image_search"))

        # Validate and collect
        results: List[Dict[str, Any]] = []
        for url, source_label in all_candidates:
            if len(results) >= max_images:
                break
            result = await _validate_image(client, url)
            if result:
                result["source"] = source_label
                results.append(result)

        if results:
            logger.info(f"Fetched {len(results)} images for '{topic[:50]}'")

        return results

    async def close(self) -> None:
        """Clean up resources."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# ── Convenience function ─────────────────────────────────────────────────────

_fetcher: Optional[ImageFetcher] = None


async def fetch_image_for_post(
    topic: str,
    article_url: str = "",
    rss_entry: Any = None,
    content_type: str = "news+reaction",
) -> Optional[Dict[str, Any]]:
    """Module-level convenience function to fetch an image for a post.

    Uses a module-level ImageFetcher instance (lazily initialized).
    """
    global _fetcher
    if _fetcher is None:
        _fetcher = ImageFetcher()
    return await _fetcher.fetch(
        topic=topic,
        article_url=article_url,
        rss_entry=rss_entry,
        content_type=content_type,
    )
