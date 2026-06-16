"""Smart Content Engine v2.0 — BMW-First automotive content pipeline for @bmw_mpower_club.

ARCHITECTURE:
  Phase 1: WEB SEARCH — Primary source: fresh BMW/automotive news from web search
  Phase 2: SCORE & SELECT — AI interest scoring with BMW focus, pick top candidates
  Phase 3: AI PICK — AI selects the BEST topic from top 5 candidates
  Phase 4: RSS FALLBACK — Supplement when web search yields nothing good
  Phase 5: DEDUPLICATE — Persistent topic registry with BMW entity extraction
  Phase 6: ENRICH — AI-powered deep content with BMW expert opinion
  Phase 7: IMAGE — Multi-strategy image sourcing with web search
  Phase 8: POST — Quality validation with BMW interest scoring

KEY FEATURES:
  - BMW-FIRST — BMW content prioritized over general automotive
  - BMW-specific search queries from bot.bmw_knowledge
  - BMW-specific interest scoring (M models, S-series engines = high interest)
  - BMW entity extraction for smart dedup
  - RSS as fallback/supplement, not primary source
  - Topic registry prevents duplicate coverage of same event
  - Date context — Маша always knows what year it is!
"""

import logging
import re
import time
import random
import hashlib
import asyncio
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

import httpx

from bot.config import config, persona
from bot.bmw_knowledge import (
    BMW_SEARCH_QUERIES, BMW_GOOGLE_NEWS_QUERIES,
    BMW_HIGH_INTEREST_KEYWORDS, BMW_LOW_INTEREST_KEYWORDS,
    BMW_AUTO_KEYWORDS_RU, BMW_AUTO_KEYWORDS_EN,
    is_bmw_topic, extract_bmw_model, extract_bmw_engine,
    build_bmw_context, BMW_MODELS, BMW_ENGINES, BMW_M_MODELS,
)
from ai.router import get_ai_router
from ai.providers.provider_manager import ROUTE_FUNCTION
from bot.web_search import web_search, search_news, search_google_news_rss

logger = logging.getLogger("masha.content_engine")

# ── Content Format Types for 24/7 schedule — BMW-themed ────────────────────────
CONTENT_FORMATS = {
    "bmw_news": {"emoji": "🏎️", "name": "BMW новость", "priority": 1},
    "world_news": {"emoji": "🌍", "name": "Мировая новость", "priority": 1},
    "m_power_day": {"emoji": "🔥", "name": "M Power день", "priority": 1},
    "part_of_day": {"emoji": "🔧", "name": "Запчасть дня", "priority": 2},
    "bmw_diy": {"emoji": "🛠️", "name": "BMW DIY", "priority": 2},
    "tech_fact": {"emoji": "🧠", "name": "Знаете ли вы?", "priority": 2},
    "global_poll": {"emoji": "💬", "name": "M Power опрос", "priority": 2},
    "masha_drive": {"emoji": "🏎️", "name": "Драйв Маши", "priority": 3},
    "garage_story": {"emoji": "🏠", "name": "Гаражная история", "priority": 3},
    "market_day": {"emoji": "🌐", "name": "Рынок дня", "priority": 3},
    "bmw_lesson": {"emoji": "🎓", "name": "BMW-урок", "priority": 3},
    "espresso": {"emoji": "☕", "name": "Эспрессо с Машей", "priority": 2},
    "legend": {"emoji": "🌙", "name": "Легенда ///M", "priority": 4},
    "fact_check": {"emoji": "🔍", "name": "Фактчек", "priority": 3},
    "night_tip": {"emoji": "🌙", "name": "Ночной совет", "priority": 4},
    "morning_greeting": {"emoji": "☀️", "name": "Доброе утро!", "priority": 1},
    "editors_life": {"emoji": "😂", "name": "Жизнь редакции", "priority": 4},
}

# Moscow timezone
_MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# ── Topic Registry ─────────────────────────────────────────────────────────────
_topic_registry: Dict[str, Dict] = {}
_REGISTRY_MAX_AGE_HOURS = 6  # Reduced from 24 — model-level keys like "bmw_m3" block all M3 articles for too long

# BMW-focused brands for entity extraction
_AUTO_BRANDS = [
    "BMW", "Alpina", "Mercedes", "Audi", "Volkswagen", "Porsche",
    "Toyota", "Honda", "Nissan", "Mazda", "Subaru", "Hyundai", "Kia",
    "Ford", "Chevrolet", "Lexus", "Volvo", "Tesla", "BYD", "Zeekr",
    "Chery", "Haval", "Geely", "Changan", "Exeed", "Tank",
    "Renault", "Peugeot", "Citroen", "Fiat", "Alfa Romeo",
    "Jaguar", "Land Rover", "Mini", "Suzuki", "Mitsubishi",
    "Infiniti", "Acura", "Genesis", "Rivian", "Lucid", "Polestar",
    "Maserati", "Ferrari", "Lamborghini", "Bentley", "Rolls-Royce",
    "Bugatti", "McLaren", "Aston Martin", "Lotus",
]

# BMW models for entity extraction
_BMW_MODELS_LIST = [
    "M2", "M3", "M4", "M5", "M6", "M8",
    "X3 M", "X4 M", "X5 M", "X6 M",
    "X1", "X2", "X3", "X4", "X5", "X6", "X7",
    "1 Series", "2 Series", "3 Series", "4 Series", "5 Series",
    "6 Series", "7 Series", "8 Series",
    "iX", "i4", "i5", "i7",
    # Generation codes
    "F90", "G90", "G80", "G82", "G87", "F80", "F82", "F87",
    "E39", "E46", "E60", "E90", "E30",
    "F10", "F30", "G20", "G30", "G60",
    "G05", "F15", "E70",
]

# BMW engines for entity extraction
_BMW_ENGINES_LIST = list(BMW_ENGINES.keys())

_NOTABLE_PEOPLE = [
    "Alonso", "Hamilton", "Verstappen", "Vettel", "Leclerc", "Norris",
    "Musk", "Маск", "Шумахер", "Сенна",
]

_MOTORSPORT_TEAMS = [
    "Red Bull", "Ferrari", "Mercedes", "McLaren", "Aston Martin",
    "F1", "Formula 1", "Формула 1", "WRC", "WEC", "Le Mans",
    "DTM", "IMSA",
]

_EVENT_KEYWORDS = [
    "reveal", "launch", "debut", "unveil", "release", "announce",
    "премьера", "запуск", "дебют", "анонс", "представлен", "выпуск",
    "recalls", "отзыв", "ban", "запрет", "record", "рекорд",
    "crash", "авария", "merger", "слияни", "bankruptcy", "банкрот",
    "redesign", "рестайлинг", "facelift", "update", "обновлен",
    "discontinue", "снят", "сняти", "spy", "шпионск", "prototype", "прототип",
]


def _extract_entities(title: str) -> str:
    """Extract key entities from a news title for dedup — BMW-focused.
    
    IMPORTANT: If we have a BMW brand but NO model, we must also have
    an engine or specific person to create a useful key. Brand+event only
    (like "bmw_debut") is TOO BROAD and blocks all BMW launch news.
    """
    title_lower = title.lower()

    # Extract brand
    brand = ""
    for b in _AUTO_BRANDS:
        if b.lower() in title_lower:
            brand = b.lower().replace(" ", "_")
            break

    # Extract BMW model
    bmw_model = ""
    for m in _BMW_MODELS_LIST:
        if m.lower() in title_lower:
            bmw_model = m.lower().replace(" ", "_")
            break

    # Extract BMW engine
    bmw_engine = ""
    for e in _BMW_ENGINES_LIST:
        if e.lower() in title_lower:
            bmw_engine = e.lower()
            break

    # Extract notable person
    person = ""
    for p in _NOTABLE_PEOPLE:
        if p.lower() in title_lower:
            person = p.lower().replace(" ", "_")
            break

    # Extract motorsport team
    team = ""
    for t in _MOTORSPORT_TEAMS:
        if t.lower() in title_lower:
            team = t.lower().replace(" ", "_")
            break

    # Extract event type
    event = ""
    for e in _EVENT_KEYWORDS:
        if e in title_lower:
            event = e
            break

    parts = [p for p in [brand, bmw_model, bmw_engine, person, team, event] if p]
    entity_key = "_".join(parts) if parts else ""
    
    # ── GUARD: Skip overly broad keys ──
    # "bmw_debut", "bmw_launch", "bmw_recall" etc. are too broad for
    # a BMW-focused channel — they block ALL BMW debut/launch/recall news.
    # Require at least a model or engine or person to make the key specific.
    if entity_key and brand and not bmw_model and not bmw_engine and not person and not team:
        # Only brand + event (like "bmw_debut") — too broad, return empty
        return ""

    return entity_key


def _is_topic_covered(entity_key: str) -> bool:
    """Check if this topic/entity was already posted about recently.
    
    IMPORTANT: Brand-only keys (e.g. "bmw") are SKIPPED — they would block
    an entire channel's content. Only compound keys like "bmw_m5" are checked.
    
    v8.4: Brand+model keys (e.g. "bmw_m3") use a SHORTER TTL (1 hour)
    and allow up to 3 posts per day. In a BMW-focused channel, multiple
    M3 articles per day are valid and should not be blocked.
    Full compound keys (e.g. "bmw_m3_recall") use the full TTL.
    """
    if not entity_key:
        return False
    
    # Skip brand-only keys — they're too broad for a focused channel
    brand_only_keys = {b.lower().replace(" ", "_") for b in _AUTO_BRANDS}
    if entity_key in brand_only_keys:
        return False
    
    entry = _topic_registry.get(entity_key)
    if not entry:
        return False
    age_hours = (time.time() - entry["last_posted"]) / 3600
    post_count = entry.get("post_count", 1)
    
    # Brand+model keys (like "bmw_m3") are still too broad — use shorter TTL
    # They block ALL articles about that model, which is too aggressive for
    # a BMW-focused channel where multiple M3/X5/i4 articles per day are normal.
    key_parts = entity_key.split("_")
    has_bmw = any(p in ("bmw", "mini", "rolls-royce", "alpina") for p in key_parts)
    part_count = len(key_parts)
    # If key is just brand+model (2 parts with BMW, or 3 parts like "bmw_x5_m")
    # use a much shorter TTL to allow more variety
    if has_bmw and part_count <= 3:
        max_age = 1  # 1 hour — allows multiple M3 posts per day
        # Allow up to 3 posts per day for brand+model keys without event context
        # (e.g. "bmw_m3" is fine 3 times/day, but "bmw_m3_recall" is more specific)
        if post_count >= 3:
            max_age = _REGISTRY_MAX_AGE_HOURS  # After 3 posts, apply full TTL to prevent spam
    else:
        max_age = _REGISTRY_MAX_AGE_HOURS  # Full TTL for specific keys like "bmw_m3_recall_s58"
    
    if age_hours > max_age:
        del _topic_registry[entity_key]
        return False
    return True


def _register_topic(entity_key: str, title: str):
    """Register that a topic was posted about."""
    if not entity_key:
        return
    now = time.time()
    if entity_key in _topic_registry:
        _topic_registry[entity_key]["post_count"] += 1
        _topic_registry[entity_key]["last_posted"] = now
        _topic_registry[entity_key]["titles"].append(title)
    else:
        _topic_registry[entity_key] = {
            "first_seen": now,
            "last_posted": now,
            "post_count": 1,
            "titles": [title],
        }

    # NOTE: Brand-only key registration REMOVED — was too aggressive for a
    # BMW-focused channel. "bmw" brand key blocked ALL subsequent BMW posts.
    # Now only model+engine specific keys (e.g. "bmw_m5", "bmw_x5_s63") are tracked.

    # Register person-only key (only if person is part of a compound key, not alone)
    # Avoid blocking all posts about a popular figure
    parts_count = len(entity_key.split("_"))
    if parts_count >= 2:  # Only if person appears with other context
        for p in _NOTABLE_PEOPLE:
            p_key = p.lower().replace(" ", "_")
            if p_key in entity_key and entity_key != p_key:
                if p_key not in _topic_registry:
                    _topic_registry[p_key] = {
                        "first_seen": now,
                        "last_posted": now,
                        "post_count": 1,
                        "titles": [f"[person-dedup] {title}"],
                    }
                else:
                    _topic_registry[p_key]["post_count"] += 1
                    _topic_registry[p_key]["last_posted"] = now
                break

    try:
        entry = _topic_registry[entity_key]
        import asyncio
        from bot.database import save_topic_to_registry
        asyncio.create_task(save_topic_to_registry(
            entity_key=entity_key,
            first_seen=entry["first_seen"],
            last_posted=entry["last_posted"],
            post_count=entry["post_count"],
            titles=entry["titles"],
        ))
    except Exception as e:
        logger.debug(f"Could not persist topic to DB: {e}")


def _cleanup_registry():
    """Remove old entries from topic registry."""
    now = time.time()
    max_age = _REGISTRY_MAX_AGE_HOURS * 3600
    expired = [k for k, v in _topic_registry.items() if now - v["last_posted"] > max_age]
    for k in expired:
        del _topic_registry[k]
    if expired:
        logger.info(f"Cleaned {len(expired)} expired topics from registry")


# ── Interest Scoring — BMW-focused ──────────────────────────────────────────────

_HIGH_INTEREST_KEYWORDS = BMW_HIGH_INTEREST_KEYWORDS + [
    "reveal", "debut", "launch", "unveil", "first", "новинка", "премьера",
    "рекорд", "record", "breakthrough",
    "дебют", "скандал", "отзыв", "ban", "recall", "revolutionary",
    "Mercedes AMG", "Porsche", "Ferrari",
    "electric", "EV", "электромобиль", "autonomous", "беспилот",
    "цена", "price", "стоимость",
    "тюнинг", "tuning", "рестайлинг", "facelift",
    "лучший", "худший", "самый", "worst", "best",
    "F1", "Formula 1", "DTM", "IMSA",
    "китайск", "Chinese cars",
]

_MEDIUM_INTEREST_KEYWORDS = [
    "update", "redesign", "обновлен", "рестайлинг",
    "test", "обзор", "тест-драйв", "review",
    "concept", "концепт", "prototype", "прототип",
    "hybrid", "гибрид", "plug-in", "PHEV",
    "новый", "new", "next-gen", "следующ",
    "мощност", "horsepower", "speed", "скорост",
    "двигатель", "engine", "turbo", "турбо",
    "автосалон", "auto show", "мотор-шоу",
    "продаж", "sales", "рынок", "market",
]

_LOW_INTEREST_KEYWORDS = BMW_LOW_INTEREST_KEYWORDS + [
    "report", "отчет", "statistics", "статистик",
    "share", "акци", "stock", "investor",
]


def _score_interest(title: str, summary: str = "") -> float:
    """Rate how interesting a news item is — BMW-focused 0-1 scale."""
    text = f"{title} {summary}".lower()
    score = 0.5

    # BMW bonus — highest priority
    if is_bmw_topic(text):
        score += 0.2

    # BMW M model bonus
    bmw_model = extract_bmw_model(text)
    if bmw_model:
        score += 0.15

    # BMW engine code bonus
    bmw_engine = extract_bmw_engine(text)
    if bmw_engine:
        score += 0.1

    # General high-interest keywords
    for kw in _HIGH_INTEREST_KEYWORDS:
        if kw.lower() in text:
            score += 0.15
            break

    medium_count = sum(1 for kw in _MEDIUM_INTEREST_KEYWORDS if kw.lower() in text)
    score += min(medium_count * 0.05, 0.15)

    for kw in _LOW_INTEREST_KEYWORDS:
        if kw.lower() in text:
            score -= 0.1
            break

    # HEAVY penalty for boring Russian auto brands
    _BORING_RUSSIAN_BRANDS = ["автоваз", "лада", "lada", "уаз", "uaz", "камаз", "kamaz",
                              "соллерс", "vesta", "granta", "niva"]
    for kw in _BORING_RUSSIAN_BRANDS:
        if kw.lower() in text:
            score -= 0.4
            break

    if len(title) > 120:
        score -= 0.1

    return max(0.1, min(1.0, score))


def _score_freshness(published_time: float) -> float:
    """Score how fresh a news item is."""
    if not published_time:
        return 0.3
    age_hours = (time.time() - published_time) / 3600
    if age_hours < 3:
        return 0.4
    elif age_hours < 6:
        return 0.3
    elif age_hours < 12:
        return 0.2
    elif age_hours < 24:
        return 0.1
    elif age_hours < 48:
        return -0.5
    else:
        return -1.0


# ── Web Search Content ──────────────────────────────────────────────────────────

# BMW-specific search queries + general auto queries
_SEARCH_QUERIES_ROTATION = BMW_SEARCH_QUERIES + [
    # General auto queries
    "автомобильные новости сегодня",
    "новые автомобили {year} премьера",
    "автоновости Россия",
    "электромобили новости {year}",
    "автомобильный рынок Россия {year} цены",
    "китайские автомобили Россия {year}",
    "automotive industry news today",
    "new car models {year} reveal",
    "electric vehicle news",
    "car industry updates",
    "car recalls and safety {year}",
    "F1 Formula 1 news {year}",
    "BMW Mercedes Audi news latest",
    "Porsche Ferrari supercar news",
    "Geneva Motor Show {year} reveals debuts",
    "SEMA Show {year} tuning custom cars",
]

_recent_query_indices: list = []
_MAX_RECENT_QUERIES = 10

_GOOGLE_NEWS_RSS_QUERIES = BMW_GOOGLE_NEWS_QUERIES + [
    ("автомобили новости", "ru", "RU"),
    ("электромобили зарядные станции", "ru", "RU"),
    ("automotive industry news", "en", "US"),
    ("electric vehicles latest", "en", "US"),
    ("car recalls safety", "en", "US"),
]


def _get_search_query() -> str:
    """Get a search query avoiding recent repetition."""
    global _recent_query_indices
    now = datetime.now(_MOSCOW_TZ)
    year = now.year

    available = [i for i in range(len(_SEARCH_QUERIES_ROTATION)) if i not in _recent_query_indices]
    if not available:
        _recent_query_indices = []
        available = list(range(len(_SEARCH_QUERIES_ROTATION)))

    idx = random.choice(available)
    _recent_query_indices.append(idx)
    if len(_recent_query_indices) > _MAX_RECENT_QUERIES:
        _recent_query_indices = _recent_query_indices[-_MAX_RECENT_QUERIES:]

    query = _SEARCH_QUERIES_ROTATION[idx]
    return query.format(year=year)


def _extract_published_time_from_snippet(snippet: str) -> float:
    """Try to extract actual publication time from search result snippet."""
    if not snippet:
        return 0
    snippet_lower = snippet.lower()
    now = datetime.now(_MOSCOW_TZ)

    stale_patterns = [
        (r'(\d+)\s+week', 7 * 24),
        (r'(\d+)\s+месяц', 30 * 24),
        (r'вчера', 24),
        (r'прошлый\s+недел', 7 * 24),
        (r'прошлый\s+месяц', 30 * 24),
        (r'прошлогод', 365 * 24),
    ]
    for pattern, hours_per_unit in stale_patterns:
        match = re.search(pattern, snippet_lower)
        if match:
            try:
                count = int(match.group(1)) if match.lastindex else 1
            except (ValueError, IndexError):
                count = 1
            hours_ago = count * hours_per_unit
            return now.timestamp() - (hours_ago * 3600)

    rel_patterns = [
        (r'(\d+)\s+hours?\s+ago', 1),
        (r'(\d+)\s+минут', 1/60),
        (r'(\d+)\s+час', 1),
        (r'(\d+)\s+дн[еяь]', 24),
        (r'(\d+)\s+days?\s+ago', 24),
        (r'сегодня|today', 0),
    ]
    for pattern, hours_per_unit in rel_patterns:
        match = re.search(pattern, snippet_lower)
        if match:
            try:
                count = int(match.group(1)) if match.lastindex else 1
            except (ValueError, IndexError):
                count = 1
            hours_ago = count * hours_per_unit
            return now.timestamp() - (hours_ago * 3600)

    return 0


def get_date_context() -> str:
    """Get current date context for AI prompts."""
    now = datetime.now(_MOSCOW_TZ)
    month_ru = ["января", "февраля", "марта", "апреля", "мая", "июня",
               "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    return f"Сегодня {now.day} {month_ru[now.month - 1]} {now.year}"


def get_editorial_aside() -> str:
    """Get a random editorial aside from persona config."""
    if persona.editorial_asides:
        return random.choice(persona.editorial_asides)
    return ""


def get_translation_uniquification_hint(lang: str = "") -> str:
    """Get hint for AI about translating and uniquifying content.
    
    Args:
        lang: Source language code (e.g., 'en', 'de'). Empty = assume Russian.
    """
    if lang and lang != "ru":
        lang_names = {
            "en": "английском",
            "de": "немецком",
            "fr": "французском",
            "it": "итальянском",
            "es": "испанском",
            "zh": "китайском",
            "ja": "японском",
            "ko": "корейском",
        }
        lang_name = lang_names.get(lang, "иностранном")
        return (
            f"ПЕРЕВОД И УНИКАЛИЗАЦИЯ: Исходная новость на {lang_name} — "
            "ПЕРЕВЕДИ на русский и УНИКАЛИЗИРУЙ текст: перескажи СВОИМИ словами, "
            "добавь мнение редакции, BMW-экспертный комментарий. "
            "НЕ копируй перевод дословно!"
        )
    return (
        "ПЕРЕВОД И УНИКАЛИЗАЦИЯ: Если исходная новость на английском — "
        "ПЕРЕВЕДИ на русский и УНИКАЛИЗИРУЙ текст: перескажи СВОИМИ словами, "
        "добавь мнение редакции, BMW-экспертный комментарий. "
        "НЕ копируй перевод дословно!"
    )


async def ai_discover_news() -> List[Dict]:
    """Ask AI to discover today's top BMW/automotive news."""
    items = []
    now = datetime.now(_MOSCOW_TZ)
    month_ru = ["января", "февраля", "марта", "апреля", "мая", "июня",
               "июля", "августа", "сентября", "октября", "ноября", "декабря"]

    date_str = f"{now.day} {month_ru[now.month - 1]} {now.year}"

    recently_posted_titles = []
    try:
        from bot.database import get_recent_post_titles
        recently_posted_titles = await get_recent_post_titles(hours=72, limit=30)
    except Exception:
        pass

    recently_posted_str = ""
    if recently_posted_titles:
        titles_list = "\n".join(f"  - {t[:80]}" for t in recently_posted_titles[:20])
        recently_posted_str = (
            f"\n\nУЖЕ ОПУБЛИКОВАНО (НЕ ПОВТОРЯЙ ЭТИ ТЕМЫ):\n{titles_list}\n"
            f"НЕ называй новости, которые дублируют уже опубликованные!"
        )

    _DISCOVERY_MODELS = ["openai-large", "mistral-large", "deepseek", "openai", "llama"]

    for model_name in _DISCOVERY_MODELS:
        try:
            response = await get_ai_router().manager.chat(
                messages=[
                    {"role": "system", "content": (
                        f"Ты BMW-эксперт. Сегодня {date_str}. "
                        f"Назови 15 самых важных и свежих BMW и автомобильных новостей СЕГОДНЯ. "
                        f"ПРИОРИТЕТ — BMW: новые модели, M Power, Alpina, тюнинг, отзывы, "
                        f"двигатели (S63, S58, B58, N55), VANOS, ISTA, BimmerCode. "
                        f"Также интересны: автоспорт (F1, DTM), электромобили, "
                        f"китайский автопром, суперкары. "
                        f"НЕ включай: АвтоВАЗ/LADA/УАЗ/ГАЗ, политику, войну. "
                        f"Каждая новость — одна строка: НОВОСТЬ | краткое описание "
                        f"Пиши на русском. ТОЛЬКО автомобили."
                        f"{recently_posted_str}"
                    )},
                ],
                model=model_name,
                temperature=0.7,
                max_tokens=1500,
                route_type=ROUTE_FUNCTION,
            )

            if response.error or not response.text or not response.text.strip():
                continue

            text = response.text.strip()
            logger.info(f"AI discovery ({model_name}): got {len(text)} chars")

            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                line = re.sub(r'^\d+[\.\)]\s*', '', line)
                line = re.sub(r'^[-•*]\s*', '', line)
                if not line:
                    continue

                parts = line.split("|", 1)
                title = parts[0].strip()
                summary = parts[1].strip() if len(parts) > 1 else ""

                if len(title) < 10:
                    continue

                if any(kw in title.lower() for kw in ["конечно", "вот список", "список новостей"]):
                    continue

                _editorial_keywords = [
                    "не ставим", "не автоновост", "не автомобильн", "отсеивать",
                    "не наш формат", "перепишу тему", "предложу свеж",
                ]
                if any(kw in title.lower() or kw in summary.lower() for kw in _editorial_keywords):
                    continue

                url = ""
                try:
                    search_results = await web_search(title[:60], max_results=1)
                    if search_results and search_results[0].url:
                        url = search_results[0].url
                        if not summary and search_results[0].snippet:
                            summary = search_results[0].snippet[:300]
                except Exception:
                    pass

                if not url:
                    url = f"ai_discovered_{hashlib.md5(title.encode()).hexdigest()[:12]}"

                items.append({
                    "source": f"ai_discovery_{model_name}",
                    "title": title,
                    "url": url,
                    "summary": summary[:500],
                    "published": time.time(),
                    "published_time": time.time(),
                    "category": "auto",
                    "lang": "ru",
                })

        except Exception as e:
            logger.warning(f"AI discovery failed for model {model_name}: {e}")
            continue

    logger.info(f"AI discovery: {len(items)} total items")
    return items


async def search_auto_news() -> List[Dict]:
    """Fetch automotive/BMW news with multi-source fallback.

    v9.0: MULTI-SOURCE — tries curated bmw-news.json first, then falls back
    to RSS feeds and web search when bmw-news.json is unavailable.

    Source priority:
      1. Curated bmw-news.json from sochiautoparts/nws (preferred — BMW-filtered, with images)
      2. RSS feeds (15+ BMW/automotive sources via BMWRSSFetcher)
      3. Web search (Google News RSS + DDG + SearXNG)

    This ensures the bot never goes silent even if the primary source
    goes down (404/timeout/etc).
    """
    try:
        from news import fetch_news_multi_source
        items = await fetch_news_multi_source(limit=500)
        logger.info(f"Loaded {len(items)} items via multi-source fallback")
        return items
    except Exception as e:
        logger.error(f"Multi-source news fetch failed: {e}")
        return []


async def get_best_news_item(items: List[Dict] = None, exclude_titles: List[str] = None) -> Optional[Dict]:
    """Select the best news item from candidates using AI interest scoring.
    
    Args:
        items: Pre-fetched items. If None, fetches fresh news.
        exclude_titles: List of titles to exclude (already posted/tried this cycle).
    
    v8.0: Added source URL dedup — if an article URL was already posted,
    it's skipped regardless of AI-generated text differences.
    """
    if items is None:
        items = await search_auto_news()

    if not items:
        return None

    # Build exclusion set from titles already tried this cycle
    exclude_set = set()
    if exclude_titles:
        for t in exclude_titles:
            # Normalize: lowercase, strip
            exclude_set.add(t.lower().strip()[:80])

    # Pre-filter: check which source URLs were already posted
    # This is the PRIMARY dedup mechanism
    posted_urls = set()
    try:
        from bot.database import is_source_url_posted
        for item in items:
            url = item.get("url", "")
            if url:
                is_posted = await is_source_url_posted(url)
                if is_posted:
                    posted_urls.add(url)
        if posted_urls:
            logger.info(f"Source URL dedup: {len(posted_urls)} already-posted URLs filtered out")
    except Exception as e:
        logger.debug(f"Source URL pre-filter failed: {e}")

    # Score and sort — v5.0: bonus for items with images (more engaging posts)
    scored = []
    for item in items:
        title = item.get("title", "")
        url = item.get("url", "")
        
        # Skip items already tried this cycle (exact match on first 80 chars)
        if title.lower().strip()[:80] in exclude_set:
            continue
        
        # Skip items whose source URL was already posted (PRIMARY dedup)
        if url and url in posted_urls:
            continue
        
        interest = _score_interest(title, item.get("summary", ""))
        freshness = _score_freshness(item.get("published_time", 0))
        total = interest + freshness
        
        # v5.0: Bonus for items with image_urls — posts with photos get 3x more views
        image_urls = item.get("image_urls", [])
        if image_urls and len(image_urls) > 0:
            total += 0.3  # Significant bonus for having photos
        
        # v8.2: Deprioritize Reddit/community posts vs real news articles
        # Reddit posts are personal stories, not news — they should rank lower
        source = item.get("source", "")
        url = item.get("url", "")
        if "reddit.com" in url or "redd.it" in url or "reddit" in source.lower():
            total -= 0.5  # Significant penalty for Reddit content
        # Personal/community posts have informal titles
        title_lower = title.lower()
        reddit_phrases = ["my first bmw", "new to me", "just bought", "just cleaned",
                          "you can only have", "what do you think", "look at this",
                          "check out my", "my baby", "finally got", "dream come true",
                          "should i buy", "rate my", "love my", "my new"]
        if any(phrase in title_lower for phrase in reddit_phrases):
            total -= 0.4  # Penalty for personal/community-style titles
        
        scored.append((total, item))

    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return None

    # Pick from top 10 (increased from 5 for more variety)
    top = scored[:10]
    if not top:
        return None

    # Check entity dedup for top candidates (topic registry)
    # Other dedup (exact hash, semantic) is checked by the caller (channel.py)
    for score, item in top:
        entity_key = _extract_entities(item.get("title", ""))
        if not _is_topic_covered(entity_key):
            return item

    # If all top candidates are entity-covered, return highest scoring anyway
    # (the caller will still do its own dedup checks)
    return top[0][1] if top else None
