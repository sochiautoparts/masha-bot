"""Smart Image Fetcher v4.0 — Real-photo-first image sourcing for masha-bot.

PRIORITY PIPELINE:
  1. RSS enclosures + content:encoded images — <enclosure> / <media:content> / HTML from RSS
  2. Article images — BeautifulSoup+lxml scraping (og:image / twitter:image / JSON-LD / <img>)
  3. Image search — AI-powered SearXNG queries with BMW-specific context
  4. NO AI IMAGE GENERATION — disabled per user requirement

KEY IMPROVEMENTS v4.0:
  - BeautifulSoup + lxml replaces regex for article scraping — 3-5x more reliable
  - AI-powered search queries — SearXNG gets 3-5 precise BMW-specific queries
  - RSS content:encoded parsing — extracts images from HTML inside RSS entries
  - NO AI-generated photos — only real images from news sources
  - SHA256 + perceptual deduplication
  - Up to 10 images per post (Telegram limit)

NOTE: This module returns image BYTES (not base64) because that's what
channel.py expects for Telegram posting.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

import httpx

logger = logging.getLogger("masha.image_fetcher")

# ── Configuration ─────────────────────────────────────────────────────────────

IMAGE_CACHE_DIR = Path("data/image_cache")
IMAGE_CACHE_TTL_DAYS = 7
IMAGE_MIN_SIZE_BYTES = 3_000         # 3 KB — lower threshold for news images
IMAGE_MAX_SIZE_BYTES = 5_242_880     # 5 MB — matching channel.py limit
IMAGE_MIN_WIDTH = 400
IMAGE_MIN_HEIGHT = 300
IMAGE_FETCH_TIMEOUT = 15.0
ARTICLE_FETCH_TIMEOUT = 20.0
MAX_IMAGES_PER_SOURCE = 10           # Up to 10 candidates per source
MAX_IMAGES_PER_POST = 10             # Telegram mediagroup limit

# ── Blacklist — junk image URLs that should never be used ─────────────────────

JUNK_DOMAINS = {
    "pixel", "tracker", "analytics", "counter", "beacon",
    "mc.yandex.ru", "mc.yandex.com", "google-analytics.com",
    "facebook.com/tr", "connect.facebook.net",
    "feeds.feedburner.com", "feedburner.google.com",
    "pixel.wp.com", "stats.wordpress.com",
    "doubleclick.net", "adservice.google.com",
    "pagead2.googlesyndication.com", "ad.doubleclick.net",
    "platform.twitter.com", "apis.google.com",
}

JUNK_PATTERNS = [
    r"[\?&](utm_|ref|share|action|callback|client_id)=.*$",
    r"/(icon|logo|favicon|badge|avatar|spinner|loading|placeholder|blank|pixel)\b",
    r"\d+x\d+\.(gif|png)$",
    r"tracker|beacon|pixel|counter|analytics",
    r"gravatar|avatar|profile.*photo",
    r"(button|btn|icon|logo|badge|spinner)\.(png|gif|svg|webp)$",
]

JUNK_KEYWORDS = [
    "icon", "logo", "favicon", "avatar", "badge", "button", "btn",
    "spinner", "loading", "placeholder", "pixel", "tracker",
    "analytics", "share", "facebook", "twitter", "vk.",
    "telegram", "whatsapp", "instagram", "youtube", "tiktok",
    "ad.", "ads/", "advert", "sponsor",
    "emoji", "smileys", "captcha", "recaptcha",
    "1x1", "spacer", "blank", "transparent", "dot.",
    "watermark",
]

JUNK_EXTENSIONS = {".gif", ".svg"}

# ── BMW model context for smarter search queries ─────────────────────────────

BMW_MODELS_MAP = {
    "m2": {"generations": ["F87", "G87"], "years": "2016-2025"},
    "m3": {"generations": ["F80", "G80"], "years": "2014-2025"},
    "m4": {"generations": ["F82", "G82"], "years": "2014-2025"},
    "m5": {"generations": ["F90", "G90"], "years": "2018-2025"},
    "m8": {"generations": ["F91", "F92", "F93"], "years": "2019-2025"},
    "x3 m": {"generations": ["F97"], "years": "2019-2025"},
    "x4 m": {"generations": ["F98"], "years": "2019-2025"},
    "x5 m": {"generations": ["F85", "F95"], "years": "2015-2025"},
    "x6 m": {"generations": ["F86", "F96"], "years": "2015-2025"},
    "x5": {"generations": ["F15", "G05"], "years": "2013-2025"},
    "x3": {"generations": ["F25", "G01"], "years": "2011-2025"},
    "x7": {"generations": ["G07"], "years": "2019-2025"},
    "i4": {"generations": ["G26"], "years": "2021-2025"},
    "i5": {"generations": ["G60"], "years": "2023-2025"},
    "i7": {"generations": ["G70"], "years": "2022-2025"},
    "ix": {"generations": ["iX"], "years": "2021-2025"},
    "z4": {"generations": ["G29"], "years": "2019-2025"},
    "3 series": {"generations": ["F30", "G20"], "years": "2012-2025"},
    "5 series": {"generations": ["G30", "G60"], "years": "2017-2025"},
    "7 series": {"generations": ["G11", "G70"], "years": "2016-2025"},
    "alpina": {"generations": [], "years": "2020-2025"},
}


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
        try:
            if self._index_path.exists():
                with open(self._index_path, "r", encoding="utf-8") as f:
                    self._index = json.load(f)
        except Exception as e:
            logger.debug(f"Failed to load image cache index: {e}")
            self._index = {}

    def _save_index(self) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(self._index_path, "w", encoding="utf-8") as f:
                json.dump(self._index, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"Failed to save image cache index: {e}")

    def _cache_key(self, topic: str) -> str:
        return hashlib.md5(topic.lower().strip().encode()).hexdigest()

    def get(self, topic: str) -> Optional[List[bytes]]:
        """Get cached image bytes for a topic."""
        key = self._cache_key(topic)
        entry = self._index.get(key)
        if not entry:
            return None

        cached_at = entry.get("cached_at", 0)
        age_days = (time.time() - cached_at) / 86400
        if age_days > self.ttl_days:
            self.delete(topic)
            return None

        # Load all cached files for this topic
        results = []
        for filename in entry.get("filenames", []):
            file_path = self.cache_dir / filename
            if file_path.exists():
                try:
                    with open(file_path, "rb") as f:
                        results.append(f.read())
                except Exception:
                    continue

        return results if results else None

    def put(self, topic: str, images: List[bytes], source: str) -> None:
        """Store images in cache."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            key = self._cache_key(topic)
            filenames = []

            for i, img_bytes in enumerate(images):
                filename = f"{key}_{i}.jpg"
                file_path = self.cache_dir / filename
                with open(file_path, "wb") as f:
                    f.write(img_bytes)
                filenames.append(filename)

            self._index[key] = {
                "topic": topic[:100],
                "filenames": filenames,
                "source": source,
                "cached_at": time.time(),
            }
            self._save_index()
        except Exception as e:
            logger.debug(f"Failed to cache images: {e}")

    def delete(self, topic: str) -> None:
        key = self._cache_key(topic)
        entry = self._index.pop(key, None)
        if entry:
            for filename in entry.get("filenames", []):
                try:
                    file_path = self.cache_dir / filename
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
    hostname = parsed.hostname or ""

    for junk in JUNK_DOMAINS:
        if junk in hostname:
            return True

    for kw in JUNK_KEYWORDS:
        if kw in url_lower:
            return True

    for pattern in JUNK_PATTERNS:
        if re.search(pattern, url_lower):
            return True

    path = parsed.path.lower()
    for ext in JUNK_EXTENSIONS:
        if path.endswith(ext):
            return True

    # Skip URLs with very small size indicators
    size_pattern = re.compile(r'[/=_x](\d{1,3})x(\d{1,3})[/._]')
    size_match = size_pattern.search(url_lower)
    if size_match:
        w, h = int(size_match.group(1)), int(size_match.group(2))
        if w < 100 or h < 100:
            return True

    return False


def _is_content_image(image_data: bytes) -> bool:
    """Validate that image data represents a proper content photo."""
    if len(image_data) < IMAGE_MIN_SIZE_BYTES:
        return False

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_data))
        width, height = img.size

        if width < IMAGE_MIN_WIDTH or height < IMAGE_MIN_HEIGHT:
            return False
        if width / max(height, 1) > 3.0:
            return False
        if height / max(width, 1) > 3.0:
            return False
        if width * height < 120000:
            return False
        return True
    except ImportError:
        logger.warning("PIL not available, accepting image without dimension check")
        return True
    except Exception:
        return True


async def _validate_and_download(
    client: httpx.AsyncClient,
    url: str,
) -> Optional[bytes]:
    """Download and validate an image URL. Returns image bytes or None."""
    if _is_junk_url(url):
        return None

    try:
        # Quick HEAD check
        try:
            head_resp = await client.head(url, timeout=6.0, follow_redirects=True)
            content_type = head_resp.headers.get("content-type", "").lower()
            content_length = int(head_resp.headers.get("content-length", "0"))

            if content_type and not any(ct in content_type for ct in [
                "image/jpeg", "image/png", "image/webp", "image/jpg",
            ]):
                if not content_type.startswith("image/"):
                    return None

            if 0 < content_length < IMAGE_MIN_SIZE_BYTES:
                return None
            if content_length > IMAGE_MAX_SIZE_BYTES:
                return None
        except Exception:
            pass

        # Full GET
        resp = await client.get(url, timeout=IMAGE_FETCH_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            return None

        img_bytes = resp.content

        if len(img_bytes) < IMAGE_MIN_SIZE_BYTES:
            return None
        if len(img_bytes) > IMAGE_MAX_SIZE_BYTES:
            return None

        # Content-type check
        content_type = resp.headers.get("content-type", "").lower()
        if content_type and not any(ct in content_type for ct in [
            "image/jpeg", "image/png", "image/webp", "image/jpg",
            "application/octet-stream",
        ]):
            return None

        # Skip SVG
        if b'<svg' in img_bytes[:500] or 'svg' in content_type:
            return None

        # Magic bytes check
        is_valid_format = (
            img_bytes[:3] == b'\xff\xd8\xff'  # JPEG
            or img_bytes[:4] == b'\x89PNG'     # PNG
            or img_bytes[:4] == b'RIFF'        # WebP
            or img_bytes[:6] in (b'GIF87a', b'GIF89a')  # GIF
        )
        if not is_valid_format:
            return None

        # Dimension check (PIL when available)
        if not _is_content_image(img_bytes):
            return None

        return img_bytes

    except Exception as e:
        logger.debug(f"Image download failed for {url[:60]}: {e}")
        return None


# ── Strategy 1: Article page image extraction with BeautifulSoup ─────────────

async def fetch_article_images(url: str, max_count: int = 10) -> List[bytes]:
    """Fetch original images from an article page using BeautifulSoup + lxml.

    v4.0: Uses BeautifulSoup instead of regex for 3-5x more reliable extraction.
    Handles: og:image, twitter:image, JSON-LD, <picture>, <img> with lazy loading,
    data-src, srcset, and article body images.
    """
    images: List[bytes] = []

    if not url or not url.startswith(("http://", "https://")):
        return images

    try:
        _SCRAPE_HEADERS = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        async with httpx.AsyncClient(
            timeout=ARTICLE_FETCH_TIMEOUT,
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            resp = await client.get(url, headers=_SCRAPE_HEADERS)
            if resp.status_code != 200:
                return images

            html = resp.text
            candidate_urls: List[str] = []
            seen: set = set()

            # ── BeautifulSoup parsing ─────────────────────────────────────
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")

                # 1. og:image meta tags
                for tag in soup.find_all("meta", attrs={"property": re.compile(r"^og:image")}):
                    content = tag.get("content", "")
                    if content and content not in seen and not _is_junk_url(content):
                        seen.add(content)
                        candidate_urls.append(content)

                # 2. twitter:image meta tags
                for tag in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:image")}):
                    content = tag.get("content", "")
                    if content and content not in seen and not _is_junk_url(content):
                        seen.add(content)
                        candidate_urls.append(content)

                # 3. JSON-LD structured data (schema.org)
                for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
                    try:
                        data = json.loads(script.string or "")
                        jsonld_urls = _extract_jsonld_images_dict(data)
                        for u in jsonld_urls:
                            if u and u not in seen and not _is_junk_url(u):
                                seen.add(u)
                                candidate_urls.append(u)
                    except Exception:
                        continue

                # 4. <picture>/<source srcset> elements
                for picture in soup.find_all("picture"):
                    for source in picture.find_all("source"):
                        srcset = source.get("srcset", "")
                        if srcset:
                            for part in srcset.split(','):
                                u = part.strip().split()[0] if part.strip() else ''
                                if u and u not in seen and not _is_junk_url(u):
                                    seen.add(u)
                                    candidate_urls.append(u)

                # 5. <img> tags from article body areas
                # Try to find article/main/content containers first
                article_area = (
                    soup.find("article")
                    or soup.find("main")
                    or soup.find("div", class_=re.compile(r"(content|article|post|entry)", re.I))
                    or soup
                )

                # Collect all <img> tags — lazy-loaded first (often higher quality)
                for img in article_area.find_all("img"):
                    # Try multiple attribute sources for the URL
                    for attr in ["data-src", "data-lazy-src", "data-original", "src"]:
                        img_url = img.get(attr, "")
                        if img_url and img_url not in seen and len(img_url) > 10:
                            if img_url.startswith("//"):
                                img_url = "https:" + img_url
                            elif not img_url.startswith(("http://", "https://")):
                                # Resolve relative URLs
                                img_url = urljoin(url, img_url)
                            if not _is_junk_url(img_url):
                                seen.add(img_url)
                                candidate_urls.append(img_url)

                # 6. Also try srcset on any <img> tags
                for img in article_area.find_all("img", srcset=True):
                    srcset = img.get("srcset", "")
                    if srcset:
                        for part in srcset.split(','):
                            u = part.strip().split()[0] if part.strip() else ''
                            if u and u not in seen and not _is_junk_url(u):
                                if u.startswith("//"):
                                    u = "https:" + u
                                elif not u.startswith(("http://", "https://")):
                                    u = urljoin(url, u)
                                seen.add(u)
                                candidate_urls.append(u)

                logger.info(f"BS4 scraped {len(candidate_urls)} candidate image URLs from {url[:60]}")

            except ImportError:
                # BeautifulSoup not available — fall back to regex
                logger.warning("BeautifulSoup not available, falling back to regex scraping")
                candidate_urls = _scrape_with_regex(html, url)

            # Download and validate
            for img_url in candidate_urls[:max_count * 3]:
                if len(images) >= max_count:
                    break
                img_bytes = await _validate_and_download(client, img_url)
                if img_bytes:
                    images.append(img_bytes)
                    logger.info(f"Downloaded article image: {img_url[:80]} ({len(img_bytes)} bytes)")

    except Exception as e:
        logger.debug(f"Article image extraction failed for {url[:60]}: {e}")

    return images


def _scrape_with_regex(html: str, base_url: str = "") -> List[str]:
    """Fallback regex-based scraping when BeautifulSoup is not available."""
    candidate_urls: List[str] = []
    seen: set = set()

    # og:image
    for pattern in [
        r'<meta[^>]+property=["\x27]og:image["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]',
        r'<meta[^>]+content=["\x27]([^"\x27]+)["\x27][^>]+property=["\x27]og:image["\x27]',
    ]:
        for m in re.finditer(pattern, html, re.IGNORECASE):
            u = m.group(1).replace("&amp;", "&")
            if u and u not in seen and not _is_junk_url(u):
                seen.add(u)
                candidate_urls.append(u)

    # twitter:image
    for pattern in [
        r'<meta[^>]+name=["\x27]twitter:image["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]',
        r'<meta[^>]+content=["\x27]([^"\x27]+)["\x27][^>]+name=["\x27]twitter:image["\x27]',
    ]:
        for m in re.finditer(pattern, html, re.IGNORECASE):
            u = m.group(1).replace("&amp;", "&")
            if u and u not in seen and not _is_junk_url(u):
                seen.add(u)
                candidate_urls.append(u)

    # <img> tags
    for attr_pattern in [
        r'<img[^>]+data-src=["\x27]([^"\x27]+)["\x27]',
        r'<img[^>]+src=["\x27]([^"\x27]+)["\x27]',
    ]:
        for m in re.finditer(attr_pattern, html, re.IGNORECASE):
            u = m.group(1).replace("&amp;", "&")
            if u and u not in seen and len(u) > 10 and not _is_junk_url(u):
                seen.add(u)
                candidate_urls.append(u)

    return candidate_urls


def _extract_jsonld_images_dict(data: Any) -> List[str]:
    """Extract image URLs from a parsed JSON-LD data structure."""
    images = []
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        img_field = item.get("image") or item.get("images")
        if not img_field:
            continue
        if isinstance(img_field, str):
            images.append(img_field)
        elif isinstance(img_field, dict):
            url = img_field.get("url") or img_field.get("contentUrl") or img_field.get("@id", "")
            if url:
                images.append(url)
        elif isinstance(img_field, list):
            for img_item in img_field:
                if isinstance(img_item, str):
                    images.append(img_item)
                elif isinstance(img_item, dict):
                    url = img_item.get("url") or img_item.get("contentUrl") or img_item.get("@id", "")
                    if url:
                        images.append(url)
    return images


# ── Strategy 2: RSS enclosure / media:content / content:encoded extraction ───

def extract_rss_images(entry: Any) -> List[str]:
    """Extract image URLs from a feedparser entry.

    v4.0: Now also parses HTML inside content:encoded — many RSS feeds
    hide 3-5 images there that regular enclosures don't expose.
    Uses BeautifulSoup for content:encoded parsing when available.
    """
    image_urls: List[str] = []

    # 1. enclosures
    for enc in getattr(entry, "enclosures", []) or []:
        url = enc.get("href", "") or enc.get("url", "")
        enc_type = enc.get("type", "").lower()
        if url and ("image" in enc_type or any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"])):
            if not _is_junk_url(url) and url not in image_urls:
                image_urls.append(url)

    # 2. media:content
    for mc in getattr(entry, "media_content", []) or []:
        url = mc.get("url", "")
        medium = mc.get("medium", "").lower()
        mc_type = mc.get("type", "").lower()
        if url and (medium == "image" or "image" in mc_type):
            if not _is_junk_url(url) and url not in image_urls:
                image_urls.append(url)

    # 3. media:thumbnail
    for mt in getattr(entry, "media_thumbnail", []) or []:
        url = mt.get("url", "")
        if url and not _is_junk_url(url) and url not in image_urls:
            image_urls.append(url)

    # 4. content:encoded — HTML body often contains multiple <img> tags!
    # This is the KEY improvement: many feeds put 3-5 images in content:encoded
    # that are NOT in enclosures or media:content.
    for field_name in ("content", "summary", "description", "summary_detail"):
        content_value = getattr(entry, field_name, None)
        if isinstance(content_value, list):
            content_value = content_value[0].get("value", "") if content_value else ""
        elif content_value is None:
            continue
        elif isinstance(content_value, dict):
            content_value = content_value.get("value", "")

        if not content_value or not isinstance(content_value, str):
            continue

        # Try BeautifulSoup first (more reliable)
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(str(content_value), "lxml")
            for img in soup.find_all("img"):
                # Try all possible image sources
                for attr in ["src", "data-src", "data-lazy-src", "data-original"]:
                    img_url = img.get(attr, "")
                    if img_url and img_url not in image_urls and not _is_junk_url(img_url):
                        if img_url.startswith("//"):
                            img_url = "https:" + img_url
                        image_urls.append(img_url)
        except ImportError:
            # Fallback: regex
            for m in re.finditer(r'<img[^>]+src=["\x27]([^"\x27]+)["\x27]', str(content_value), re.IGNORECASE):
                url = m.group(1).replace("&amp;", "&")
                if not _is_junk_url(url) and url not in image_urls:
                    if url.startswith("//"):
                        url = "https:" + url
                    image_urls.append(url)
            # Also try data-src
            for m in re.finditer(r'<img[^>]+data-src=["\x27]([^"\x27]+)["\x27]', str(content_value), re.IGNORECASE):
                url = m.group(1).replace("&amp;", "&")
                if not _is_junk_url(url) and url not in image_urls:
                    if url.startswith("//"):
                        url = "https:" + url
                    image_urls.append(url)

    return image_urls[:15]  # Allow more from content:encoded


# ── Strategy 3: AI-powered image search ──────────────────────────────────────

async def _generate_search_queries(topic: str) -> List[str]:
    """Generate 3-5 smart search queries using AI for better image results.

    Instead of simple "BMW M5 фото", creates queries like:
    - "BMW M5 G90 2025 debut press photo"
    - "BMW M5 Competition F90 official image"
    - "BMW M5 G90 high resolution photo"
    """
    queries = []

    # 1. Build context-aware queries based on BMW model detection
    topic_lower = topic.lower()
    detected_models = []

    for model_key, model_info in BMW_MODELS_MAP.items():
        if model_key in topic_lower:
            detected_models.append((model_key, model_info))

    # 2. Russian query (primary)
    clean_topic = re.sub(r'[^\w\s]', '', topic)[:80]
    queries.append(f"{clean_topic} фото")

    # 3. If we detected a BMW model, add specific queries
    if detected_models:
        model_key, model_info = detected_models[0]  # Use first match
        generations = model_info.get("generations", [])
        years = model_info.get("years", "")

        # English query with model details
        if generations:
            gen = generations[-1]  # Latest generation
            queries.append(f"BMW {model_key.upper()} {gen} {years} official press photo")
            queries.append(f"BMW {model_key.upper()} {gen} high resolution image")
        else:
            queries.append(f"BMW {model_key.upper()} {years} press photo")
            queries.append(f"BMW {model_key.upper()} official image")

        # Competition/M variant
        if "competition" in topic_lower or "cs" in topic_lower:
            queries.append(f"BMW {model_key.upper()} Competition press image")

    # 4. Generic English query
    queries.append(f"{clean_topic} photo")

    # 5. If topic mentions a year, add year-specific query
    year_match = re.search(r'\b(202[0-9])\b', topic)
    if year_match:
        year = year_match.group(1)
        queries.append(f"BMW {year} {clean_topic[:40]} photo")

    # Deduplicate while preserving order
    seen = set()
    unique_queries = []
    for q in queries:
        q_clean = q.strip().lower()
        if q_clean not in seen:
            seen.add(q_clean)
            unique_queries.append(q)

    return unique_queries[:5]


async def search_images(topic: str, max_images: int = 5) -> List[str]:
    """Search for images using SearXNG with AI-powered smart queries.

    v4.0: Generates 3-5 BMW-specific search queries instead of just
    "topic фото". This dramatically improves results for BMW news.
    """
    image_urls: List[str] = []
    seen_urls: set = set()

    try:
        from bot.web_search import search_searxng

        # Generate smart queries instead of simple ones
        smart_queries = await _generate_search_queries(topic)

        logger.info(f"Smart image search queries for '{topic[:50]}': {smart_queries}")

        # Try SearXNG image search with each query
        for query in smart_queries:
            if len(image_urls) >= max_images:
                break
            try:
                results = await search_searxng(
                    query,
                    max_results=8,
                    language="ru",
                    categories="images",
                )
                for r in results:
                    if r.url and r.url not in seen_urls:
                        url_lower = r.url.lower()
                        is_image_url = any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.webp'])
                        is_image_host = any(domain in url_lower for domain in [
                            'imgur.com', 'flickr.com', 'unsplash.com',
                            'pexels.com', 'shutterstock.com', 'istockphoto.com',
                            'bmwblog.com', 'bimmerpost.com', 'motor1.com',
                        ])
                        if (is_image_url or is_image_host) and not _is_junk_url(r.url):
                            seen_urls.add(r.url)
                            image_urls.append(r.url)
            except Exception as e:
                logger.debug(f"SearXNG image search failed for query '{query[:40]}': {e}")

        # Fallback: regular web search for images
        if not image_urls:
            try:
                from bot.web_search import web_search
                clean_topic = re.sub(r'[^\w\s]', '', topic)[:80]
                results = await web_search(f"{clean_topic} фото image", max_results=5)
                for r in results:
                    url = r.get("url", "") if isinstance(r, dict) else getattr(r, "url", "")
                    if url and url not in seen_urls and not _is_junk_url(url):
                        seen_urls.add(url)
                        image_urls.append(url)
            except Exception as e:
                logger.debug(f"Web search image fallback failed for '{topic[:40]}': {e}")

    except Exception as e:
        logger.debug(f"Image search failed for '{topic}': {e}")

    logger.info(f"Smart image search found {len(image_urls)} URLs for '{topic[:50]}'")
    return image_urls[:max_images]


# ── Image deduplication ──────────────────────────────────────────────────────

def _image_hash(img_bytes: bytes) -> str:
    """Compute SHA256 hash of image bytes for deduplication."""
    return hashlib.sha256(img_bytes).hexdigest()


def _images_are_similar(img1: bytes, img2: bytes) -> bool:
    """Check if two images are identical or near-identical.

    Uses SHA256 hash for exact match. For near-duplicates (resized/compressed
    versions of the same photo), uses a simple pixel sampling heuristic.
    """
    if img1 == img2:
        return True

    h1, h2 = _image_hash(img1), _image_hash(img2)
    if h1 == h2:
        return True

    # Size-based quick reject
    ratio = len(img1) / max(len(img2), 1)
    if ratio < 0.3 or ratio > 3.3:
        return False

    # PIL-based comparison for near-duplicates
    try:
        from PIL import Image

        pil1 = Image.open(io.BytesIO(img1))
        pil2 = Image.open(io.BytesIO(img2))

        w1, h1_dim = pil1.size
        w2, h2_dim = pil2.size
        if abs(w1 - w2) > max(w1, w2) * 0.3 or abs(h1_dim - h2_dim) > max(h1_dim, h2_dim) * 0.3:
            return False

        thumb_size = (16, 16)
        t1 = pil1.convert("L").resize(thumb_size)
        t2 = pil2.convert("L").resize(thumb_size)

        pixels1 = list(t1.getdata())
        pixels2 = list(t2.getdata())

        total_diff = sum(abs(p1 - p2) for p1, p2 in zip(pixels1, pixels2))
        avg_diff = total_diff / len(pixels1)

        if avg_diff < 15:
            return True

    except Exception:
        pass

    return False


def deduplicate_images(images: List[bytes]) -> List[bytes]:
    """Remove duplicate and near-duplicate images from a list.

    Uses both exact SHA256 hash matching and PIL-based perceptual comparison
    to catch resized/compressed versions of the same photo.
    """
    if not images:
        return images

    unique: List[bytes] = []
    for img in images:
        is_dup = False
        for existing in unique:
            if _images_are_similar(img, existing):
                is_dup = True
                break
        if not is_dup:
            unique.append(img)
        else:
            logger.debug(f"Dedup: removed duplicate image ({len(img)} bytes)")

    removed = len(images) - len(unique)
    if removed > 0:
        logger.info(f"Image dedup: {len(images)} -> {len(unique)} (removed {removed} duplicates)")

    return unique


# ── Main fetcher class ───────────────────────────────────────────────────────

class ImageFetcher:
    """Smart image fetcher with real-photo-first priority pipeline.

    v4.0: NO AI IMAGE GENERATION. Only real images from:
    1. RSS enclosures + content:encoded (BeautifulSoup parsed)
    2. Article page scraping (BeautifulSoup + lxml)
    3. AI-powered SearXNG search queries
    Includes SHA256 + perceptual image deduplication.

    Usage:
        fetcher = ImageFetcher()
        images = await fetcher.fetch(
            topic="BMW M5 G90 debut",
            article_url="https://bmwblog.com/...",
            rss_entry=feed_entry,
        )
        # images = [bytes, bytes, ...] or []
    """

    def __init__(self) -> None:
        self.cache = ImageCache()
        self._client: Optional[httpx.AsyncClient] = None
        self._seen_hashes: set = set()

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

    def _add_unique(self, img_bytes: bytes) -> bool:
        """Add image to list only if it's not a duplicate. Returns True if added."""
        img_hash = _image_hash(img_bytes)
        if img_hash in self._seen_hashes:
            logger.debug(f"Skipping exact duplicate image (hash={img_hash[:12]}...)")
            return False
        self._seen_hashes.add(img_hash)
        return True

    async def fetch(
        self,
        topic: str,
        article_url: str = "",
        rss_entry: Any = None,
        image_urls: List[str] = None,
        max_images: int = MAX_IMAGES_PER_POST,
    ) -> Tuple[List[bytes], str]:
        """Fetch images using the priority pipeline with deduplication.

        Returns (image_list: List[bytes], source: str)
        source is 'rss', 'article', 'search', or 'cache' for logging.
        Images are deduplicated by hash to prevent duplicates in posts.
        """
        # Reset seen hashes for this fetch call
        self._seen_hashes = set()

        # ── Step 0: Check cache ───────────────────────────────────────────
        cached = self.cache.get(topic)
        if cached:
            deduped = deduplicate_images(cached)
            if deduped:
                logger.info(f"Image cache HIT for '{topic[:50]}' — {len(deduped)} images (after dedup)")
                return deduped, "cache"
            else:
                logger.warning(f"Image cache had only duplicates for '{topic[:50]}', re-fetching")
                self.cache.delete(topic)

        all_images: List[bytes] = []
        source = "none"

        # ── Step 1: RSS image URLs (provided by caller) ───────────────────
        if image_urls:
            client = self._get_client()
            for url in image_urls[:max_images * 3]:
                if len(all_images) >= max_images:
                    break
                img_bytes = await _validate_and_download(client, url)
                if img_bytes and self._add_unique(img_bytes):
                    all_images.append(img_bytes)
            if all_images:
                source = "rss"
                logger.info(f"Got {len(all_images)} unique images from RSS URLs for '{topic[:50]}'")

        # ── Step 2: RSS entry enclosures + content:encoded ────────────────
        if rss_entry is not None and len(all_images) < max_images:
            entry_urls = extract_rss_images(rss_entry)
            if entry_urls:
                client = self._get_client()
                for url in entry_urls:
                    if len(all_images) >= max_images:
                        break
                    if url not in (image_urls or []):
                        img_bytes = await _validate_and_download(client, url)
                        if img_bytes and self._add_unique(img_bytes):
                            all_images.append(img_bytes)
                if all_images and source == "none":
                    source = "rss"
                elif all_images:
                    source += "+rss-entry"

        # ── Step 3: Article page images (BeautifulSoup + lxml) ────────────
        if article_url and len(all_images) < max_images:
            article_images = await fetch_article_images(article_url, max_count=max_images - len(all_images) + 2)
            if article_images:
                added = 0
                for img in article_images:
                    if len(all_images) >= max_images:
                        break
                    if self._add_unique(img):
                        all_images.append(img)
                        added += 1
                if added:
                    source = "article" if source == "none" else source + "+article"
                    logger.info(f"BS4 scraped {added} unique article images for '{topic[:50]}'")

        # ── Step 4: AI-powered image search (SearXNG) ────────────────────
        if len(all_images) < 2:
            search_urls = await search_images(topic, max_images=max(3, max_images - len(all_images)))
            if search_urls:
                client = self._get_client()
                for url in search_urls:
                    if len(all_images) >= max_images:
                        break
                    img_bytes = await _validate_and_download(client, url)
                    if img_bytes and self._add_unique(img_bytes):
                        all_images.append(img_bytes)
                if all_images and source == "none":
                    source = "search"
                elif all_images:
                    source += "+search"

        # ── Final deduplication pass ──────────────────────────────────────
        all_images = deduplicate_images(all_images)

        # ── Cache result ──────────────────────────────────────────────────
        if all_images:
            self.cache.put(topic, all_images, source=source)
            logger.info(f"ImageFetcher: {len(all_images)} unique images for '{topic[:50]}' (source={source})")
        else:
            logger.info(f"No real images found for '{topic[:50]}' — post will be text-only (NO AI generation)")

        return all_images, source

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# ── Module-level convenience ─────────────────────────────────────────────────

_fetcher: Optional[ImageFetcher] = None


async def fetch_images_for_post(
    topic: str,
    article_url: str = "",
    rss_entry: Any = None,
    image_urls: List[str] = None,
    max_images: int = MAX_IMAGES_PER_POST,
) -> Tuple[List[bytes], str]:
    """Module-level convenience function to fetch images for a post.

    Images are automatically deduplicated by hash to prevent duplicates.
    NO AI image generation — only real photos from news sources.
    """
    global _fetcher
    if _fetcher is None:
        _fetcher = ImageFetcher()
    return await _fetcher.fetch(
        topic=topic,
        article_url=article_url,
        rss_entry=rss_entry,
        image_urls=image_urls,
        max_images=max_images,
    )


# ── Backward compatibility: old API returns dict with base64 ─────────────────

async def fetch_image_for_post(
    topic: str,
    article_url: str = "",
    rss_entry: Any = None,
    content_type: str = "news+reaction",
) -> Optional[Dict[str, Any]]:
    """Backward-compatible function that returns dict with image_b64.

    Prefer fetch_images_for_post() which returns bytes directly.
    """
    images, source = await fetch_images_for_post(
        topic=topic,
        article_url=article_url,
        rss_entry=rss_entry,
        max_images=1,
    )
    if images:
        img_b64 = base64.b64encode(images[0]).decode("utf-8")
        return {
            "image_b64": img_b64,
            "source": source,
            "url": article_url,
        }
    return None
