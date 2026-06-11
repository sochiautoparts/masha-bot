"""
Channel Manager -- Posts to @bmw_mpower_club with BMW-themed formatting.
Handles news posts, partner posts, scheduled content, reactions,
media, polls, and internet news search.
Properly enforces Telegram character limits: 1024 with media, 4096 without.

v2.0 KEY CHANGES:
- PRIORITIZE real photos from news sources (up to 10 per post)
- Enhanced article scraping (og:image + twitter:image + article body <img>)
- Web search image enrichment BEFORE AI generation
- Allow text-only posts as last resort (channel silence is worse)
- Strip "🔥 Мнение Маши" banned headings from post text
- Support up to 10 media files per Telegram post
"""

import logging
import time
import random
import asyncio
import tempfile
import os
import re
import hashlib
from urllib.parse import quote
import httpx
from typing import Optional, List, Dict, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile, ReactionTypeEmoji, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from bot.config import config, persona
from bot.database import (
    add_channel_post, get_today_post_count, get_hourly_post_count, get_unposted_news,
    mark_news_posted, add_partner_post, get_today_partner_post_count,
    is_duplicate_post, add_post_fingerprint, cleanup_old_fingerprints,
    get_recent_post_titles, DB_PATH,
)
from ai.router import get_ai_router
from bot.partners import partner_manager
from bot.web_search import web_search, search_news, SearchResult
from bot.content_engine import (
    get_best_news_item, enrich_with_search_images, get_date_context,
    _is_topic_covered, _extract_entities, _score_interest,
    _register_topic, get_editorial_aside, get_translation_uniquification_hint,
)

logger = logging.getLogger("masha.channel")

# ── Reactions to add to posts ───────────────────────────────────────────────

POST_REACTIONS = ["👍", "🔥", "🏎️", "😍", "👏", "💯", "⚡", "///M"]

# ── How many images per news post ───────────────────────────────────────────
# Telegram allows up to 10 media per post.
# We aim for rich visual posts with multiple relevant images from news sources.
NEWS_IMAGES_MIN = 2
NEWS_IMAGES_MAX = 10
MAX_IMAGES_PER_POST = 10
MAX_RSS_IMAGES = 10
MAX_SCRAPE_IMAGES = 10
MAX_SEARCH_IMAGES = 5

# ── Poll topics for channel engagement — BMW-themed ──────────────────────────

POLL_TEMPLATES = [
    "Что думаете, бимеры?",
    "M Power или нет?",
    "Ваше мнение?",
    "///M — за или против?",
    "Что скажет редакция?",
    "Баварский опрос!",
    "Голосуем, M-энтузиасты!",
]

# Moscow timezone
_MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# ── Keyword-based semantic dedup ────────────────────────────────────────────
_recent_post_keywords: list = []
_MAX_RECENT_POSTS = 100

_SEMANTIC_STOP_WORDS = frozenset([
    "в", "на", "с", "о", "у", "по", "из", "за", "от", "до", "к", "не", "и", "но",
    "а", "что", "как", "это", "тот", "этот", "для", "при", "через", "между",
    "после", "перед", "без", "под", "над", "об", "со", "то", "же", "ли", "бы",
    "уже", "ещё", "еще", "также", "тоже", "или", "либо", "год", "могут", "будет",
    "стал", "стала", "был", "была", "есть", "может", "очень", "так", "где", "когда",
])

# BMW-specific core words for semantic dedup
_BMW_CORE_WORDS = frozenset([
    "bmw", "бмв", "бимер", "баварец", "m5", "m3", "m4", "m2", "m8",
    "x5", "x3", "x6", "x7", "x4", "x1",
    "s63", "s58", "s55", "b58", "n55", "n54", "n63", "s68",
    "vanos", "valvetronic", "xdrive",
    "alpina", "mpower", "competition",
    "f90", "g90", "g80", "g82", "g87", "f80", "f82",
    "e39", "e46", "e60", "e90",
    # General
    "reveal", "launch", "debut", "unveil", "release", "announce",
    "recall", "отзыв", "запрет", "record", "рекорд",
    "авария", "слияни", "банкрот", "рестайлинг", "facelift",
    "премьера", "запуск", "дебют", "анонс", "представлен",
    "скандал", "scandal", "проблем", "продаж", "цена",
    "porsche", "mercedes", "audi", "ferrari", "tesla",
    "тюнинг", "tuning", "электромобиль", "electric",
])


def _is_semantically_duplicate(title: str) -> bool:
    """Check if 3+ significant words from title match a recently posted title."""
    global _recent_post_keywords

    words = re.findall(r'[a-zа-яё]{3,}', title.lower())
    significant = [w for w in words if w not in _SEMANTIC_STOP_WORDS]

    if len(significant) < 2:
        return False

    core_words = [w for w in significant if w in _BMW_CORE_WORDS]

    for recent_words in _recent_post_keywords:
        matches = sum(1 for w in significant if w in recent_words)
        if matches >= 3:
            return True

        if len(core_words) >= 2:
            recent_core = [w for w in recent_words if w in _BMW_CORE_WORDS]
            core_matches = sum(1 for w in core_words if w in recent_core)
            if core_matches >= 2:
                return True

    return False


def _record_post_title(title: str):
    """Record a posted title's significant words for semantic dedup."""
    global _recent_post_keywords
    words = re.findall(r'[a-zа-яё]{3,}', title.lower())
    significant = [w for w in words if w not in _SEMANTIC_STOP_WORDS]
    _recent_post_keywords.append(significant)
    if len(_recent_post_keywords) > _MAX_RECENT_POSTS:
        _recent_post_keywords = _recent_post_keywords[-_MAX_RECENT_POSTS:]


def _clean_post_text(text: str) -> str:
    """Clean post text: remove markdown, formatting artifacts, AI meta-comments,
    and BANNED headings like '🔥 Мнение Маши'."""
    if not text:
        return text

    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'data:\s*\{[^}]*\}', '', text)
    text = re.sub(r'\[DONE\]', '', text)

    for phrase in ["As an AI", "Как AI", "Как искусственный интеллект",
                   "powered by pollinations", "pollinations.ai"]:
        text = re.sub(rf'.*{re.escape(phrase)}.*', '', text, flags=re.IGNORECASE)

    text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)

    meta_comment_patterns = [
        r'[^\n]*тему\s+в\s+канал\s+не\s+ставим[^\n]*',
        r'[^\n]*не\s+наш\s+формат[^\n]*',
        r'[^\n]*перепишу\s+тему[^\n]*',
        r'[^\n]*дубликат[^\n]*',
        r'[^\n]*already\s+(posted|published|covered)[^\n]*',
        r'[^\n]*do\s+not\s+(publish|post)[^\n]*',
    ]
    for pattern in meta_comment_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    # ── Remove banned opening headings that AI sometimes ignores instructions about ──
    # These are EXPLICITLY FORBIDDEN in prompts but AI sometimes generates them anyway
    _banned_openings = [
        "🔥 Мнение Маши",
        "Мнение Маши",
        "🔥 Мнение редакции",
        "Мнение редакции",
        "🔥 Мнение",
        "Мнение Маши (с сарказмом и BMW-экспертизой)",
    ]
    for banned in _banned_openings:
        # Match opening at start of text, possibly with emoji prefix, dashes, colons
        pattern = rf'^[\s]*[-–—]?\s*(?:\S+\s+)?{re.escape(banned)}[^\n]*\n*'
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        # Also match anywhere in text if on its own line
        pattern = rf'\n[\s]*[-–—]?\s*(?:\S+\s+)?{re.escape(banned)}[^\n]*\n*'
        text = re.sub(pattern, '\n', text, flags=re.IGNORECASE)

    for prefix in ["Маша:", "Masha:", "Assistant:"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

    formal_phrases = [
        ("Редакция сообщает:", ""), ("Редакция сообщает —", ""),
        ("Редакция сообщает", ""),
        ("Редакция @bmw_mpower_club сообщает:", ""),
        ("Редакция @bmw_mpower_club сообщает", ""),
    ]
    for phrase, replacement in formal_phrases:
        if phrase in text:
            text = text.replace(phrase, replacement)

    _editorial_trigger_phrases = [
        "не ставим", "не наш формат",
        "перепишу тему", "напишу готовый",
        "не для публикации", "внутренняя заметка",
        "для редакции", "редакционная",
    ]
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        line_lower = line.lower().strip()
        is_editorial = False
        for trigger in _editorial_trigger_phrases:
            if trigger in line_lower:
                is_editorial = True
                break
        if not is_editorial:
            cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines)

    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    return text


def _validate_post_text(text: str) -> bool:
    """Validate post text before sending to channel."""
    if not text or not text.strip():
        return False

    text_lower = text.lower()

    # Block SSE artifacts
    sse_patterns = [r'data:\s*\{', r'\[DONE\]']
    for pattern in sse_patterns:
        if re.search(pattern, text_lower):
            return False

    # Block API errors
    error_patterns = ["authentication error", "no api key", "model not found",
                      "rate limit", "internal server error", "bad request"]
    for pattern in error_patterns:
        if pattern in text_lower:
            return False

    # Block provider ad artifacts
    ad_patterns = ["pollinations.ai", "powered by pollinations"]
    for pattern in ad_patterns:
        if pattern in text_lower:
            return False

    # Block raw JSON
    if text.strip().startswith(('{', '[', '```', 'data:')):
        return False

    # Block editorial leakage
    duplicate_indicator_phrases = [
        "тему в канал не ставим", "не наш формат", "перепишу тему",
        "дубликат", "это повтор", "я не буду публиковать",
        "already posted", "already published", "do not publish",
    ]
    for phrase in duplicate_indicator_phrases:
        if phrase in text_lower:
            logger.warning(f"Post BLOCKED (duplicate indicator '{phrase}')")
            return False

    # Block political/war content
    blocked_keywords = [
        "путин", "кремль", "госдума", "президент росс",
        "сво ", "специальная военная", "мобилизац", "санкци",
        "украин", "нато", "nato",
        "навальн", "оппозиц", "протест", "митинг",
        "политик", "депутат", "законопроект", "выборы ", "голосован",
    ]
    blocked_auto_brands = [
        "автоваз", "лада", "lada", "уаз", "uaz", "камаз", "kamaz",
        "соллерс", "vesta", "granta", "niva",
    ]
    for keyword in blocked_keywords:
        if keyword in text_lower:
            logger.warning(f"Post BLOCKED (keyword '{keyword}')")
            return False
    for keyword in blocked_auto_brands:
        if keyword in text_lower:
            logger.warning(f"Post BLOCKED (non-BMW brand '{keyword}')")
            return False

    # AUTO-RELEVANCE CHECK — BMW-focused
    _auto_required_keywords = [
        # BMW-specific
        "bmw", "бмв", "бимер", "баварец", "///m", "m power", "mpower",
        "m5", "m3", "m4", "m2", "m8", "x5", "x3", "x6", "x7",
        "s63", "s58", "s55", "b58", "n55", "n54", "s68",
        "vanos", "valvetronic", "xdrive", "alpina",
        "bimmercode", "ista", "realoem",
        # General auto
        "авто", "автомобиль", "машина", "мотор", "двигатель", "кузов", "салон",
        "транспорт", "запчас", "ремонт", "сервис", "шин", "колес",
        "бензин", "дизел", "электромобиль", "гибрид",
        "продаж", "авторынок", "автосалон", "дилер",
        "тест-драйв", "обзор", "концепт", "рестайлинг",
        "гонк", "ралли", "формул", "F1",
        # Car brand names
        "Mercedes", "Audi", "Porsche", "Ferrari", "Lamborghini",
        "Tesla", "Volkswagen", "Toyota", "Honda", "Lexus",
        "car", "auto", "vehicle", "motor", "engine",
        "SUV", "sedan", "coupe", "EV", "PHEV",
        "recall", "redesign", "launch", "debut",
    ]
    has_auto_keyword = any(kw.lower() in text_lower for kw in _auto_required_keywords)
    if not has_auto_keyword:
        logger.warning(f"Post BLOCKED (no auto-relevant keywords)")
        return False

    return True


def _validate_post_text_partner(text: str) -> bool:
    """Validate partner post text — RELAXED version."""
    if not text or not text.strip():
        return False

    text_lower = text.lower()

    sse_patterns = [r'data:\s*\{', r'\[DONE\]']
    for pattern in sse_patterns:
        if re.search(pattern, text_lower):
            return False

    error_patterns = ["authentication error", "no api key", "model not found",
                      "rate limit", "internal server error"]
    for pattern in error_patterns:
        if pattern in text_lower:
            return False

    ad_patterns = ["pollinations.ai", "powered by pollinations"]
    for pattern in ad_patterns:
        if pattern in text_lower:
            return False

    if text.strip().startswith(('{', '[', '```', 'data:')):
        return False

    return True


def _ensure_footer(text: str) -> str:
    """Ensure post has proper footer matching @bmw_mpower_club format."""
    text = re.sub(r'\n*Автор\s+@asmasha_bot', '', text)
    text = re.sub(r'\n*@bmw_mpower_club', '', text)
    text = re.sub(r'\n*#bmw_mpower_club', '', text)
    text = text.rstrip()
    text += "\n\nАвтор @asmasha_bot\n@bmw_mpower_club\n#bmw_mpower_club"
    return text


def _enforce_char_limit(text: str, has_media: bool) -> str:
    """Smart character limit enforcement — always preserves footer."""
    footer = "\n\nАвтор @asmasha_bot\n@bmw_mpower_club\n#bmw_mpower_club"
    char_limit = config.TELEGRAM_CAPTION_LIMIT if has_media else config.TELEGRAM_TEXT_LIMIT

    if len(text) <= char_limit:
        return text

    content = text
    for foot_part in ["\n\nАвтор @asmasha_bot", "\n@bmw_mpower_club", "\n#bmw_mpower_club"]:
        content = content.replace(foot_part, "")
    content = content.rstrip()

    max_content = char_limit - len(footer)
    if max_content < 100:
        return footer.lstrip('\n')

    if len(content) > max_content:
        content = content[:max_content - 3] + "..."

    return content + footer


class ChannelManager:
    """Manages posting to the @bmw_mpower_club channel."""

    def __init__(self):
        self._bot: Optional[Bot] = None
        self._last_post_time: float = 0
        self._last_partner_time: float = 0
        self._last_poll_time: float = 0
        self._poll_count: int = 0
        self._post_model_index: int = 0
        self._semantic_loaded: bool = False

    _CONTENT_MODELS_ROTATION = [
        "openai-large", "mistral-large", "deepseek",
        "openai", "llama", "mistral", "deepseek-r1",
        "qwen-coder", "llama-scale", "searchgpt",
    ]

    def set_bot(self, bot: Bot) -> None:
        """Set the bot instance for sending messages."""
        self._bot = bot

    async def load_recent_semantic_data(self) -> None:
        """Load recently posted titles from DB into in-memory semantic dedup."""
        if self._semantic_loaded:
            return
        try:
            titles = await get_recent_post_titles(hours=72, limit=50)
            for title in titles:
                _record_post_title(title)
            self._semantic_loaded = True
            logger.info(f"Loaded {len(titles)} recent post titles into semantic dedup")
        except Exception as e:
            logger.warning(f"Could not load recent post titles: {e}")

    async def _add_reaction(self, chat_id, message_id: int) -> None:
        """Add a reaction to a post."""
        try:
            emoji = random.choice(POST_REACTIONS)
            await self._bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
            logger.info(f"Added reaction {emoji} to message {message_id}")
        except Exception as e:
            logger.debug(f"Could not add reaction: {e}")

    async def _download_news_images(self, image_urls: List[str], max_count: int = 3) -> List[bytes]:
        """Download real images from news source URLs.
        
        Tries each URL, downloads only valid content images.
        Filters out: icons, logos, banners, buttons, social media, tracking pixels,
        and images with abnormal dimensions (too wide/narrow = banners/ads).
        Returns list of image data bytes.
        """
        images = []
        if not image_urls:
            return images

        MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB

        for url in image_urls[:max_count * 3]:
            if len(images) >= max_count:
                break

            if self._is_junk_image_url(url):
                continue

            try:
                async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                    response = await client.get(url, headers={
                        "User-Agent": "MashaBot/1.0 (+https://t.me/asmasha_bot)",
                    })
                    if response.status_code != 200:
                        continue

                    content = response.content
                    content_type = response.headers.get("content-type", "")

                    # Validate: must be an image and at least 3KB
                    if len(content) < 3000:
                        continue

                    if len(content) > MAX_IMAGE_SIZE:
                        continue

                    # Skip SVG
                    if b'<svg' in content[:500] or 'svg' in content_type:
                        continue

                    if not any(ft in content_type for ft in ["image/jpeg", "image/png", "image/webp", "image/gif"]):
                        if content[:3] == b'\xff\xd8\xff' or content[:4] == b'\x89PNG':
                            pass
                        elif content[:4] == b'RIFF' and content[8:12] == b'WEBP':
                            pass
                        elif content[:6] in (b'GIF87a', b'GIF89a'):
                            pass
                        else:
                            continue

                    if not self._is_content_image(content):
                        continue

                    images.append(content)
                    logger.info(f"Downloaded news image: {url[:80]} ({len(content)} bytes)")

            except Exception as e:
                logger.debug(f"Failed to download image {url[:50]}: {e}")
                continue

        logger.info(f"Downloaded {len(images)} real images from news")
        return images

    @staticmethod
    def _is_junk_image_url(url: str) -> bool:
        """Check if an image URL is likely non-content.
        
        NOTE: Intentionally NOT filtering 'crop', 'resize', 'scaled', 'preview'
        because WordPress and other CMS use these in URLs for full-size images too.
        """
        url_lower = url.lower()
        junk_keywords = [
            "icon", "logo", "favicon", "avatar", "badge", "button", "btn",
            "spinner", "loading", "placeholder", "pixel", "tracker",
            "analytics", "share", "facebook", "twitter", "vk.",
            "telegram", "whatsapp", "instagram", "youtube", "tiktok",
            "ad.", "ads/", "advert", "sponsor",
            "emoji", "smileys", "captcha", "recaptcha",
            "1x1", "spacer", "blank", "transparent", "dot.",
            "watermark",
        ]
        for kw in junk_keywords:
            if kw in url_lower:
                return True

        # Skip URLs with very small size indicators
        size_pattern = re.compile(r'[/=_x](\d{1,3})x(\d{1,3})[/._]')
        size_match = size_pattern.search(url_lower)
        if size_match:
            w, h = int(size_match.group(1)), int(size_match.group(2))
            if w < 100 or h < 100:
                return True

        return False

    @staticmethod
    def _is_content_image(image_data: bytes) -> bool:
        """Validate that image data represents a proper content photo.
        
        If PIL (Pillow) is available, checks dimensions and aspect ratio.
        If PIL is not installed (e.g. GitHub Actions), falls back to minimum
        file size check (3KB) as a reasonable proxy for content images.
        """
        if len(image_data) < 3000:
            return False
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_data))
            width, height = img.size
            if width < 400 or height < 300:
                return False
            if width / max(height, 1) > 3.0:
                return False
            if height / max(width, 1) > 3.0:
                return False
            if width * height < 120000:
                return False
            return True
        except ImportError:
            # PIL not available — fall back to file size check only
            logger.debug("PIL not available, using file-size fallback for image validation")
            return len(image_data) >= 3072  # 3KB minimum
        except Exception:
            return True

    async def _fetch_pexels_images(self, query: str, max_count: int = 2) -> List[bytes]:
        """Fetch images from Pexels API (free, 200 req/hour). Requires PEXELS_API_KEY."""
        images = []
        api_key = os.environ.get("PEXELS_API_KEY", "")
        if not api_key:
            return images

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(
                    "https://api.pexels.com/v1/search",
                    params={"query": query, "per_page": max_count, "locale": "ru-RU"},
                    headers={"Authorization": api_key},
                )
                if response.status_code == 200:
                    data = response.json()
                    for photo in data.get("photos", [])[:max_count]:
                        img_url = photo.get("src", {}).get("large", "")
                        if not img_url:
                            img_url = photo.get("src", {}).get("medium", "")
                        if img_url:
                            img_resp = await client.get(img_url)
                            if img_resp.status_code == 200 and len(img_resp.content) > 3000:
                                if self._is_content_image(img_resp.content):
                                    images.append(img_resp.content)
                                    logger.info(f"Pexels image downloaded: {img_url[:60]} ({len(img_resp.content)} bytes)")
        except Exception as e:
            logger.debug(f"Pexels image fetch error: {e}")
        return images

    async def _fetch_pixabay_images(self, query: str, max_count: int = 2) -> List[bytes]:
        """Fetch images from Pixabay API (free, 5000 req/hour). Requires PIXABAY_API_KEY."""
        images = []
        api_key = os.environ.get("PIXABAY_API_KEY", "")
        if not api_key:
            return images

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(
                    "https://pixabay.com/api/",
                    params={
                        "key": api_key,
                        "q": query,
                        "image_type": "photo",
                        "per_page": max_count,
                        "category": "transportation",
                        "min_width": 800,
                        "min_height": 600,
                        "safesearch": "true",
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    for hit in data.get("hits", [])[:max_count]:
                        img_url = hit.get("largeImageURL", "") or hit.get("webformatURL", "")
                        if img_url:
                            img_resp = await client.get(img_url)
                            if img_resp.status_code == 200 and len(img_resp.content) > 3000:
                                if self._is_content_image(img_resp.content):
                                    images.append(img_resp.content)
                                    logger.info(f"Pixabay image downloaded: {img_url[:60]} ({len(img_resp.content)} bytes)")
        except Exception as e:
            logger.debug(f"Pixabay image fetch error: {e}")
        return images

    async def _fetch_wikimedia_images(self, query: str, max_count: int = 2) -> List[bytes]:
        """Fetch images from Wikimedia Commons (free, no API key needed)."""
        images = []
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                search_resp = await client.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": query,
                        "srnamespace": "6",
                        "format": "json",
                        "srlimit": max_count * 3,
                    },
                )
                if search_resp.status_code != 200:
                    return images

                search_data = search_resp.json()
                titles = []
                for item in search_data.get("query", {}).get("search", []):
                    title = item.get("title", "")
                    if title and "File:" in title:
                        titles.append(title)

                if not titles:
                    return images

                image_info_resp = await client.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        "action": "query",
                        "titles": "|".join(titles[:5]),
                        "prop": "imageinfo",
                        "iiprop": "url|size",
                        "iiurlwidth": 1200,
                        "format": "json",
                    },
                )
                if image_info_resp.status_code != 200:
                    return images

                info_data = image_info_resp.json()
                pages = info_data.get("query", {}).get("pages", {})
                image_urls = []
                for page_id, page_data in pages.items():
                    if page_id == "-1":
                        continue
                    image_info = page_data.get("imageinfo", [])
                    for info in image_info:
                        url = info.get("thumburl", "") or info.get("url", "")
                        width = info.get("width", 0)
                        height = info.get("height", 0)
                        if url and width >= 600 and height >= 400 and "svg" not in url.lower():
                            image_urls.append(url)

                for img_url in image_urls[:max_count]:
                    try:
                        img_resp = await client.get(img_url)
                        if img_resp.status_code == 200 and len(img_resp.content) > 5000:
                            if self._is_content_image(img_resp.content):
                                images.append(img_resp.content)
                                logger.info(f"Wikimedia image downloaded: {img_url[:60]} ({len(img_resp.content)} bytes)")
                    except Exception:
                        continue

        except Exception as e:
            logger.debug(f"Wikimedia image fetch error: {e}")
        return images

    async def _fetch_stock_bmw_images(self) -> List[bytes]:
        """Fetch a BMW stock photo from reliable public URLs as ultimate fallback."""
        images = []
        
        bmw_prompts = [
            "BMW M5 F90 Competition, front three-quarter view, professional automotive photography, dramatic lighting, 4k, no text",
            "BMW M3 G80, side profile, studio lighting, M Performance, no text",
            "BMW X5 M Competition, dynamic shot, professional car photography, no text",
        ]
        prompt = random.choice(bmw_prompts)
        seed = random.randint(1, 999999)
        
        try:
            encoded_prompt = quote(prompt, safe="")
            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            params = {
                "width": 1024,
                "height": 768,
                "model": "flux",
                "nologo": "true",
                "enhance": "true",
                "seed": seed,
            }
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                response = await client.get(url, params=params)
                if response.status_code == 200 and len(response.content) > 5000:
                    if self._is_content_image(response.content):
                        images.append(response.content)
                        logger.info(f"Stock BMW image from Pollinations: seed={seed} ({len(response.content)} bytes)")
                        return images
        except Exception as e:
            logger.debug(f"Stock Pollinations image failed: {e}")
        
        # Public BMW press images
        stock_urls = [
            "https://www.bmw.com/content/dam/bmw/marketBMWCOM/bmw_com/categories/automotive-life/bmw-m-1000-xr/bmw-m-1000-xr-stage-teaser-hd.jpg",
            "https://www.bmw.com/content/dam/bmw/marketBMWCOM/bmw_com/categories/m/m-automobiles/bmw-m3-cs-stage-teaser-hd.jpg",
            "https://www.bmw.com/content/dam/bmw/marketBMWCOM/bmw_com/categories/m/m-automobiles/bmw-m5-stage-teaser-hd.jpg",
        ]
        
        for url in stock_urls:
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    response = await client.get(url, headers={
                        "User-Agent": "MashaBot/1.0 (+https://t.me/asmasha_bot)",
                    })
                    if response.status_code == 200 and len(response.content) > 5000:
                        if self._is_content_image(response.content):
                            images.append(response.content)
                            logger.info(f"Stock BMW image downloaded from URL ({len(response.content)} bytes)")
                            return images
            except Exception:
                continue
        
        return images

    async def _generate_post_images(self, news_title: str, count: int = 1) -> List[bytes]:
        """Generate images for a news post using AI with full fallback chain.

        Tries Pollinations (gen→legacy→retry) then Cloudflare Workers AI (SDXL).
        Returns list of image bytes (may be empty if all fail).
        LIMITED to max 2 model attempts to avoid timeout/OOM on GitHub Actions.
        """
        images = []
        prompts = [
            f"BMW M5 F90 professional automotive photography: {news_title}. "
            f"Front three-quarter view, vibrant colors, high quality, dramatic lighting, no text.",
            f"BMW automotive news illustration: {news_title}. "
            f"Side profile shot, studio lighting, sleek design, M Power styling, no text.",
        ]
        selected_prompts = prompts[:min(count, len(prompts))]

        _IMAGE_MODELS = ["flux", "flux-pro"]
        attempts = 0
        max_attempts = 2

        for i, prompt in enumerate(selected_prompts):
            for img_model in _IMAGE_MODELS:
                attempts += 1
                if attempts > max_attempts:
                    break
                try:
                    image_data = await asyncio.wait_for(
                        get_ai_router()._primary.generate_image(prompt, model=img_model),
                        timeout=60.0
                    )
                    if image_data:
                        images.append(image_data)
                        break
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.debug(f"Image gen #{i+1} with {img_model} failed: {e}")
                    try:
                        image_data = await asyncio.wait_for(
                            get_ai_router()._primary.generate_image_free(prompt, model=img_model),
                            timeout=60.0
                        )
                        if image_data:
                            images.append(image_data)
                            break
                    except Exception:
                        pass
                    continue
            if images:
                break
            if attempts >= max_attempts:
                break

        logger.info(f"Generated {len(images)}/{count} AI images for post ({attempts} attempts)")
        return images

    async def _scrape_article_images(self, article_url: str, max_count: int = 10) -> List[bytes]:
        """Scrape images from a news article page.
        
        Extracts images from multiple sources in priority order:
        1. og:image meta tags (usually the main article image)
        2. twitter:image meta tags
        3. JSON-LD structured data (schema.org image field)
        4. <picture>/<source srcset> elements
        5. <img> tags from article body areas (src + data-src for lazy loading)
        
        Returns list of image data bytes (up to max_count).
        """
        images = []
        try:
            # Use a realistic browser User-Agent — many news sites block bot-like UAs
            _SCRAPE_HEADERS = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, max_redirects=5) as client:
                response = await client.get(article_url, headers=_SCRAPE_HEADERS)
                if response.status_code != 200:
                    logger.debug(f"Scrape HTTP {response.status_code} for {article_url[:60]}")
                    return images

                html = response.text
                
                # 1. Extract og:image first (usually the main article image)
                og_images = re.findall(r'<meta[^>]+property=["\x27]og:image["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                og_images += re.findall(r'<meta[^>]+content=["\x27]([^"\x27]+)["\x27][^>]+property=["\x27]og:image["\x27]', html, re.IGNORECASE)
                # Also og:image:url and og:image:secure_url
                og_images += re.findall(r'<meta[^>]+property=["\x27]og:image:url["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                og_images += re.findall(r'<meta[^>]+property=["\x27]og:image:secure_url["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                
                # 2. Extract twitter:image
                tw_images = re.findall(r'<meta[^>]+name=["\x27]twitter:image["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                tw_images += re.findall(r'<meta[^>]+content=["\x27]([^"\x27]+)["\x27][^>]+name=["\x27]twitter:image["\x27]', html, re.IGNORECASE)
                
                # 3. Extract images from JSON-LD structured data (schema.org)
                jsonld_images = self._extract_jsonld_images(html)
                
                # 4. Extract from <picture>/<source srcset> elements
                srcset_images = []
                # Find <picture> blocks and their <source srcset="...">
                picture_blocks = re.findall(r'<picture[^>]*>(.*?)</picture>', html, re.IGNORECASE | re.DOTALL)
                for block in picture_blocks:
                    srcsets = re.findall(r'srcset=["\x27]([^"\x27]+)["\x27]', block, re.IGNORECASE)
                    for srcset in srcsets:
                        # srcset can have multiple URLs with descriptors: "url1 1x, url2 2x"
                        for part in srcset.split(','):
                            url = part.strip().split()[0] if part.strip() else ''
                            if url:
                                srcset_images.append(url)
                
                # 5. Extract <img> tags — from article body areas
                article_html = ""
                for pattern in [r'<article[^>]*>(.*?)</article>', r'<main[^>]*>(.*?)</main>', r'<div[^>]+class=["\x27][^"\x27]*(?:content|article|post|entry)[^"\x27]*["\x27][^>]*>(.*?)</div>']:
                    matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
                    for match in matches:
                        article_html += match + "\n"
                
                # If no article body found, try all <img> tags as fallback
                search_html = article_html if article_html else html
                all_img_urls = re.findall(r'<img[^>]+src=["\x27]([^"\x27]+)["\x27]', search_html, re.IGNORECASE)
                # Also check data-src for lazy-loaded images
                lazy_img_urls = re.findall(r'<img[^>]+data-src=["\x27]([^"\x27]+)["\x27]', search_html, re.IGNORECASE)
                # And data-lazy-src
                lazy_img_urls += re.findall(r'<img[^>]+data-lazy-src=["\x27]([^"\x27]+)["\x27]', search_html, re.IGNORECASE)
                all_img_urls = lazy_img_urls + all_img_urls  # Lazy images first (often higher quality)
                
                # Prioritize: og:image > twitter:image > JSON-LD > srcset > article body images
                candidate_urls = []
                seen = set()
                for url_list in [og_images, tw_images, jsonld_images, srcset_images, all_img_urls]:
                    for url in url_list:
                        if url and url not in seen and len(url) > 10:
                            if url.startswith("//"):
                                url = "https:" + url
                            if not self._is_junk_image_url(url):
                                seen.add(url)
                                candidate_urls.append(url)
                
                logger.info(f"Scraped {len(candidate_urls)} candidate image URLs from {article_url[:60]}")
                images = await self._download_news_images(candidate_urls, max_count=max_count)

        except Exception as e:
            logger.debug(f"Article scraping failed: {e}")

        return images
    
    @staticmethod
    def _extract_jsonld_images(html: str) -> List[str]:
        """Extract image URLs from JSON-LD structured data in HTML.
        
        Many modern news sites use schema.org JSON-LD with 'image' field
        that contains high-quality article images. This is often the best
        source for article images, even better than og:image.
        """
        images = []
        try:
            # Find all JSON-LD blocks
            jsonld_blocks = re.findall(
                r'<script[^>]+type=["\x27]application/ld\+json["\x27][^>]*>(.*?)</script>',
                html, re.IGNORECASE | re.DOTALL
            )
            for block in jsonld_blocks:
                try:
                    import json
                    data = json.loads(block)
                    # Handle both single objects and arrays
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        # Extract image field — can be string, dict, or list
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
                except (json.JSONDecodeError, Exception):
                    continue
        except Exception:
            pass
        return images

    async def _get_post_images(self, news_item: Dict) -> tuple:
        """Get images for a news post — PRIORITIZE real photos from news sources.
        
        KEY CHANGE: Real photos from the actual news article are FAR more valuable
        than AI-generated images. We now aggressively fetch up to MAX_IMAGES_PER_POST (10)
        real photos from RSS feeds, article scraping, and web search BEFORE falling
        back to AI generation.

        Priority:
        1. RSS images (from news source) — up to MAX_RSS_IMAGES
        2. Article page scraping (og:image, twitter:image, article body) — up to MAX_SCRAPE_IMAGES
        3. Web search enrichment (find images for this specific news) — up to MAX_SEARCH_IMAGES
        4. Pexels API (free car photos, needs key)
        5. Pixabay API (free car photos, needs key)
        6. Wikimedia Commons (free, no key needed)
        7. AI generation — LAST RESORT
        8. Stock BMW photo fallback — ABSOLUTE LAST RESORT

        Returns (image_list: List[bytes], source: str)
        """
        image_list = []
        source = "none"
        title = news_item.get("title", "")

        # Extract BMW model for better image search
        bmw_query = "BMW car"
        title_lower = title.lower()
        for model in ["M5", "M3", "M4", "M2", "M8", "X5", "X3", "X6", "X7", "X4", "X1",
                       "i7", "i5", "i4", "iX", "Z4", "Alpina"]:
            if model.lower() in title_lower:
                bmw_query = f"BMW {model}"
                break

        # 1. Try RSS images — download up to MAX_RSS_IMAGES real photos
        rss_image_urls = news_item.get("image_urls", [])
        if rss_image_urls:
            try:
                rss_images = await self._download_news_images(
                    rss_image_urls,
                    max_count=MAX_RSS_IMAGES
                )
                if rss_images:
                    image_list.extend(rss_images)
                    source = "real"
                    logger.info(f"Using {len(rss_images)} real images from RSS for: {title[:50]}")
            except Exception as e:
                logger.warning(f"Failed to download RSS images: {e}")

        # 2. Try scraping article page for images — ALWAYS try if URL exists
        if news_item.get("url") and len(image_list) < MAX_IMAGES_PER_POST:
            try:
                scraped = await self._scrape_article_images(
                    news_item["url"],
                    max_count=MAX_SCRAPE_IMAGES
                )
                if scraped:
                    for img in scraped:
                        if len(image_list) >= MAX_IMAGES_PER_POST:
                            break
                        image_list.append(img)
                    source = "scraped" if source == "none" else source + "+scraped"
                    logger.info(f"Scraped {len(scraped)} images for: {title[:50]}")
            except Exception as e:
                logger.debug(f"Article scraping skipped: {e}")

        # 3. Web search enrichment — find MORE images for this specific news
        if len(image_list) < NEWS_IMAGES_MIN and title:
            try:
                search_image_urls = await enrich_with_search_images(title, max_images=5)
                if search_image_urls:
                    searched = await self._download_news_images(search_image_urls, max_count=MAX_SEARCH_IMAGES)
                    if searched:
                        for img in searched:
                            if len(image_list) >= MAX_IMAGES_PER_POST:
                                break
                            image_list.append(img)
                        source = "search" if source == "none" else source + "+search"
                        logger.info(f"Found {len(searched)} images via web search for: {title[:50]}")
            except Exception as e:
                logger.debug(f"Web search image enrichment skipped: {e}")

        # 4. Try Pexels API (free, high-quality car photos)
        if len(image_list) < 2:
            try:
                pexels_images = await self._fetch_pexels_images(bmw_query, max_count=2)
                if pexels_images:
                    image_list.extend(pexels_images)
                    source = "pexels" if source == "none" else source + "+pexels"
            except Exception as e:
                logger.debug(f"Pexels image fetch error: {e}")

        # 5. Try Pixabay API (free, large collection)
        if len(image_list) < 2:
            try:
                pixabay_images = await self._fetch_pixabay_images(bmw_query, max_count=2)
                if pixabay_images:
                    image_list.extend(pixabay_images)
                    source = "pixabay" if source == "none" else source + "+pixabay"
            except Exception as e:
                logger.debug(f"Pixabay image fetch error: {e}")

        # 6. Try Wikimedia Commons (free, no API key needed)
        if len(image_list) < 2:
            try:
                wiki_images = await self._fetch_wikimedia_images(bmw_query, max_count=2)
                if wiki_images:
                    image_list.extend(wiki_images)
                    source = "wikimedia" if source == "none" else source + "+wikimedia"
            except Exception as e:
                logger.debug(f"Wikimedia image fetch error: {e}")

        # 7. AI generation fallback — LAST RESORT (all real sources failed)
        if not image_list:
            try:
                ai_images = await self._generate_post_images(title, count=1)
                if ai_images:
                    image_list.extend(ai_images)
                    source = "ai"
                    logger.info(f"Generated {len(ai_images)} AI images (no real images found)")
                else:
                    # Specific prompts failed — try ONE generic prompt
                    try:
                        img_data = await asyncio.wait_for(
                            get_ai_router()._primary.generate_image(
                                "Beautiful BMW car on a scenic road, professional automotive "
                                "photography, golden hour, no text.",
                                model="flux",
                            ),
                            timeout=60.0,
                        )
                        if img_data:
                            image_list = [img_data]
                            source = "ai-generic"
                            logger.info("Generated generic AI BMW image")
                    except (asyncio.TimeoutError, Exception) as e:
                        logger.warning(f"Generic image generation failed: {e}")
            except Exception as e:
                logger.warning(f"AI image generation skipped: {e}")

        # 8. Stock BMW photo fallback (absolute last resort)
        if not image_list:
            try:
                stock_images = await self._fetch_stock_bmw_images()
                if stock_images:
                    image_list.extend(stock_images)
                    source = "stock"
                    logger.info("Using stock BMW photo as last resort")
            except Exception as e:
                logger.debug(f"Stock BMW image fallback error: {e}")

        # HARD LIMIT: never more than MAX_IMAGES_PER_POST (10 max — Telegram limit)
        image_list = image_list[:MAX_IMAGES_PER_POST]

        if not image_list:
            logger.warning(
                f"No images found for post: {title[:60]}. "
                f"All image sources failed (RSS, scrape, Pexels, Pixabay, Wikimedia, search, AI, stock). "
                f"Post will be published as text-only."
            )

        return image_list, source

    async def _generate_post_text(self, news_item: Dict) -> Optional[str]:
        """Generate post text for a news item using AI."""
        title = news_item.get("title", "")
        summary = news_item.get("summary", "")
        source_url = news_item.get("url", "")

        # Build context
        context_parts = [get_date_context()]

        # BMW-specific context
        try:
            from bot.bmw_knowledge import build_bmw_context
            bmw_ctx = build_bmw_context(f"{title} {summary}")
            if bmw_ctx:
                context_parts.append(bmw_ctx)
        except Exception:
            pass

        if summary:
            context_parts.append(f"Исходная новость: {summary[:500]}")

        if source_url:
            context_parts.append(f"Источник: {source_url}")

        # Add editorial aside hint
        aside = get_editorial_aside()
        if aside:
            context_parts.append(f"Редакционная шутка (используй если уместно): {aside}")

        extra_context = "\n\n".join(context_parts)

        # Generate with AI using persona
        try:
            full_context = ""
            if summary:
                full_context = f"Исходная новость: {summary[:500]}"
            if extra_context:
                full_context = (full_context + "\n\n" + extra_context).strip() if full_context else extra_context

            response = await get_ai_router().generate_channel_post(
                topic=title,
                context=full_context,
            )

            if response.error or not response.text:
                logger.warning(f"AI post generation failed: {response.error_message}")
                return None

            return response.text

        except Exception as e:
            logger.error(f"Post text generation error: {e}")
            return None

    async def run_scheduled_post(self) -> bool:
        """Try to create and post content to the channel.

        v2.0: Posts are published WITH images whenever possible.
        If no images are available, text-only is allowed as a last resort
        (channel silence is worse than a post without a photo).
        """
        try:
            # Check posting limits
            today_count = await get_today_post_count()
            if today_count >= config.CHANNEL_MAX_POSTS_PER_DAY:
                logger.info("Daily post limit reached")
                return False

            hourly_count = await get_hourly_post_count()
            if hourly_count >= config.CHANNEL_MAX_POSTS_PER_HOUR:
                logger.info("Hourly post limit reached")
                return False

            # Get best news item
            news_item = await get_best_news_item()
            if not news_item:
                logger.info("No suitable news item found")
                return False

            # Check dedup
            if await is_duplicate_post(news_item["title"], hours=48):
                logger.info(f"Duplicate post: {news_item['title'][:60]}")
                return False

            if _is_semantically_duplicate(news_item["title"]):
                logger.info(f"Semantic duplicate: {news_item['title'][:60]}")
                return False

            # Entity dedup
            entity_key = _extract_entities(news_item["title"])
            if _is_topic_covered(entity_key):
                logger.info(f"Topic already covered: {entity_key}")
                return False

            # Generate post text
            post_text = await self._generate_post_text(news_item)
            if not post_text:
                return False

            # Clean and validate
            post_text = _clean_post_text(post_text)
            if not _validate_post_text(post_text):
                logger.warning(f"Post validation failed: {post_text[:80]}")
                return False

            # Get images — prioritize REAL photos from news, AI generation as LAST resort
            image_data_list, image_source = await self._get_post_images(news_item)
            has_media = len(image_data_list) > 0
            media_count = len(image_data_list) if has_media else 0

            # ── SMART MEDIA DECISION ──
            #
            # RULES (Telegram limits: caption=1024, text-only=4096):
            #   1. Post with photo — ALWAYS preferred.
            #   2. Post without photo — only when no image is available at all.
            #      Channel silence is WORSE than a post without photo.
            #
            _CAPTION_LIMIT = config.TELEGRAM_CAPTION_LIMIT   # 1024
            _TEXT_LIMIT = config.TELEGRAM_TEXT_LIMIT          # 4096

            if not has_media and len(post_text) <= _CAPTION_LIMIT:
                # No media + short text — try one more image generation attempt
                logger.warning(
                    f"Post has NO media and text is {len(post_text)} chars. "
                    f"Attempting last-resort image generation..."
                )
                try:
                    last_resort = await self._generate_post_images(
                        news_item.get("title", ""), count=1
                    )
                    if last_resort:
                        image_data_list = last_resort
                        has_media = True
                        image_source = "ai-last-resort"
                        logger.info("Last-resort AI image gen succeeded")
                except Exception as e:
                    logger.debug(f"Last-resort image gen failed: {e}")

                if not has_media:
                    # PUBLISH TEXT-ONLY as last resort — better than channel silence
                    logger.warning(
                        f"POSTING TEXT-ONLY (last resort): No images available, text is "
                        f"{len(post_text)} chars. Channel silence is worse than no-photo post."
                    )

            elif has_media and len(post_text) > _CAPTION_LIMIT:
                # Has media + text too long — compress to keep media
                logger.info(
                    f"Post text {len(post_text)} chars > caption limit {_CAPTION_LIMIT}. "
                    f"Compressing text to preserve media attachment."
                )
                compressed = _enforce_char_limit(post_text, has_media=True)
                if len(compressed) <= _CAPTION_LIMIT and len(compressed) >= 400:
                    post_text = compressed
                else:
                    # Check if content is interesting enough for text-only
                    interest_score = _score_interest(
                        news_item.get("title", ""),
                        news_item.get("summary", "")
                    )
                    if interest_score >= 0.5 and len(post_text) <= _TEXT_LIMIT:
                        has_media = False
                        image_data_list = []
                        logger.info(f"Text too long for caption, interest={interest_score:.2f}. Publishing text-only.")
                    else:
                        post_text = _enforce_char_limit(post_text, has_media=True)

            elif not has_media and len(post_text) > _CAPTION_LIMIT:
                # No media + long text — check if interesting enough for text-only
                interest_score = _score_interest(
                    news_item.get("title", ""),
                    news_item.get("summary", "")
                )
                if interest_score < 0.5 or len(post_text) > _TEXT_LIMIT:
                    post_text = _enforce_char_limit(post_text, has_media=False)

            # Ensure footer and char limit
            post_text = _ensure_footer(post_text)
            post_text = _enforce_char_limit(post_text, has_media)

            # HARD SAFETY CHECK: never more than MAX_IMAGES_PER_POST images
            if has_media and len(image_data_list) > MAX_IMAGES_PER_POST:
                logger.warning(f"SAFETY: Truncating {len(image_data_list)} images to {MAX_IMAGES_PER_POST}")
                image_data_list = image_data_list[:MAX_IMAGES_PER_POST]

            # Post to channel
            sent_message = None
            try:
                if has_media and image_data_list:
                    # Save images to temp files
                    tmp_paths = []
                    for i, img_data in enumerate(image_data_list[:MAX_IMAGES_PER_POST]):
                        tmp_path = os.path.join(tempfile.gettempdir(), f"masha_post_{int(time.time())}_{i}.jpg")
                        with open(tmp_path, "wb") as f:
                            f.write(img_data)
                        tmp_paths.append(tmp_path)

                    if len(tmp_paths) == 1:
                        # Single image — use send_photo
                        photo = FSInputFile(tmp_paths[0], filename="masha_post.jpg")
                        sent_message = await self._bot.send_photo(
                            chat_id=config.CHANNEL_ID,
                            photo=photo,
                            caption=post_text[:1024],
                            parse_mode=ParseMode.HTML,
                        )
                    else:
                        # Multiple images — use send_media_group (album up to 10)
                        media_group = []
                        for i, tmp_path in enumerate(tmp_paths):
                            photo_file = FSInputFile(tmp_path, filename=f"masha_post_{i}.jpg")
                            if i == 0:
                                media_group.append(InputMediaPhoto(
                                    media=photo_file,
                                    caption=post_text[:1024],
                                    parse_mode=ParseMode.HTML,
                                ))
                            else:
                                media_group.append(InputMediaPhoto(media=photo_file))

                        sent_messages = await self._bot.send_media_group(
                            chat_id=config.CHANNEL_ID,
                            media=media_group,
                        )
                        if sent_messages:
                            sent_message = sent_messages[0]

                    # Clean up temp files
                    for tmp_path in tmp_paths:
                        try:
                            os.unlink(tmp_path)
                        except Exception:
                            pass
                else:
                    # Text-only post (no images available)
                    sent_message = await self._bot.send_message(
                        chat_id=config.CHANNEL_ID,
                        text=post_text,
                        parse_mode=ParseMode.HTML,
                    )

                if sent_message:
                    # Add reaction
                    await self._add_reaction(config.CHANNEL_ID, sent_message.message_id)

                    # Save to DB
                    await add_channel_post(
                        content=post_text,
                        message_id=sent_message.message_id,
                        post_type="news",
                        source_url=news_item.get("url", ""),
                    )

                    # Mark news as posted
                    if news_item.get("url"):
                        await mark_news_posted(news_item["url"])

                    # Register topic for dedup
                    _register_topic(entity_key, news_item["title"])
                    _record_post_title(news_item["title"])

                    logger.info(f"✅ Post published: {news_item['title'][:60]} (images={media_count}, source={image_source})")
                    return True

            except Exception as e:
                logger.error(f"Error posting to channel: {e}")
                return False

        except Exception as e:
            logger.error(f"Scheduled post error: {e}", exc_info=True)
            return False

    async def post_partner_content(self) -> bool:
        """Post partner content to the channel with partner logo image.

        Partner posts ALWAYS have an image:
        1. Try downloading the partner's logo from image_url
        2. Fallback: try Wikimedia/AI generation
        3. Post as text-only only if absolutely no image is available
        """
        if not partner_manager.should_post_partner():
            return False

        program = partner_manager.get_random_program()
        if not program:
            return False

        post_content = await partner_manager.generate_partner_post_content(program)

        if not _validate_post_text_partner(post_content):
            return False

        # Try to get an image for the partner post
        image_data = None

        # 1. Try downloading the partner's logo/image
        logo_url = getattr(program, 'image_url', '') or getattr(program, 'logo_url', '') or getattr(program, 'image', '')
        if logo_url:
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(logo_url, headers={
                        "User-Agent": "MashaBot/1.0 (+https://t.me/asmasha_bot)",
                    })
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        content_type = resp.headers.get("content-type", "")
                        if any(ft in content_type for ft in ["image/jpeg", "image/png", "image/webp", "image/gif"]):
                            image_data = resp.content
                            logger.info(f"Partner logo downloaded: {program.name} ({len(resp.content)} bytes)")
            except Exception as e:
                logger.debug(f"Partner logo download failed for {program.name}: {e}")

        # 2. Try Wikimedia with partner name
        if not image_data:
            try:
                wiki_images = await self._fetch_wikimedia_images(program.name, max_count=1)
                if wiki_images:
                    image_data = wiki_images[0]
                    logger.info(f"Wikimedia fallback image for partner: {program.name}")
            except Exception as e:
                logger.debug(f"Wikimedia fallback for partner {program.name}: {e}")

        # 3. Try AI generation
        if not image_data:
            try:
                ai_images = await self._generate_post_images(
                    f"professional logo design for {program.name}", count=1
                )
                if ai_images:
                    image_data = ai_images[0]
                    logger.info(f"AI-generated image for partner: {program.name}")
            except Exception as e:
                logger.debug(f"AI image gen for partner {program.name}: {e}")

        # 4. Try stock BMW image as last resort
        if not image_data:
            try:
                stock_images = await self._fetch_stock_bmw_images()
                if stock_images:
                    image_data = stock_images[0]
                    logger.info(f"Stock BMW image for partner post: {program.name}")
            except Exception as e:
                logger.debug(f"Stock image for partner {program.name}: {e}")

        try:
            if image_data:
                # Post with image
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp.write(image_data)
                    tmp_path = tmp.name

                try:
                    photo = FSInputFile(tmp_path, filename=f"partner_{program.id}.jpg")
                    sent = await self._bot.send_photo(
                        chat_id=config.CHANNEL_ID,
                        photo=photo,
                        caption=post_content,
                    )
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
            else:
                # No image available — post text-only (partner posts should not be skipped)
                logger.warning(f"Partner post without image: {program.name}")
                sent = await self._bot.send_message(
                    chat_id=config.CHANNEL_ID,
                    text=post_content,
                )

            if sent:
                await add_partner_post(
                    program_id=program.id,
                    program_name=program.name,
                    category=program.category or "general",
                    affiliate_url=program.goto_link,
                    post_content=post_content,
                    message_id=sent.message_id,
                )
                partner_manager.mark_posted()
                logger.info(f"Partner post published: {program.name} (with_image={image_data is not None})")
                return True
        except Exception as e:
            logger.error(f"Partner post error: {e}")

        return False


# Global instance
channel_manager = ChannelManager()
