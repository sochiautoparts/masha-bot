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
ai_router = get_ai_router()
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
_REGISTRY_MAX_AGE_HOURS = 72

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
    """Extract key entities from a news title for dedup — BMW-focused."""
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

    return entity_key


def _is_topic_covered(entity_key: str) -> bool:
    """Check if this topic/entity was already posted about recently."""
    if not entity_key:
        return False
    entry = _topic_registry.get(entity_key)
    if not entry:
        return False
    age_hours = (time.time() - entry["last_posted"]) / 3600
    if age_hours > _REGISTRY_MAX_AGE_HOURS:
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

    # Register brand-only key
    for b in _AUTO_BRANDS:
        b_key = b.lower().replace(" ", "_")
        if b_key in entity_key:
            if b_key not in _topic_registry:
                _topic_registry[b_key] = {
                    "first_seen": now,
                    "last_posted": now,
                    "post_count": 1,
                    "titles": [f"[brand-dedup] {title}"],
                }
            else:
                _topic_registry[b_key]["post_count"] += 1
                _topic_registry[b_key]["last_posted"] = now
            break

    # Register person-only key
    for p in _NOTABLE_PEOPLE:
        p_key = p.lower().replace(" ", "_")
        if p_key in entity_key:
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


def get_translation_uniquification_hint() -> str:
    """Get hint for AI about translating and uniquifying content."""
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
            response = await ai_router.manager.chat(
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
    """Search for automotive/BMW news using web search."""
    items = []

    query = _get_search_query()
    logger.info(f"Web search query: {query[:60]}")

    try:
        results = await web_search(query, max_results=8)
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return items

    for r in results:
        published_time = _extract_published_time_from_snippet(r.snippet)

        items.append({
            "source": "web_search",
            "title": r.title,
            "url": r.url,
            "summary": r.snippet[:500] if r.snippet else "",
            "published": published_time or time.time(),
            "published_time": published_time,
            "category": "auto",
            "lang": "en" if any(c.isascii() for c in r.title[:20]) else "ru",
        })

    # Also try Google News RSS
    try:
        gn_query = random.choice(BMW_GOOGLE_NEWS_QUERIES)
        gn_results = await search_google_news_rss(
            query=gn_query[0], lang=gn_query[1], gl=gn_query[2], max_results=5
        )
        for r in gn_results:
            items.append({
                "source": "google_news_rss",
                "title": r.title,
                "url": r.url,
                "summary": r.snippet[:500] if r.snippet else "",
                "published": time.time(),
                "published_time": time.time(),
                "category": "auto",
                "lang": gn_query[1],
            })
    except Exception as e:
        logger.debug(f"Google News RSS search failed: {e}")

    return items


async def enrich_with_search_images(title: str, max_images: int = 3) -> List[str]:
    """Search for images related to a news topic."""
    image_urls = []
    try:
        clean_title = re.sub(r'[^\w\s]', '', title)[:60]
        results = await web_search(f"{clean_title} BMW photo", max_results=3)
        for r in results:
            if r.url and any(ext in r.url.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                image_urls.append(r.url)
    except Exception as e:
        logger.debug(f"Image search failed: {e}")
    return image_urls[:max_images]


async def get_best_news_item(items: List[Dict] = None) -> Optional[Dict]:
    """Select the best news item from candidates using AI interest scoring."""
    if items is None:
        items = await search_auto_news()

    if not items:
        return None

    # Score and sort
    scored = []
    for item in items:
        interest = _score_interest(item["title"], item.get("summary", ""))
        freshness = _score_freshness(item.get("published_time", 0))
        total = interest + freshness
        scored.append((total, item))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Pick from top 5
    top = scored[:5]
    if not top:
        return None

    # Check dedup for top candidates
    for score, item in top:
        entity_key = _extract_entities(item["title"])
        if not _is_topic_covered(entity_key):
            return item

    # If all top candidates are covered, return highest scoring anyway
    return top[0][1] if top else None
