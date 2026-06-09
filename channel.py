"""
Channel Manager -- Posts to @bmw_mpower_club with BMW-themed formatting.
Handles news posts, partner posts, scheduled content, reactions,
media, polls, and internet news search.
Properly enforces Telegram character limits: 1024 with media, 4096 without.
"""

import logging
import time
import random
import asyncio
import tempfile
import os
import re
import hashlib
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
ai_router = get_ai_router()
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
NEWS_IMAGES_MIN = 2
NEWS_IMAGES_MAX = 3
MAX_IMAGES_PER_POST = 10
MAX_RSS_IMAGES = 5
MAX_SCRAPE_IMAGES = 5
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
    """Clean post text: remove markdown, formatting artifacts, AI meta-comments."""
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
        "openai-large", "gpt-5.5", "mistral-4", "deepseek",
        "qwen-large", "deepseek-pro", "deepseek-v4", "minimax-m3",
        "qwen3-coder", "llama-3.3", "nova-2",
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
        """Download real images from news source URLs."""
        images = []
        if not image_urls:
            return images

        MAX_IMAGE_SIZE = 2 * 1024 * 1024

        for url in image_urls[:max_count * 3]:
            if len(images) >= max_count:
                break

            if self._is_junk_image_url(url):
                continue

            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    response = await client.get(url, headers={
                        "User-Agent": "MashaBot/1.0 (+https://t.me/asmasha_bot)",
                    })
                    if response.status_code != 200:
                        continue

                    content = response.content
                    if len(content) < 3000 or len(content) > MAX_IMAGE_SIZE:
                        continue

                    if b'<svg' in content[:500]:
                        continue

                    content_type = response.headers.get("content-type", "")
                    if not any(ft in content_type for ft in ["image/jpeg", "image/png", "image/webp", "image/gif"]):
                        if content[:3] == b'\xff\xd8\xff' or content[:4] == b'\x89PNG':
                            pass
                        elif content[:4] == b'RIFF' and content[8:12] == b'WEBP':
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

        return images

    @staticmethod
    def _is_junk_image_url(url: str) -> bool:
        """Check if an image URL is likely non-content."""
        url_lower = url.lower()
        junk_keywords = [
            "icon", "logo", "favicon", "avatar", "badge", "button",
            "banner", "spinner", "placeholder", "pixel", "tracker",
            "ad.", "ads/", "advert", "emoji", "captcha",
            "1x1", "spacer", "blank", "transparent",
            "thumb", "small", "preview", "mini", "tiny", "crop",
        ]
        for kw in junk_keywords:
            if kw in url_lower:
                return True
        return False

    @staticmethod
    def _is_content_image(image_data: bytes) -> bool:
        """Validate that image data represents a proper content photo."""
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
            return True
        except Exception:
            return True

    async def _generate_post_images(self, news_title: str, count: int = 1) -> List[bytes]:
        """Generate images for a news post using AI (fallback)."""
        images = []
        prompts = [
            f"BMW M5 F90 professional automotive photography: {news_title}. "
            f"Front three-quarter view, vibrant colors, high quality, dramatic lighting, no text.",
            f"BMW automotive news illustration: {news_title}. "
            f"Side profile shot, studio lighting, sleek design, M Power styling, no text.",
            f"BMW M Performance car: {news_title}. "
            f"Dynamic composition, professional car photography, vivid colors, no text.",
            f"BMW interior detail: {news_title}. "
            f"M Sport steering wheel, dashboard, premium feel, cinematic lighting, no text.",
        ]
        selected_prompts = prompts[:min(count, len(prompts))]

        for i, prompt in enumerate(selected_prompts):
            try:
                response = await ai_router.manager.generate_image(prompt=prompt, model="flux")
                if response.ok and response.image_b64:
                    import base64
                    img_bytes = base64.b64decode(response.image_b64)
                    images.append(img_bytes)
                elif response.ok and response.image_url:
                    # Download the image from the URL
                    try:
                        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                            dl_resp = await client.get(response.image_url)
                            if dl_resp.status_code == 200 and len(dl_resp.content) > 1000:
                                images.append(dl_resp.content)
                    except Exception as dl_err:
                        logger.debug(f"Failed to download generated image: {dl_err}")
            except Exception as e:
                logger.error(f"Image generation #{i+1} failed: {e}")

        logger.info(f"Generated {len(images)}/{count} AI images for post")
        return images

    async def _get_post_images(self, news_item: Dict) -> tuple:
        """Get images for a news post with smart strategy."""
        has_images = False
        image_data_list = []

        # 1. Try RSS images
        if news_item.get("image_urls"):
            try:
                downloaded = await self._download_news_images(news_item["image_urls"], max_count=2)
                image_data_list.extend(downloaded)
            except Exception as e:
                logger.debug(f"RSS image download error: {e}")

        # 2. Try scraping article page
        if len(image_data_list) < 2 and news_item.get("url"):
            try:
                scraped = await self._scrape_article_images(news_item["url"], max_count=2)
                image_data_list.extend(scraped)
            except Exception as e:
                logger.debug(f"Article scraping error: {e}")

        # 3. Web search enrichment
        if len(image_data_list) < 2:
            try:
                search_image_urls = await enrich_with_search_images(news_item["title"], max_images=2)
                if search_image_urls:
                    downloaded = await self._download_news_images(search_image_urls, max_count=2)
                    image_data_list.extend(downloaded)
            except Exception as e:
                logger.debug(f"Search image enrichment error: {e}")

        # 4. AI generation fallback
        if not image_data_list:
            try:
                ai_images = await self._generate_post_images(news_item["title"], count=1)
                image_data_list.extend(ai_images)
            except Exception as e:
                logger.debug(f"AI image generation error: {e}")

        has_images = len(image_data_list) > 0
        return has_images, image_data_list

    async def _scrape_article_images(self, article_url: str, max_count: int = 5) -> List[bytes]:
        """Scrape images from a news article page."""
        images = []
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(article_url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                })
                if response.status_code != 200:
                    return images

                html = response.text

                og_images = re.findall(r'<meta[^>]+property=["\x27]og:image["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE)
                og_images += re.findall(r'<meta[^>]+content=["\x27]([^"\x27]+)["\x27][^>]+property=["\x27]og:image["\x27]', html, re.IGNORECASE)

                candidate_urls = []
                seen = set()
                for url in og_images:
                    if url and url not in seen and len(url) > 30:
                        if url.startswith("//"):
                            url = "https:" + url
                        if not self._is_junk_image_url(url):
                            seen.add(url)
                            candidate_urls.append(url)

                images = await self._download_news_images(candidate_urls, max_count=max_count)
        except Exception as e:
            logger.debug(f"Article scraping failed: {e}")

        return images

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
            # Build combined context from summary + extra_context
            full_context = ""
            if summary:
                full_context = f"Исходная новость: {summary[:500]}"
            if extra_context:
                full_context = (full_context + "\n\n" + extra_context).strip() if full_context else extra_context

            response = await ai_router.generate_channel_post(
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
        """Try to create and post content to the channel."""
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

            # Get images
            has_images, image_data_list = await self._get_post_images(news_item)

            # Ensure footer and char limit
            post_text = _ensure_footer(post_text)
            post_text = _enforce_char_limit(post_text, has_media=has_images)

            # Post to channel
            sent_message = None
            try:
                if has_images and image_data_list:
                    if len(image_data_list) == 1:
                        # Single photo
                        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
                            f.write(image_data_list[0])
                            temp_path = f.name

                        photo = FSInputFile(temp_path)
                        sent_message = await self._bot.send_photo(
                            chat_id=config.CHANNEL_ID,
                            photo=photo,
                            caption=post_text[:1024],
                            parse_mode=ParseMode.HTML,
                        )
                        try:
                            os.unlink(temp_path)
                        except Exception:
                            pass
                    else:
                        # Album
                        media_group = []
                        for i, img_data in enumerate(image_data_list[:3]):
                            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
                                f.write(img_data)
                                temp_path = f.name

                            if i == 0:
                                media = InputMediaPhoto(
                                    media=FSInputFile(temp_path),
                                    caption=post_text[:1024],
                                    parse_mode=ParseMode.HTML,
                                )
                            else:
                                media = InputMediaPhoto(media=FSInputFile(temp_path))

                            media_group.append(media)

                        sent_messages = await self._bot.send_media_group(
                            chat_id=config.CHANNEL_ID,
                            media=media_group,
                        )
                        if sent_messages:
                            sent_message = sent_messages[0]

                        for m in media_group:
                            try:
                                if hasattr(m.media, 'path'):
                                    os.unlink(m.media.path)
                            except Exception:
                                pass
                else:
                    # Text-only post
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

                    logger.info(f"✅ Post published: {news_item['title'][:60]}")
                    return True

            except Exception as e:
                logger.error(f"Error posting to channel: {e}")
                return False

        except Exception as e:
            logger.error(f"Scheduled post error: {e}", exc_info=True)
            return False

    async def post_partner_content(self) -> bool:
        """Post partner content to the channel."""
        if not partner_manager.should_post_partner():
            return False

        program = partner_manager.get_random_program()
        if not program:
            return False

        post_content = await partner_manager.generate_partner_post_content(program)

        if not _validate_post_text_partner(post_content):
            return False

        try:
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
                logger.info(f"Partner post published: {program.name}")
                return True
        except Exception as e:
            logger.error(f"Partner post error: {e}")

        return False


# Global instance
channel_manager = ChannelManager()
