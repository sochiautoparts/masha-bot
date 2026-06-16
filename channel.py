"""
Channel Manager -- Posts to @bmw_mpower_club with BMW-themed formatting.
Handles news posts, partner posts, scheduled content, reactions,
media, polls, and internet news search.
Properly enforces Telegram character limits: 1024 with media, 4096 without.

v8.0 KEY CHANGES:
- NEWS SOURCE: bmw-news.json from sochiautoparts/nws (hourly-updated BMW news)
  - BMW-pre-filtered content from 36 RSS sources
  - Single image per article (pre-curated, no junk/thumbnail/logo photos!)
  - Direct article URLs — no Google News redirects to resolve
  - Consistent data format, no broken feeds, no rate limits
- Fallback: RSS (15+ feeds) → Web Search when bmw-news.json unavailable
- Image pipeline: images from bmw-news.json → fallback to article scrape → AI generation
- Dedup: 5-layer protection (DB unique, fingerprint, semantic Jaccard, entity, title)
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
    get_recent_post_titles, is_source_url_posted, DB_PATH,
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
_MAX_RECENT_POSTS = 30  # v4.0: Reduced from 50 — fewer comparisons = fewer false positives

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
    """Check if a title is semantically duplicate of recently posted titles.

    v5.0: COMPLETELY REWRITTEN — was still blocking too many posts.
    Old approach: count word overlaps → too many false positives in a BMW channel
    where every title contains "BMW", "M3", etc.

    New approach: Jaccard similarity on significant words.
    - Computes intersection/union ratio between current and recent titles
    - Only flags as duplicate if similarity > 0.6 (60% of words overlap)
    - This means titles must share MOST of their meaningful words to be blocked
    - BMW/M3/etc. appearing in both titles won't trigger it unless the
      REST of the title is also nearly identical
    """
    global _recent_post_keywords

    words = re.findall(r'[a-zа-яё]{3,}', title.lower())
    significant = [w for w in words if w not in _SEMANTIC_STOP_WORDS]

    if len(significant) < 3:
        return False

    current_set = set(significant)

    for recent_words in _recent_post_keywords:
        recent_set = set(recent_words)
        if not recent_set:
            continue

        # Jaccard similarity: |intersection| / |union|
        intersection = current_set & recent_set
        union = current_set | recent_set

        if not union:
            continue

        similarity = len(intersection) / len(union)

        # High similarity = near-identical title → duplicate
        if similarity > 0.6:
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

    # AUTO-RELEVANCE CHECK — STRICTLY BMW-focused
    # The bot is about BMW. Non-BMW auto content should NOT pass.
    _bmw_required_keywords = [
        # BMW-specific
        "bmw", "бмв", "бимер", "баварец", "///m", "m power", "mpower",
        "m5", "m3", "m4", "m2", "m8", "x5", "x3", "x6", "x7", "x1", "x2", "x4",
        "s63", "s58", "s55", "b58", "n55", "n54", "s68", "b48", "b46", "b38",
        "vanos", "valvetronic", "xdrive", "alpina",
        "bimmercode", "ista", "realoem",
        # BMW electric
        "ix3", "ix1", "ix2", "i4", "i5", "i7", "i3", "i8", "im3",
        # BMW platforms / concepts
        "neue klasse", "m sport", "m performance",
        # BMW model codes
        "g20", "g80", "g82", "g87", "g60", "g70", "g65",
        "f90", "f80", "f82", "f87", "f30", "f10",
        "e46", "e39", "e30", "e36", "e28",
        # BMW Group brands
        "mini cooper", "mini countryman", "rolls-royce",
        # BMW in Russian
        "баварский моторный", "баварец",
        # BMW-specific design terms
        "kidney grille", "hofmeister kink", "angel eyes",
        # BMW racing
        "bmw m motorsport", "bmw m team", "bmw m hybrid",
        "bmw art car",
    ]
    has_bmw_keyword = any(kw.lower() in text_lower for kw in _bmw_required_keywords)
    if not has_bmw_keyword:
        logger.warning(f"Post BLOCKED (no BMW-relevant keywords)")
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
    """Smart character limit enforcement — always preserves footer.

    v5.0: FIXED — ensures the FINAL text (content + footer) fits within the limit.
    Previous version could exceed the limit after smart truncation + footer append.
    """
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

    if len(content) <= max_content:
        return content + footer

    # Smart truncation — cut at last paragraph break, then sentence
    trimmed = content[:max_content]

    # Try cutting at last paragraph break (\n\n)
    last_para = trimmed.rfind('\n\n')
    if last_para > max_content * 0.5:
        trimmed = trimmed[:last_para]
    else:
        # Try cutting at last sentence end (. ! ?)
        last_sent = max(trimmed.rfind('. '), trimmed.rfind('! '), trimmed.rfind('? '))
        if last_sent > max_content * 0.5:
            trimmed = trimmed[:last_sent + 1]
        else:
            # Fallback: simple truncation
            trimmed = trimmed.rstrip() + "..."

    result = trimmed.rstrip() + footer

    # FINAL SAFETY: if still over limit, hard-truncate the content part
    if len(result) > char_limit:
        overhead = len(result) - char_limit
        # Trim the content part (before footer) to make it fit
        content_only = trimmed.rstrip()
        if len(content_only) > overhead:
            content_only = content_only[:len(content_only) - overhead].rstrip()
            # Re-trim at sentence boundary if possible
            last_sent = max(content_only.rfind('. '), content_only.rfind('! '), content_only.rfind('? '))
            if last_sent > len(content_only) * 0.5:
                content_only = content_only[:last_sent + 1]
            result = content_only.rstrip() + footer
        else:
            result = footer.lstrip('\n')

    return result


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

    # ── IMAGE PIPELINE v5.0 — STRICT QUALITY CONTROL ──────────────────────────
    #
    # PHILOSOPHY: Only REAL article photos — no logos, avatars, icons, banners,
    # thumbnails, or sidebar navigation images.
    #
    # PRIORITY:
    # 1. RSS image_urls — extracted from feed enclosures/media:content/summary
    # 2. Article page scraping — og:image, twitter:image, JSON-LD ONLY
    #    (NOT random <img> tags — they contain logos, nav icons, etc.)
    #
    # KEY RULE: Better NO photo than a WRONG photo (logo, icon, avatar, banner)

    # Domains that NEVER contain real article photos
    _JUNK_IMAGE_DOMAINS = frozenset([
        'gravatar.com', 'wp.com', 'google.com', 'googlesyndication.com',
        'facebook.com', 'twitter.com', 'instagram.com', 'youtube.com',
        'doubleclick.net', 'adservice.google.com',
    ])

    # URL path patterns that NEVER contain real article photos
    _JUNK_IMAGE_PATHS = frozenset([
        'favicon', '1x1', 'pixel', 'spacer', 'blank.gif', 'gravatar',
        'analytics', 'tracker', 'beacon', 'logo', 'icon', 'avatar',
        'badge', 'button', 'banner', 'ad.', 'ads/', 'sponsor',
        'social', 'share', 'follow', 'subscribe', 'newsletter',
        'doubleclick', 'adservice', 'googlesyndication',
        # Navigation thumbs common in BMW Blog sidebar
        '-thumb.', '-thumb/', '/thumb/', '/thumbnail/', '/thumbs/',
        '_thumb.', '_tiny.', '-tiny.',
        # Common WordPress theme assets
        '/assets/images/', '/assets/img/', '/themes/',
        # Common widget/plugin images
        '/widgets/', '/plugins/',
    ])

    async def _download_images(self, image_urls: List[str], max_count: int = 10, curated: bool = False) -> List[bytes]:
        """Download images from URLs with quality validation.

        v8.2: Added `curated` flag — when images come from curated news.json,
        we skip URL-based junk/thumbnail filters since they've already been
        pre-filtered by _filter_curated_images(). Only basic validation
        (file size, magic bytes, dimensions) is applied to curated images.

        Filter layers:
        1. URL domain check — block known junk domains (gravatar, ads, etc.)
        2. URL path check — block logos, icons, thumbs, theme assets, etc.
           [SKIPPED for curated images — already pre-filtered]
        3. File size check — 10KB min for curated, 15KB for scraped
        4. Magic bytes check — must be JPEG/PNG/WebP
        5. Dimension check — min 300x200 for curated, 400x300 for scraped
        6. Dedup by URL — same image won't be downloaded twice

        Returns list of image data bytes.
        """
        images = []
        seen_urls = set()
        if not image_urls:
            return images

        MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
        MIN_IMAGE_SIZE = 10_000 if curated else 15_000  # Lower threshold for curated images

        # Use a single client for all downloads
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        ) as client:
            for url in image_urls:
                if len(images) >= max_count:
                    break

                # Normalize URL
                url = url.replace("&amp;", "&")
                if url.startswith("//"):
                    url = "https:" + url
                if not url.startswith(("http://", "https://")):
                    continue

                # Dedup
                url_stripped = url.split("?")[0].lower()  # Compare without query params
                if url_stripped in seen_urls:
                    continue
                seen_urls.add(url_stripped)

                url_lower = url.lower()

                # Skip SVG — always icons/logos
                if url_lower.endswith('.svg') or '.svg?' in url_lower:
                    continue

                # Skip data: URIs
                if url.startswith('data:'):
                    continue

                # ── URL-BASED FILTERS (only for non-curated images) ──
                # Curated images from news.json have already been filtered
                # by _filter_curated_images() — no need to re-check
                if not curated:
                    # Domain check
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    domain = parsed.netloc.lower()
                    if any(junk_domain in domain for junk_domain in self._JUNK_IMAGE_DOMAINS):
                        logger.debug(f"Skipping junk domain: {domain}")
                        continue

                    # Path/pattern check
                    if any(kw in url_lower for kw in self._JUNK_IMAGE_PATHS):
                        logger.debug(f"Skipping junk path: {url[:80]}")
                        continue

                    # Thumbnail URL check
                    from bot.sources.image_fetcher import _is_thumbnail_url
                    if _is_thumbnail_url(url):
                        logger.debug(f"Skipping thumbnail URL: {url[:80]}")
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

                    # Dimension check with PIL if available
                    try:
                        from PIL import Image
                        import io
                        img = Image.open(io.BytesIO(content))
                        w, h = img.size
                        # v8.2: Lower minimums for curated images (300x200)
                        # since they're already pre-filtered by news.json
                        min_w = 300 if curated else 400
                        min_h = 200 if curated else 300
                        if w < min_w or h < min_h:
                            logger.debug(f"Skipping small image: {w}x{h} from {url[:60]}")
                            continue
                        # Skip extreme aspect ratios (banners/ads)
                        if w / max(h, 1) > 4.0 or h / max(w, 1) > 4.0:
                            logger.debug(f"Skipping extreme aspect: {w}x{h} from {url[:60]}")
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

        v5.0: COMPLETELY REWRITTEN — strict quality control.
        Previous version scraped ALL <img> tags from the page, which resulted
        in logos, avatars, sidebar navigation, icons, and banners appearing
        in Telegram posts.

        NEW STRATEGY — metadata-first:
        1. og:image / twitter:image — the site's own curated article image
        2. JSON-LD schema.org image — structured data, very reliable
        3. <img> from <article> body ONLY if metadata yields nothing
        4. NEVER scrape all-page <img> — it's always full of junk

        Returns list of image data bytes (up to max_count).
        """
        images = []
        try:
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
                    """Add a URL to candidates — STRICT filtering."""
                    if not url or len(url) < 10 or url in seen:
                        return
                    if url.startswith("//"):
                        url = "https:" + url
                    if not url.startswith(("http://", "https://")):
                        return
                    url = url.replace("&amp;", "&")
                    # Domain check
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    domain = parsed.netloc.lower()
                    if any(jd in domain for jd in self._JUNK_IMAGE_DOMAINS):
                        return
                    # Path check
                    url_lower = url.lower()
                    if any(kw in url_lower for kw in self._JUNK_IMAGE_PATHS):
                        return
                    if _is_thumbnail_url_check(url):
                        return
                    if url_lower.endswith('.svg') or '.svg?' in url_lower:
                        return
                    if url.startswith('data:'):
                        return
                    seen.add(url)
                    candidate_urls.append(url)

                def _is_thumbnail_url_check(url: str) -> bool:
                    """Inline thumbnail check to avoid circular import."""
                    from bot.sources.image_fetcher import _is_thumbnail_url
                    return _is_thumbnail_url(url)

                # ── STEP 1: og:image (HIGHEST priority — curated by the site) ──
                og_images = re.findall(r'<meta[^>]+property=["\x27]og:image["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                og_images += re.findall(r'<meta[^>]+content=["\x27]([^"\x27]+)["\x27][^>]+property=["\x27]og:image["\x27]', html, re.IGNORECASE)
                og_images += re.findall(r'<meta[^>]+property=["\x27]og:image:url["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                og_images += re.findall(r'<meta[^>]+property=["\x27]og:image:secure_url["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                for url in og_images:
                    _add_url(url)

                # ── STEP 2: twitter:image ──
                tw_images = re.findall(r'<meta[^>]+name=["\x27]twitter:image["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                tw_images += re.findall(r'<meta[^>]+content=["\x27]([^"\x27]+)["\x27][^>]+name=["\x27]twitter:image["\x27]', html, re.IGNORECASE)
                for url in tw_images:
                    _add_url(url)

                # ── STEP 3: JSON-LD structured data (schema.org) ──
                jsonld_images = self._extract_jsonld_images(html)
                for url in jsonld_images:
                    _add_url(url)

                # ── STEP 4 (FALLBACK): <img> from <article> body ONLY ──
                # Only try this if metadata (og:image, twitter:image, JSON-LD) yielded nothing
                if not candidate_urls:
                    logger.info(f"No metadata images found for {article_url[:60]}, trying <article> body")

                    article_html = ""
                    # Try <article> tag first — most reliable for article content
                    for pattern in [r'<article[^>]*>(.*?)</article>']:
                        matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
                        for match in matches:
                            article_html += match + "\n"

                    # If no <article>, try entry-content div
                    if not article_html:
                        for pattern in [
                            r'<div[^>]+class=["\x27][^"\x27]*(?:entry-content|article-body|post-content|single-content)[^"\x27]*["\x27][^>]*>(.*?)</div>',
                        ]:
                            matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
                            for match in matches:
                                article_html += match + "\n"

                    if article_html:
                        # data-src first (lazy loaded — often higher quality)
                        for url in re.findall(r'<img[^>]+data-src=["\x27]([^"\x27]+)["\x27]', article_html, re.IGNORECASE):
                            _add_url(url)
                        for url in re.findall(r'<img[^>]+data-lazy-src=["\x27]([^"\x27]+)["\x27]', article_html, re.IGNORECASE):
                            _add_url(url)
                        # Regular src
                        for url in re.findall(r'<img[^>]+src=["\x27]([^"\x27]+)["\x27]', article_html, re.IGNORECASE):
                            _add_url(url)
                    # NOTE: We NEVER fall back to all-page <img> tags.
                    # If neither metadata nor <article> body has images, we give up.
                    # Better no photo than a logo/avatar/sidebar image.

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

        Strategy (in order):
        1. If not a Google redirect, return as-is
        2. Try HTTP redirect follow on the Google News URL itself
        3. Try DDG search with the article title
        4. If nothing works, try extracting from Google's redirect page

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
            logger.debug(f"Google News URL without title, can't resolve: {url[:60]}")
            return url

        # Strategy 1: Try following HTTP redirects on the Google News URL
        # Sometimes Google redirects directly to the article
        logger.info(f"Resolving Google News URL for: {title[:50]}")
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, max_redirects=10) as client:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
                resp = await client.get(url, headers=headers)
                final_url = str(resp.url)
                # Check if the redirect landed on a real article (not google.com)
                if final_url and 'google.com' not in final_url and final_url.startswith('http'):
                    logger.info(f"Google URL resolved via redirect → {final_url[:60]}")
                    return final_url
        except Exception as e:
            logger.debug(f"Google redirect follow failed: {e}")

        # Strategy 2: Try DDG search with the article title
        try:
            from bot.web_search import search_ddg_html
            # Clean title for search — remove site names in brackets, trailing dashes
            clean_title = re.sub(r'\s*[-–—]\s*[^–—]*$', '', title).strip()
            # Remove common Google News suffixes like " - BMW Blog"
            clean_title = re.sub(r'\s*[-–—|]\s*(BMW Blog|BimmerFile|Electrek|CarScoops|Autocar|AutoExpress|Reddit|InsideEVs)$', '', clean_title, flags=re.IGNORECASE).strip()
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
                    logger.info(f"Resolved Google URL via DDG → {r_url[:60]}")
                    return r_url
        except Exception as e:
            logger.debug(f"Google URL resolution via DDG failed: {e}")

        # Strategy 3: Try extracting the actual URL from Google's redirect page HTML
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                })
                html = resp.text
                # Google redirect pages often contain the target URL in data attributes or JS
                # Look for patterns like: data-url="...", href="...", window.location="..."
                url_patterns = [
                    r'data-url=["\x27](https?://[^"\x27]+)["\x27]',
                    r'window\.location\s*=\s*["\x27](https?://[^"\x27]+)["\x27]',
                    r'href=["\x27](https?://(?!news\.google\.com)[^"\x27]+)["\x27]',
                    r'<a[^>]+href=["\x27](https?://(?!news\.google\.com|google\.com)[^"\x27]+)["\x27]',
                    r'url=([^&"\x27]+)',
                ]
                for pattern in url_patterns:
                    matches = re.findall(pattern, html, re.IGNORECASE)
                    for match in matches:
                        from urllib.parse import unquote
                        candidate = unquote(match)
                        if candidate.startswith('http') and 'google.com' not in candidate:
                            logger.info(f"Resolved Google URL from HTML → {candidate[:60]}")
                            return candidate
        except Exception as e:
            logger.debug(f"Google URL HTML extraction failed: {e}")

        # Couldn't resolve — return original
        logger.warning(f"Could not resolve Google News URL: {url[:60]}")
        return url

    async def _get_post_images(self, news_item: Dict, resolved_url: str = "") -> tuple:
        """Get images for a news post — ONLY from curated news.json images.

        v8.0: USE ONLY PHOTOS FROM NEWS.JSON — no more article scraping.
        Each news item in news.json has a pre-curated `images[]` list
        with direct, high-quality image URLs. No more scraping, no more junk.

        The curated source already filters out thumbnails, logos, icons, etc.
        These are REAL article photos selected by the news curator.

        Returns (image_list: List[bytes], source: str)
        """
        image_list = []
        source = "none"
        title = news_item.get("title", "")

        # Download curated images — skip URL-based junk filters (already pre-filtered)
        curated_image_urls = news_item.get("image_urls", [])
        if curated_image_urls:
            try:
                curated_images = await self._download_images(curated_image_urls, max_count=MAX_IMAGES_PER_POST, curated=True)
                if curated_images:
                    image_list.extend(curated_images)
                    source = "curated"
                    logger.info(f"Downloaded {len(curated_images)} curated images for: {title[:50]}")
            except Exception as e:
                logger.debug(f"Curated image download failed: {e}")

        # Fallback: try article scraping if curated images failed or were empty
        if not image_list and resolved_url:
            try:
                scraped_images = await self._scrape_article_images(resolved_url, max_count=3)
                if scraped_images:
                    image_list.extend(scraped_images)
                    source = "scraped"
                    logger.info(f"Fallback: scraped {len(scraped_images)} images from article for: {title[:50]}")
            except Exception as e:
                logger.debug(f"Article scraping fallback failed: {e}")

        # Hard limit
        image_list = image_list[:MAX_IMAGES_PER_POST]

        if not image_list:
            logger.info(
                f"No images available for: {title[:60]}. "
                f"Post will be published as text-only."
            )

        return image_list, source

    async def _generate_post_text(self, news_item: Dict, has_media: bool = False, media_count: int = 0, resolved_url: str = "") -> Optional[str]:
        """Generate post text for a news item using AI.

        v7.0: Optimized for curated news.json source.
        - news.json provides direct URLs (no Google News redirects)
        - Summary from news.json is already good quality
        - Still tries to scrape full article for more detail
        - Falls back to curated summary if scraping fails
        """
        title = news_item.get("title", "")
        summary = news_item.get("summary", "")

        # ── Step 1: Use pre-resolved URL if available ──
        # news.json URLs are already direct — no Google News redirects
        if resolved_url:
            article_url = resolved_url
        else:
            article_url = news_item.get("url", "")
            # Only resolve if it's actually a Google redirect (rare for news.json)
            if article_url and 'news.google.com' in article_url:
                article_url = await self._resolve_article_url(article_url, title=title)

        # ── Step 2: Try to scrape FULL article text for more detail ──
        full_article_text = ""
        if article_url:
            try:
                from bot.sources.image_fetcher import fetch_article_text
                full_article_text = await fetch_article_text(article_url, max_chars=3000)
                if full_article_text:
                    logger.info(f"Scraped {len(full_article_text)} chars from article: {title[:50]}")
            except Exception as e:
                logger.debug(f"Article text scraping failed: {e}")

        # ── Step 3: Build context for AI ──
        context_parts = [get_date_context()]

        # BMW-specific context
        try:
            from bot.bmw_knowledge import build_bmw_context
            bmw_ctx = build_bmw_context(f"{title} {summary}")
            if bmw_ctx:
                context_parts.append(bmw_ctx)
        except Exception:
            pass

        # Use the BEST available text source:
        # 1. Scraped full article (best — complete text from the page)
        # 2. Curated summary from news.json (good — already detailed)
        if full_article_text:
            context_parts.append(
                f"ПОЛНЫЙ ТЕКСТ СТАТЬИ (используй факты для написания уникального поста):\n{full_article_text}"
            )
        elif summary:
            context_parts.append(
                f"Исходная новость (используй факты для написания уникального поста):\n{summary[:800]}"
            )

        if article_url:
            context_parts.append(f"Источник: {article_url}")

        # Add editorial aside hint
        aside = get_editorial_aside()
        if aside:
            context_parts.append(f"Редакционная шутка (используй если уместно): {aside}")

        # Add translation/uniquification hint
        lang = news_item.get("lang", "")
        uniquify_hint = get_translation_uniquification_hint(lang)
        if uniquify_hint:
            context_parts.append(uniquify_hint)

        # Explicit instruction to write unique content, not copy
        context_parts.append(
            "ЗАДАЧА: Прочитай факты из статьи и напиши СОВЕРШЕННО НОВЫЙ, УНИКАЛЬНЫЙ текст. "
            "НЕ копируй и НЕ пересказывай близко к тексту — собери факты и напиши СВОЙ текст. "
            "Если статья на английском — ПЕРЕВЕДИ факты и напиши пост на русском."
        )

        full_context = "\n\n".join(context_parts)

        # Determine content type from news item category
        content_type = news_item.get("content_type", "news+reaction")
        if not content_type or content_type in ("auto", "bmw_official", "bmw_community", "bmw_news"):
            content_type = "news+reaction"

        # Generate with AI using persona
        try:
            response = await get_ai_router().generate_channel_post(
                topic=title,
                context=full_context,
                content_type=content_type,
                has_media=has_media,
                media_count=media_count,
            )

            if response.error or not response.text:
                logger.warning(f"AI post generation failed: {response.error_message}")

                # ── LOCAL-ONLY FALLBACK ──
                # When all cloud providers failed, try the local model directly
                # with a simplified prompt optimized for the 4B model
                logger.info("Cloud providers failed — trying LOCAL-ONLY fallback for post generation")
                try:
                    local_response = await get_ai_router().generate_local_post(
                        title=title,
                        summary=summary or "",
                        has_media=has_media,
                    )
                    if local_response.ok and local_response.text:
                        logger.info(
                            "LOCAL-ONLY post generated successfully (%d chars, provider=%s)",
                            len(local_response.text), local_response.provider,
                        )
                        text = local_response.text
                    else:
                        logger.warning(f"LOCAL-ONLY fallback also failed: {local_response.error or 'empty response'}")
                        text = ""
                except Exception as local_err:
                    logger.error(f"LOCAL-ONLY fallback exception: {local_err}")
                    text = ""
            else:
                text = response.text

        except Exception as e:
            logger.error(f"AI generation error: {e}")

            # ── LOCAL-ONLY FALLBACK (exception path) ──
            # Even if the main generation threw an exception, try the local model
            logger.info("AI generation threw exception — trying LOCAL-ONLY fallback")
            try:
                local_response = await get_ai_router().generate_local_post(
                    title=title,
                    summary=summary or "",
                    has_media=has_media,
                )
                if local_response.ok and local_response.text:
                    logger.info(
                        "LOCAL-ONLY post generated after exception (%d chars)",
                        len(local_response.text),
                    )
                    text = local_response.text
                else:
                    logger.warning(f"LOCAL-ONLY fallback failed after exception: {local_response.error}")
                    return None
            except Exception as local_err:
                logger.error(f"LOCAL-ONLY fallback exception after main error: {local_err}")
                return None

        return text.strip() if text else None

    async def run_scheduled_post(self, exclude_titles: list = None) -> bool | dict:
        """Try to create and post content to the channel.

        v3.2: Retry with different article if dedup blocks the first one.
        Accepts exclude_titles to avoid re-selecting same article within a cycle.
        Returns the news_item dict on success (for tracking), True for backward compat,
        or False on failure.
        
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

            # Build list of titles to exclude (passed in + any we try and fail)
            tried_titles = list(exclude_titles) if exclude_titles else []
            max_retries = 10  # Try up to 10 different articles — dedup/AI may block several

            # v14.0 FIX: Restructured loop — AI text generation now happens INSIDE the loop
            # so that if generation fails for one item, the next item is tried (was: returned
            # False immediately, causing the bot to retry the SAME highest-scoring item
            # forever). Also: when get_best_news_item returns None (no items / all posted),
            # we now fall through to the evergreen fallback instead of returning False
            # (was: bot went silent for hours when the 71-item news pool was exhausted).

            selected_item: Optional[Dict] = None
            post_text: Optional[str] = None
            image_data_list: list = []
            image_source: str = ""
            has_media: bool = False
            media_count: int = 0
            resolved_url: str = ""
            entity_key: str = ""

            for attempt in range(max_retries):
                # Get best news item — pass exclude_titles to avoid re-selecting
                news_item = await get_best_news_item(exclude_titles=tried_titles)
                if not news_item:
                    # No items available (HTTP fail or all 71 URLs already posted)
                    # — break out and try evergreen fallback below
                    logger.info("No suitable news item found after exclusions — will try evergreen fallback")
                    break

                title = news_item.get("title", "")
                source_url = news_item.get("url", "")

                # ── PRIMARY DEDUP: Source URL ──
                # If the same article URL was already posted, skip it
                # regardless of what text the AI generates. This prevents
                # the same news being posted multiple times with different text.
                if source_url and await is_source_url_posted(source_url):
                    logger.info(f"Source URL already posted (attempt {attempt+1}): {source_url[:60]}")
                    tried_titles.append(title)
                    continue

                # ── SECONDARY DEDUP: Generated text hash ──
                if await is_duplicate_post(title, hours=48):
                    logger.info(f"Duplicate post (attempt {attempt+1}): {title[:60]}")
                    tried_titles.append(title)
                    continue

                # ── TERTIARY DEDUP: Semantic similarity ──
                if _is_semantically_duplicate(title):
                    logger.info(f"Semantic duplicate (attempt {attempt+1}): {title[:60]}")
                    tried_titles.append(title)
                    continue

                # ── ENTITY DEDUP: Topic registry ──
                entity_key = _extract_entities(title)
                if _is_topic_covered(entity_key):
                    logger.info(f"Topic already covered (attempt {attempt+1}): {entity_key}")
                    tried_titles.append(title)
                    continue

                # Found a non-duplicate item! Now try to actually generate the post text.
                # v7.0: news.json URLs are already direct — no need to resolve Google News redirects.
                # Only resolve if the URL is actually a Google redirect (rare for news.json).
                resolved_url = source_url
                if source_url and 'news.google.com' in source_url:
                    resolved_url = await self._resolve_article_url(source_url, title=title)
                    news_item["_resolved_url"] = resolved_url

                # v4.0: Get images FIRST so AI knows the char limit (1024 with media, 4096 without)
                image_data_list, image_source = await self._get_post_images(news_item, resolved_url=resolved_url)
                has_media = len(image_data_list) > 0
                media_count = len(image_data_list) if has_media else 0

                # Generate post text — now knows about media attachment, uses pre-resolved URL
                attempt_text = await self._generate_post_text(
                    news_item, has_media=has_media, media_count=media_count, resolved_url=resolved_url
                )

                if attempt_text:
                    # Success! Keep this item and its generated text.
                    selected_item = news_item
                    post_text = attempt_text
                    break

                # AI generation failed for THIS item — add to tried_titles so the next
                # iteration picks a DIFFERENT article (was: returned False, same item
                # retried forever on the next cycle).
                logger.info(
                    f"AI generation failed for (attempt {attempt+1}): {title[:60]} — trying next item"
                )
                tried_titles.append(title)
                continue
            else:
                # All retries exhausted — try evergreen/BMW fact fallback
                logger.info(f"All {max_retries} attempts blocked by dedup or AI failure, trying evergreen fallback")

            # ── EVERGREEN FALLBACK ──
            # Reached when: (a) get_best_news_item returned None (no items available),
            # (b) all 10 attempts were deduped, or (c) all 10 attempts failed AI generation.
            # In ALL these cases the bot previously returned False and went silent for
            # hours. Now we fall through to evergreen content so the channel keeps
            # receiving posts.
            if not selected_item or not post_text:
                logger.info("Trying evergreen fallback (no news item could be posted)")
                evergreen_result = await self._post_evergreen_fallback()
                if evergreen_result:
                    return evergreen_result
                return False

            news_item = selected_item

            # Clean and validate
            post_text = _clean_post_text(post_text)
            if not _validate_post_text(post_text):
                logger.warning(f"Post validation failed: {post_text[:80]}")
                return False

            # ── MEDIA DECISION ──
            #
            # RULES (Telegram limits: caption=1024, text-only=4096):
            #   1. Post with photo — ALWAYS preferred.
            #   2. Post without photo — when article has no images.
            #      Better no photo than a wrong (irrelevant) photo.
            #
            _CAPTION_LIMIT = config.TELEGRAM_CAPTION_LIMIT   # 1024
            _TEXT_LIMIT = config.TELEGRAM_TEXT_LIMIT          # 4096

            # v5.0: SIMPLIFIED — single enforcement path instead of multiple
            if has_media and len(post_text) > _CAPTION_LIMIT:
                # Has media + text too long — try compressing to keep media
                compressed = _enforce_char_limit(post_text, has_media=True)
                if len(compressed) >= 300:
                    post_text = compressed
                else:
                    # Compression made text too short — check if text-only is better
                    interest_score = _score_interest(
                        news_item.get("title", ""),
                        news_item.get("summary", "")
                    )
                    if interest_score >= 0.5 and len(post_text) <= _TEXT_LIMIT:
                        has_media = False
                        image_data_list = []
                        logger.info(f"Text too long for caption, interest={interest_score:.2f}. Publishing text-only.")
                    else:
                        # Force compress even if short — media is important
                        post_text = compressed

            # Ensure footer and final char limit (this is the ONLY enforcement point)
            post_text = _ensure_footer(post_text)
            post_text = _enforce_char_limit(post_text, has_media)

            # FINAL SIZE LOG
            logger.info(f"Final post: {len(post_text)} chars, has_media={has_media}, images={len(image_data_list) if has_media else 0}")

            # HARD SAFETY CHECK: never more than MAX_IMAGES_PER_POST images
            if has_media and len(image_data_list) > MAX_IMAGES_PER_POST:
                logger.warning(f"SAFETY: Truncating {len(image_data_list)} images to {MAX_IMAGES_PER_POST}")
                image_data_list = image_data_list[:MAX_IMAGES_PER_POST]

            # Post to channel
            sent_message = None
            try:
                if has_media and image_data_list:
                    # v5.0: _enforce_char_limit already ensured text fits caption limit
                    # No need for redundant [:1024] truncation here

                    # Save images to temp files — resize if needed for Telegram
                    # Telegram photo limits: max 10MB, each side 10-10000px,
                    # total w+h <= 10000. Invalid dimensions = PHOTO_INVALID_DIMENSIONS
                    tmp_paths = []
                    for i, img_data in enumerate(image_data_list[:MAX_IMAGES_PER_POST]):
                        tmp_path = os.path.join(tempfile.gettempdir(), f"masha_post_{int(time.time())}_{i}.jpg")
                        # Try to validate and resize with PIL before saving
                        try:
                            from PIL import Image
                            import io
                            img = Image.open(io.BytesIO(img_data))
                            w, h = img.size
                            needs_resize = False

                            # Telegram requires: each side between 10-10000px, w+h <= 10000
                            if w < 10 or h < 10:
                                logger.warning(f"Image too small ({w}x{h}), skipping")
                                continue
                            if w + h > 10000:
                                # Scale down so w+h <= 8000 (safe margin)
                                scale = 8000 / (w + h)
                                new_w = int(w * scale)
                                new_h = int(h * scale)
                                img = img.resize((new_w, new_h), Image.LANCZOS)
                                needs_resize = True
                                logger.info(f"Resized image: {w}x{h} -> {new_w}x{new_h} (w+h limit)")
                            elif w > 10000 or h > 10000:
                                # Single side exceeds Telegram max
                                scale = 9000 / max(w, h)
                                new_w = int(w * scale)
                                new_h = int(h * scale)
                                img = img.resize((new_w, new_h), Image.LANCZOS)
                                needs_resize = True
                                logger.info(f"Resized image: {w}x{h} -> {new_w}x{new_h} (side limit)")

                            if needs_resize:
                                # Convert to RGB if necessary (for JPEG save)
                                if img.mode in ('RGBA', 'P', 'LA'):
                                    img = img.convert('RGB')
                                buf = io.BytesIO()
                                img.save(buf, format='JPEG', quality=90)
                                img_data = buf.getvalue()

                        except ImportError:
                            logger.debug("PIL not available — saving image as-is")
                        except Exception as e:
                            logger.debug(f"Image resize check failed: {e} — saving as-is")

                        with open(tmp_path, "wb") as f:
                            f.write(img_data)
                        tmp_paths.append(tmp_path)

                    if len(tmp_paths) == 1:
                        # Single image — use send_photo
                        photo = FSInputFile(tmp_paths[0], filename="masha_post.jpg")
                        sent_message = await self._bot.send_photo(
                            chat_id=config.CHANNEL_ID,
                            photo=photo,
                            caption=post_text,
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
                                    caption=post_text,
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

                    # Save to DB — v5.0: pass has_image and image_url for stats
                    first_image_url = ""
                    if has_media and news_item.get("image_urls"):
                        first_image_url = news_item["image_urls"][0] if isinstance(news_item["image_urls"], list) else ""
                    await add_channel_post(
                        content=post_text,
                        message_id=sent_message.message_id,
                        post_type="news",
                        source_url=news_item.get("url", ""),
                        has_image=has_media,
                        image_url=first_image_url,
                    )

                    # Mark news as posted
                    if news_item.get("url"):
                        await mark_news_posted(news_item["url"])

                    # Register topic for dedup
                    _register_topic(entity_key, news_item["title"])
                    _record_post_title(news_item["title"])

                    logger.info(f"✅ Post published: {news_item['title'][:60]} (images={media_count}, source={image_source})")
                    return news_item  # Return news_item for cycle tracking

            except Exception as e:
                logger.error(f"Error posting to channel: {e}")
                # If photo failed, try posting as text-only (better than losing the post)
                if has_media and "PHOTO" in str(e).upper():
                    logger.info("Photo failed — retrying as text-only post")
                    try:
                        sent_message = await self._bot.send_message(
                            chat_id=config.CHANNEL_ID,
                            text=post_text[:4096],
                            parse_mode=ParseMode.HTML,
                        )
                        if sent_message:
                            await self._add_reaction(config.CHANNEL_ID, sent_message.message_id)
                            await add_channel_post(
                                content=post_text,
                                message_id=sent_message.message_id,
                                post_type="news",
                                source_url=news_item.get("url", ""),
                                has_image=False,
                                image_url="",
                            )
                            if news_item.get("url"):
                                await mark_news_posted(news_item["url"])
                            _register_topic(entity_key, news_item["title"])
                            _record_post_title(news_item["title"])
                            logger.info(f"✅ Text-only fallback post published: {news_item['title'][:60]}")
                            return news_item
                    except Exception as e2:
                        logger.error(f"Text-only fallback also failed: {e2}")
                return False

        except Exception as e:
            logger.error(f"Scheduled post error: {e}", exc_info=True)
            return False

    # ── Partner image pipeline ──────────────────────────────────────────────

    @staticmethod
    def _svg_to_png(svg_data: bytes, width: int = 800, height: int = 600) -> Optional[bytes]:
        """Convert SVG data to PNG using cairosvg.

        Creates a white-background PNG from an SVG logo.
        Returns PNG bytes or None if conversion fails.
        """
        try:
            import cairosvg
            import io
            png_bytes = cairosvg.svg2png(
                bytestring=svg_data,
                output_width=width,
                output_height=height,
                background_color="white",
            )
            if png_bytes and len(png_bytes) > 500:
                return png_bytes
        except ImportError:
            logger.debug("cairosvg not available for SVG→PNG conversion")
        except Exception as e:
            logger.debug(f"SVG→PNG conversion failed: {e}")
        return None

    @staticmethod
    def _create_partner_card(logo_png: bytes, partner_name: str, category: str = "") -> Optional[bytes]:
        """Create a branded partner card image with logo + text overlay.

        Layout:
        ┌────────────────────────────────┐
        │  BMW ///M Power Club           │  ← Header
        │  ─────────────────────         │
        │                                │
        │       [PARTNER LOGO]           │  ← Centered logo
        │                                │
        │  ─────────────────────         │
        │  Партнёр канала               │  ← Footer
        └────────────────────────────────┘

        Returns JPEG bytes or None on failure.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
            import io

            # Create canvas — 800x600 dark BMW-themed background
            W, H = 800, 600
            img = Image.new("RGB", (W, H), color=(20, 20, 30))
            draw = ImageDraw.Draw(img)

            # Try to load fonts
            try:
                font_large = ImageFont.truetype("/usr/share/fonts/truetype/chinese/NotoSansSC[wght].ttf", 28)
                font_small = ImageFont.truetype("/usr/share/fonts/truetype/chinese/NotoSansSC[wght].ttf", 18)
                font_header = ImageFont.truetype("/usr/share/fonts/truetype/chinese/NotoSansSC[wght].ttf", 22)
            except Exception:
                try:
                    font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
                    font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
                    font_header = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
                except Exception:
                    font_large = ImageFont.load_default()
                    font_small = ImageFont.load_default()
                    font_header = ImageFont.load_default()

            # Header: "BMW ///M Power Club"
            header_text = "BMW ///M Power Club"
            draw.text((W // 2, 30), header_text, fill=(0, 150, 255), font=font_header, anchor="mt")

            # Separator line
            draw.line([(50, 65), (W - 50, 65)], fill=(0, 100, 200), width=2)

            # Center partner logo
            try:
                logo_img = Image.open(io.BytesIO(logo_png)).convert("RGBA")
                # Resize to fit: max 400x350
                logo_w, logo_h = logo_img.size
                max_w, max_h = 400, 350
                ratio = min(max_w / max(logo_w, 1), max_h / max(logo_h, 1))
                if ratio < 1:
                    logo_img = logo_img.resize(
                        (int(logo_w * ratio), int(logo_h * ratio)),
                        Image.LANCZOS,
                    )
                logo_w, logo_h = logo_img.size

                # Paste centered on white background
                logo_bg = Image.new("RGBA", (logo_w + 20, logo_h + 20), (255, 255, 255, 240))
                logo_bg.paste(logo_img, (10, 10), logo_img if logo_img.mode == "RGBA" else None)

                paste_x = (W - logo_bg.width) // 2
                paste_y = 80 + (400 - logo_bg.height) // 2
                img.paste(logo_bg, (paste_x, paste_y), logo_bg if logo_bg.mode == "RGBA" else None)
            except Exception as e:
                logger.debug(f"Partner logo paste failed: {e}")

            # Separator line
            draw.line([(50, H - 90), (W - 50, H - 90)], fill=(0, 100, 200), width=2)

            # Footer: "Партнёр канала"
            footer_text = "🤝 Партнёр канала"
            draw.text((W // 2, H - 50), footer_text, fill=(180, 180, 180), font=font_small, anchor="mt")

            # Partner name (if fits)
            if partner_name:
                name_display = partner_name[:30]
                draw.text((W // 2, H - 25), name_display, fill=(255, 255, 255), font=font_small, anchor="mt")

            # Convert to JPEG
            output = io.BytesIO()
            img.convert("RGB").save(output, format="JPEG", quality=90)
            return output.getvalue()
        except Exception as e:
            logger.debug(f"Partner card creation failed: {e}")
            return None

    async def _get_partner_image(self, program) -> Optional[bytes]:
        """Get image for a partner post — from admitad_ads.json image URLs.

        v8.2: SIMPLE — just download the image from the source, no branded cards.
        If SVG → convert to PNG (Telegram doesn't support SVG).
        If raster (jpg/png/webp) → use as-is.
        No overlays, no cards, no branding — just the raw image.

        Returns image bytes (JPEG or PNG) or None.
        """
        # Collect all image URLs from the partner program
        image_urls = []
        for attr in ('image_url', 'logo_url', 'image', 'brand_logo', 'advertiser_logo', 'logo'):
            url = getattr(program, attr, '')
            if url and url not in image_urls:
                image_urls.append(url)

        if not image_urls:
            logger.info(f"No image URLs for partner: {program.name}")
            return None

        for url in image_urls:
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(url, headers={
                        "User-Agent": "MashaBot/1.0 (+https://t.me/asmasha_bot)",
                    })
                    if resp.status_code != 200:
                        continue

                    content = resp.content
                    if len(content) < 200:
                        continue

                    content_type = resp.headers.get("content-type", "").lower()
                    url_lower = url.lower()

                    # ── Case 1: Raster image (jpg/png/webp) → use as-is ──
                    if any(ft in content_type for ft in ["image/jpeg", "image/png", "image/webp", "image/gif"]) or \
                       any(url_lower.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']):
                        logger.info(f"Partner image downloaded: {program.name} ({len(content)} bytes)")
                        return content

                    # ── Case 2: SVG → convert to PNG (Telegram doesn't support SVG) ──
                    if "svg" in content_type or url_lower.endswith('.svg'):
                        png_data = self._svg_to_png(content, width=800, height=600)
                        if png_data:
                            logger.info(f"Partner SVG→PNG: {program.name} ({len(png_data)} bytes)")
                            return png_data
                        continue

            except Exception as e:
                logger.debug(f"Partner image download failed for {program.name} from {url[:60]}: {e}")
                continue

        logger.info(f"No usable image for partner: {program.name}")
        return None

    async def post_partner_content(self) -> bool:
        """Post partner content to the channel.

        v8.2: Uses raw images from admitad_ads.json — SVG→PNG conversion only.
        No branded cards — just the partner image as-is.
        Always tries to post WITH an image for maximum engagement.
        """
        if not partner_manager.should_post_partner():
            return False

        program = partner_manager.get_random_program()
        if not program:
            return False

        post_content = await partner_manager.generate_partner_post_content(program)

        if not _validate_post_text_partner(post_content):
            return False

        # Get partner image — from admitad_ads.json (SVG→PNG conversion built-in)
        image_data = await self._get_partner_image(program)

        try:
            if image_data:
                # Post with image — enforce caption limit
                if len(post_content) > 1024:
                    post_content = _enforce_char_limit(post_content, has_media=True)
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
                # No image available — post text-only
                if len(post_content) > 4096:
                    post_content = _enforce_char_limit(post_content, has_media=False)
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

    async def _post_evergreen_fallback(self) -> bool | dict:
        """Post evergreen/BMW fact content when all news items are blocked by dedup.

        This prevents the bot from reporting "3 consecutive empty cycles" and sending
        false alerts. When dedup blocks everything, we post a BMW fact or evergreen
        content instead of returning False.
        """
        try:
            # Try loading the evergreen pool
            import json
            from pathlib import Path
            evergreen_path = Path(__file__).parent / "data" / "evergreen_pool.json"
            if not evergreen_path.exists():
                logger.debug("Evergreen pool not found, skipping fallback")
                return False

            with open(evergreen_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pool = data.get("evergreen_pool", [])
            if not pool:
                logger.debug("Evergreen pool empty, skipping fallback")
                return False

            # Pick a random evergreen item
            import random as _rand
            item = _rand.choice(pool)
            topic = item.get("topic", "")
            context = item.get("context", "")
            content_type = item.get("content_type", "lore/history")
            character_hint = item.get("character_hint", "Маша")

            if not topic:
                return False

            # Build a simple evergreen news_item-like dict for post generation
            evergreen_item = {
                "title": topic,
                "summary": context,
                "url": "",
                "content_type": content_type,
                "lang": "ru",
            }

            # Generate post text using AI
            post_text = await self._generate_post_text(
                evergreen_item, has_media=False, media_count=0, resolved_url=""
            )
            if not post_text:
                # Even AI failed — post a simple static BMW fact
                post_text = f"🏎️ {topic}\n\n{context}\n\n#BMW #BMWMpower #МашаБМВ"

            # Clean and validate
            post_text = _clean_post_text(post_text)
            if not _validate_post_text(post_text):
                logger.warning("Evergreen post validation failed")
                return False

            # Enforce text limit (no media for evergreen)
            if len(post_text) > config.TELEGRAM_TEXT_LIMIT:
                post_text = _enforce_char_limit(post_text, has_media=False)

            # Send as text-only post
            sent = await self._bot.send_message(
                chat_id=config.CHANNEL_ID,
                text=post_text,
            )
            if sent:
                await add_channel_post(
                    content=post_text[:500],
                    message_id=sent.message_id,
                    post_type=content_type,
                    source_url="evergreen",
                )
                # Add reaction
                await self._add_reaction(config.CHANNEL_ID, sent.message_id)
                logger.info(f"Evergreen post published: {topic[:50]}")
                return evergreen_item

        except Exception as e:
            logger.error(f"Evergreen fallback error: {e}")

        return False


# Global instance
channel_manager = ChannelManager()
