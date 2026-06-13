"""
Channel Manager -- Posts to @bmw_mpower_club with BMW-themed formatting.
Handles news posts, partner posts, scheduled content, reactions,
media, polls, and internet news search.
Properly enforces Telegram character limits: 1024 with media, 4096 without.

v3.0 KEY CHANGES:
- RADICALLY SIMPLIFIED image pipeline: ONLY photos from the article page
- Automotive news ALWAYS comes with photos — we just extract them
- Removed: Pexels, Pixabay, Wikimedia, AI generation, stock BMW, web search images
  (all of those return IRRELEVANT photos that have nothing to do with the news)
- No photo = text-only post. Better no photo than a wrong photo.
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
    get_best_news_item, get_date_context,
    _is_topic_covered, _extract_entities, _score_interest,
    _register_topic, get_editorial_aside, get_translation_uniquification_hint,
)

logger = logging.getLogger("masha.channel")

# ── Reactions to add to posts ───────────────────────────────────────────────

POST_REACTIONS = ["👍", "🔥", "🏎️", "😍", "👏", "💯", "⚡", "///M"]

# ── How many images per news post ───────────────────────────────────────────
# Telegram allows up to 10 media per post.
MAX_IMAGES_PER_POST = 10

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
_MAX_RECENT_POSTS = 50  # Reduced from 100 — 100 was too aggressive

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
        if matches >= 5:  # Raised from 3 — too many false positives for BMW channel
            return True

        if len(core_words) >= 3:  # Raised from 2 — need more specific overlap
            recent_core = [w for w in recent_words if w in _BMW_CORE_WORDS]
            core_matches = sum(1 for w in core_words if w in recent_core)
            if core_matches >= 3:  # Raised from 2 — BMW terms are too common in our posts
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
        "qwen-coder", "llama-scale",
        # REMOVED: "searchgpt" — invalid on gen.pollinations.ai (400 errors)
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

    # ── IMAGE PIPELINE v3.0 — ARTICLE-ONLY ───────────────────────────────────
    #
    # PHILOSOPHY: Automotive news articles ALWAYS come with photos.
    # We extract photos directly from the article page. That's it.
    # No stock photos, no AI generation, no web search images — they're all
    # irrelevant to the specific news. Better no photo than a wrong photo.

    async def _download_images(self, image_urls: List[str], max_count: int = 10) -> List[bytes]:
        """Download images from URLs with minimal validation.

        Just checks: is it a valid image? Is it big enough? That's it.
        No complex filtering — if the URL comes from the article page, it's relevant.
        Returns list of image data bytes.
        """
        images = []
        if not image_urls:
            return images

        MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
        MIN_IMAGE_SIZE = 5_000            # 5KB — skip tracking pixels and tiny icons

        # Use a single client for all downloads
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        ) as client:
            for url in image_urls:
                if len(images) >= max_count:
                    break

                # Quick URL-level sanity check — only skip OBVIOUS non-content
                url_lower = url.lower()
                if any(kw in url_lower for kw in ['favicon', '1x1', 'pixel', 'spacer',
                                                    'blank.gif', 'gravatar', 'analytics',
                                                    'tracker', 'beacon']):
                    continue

                try:
                    response = await client.get(url)
                    if response.status_code != 200:
                        continue

                    content = response.content

                    # Size check
                    if len(content) < MIN_IMAGE_SIZE or len(content) > MAX_IMAGE_SIZE:
                        continue

                    # Must be an actual image: check magic bytes (JPEG, PNG, WebP)
                    is_valid = (
                        content[:2] == b'\xff\xd8'               # JPEG
                        or content[:8] == b'\x89PNG\r\n\x1a\n'   # PNG
                        or content[:4] == b'RIFF'                 # WebP
                    )
                    if not is_valid:
                        continue

                    # Dimension check with PIL if available — skip tiny/banners
                    try:
                        from PIL import Image
                        import io
                        img = Image.open(io.BytesIO(content))
                        w, h = img.size
                        if w < 300 or h < 200:
                            continue
                        # Skip extreme aspect ratios (banners/ads)
                        if w / max(h, 1) > 4.0 or h / max(w, 1) > 4.0:
                            continue
                    except ImportError:
                        pass  # No PIL — trust the size + magic bytes check
                    except Exception:
                        pass

                    images.append(content)
                    logger.info(f"Downloaded image: {url[:80]} ({len(content)} bytes)")

                except (httpx.TimeoutException, httpx.HTTPError):
                    continue
                except Exception:
                    continue

        logger.info(f"Downloaded {len(images)}/{len(image_urls)} images")
        return images

    async def _scrape_article_images(self, article_url: str, max_count: int = 10) -> List[bytes]:
        """Scrape images from a news article page.

        Extracts images from multiple sources in priority order:
        1. og:image meta tags (usually the main article image)
        2. twitter:image meta tags
        3. JSON-LD structured data (schema.org image field)
        4. <img> tags from article body areas (src + data-src for lazy loading)
        5. <picture>/<source srcset> elements

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

                # Collect candidate URLs in priority order
                candidate_urls = []
                seen = set()

                def _add_url(url: str):
                    """Add a URL to candidates if not seen and not obviously junk."""
                    if not url or len(url) < 10 or url in seen:
                        return
                    # Normalize protocol-relative URLs
                    if url.startswith("//"):
                        url = "https:" + url
                    # Quick junk filter
                    url_lower = url.lower()
                    if any(kw in url_lower for kw in ['favicon', '1x1', 'pixel', 'spacer',
                                                        'blank.gif', 'gravatar', 'analytics',
                                                        'tracker', 'beacon']):
                        return
                    seen.add(url)
                    candidate_urls.append(url)

                # 1. og:image (usually the main article image — HIGHEST priority)
                og_images = re.findall(r'<meta[^>]+property=["\x27]og:image["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                og_images += re.findall(r'<meta[^>]+content=["\x27]([^"\x27]+)["\x27][^>]+property=["\x27]og:image["\x27]', html, re.IGNORECASE)
                og_images += re.findall(r'<meta[^>]+property=["\x27]og:image:url["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                og_images += re.findall(r'<meta[^>]+property=["\x27]og:image:secure_url["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                for url in og_images:
                    _add_url(url.replace("&amp;", "&"))

                # 2. twitter:image
                tw_images = re.findall(r'<meta[^>]+name=["\x27]twitter:image["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                tw_images += re.findall(r'<meta[^>]+content=["\x27]([^"\x27]+)["\x27][^>]+name=["\x27]twitter:image["\x27]', html, re.IGNORECASE)
                for url in tw_images:
                    _add_url(url.replace("&amp;", "&"))

                # 3. JSON-LD structured data (schema.org)
                jsonld_images = self._extract_jsonld_images(html)
                for url in jsonld_images:
                    _add_url(url)

                # 4. <img> tags from article body areas (THE MOST IMPORTANT for multi-photo articles)
                # Automotive articles typically have 5-10 photos in the article body
                article_html = ""
                for pattern in [r'<article[^>]*>(.*?)</article>',
                                r'<main[^>]*>(.*?)</main>',
                                r'<div[^>]+class=["\x27][^"\x27]*(?:content|article|post|entry|gallery)[^"\x27]*["\x27][^>]*>(.*?)</div>']:
                    matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
                    for match in matches:
                        article_html += match + "\n"

                # If article body found, extract images from there
                if article_html:
                    # data-src (lazy loading — often higher quality)
                    lazy_urls = re.findall(r'<img[^>]+data-src=["\x27]([^"\x27]+)["\x27]', article_html, re.IGNORECASE)
                    lazy_urls += re.findall(r'<img[^>]+data-lazy-src=["\x27]([^"\x27]+)["\x27]', article_html, re.IGNORECASE)
                    for url in lazy_urls:
                        _add_url(url.replace("&amp;", "&"))

                    # Regular src
                    src_urls = re.findall(r'<img[^>]+src=["\x27]([^"\x27]+)["\x27]', article_html, re.IGNORECASE)
                    for url in src_urls:
                        _add_url(url.replace("&amp;", "&"))

                    # srcset (responsive images)
                    srcset_matches = re.findall(r'srcset=["\x27]([^"\x27]+)["\x27]', article_html, re.IGNORECASE)
                    for srcset in srcset_matches:
                        for part in srcset.split(','):
                            url = part.strip().split()[0] if part.strip() else ''
                            _add_url(url)
                else:
                    # No article body found — try all <img> in the page as last resort
                    all_img = re.findall(r'<img[^>]+src=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                    lazy_all = re.findall(r'<img[^>]+data-src=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                    for url in lazy_all:
                        _add_url(url.replace("&amp;", "&"))
                    for url in all_img:
                        _add_url(url.replace("&amp;", "&"))

                # 5. <picture>/<source srcset> elements
                picture_blocks = re.findall(r'<picture[^>]*>(.*?)</picture>', html, re.IGNORECASE | re.DOTALL)
                for block in picture_blocks:
                    srcsets = re.findall(r'srcset=["\x27]([^"\x27]+)["\x27]', block, re.IGNORECASE)
                    for srcset in srcsets:
                        for part in srcset.split(','):
                            url = part.strip().split()[0] if part.strip() else ''
                            _add_url(url)

                logger.info(f"Scraped {len(candidate_urls)} candidate image URLs from {article_url[:60]}")

                # Download and validate the candidates
                if candidate_urls:
                    images = await self._download_images(candidate_urls, max_count=max_count)

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
            import json
            # Find all JSON-LD blocks
            jsonld_blocks = re.findall(
                r'<script[^>]+type=["\x27]application/ld\+json["\x27][^>]*>(.*?)</script>',
                html, re.IGNORECASE | re.DOTALL
            )
            for block in jsonld_blocks:
                try:
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

    async def _resolve_article_url(self, url: str, title: str = "") -> str:
        """Resolve a news URL to a direct article link.

        Google News RSS returns redirect URLs like:
          https://news.google.com/rss/articles/CBMidkFV...
        These don't resolve to the real article — they lead to a Google
        intermediate page with no article content to scrape.

        Solution: If the URL is a Google News redirect, search DDG for the
        article title to find the direct URL. This works because the title
        is unique enough to find the exact article.

        Returns the resolved (direct) URL, or the original URL if not a redirect.
        """
        if not url:
            return url

        # Detect Google News redirect URLs
        is_google_redirect = (
            'news.google.com' in url
            or 'google.com/rss/articles' in url
            or 'google.com/articles/' in url
        )

        if not is_google_redirect:
            return url

        if not title:
            # Can't resolve without title — return as-is
            logger.debug(f"Google News URL without title, can't resolve: {url[:60]}")
            return url

        # Try to find the direct URL via DDG search
        logger.info(f"Resolving Google News URL for: {title[:50]}")
        try:
            from bot.web_search import search_ddg_html
            # Clean title for search — remove site names in brackets
            clean_title = re.sub(r'\s*[-–—]\s*[^–—]*$', '', title).strip()
            search_query = clean_title[:80]

            results = await search_ddg_html(search_query, max_results=5)
            for r in results:
                r_url = r.url
                # Skip Google redirects, DDG redirect links
                if 'google.com' in r_url or 'duckduckgo.com' in r_url:
                    continue
                # Extract real URL from DDG redirect if needed
                if 'uddg=' in r_url:
                    from urllib.parse import unquote, urlparse, parse_qs
                    parsed = urlparse(r_url)
                    params = parse_qs(parsed.query)
                    if 'uddg' in params:
                        r_url = unquote(params['uddg'][0])
                if r_url.startswith('//'):
                    r_url = 'https:' + r_url
                # Found a direct URL
                if r_url.startswith('http'):
                    logger.info(f"Resolved Google URL → {r_url[:60]}")
                    return r_url
        except Exception as e:
            logger.debug(f"Google URL resolution failed: {e}")

        # Couldn't resolve — return original
        logger.warning(f"Could not resolve Google News URL: {url[:60]}")
        return url

    async def _get_post_images(self, news_item: Dict) -> tuple:
        """Get images for a news post — ONLY from the article page.

        v3.1: Improved URL resolution.
        Automotive news articles ALWAYS come with photos.
        We extract them directly from the article. That's it.

        Steps:
        1. Resolve URL (Google News redirects → direct article URL)
        2. Download RSS image URLs if available (fast, no scraping needed)
        3. Scrape the article page for MORE images (up to 10 total)

        No fallbacks to stock photos, AI generation, or web search images.
        If the article has no photos = text-only post. Better than wrong photos.

        Returns (image_list: List[bytes], source: str)
        """
        image_list = []
        source = "none"
        title = news_item.get("title", "")

        # Step 0: Resolve Google News redirect URLs to direct article URLs
        article_url = news_item.get("url", "")
        if article_url:
            article_url = await self._resolve_article_url(article_url, title=title)

        # Step 1: Download RSS image URLs FIRST — these are the fastest
        # (already extracted from feed, no HTML scraping needed)
        rss_image_urls = news_item.get("image_urls", [])
        if rss_image_urls:
            try:
                rss_images = await self._download_images(rss_image_urls, max_count=MAX_IMAGES_PER_POST)
                if rss_images:
                    image_list.extend(rss_images)
                    source = "rss"
                    logger.info(f"Downloaded {len(rss_images)} RSS images for: {title[:50]}")
            except Exception as e:
                logger.debug(f"RSS image download failed: {e}")

        # Step 2: Scrape article page for MORE images
        # This gives us the full gallery (up to 10 photos per article)
        if article_url and len(image_list) < MAX_IMAGES_PER_POST:
            try:
                scraped = await self._scrape_article_images(
                    article_url,
                    max_count=MAX_IMAGES_PER_POST - len(image_list)
                )
                if scraped:
                    image_list.extend(scraped)
                    source = "article" if source == "none" else source + "+article"
                    logger.info(f"Scraped {len(scraped)} images from article: {title[:50]}")
            except Exception as e:
                logger.debug(f"Article scraping failed: {e}")

        # Hard limit
        image_list = image_list[:MAX_IMAGES_PER_POST]

        if not image_list:
            logger.info(
                f"No images from article for: {title[:60]}. "
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

            text = response.text or ""

        except Exception as e:
            logger.error(f"AI generation error: {e}")
            return None

        return text.strip() if text else None

    async def run_scheduled_post(self) -> bool:
        """Try to create and post content to the channel.

        v3.0: Simplified image pipeline — only article photos.
        No photo = text-only post. Better no photo than wrong photo.
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

            # Get images — ONLY from article page
            image_data_list, image_source = await self._get_post_images(news_item)
            has_media = len(image_data_list) > 0
            media_count = len(image_data_list) if has_media else 0

            # ── MEDIA DECISION ──
            #
            # RULES (Telegram limits: caption=1024, text-only=4096):
            #   1. Post with photo — ALWAYS preferred.
            #   2. Post without photo — when article has no images.
            #      Better no photo than a wrong (irrelevant) photo.
            #
            _CAPTION_LIMIT = config.TELEGRAM_CAPTION_LIMIT   # 1024
            _TEXT_LIMIT = config.TELEGRAM_TEXT_LIMIT          # 4096

            if has_media and len(post_text) > _CAPTION_LIMIT:
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
                    # Text-only post — no images available from article
                    sent_message = await self._bot.send_message(
                        chat_id=config.CHANNEL_ID,
                        text=post_text[:4096],
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
        """Post partner content to the channel.

        Partner posts try to get an image from the partner's logo URL.
        If no image available, post as text-only.
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

        # Try downloading the partner's logo/image
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
