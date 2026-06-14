"""Scheduler for masha-bot — determines when and what to post.

Hybrid scheduling with priority-based content selection:
- Priority 1: Urgent BMW news (recalls, new models, Nürburgring records)
- Priority 2: Theme day content (M-Monday, Tech Tuesday, etc.)
- Priority 3: Evergreen buffer (pre-made content when no fresh news)
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Theme day definitions ─────────────────────────────────────────────────────

THEME_DAYS: dict[int, dict[str, Any]] = {
    0: {  # Monday
        "name": "M-Monday",
        "emoji": "🔥",
        "description": "M-модели, M-division новости, M Performance",
        "default_type": "news+reaction",
        "topics": [
            "BMW M3 G80 — новый рекорд Нюрбургринга?",
            "M5 F90 Competition — король седанов",
            "M4 CSL — наследие CSL",
            "M2 G87 — компактная мощь",
            "XM — M-гибрид будущего",
            "M Performance Parts — что стоит купить",
            "M-division история: от M1 до XM",
            "M3 Touring — мечта стала реальностью",
        ],
    },
    1: {  # Tuesday
        "name": "Tech Tuesday",
        "emoji": "🔧",
        "description": "Двигатели, VANOS, Valvetronic, кодинг",
        "default_type": "DIY/how-to",
        "topics": [
            "VANOS — как работает и почему стучит",
            "B58 vs N55 — эволюция рядной шестёрки",
            "S63 — битурбо V8 от M5 F90",
            "Valvetronic — бесступенчатый впуск",
            "xDrive — полный привод BMW",
            "DME/DDE — мозги вашего BMW",
            "ISTA vs INPA — что выбрать для диагностики",
            "BimmerCode — кодинг своими руками",
            "DKG / ZF 8HP — коробки BMW",
        ],
    },
    2: {  # Wednesday
        "name": "Workshop Wednesday",
        "emoji": "🔩",
        "description": "DIY, обслуживание, советы Серёги",
        "default_type": "DIY/how-to",
        "topics": [
            "Замена масла в N55 — пошаговая инструкция",
            "VANOS соленоиды — чистка или замена?",
            "Тормозные колодки для M5 — OEM или аналог?",
            "Фильтры BMW — когда менять и какие брать",
            "Проблемы B48 — что знать владельцу",
            "Катушки зажигания — OEM vs aftermarket",
            "Антифриз BMW — какой лить и почему",
            "Диагностика ISTA — первый раз",
        ],
    },
    3: {  # Thursday
        "name": "Throwback Thursday",
        "emoji": "⏪",
        "description": "Классика BMW, E30, E39, E46, история",
        "default_type": "lore/history",
        "topics": [
            "E30 M3 — первая эмка BMW",
            "E39 M5 — лучший спортивный седан",
            "E46 M3 CSL — легенда Нюрбургринга",
            "2002 Turbo — первый турбо BMW",
            "M1 Procar — суперкар от BMW",
            "E28 M5 — оригинальная M-пятёрка",
            "E36 M3 — доступная классика",
            "Z3 M Coupe — тапок Clarkson",
            "BMW 507 — красота 50-х",
        ],
    },
    4: {  # Friday
        "name": "Freaky Friday",
        "emoji": "🤪",
        "description": "Кастом, тюнинг, Alpina, AC Schnitzer",
        "default_type": "polls/debates",
        "topics": [
            "Alpina B3 vs M340i — что выбрать?",
            "AC Schnitzer — тюнинг со вкусом?",
            "M Performance выхлоп — стоит ли?",
            "Individual цвета — топ-5 самых редких",
            "Чип-тюнинг B58 — Stage 1, 2, 3",
            "Bagged BMW — круто или нет?",
            " widestance BMW — за и против",
            "BMW Tokyo Auto Salon — лучшие проекты",
        ],
    },
    5: {  # Saturday
        "name": "Spotlight Saturday",
        "emoji": "🔦",
        "description": "Подробный обзор конкретной модели",
        "default_type": "news+reaction",
        "topics": [
            "BMW iX M60 — электрический M?",
            "BMW X5 M Competition — SUV-монстр",
            "BMW 3 серии G20 — бестселлер",
            "BMW i4 M50 — электромобиль с характером",
            "BMW Z4 M40i — последний родстер?",
            "BMW X3 M — компактный SUV-ракета",
            "BMW 7 серии G70 — флагман нового поколения",
            "BMW i7 — роскошь будущего",
        ],
    },
    6: {  # Sunday
        "name": "Sunday Drive",
        "emoji": "🛣️",
        "description": "Культура вождения, Нюрбургринг, роад-трипы",
        "default_type": "lore/history",
        "topics": [
            "Нюрбургринг — дом BMW",
            "M5 на Нюрбургринге — lap guide",
            "Лучшие дороги для BMW в Европе",
            "BMW Driving Experience — стоит ли?",
            "Зимний дрифт на M5 — инструкция",
            "M-цвета: синий, фиолетовый, красный — история",
            "BMW и кино — самые эпичные погони",
            "BMW клубы — зачем вступать",
        ],
    },
}

# ── Urgent news keywords ─────────────────────────────────────────────────────

URGENT_KEYWORDS = [
    "recall", "отзыв", "проблема безопасности",
    "new model", "новая модель", "премьера",
    "nürburgring record", "рекорд нюрбургринга",
    "m-power", "///m", "m3 g80", "m4 g82",
    "bmw recall", "bmw отзывная",
    "electric bmw", "электробmw",
    "i5 m60", "i7 m70", "ix m60",
    "m5 g90", "новый m5",
]

# ── Posting schedule (UTC hours) ─────────────────────────────────────────────

DEFAULT_POSTING_HOURS = list(range(24))  # Every hour: 0,1,2,...,23


class Scheduler:
    """Determines when and what content to produce."""

    def __init__(self) -> None:
        self._last_urgent_check: Optional[datetime] = None

    def get_current_theme(self) -> dict[str, Any] | None:
        """Get the theme for today based on the day of week."""
        now = datetime.now(timezone.utc)
        day = now.weekday()  # 0=Monday
        theme = THEME_DAYS.get(day)
        if theme:
            # Pick a random topic for today's theme
            topics = theme.get("topics", [])
            if topics:
                # Use day + week number to vary topics
                week_num = now.isocalendar()[1]
                topic_index = (week_num + day) % len(topics)
                theme["topic"] = topics[topic_index]
            return theme
        return None

    def should_post_now(self, posts_today: int, max_posts: int = 20) -> bool:
        """Check if we should post right now."""
        if posts_today >= max_posts:
            return False

        now = datetime.now(timezone.utc)
        current_hour = now.hour

        # Check if current hour is in posting schedule
        return current_hour in DEFAULT_POSTING_HOURS

    def get_content_type_distribution(self) -> str:
        """Select content type based on distribution weights.

        Distribution:
        - news+reaction: 50%
        - DIY/how-to: 10%
        - polls/debates: 10%
        - lore/history: 10%
        - garage stories: 10%
        - partner: 10% (separate hourly post)
        """
        weights = [50, 10, 10, 10, 10, 10]
        types = [
            "news+reaction",
            "DIY/how-to",
            "polls/debates",
            "lore/history",
            "garage stories",
            "partner",
        ]
        return random.choices(types, weights=weights, k=1)[0]

    def is_urgent_topic(self, text: str) -> bool:
        """Check if a news item is urgent."""
        text_lower = text.lower()
        return any(kw in text_lower for kw in URGENT_KEYWORDS)

    def get_next_post_time(self, posts_today: int) -> Optional[datetime]:
        """Calculate the next recommended post time."""
        now = datetime.now(timezone.utc)

        for hour in sorted(DEFAULT_POSTING_HOURS):
            candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > now:
                return candidate

        # Next day first slot
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(
            hour=DEFAULT_POSTING_HOURS[0], minute=0, second=0, microsecond=0
        )

    def get_theme_for_day(self, weekday: int | None = None) -> dict[str, Any] | None:
        """Get theme for a specific day (0=Monday)."""
        if weekday is None:
            weekday = datetime.now(timezone.utc).weekday()
        return THEME_DAYS.get(weekday)

    def get_all_themes(self) -> dict[int, dict[str, Any]]:
        """Return all theme day definitions."""
        return THEME_DAYS.copy()
