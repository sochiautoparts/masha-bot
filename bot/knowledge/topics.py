"""Topic scheduling and management for masha-bot.

Theme days:
- M-Monday: M-models, M-division news, M Performance
- Tech Tuesday: Engines, VANOS, Valvetronic, coding
- Workshop Wednesday: DIY, maintenance, Серёга's tips
- Throwback Thursday: Classic BMW, E30, E39, E46, history
- Freaky Friday: Custom BMW, tuning, Alpina, AC Schnitzer
- Spotlight Saturday: Spotlight on specific model
- Sunday Drive: Driving culture, Nürburgring, road trips
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
TOPIC_SCHEDULE_PATH = DATA_DIR / "topic_schedule.json"

# ── Theme day definitions (same as scheduler but with topics) ─────────────────

THEME_TOPICS: dict[str, dict[str, Any]] = {
    "M-Monday": {
        "day": 0,
        "emoji": "🔥",
        "content_type": "news+reaction",
        "topics": [
            {"id": "mm-01", "topic": "BMW M3 G80 — новый рекорд Нюрбургринга?", "type": "news+reaction"},
            {"id": "mm-02", "topic": "M5 F90 Competition — король седанов", "type": "lore/history"},
            {"id": "mm-03", "topic": "M4 CSL — наследие CSL-линии", "type": "lore/history"},
            {"id": "mm-04", "topic": "M2 G87 — компактная мощь нового поколения", "type": "news+reaction"},
            {"id": "mm-05", "topic": "XM Label — M-гибрид 738 л.с.", "type": "news+reaction"},
            {"id": "mm-06", "topic": "M Performance Parts — что стоит купить для вашей эмки", "type": "DIY/how-to"},
            {"id": "mm-07", "topic": "M-division история: от M1 до XM", "type": "lore/history"},
            {"id": "mm-08", "topic": "M3 Touring G81 — мечта стала реальностью", "type": "news+reaction"},
            {"id": "mm-09", "topic": "M5 CS F90 — 635 л.с. лимитированного безумия", "type": "lore/history"},
            {"id": "mm-10", "topic": "M vs AMG: вечный спор", "type": "polls/debates"},
            {"id": "mm-11", "topic": "Новый M5 G90 гибрид — 717 л.с., но стоит ли?", "type": "polls/debates"},
            {"id": "mm-12", "topic": "M3 Competition xDrive vs RWD — что быстрее?", "type": "polls/debates"},
        ],
    },
    "Tech Tuesday": {
        "day": 1,
        "emoji": "🔧",
        "content_type": "DIY/how-to",
        "topics": [
            {"id": "tt-01", "topic": "VANOS — как работает и почему стучит", "type": "DIY/how-to"},
            {"id": "tt-02", "topic": "B58 vs N55 — эволюция рядной шестёрки BMW", "type": "lore/history"},
            {"id": "tt-03", "topic": "S63 — битурбо V8 от M5 F90 изнутри", "type": "lore/history"},
            {"id": "tt-04", "topic": "Valvetronic — бесступенчатый впуск BMW", "type": "DIY/how-to"},
            {"id": "tt-05", "topic": "xDrive vs RWD — полный привод или чистый зад?", "type": "polls/debates"},
            {"id": "tt-06", "topic": "DME/DDE — мозги вашего BMW", "type": "DIY/how-to"},
            {"id": "tt-07", "topic": "ISTA vs INPA vs Carly vs BimmerCode — что выбрать", "type": "DIY/how-to"},
            {"id": "tt-08", "topic": "S68 — новое поколение V8 BMW", "type": "news+reaction"},
            {"id": "tt-09", "topic": "DKG vs ZF 8HP — какая коробка лучше для M?", "type": "polls/debates"},
            {"id": "tt-10", "topic": "Проблема N20 цепи ГРМ — что нужно знать", "type": "DIY/how-to"},
        ],
    },
    "Workshop Wednesday": {
        "day": 2,
        "emoji": "🔩",
        "content_type": "DIY/how-to",
        "topics": [
            {"id": "ww-01", "topic": "Замена масла в N55 — пошаговая инструкция от Серёги", "type": "DIY/how-to"},
            {"id": "ww-02", "topic": "VANOS соленоиды — чистка или замена?", "type": "DIY/how-to"},
            {"id": "ww-03", "topic": "Тормозные колодки для M5 — OEM или аналог?", "type": "polls/debates"},
            {"id": "ww-04", "topic": "Фильтры BMW — когда менять и какие брать", "type": "DIY/how-to"},
            {"id": "ww-05", "topic": "Проблемы B48 — что знать каждому владельцу", "type": "news+reaction"},
            {"id": "ww-06", "topic": "Катушки зажигания — OEM vs aftermarket", "type": "polls/debates"},
            {"id": "ww-07", "topic": "Антифриз BMW — какой лить и почему", "type": "DIY/how-to"},
            {"id": "ww-08", "topic": "Диагностика ISTA — первый раз, пошагово", "type": "DIY/how-to"},
            {"id": "ww-09", "topic": "Замена турбины на N55 — опыт Серёги", "type": "garage stories"},
            {"id": "ww-10", "topic": "Почему течёт масло из-под клапанной крышки B48", "type": "DIY/how-to"},
        ],
    },
    "Throwback Thursday": {
        "day": 3,
        "emoji": "⏪",
        "content_type": "lore/history",
        "topics": [
            {"id": "th-01", "topic": "E30 M3 — первая эмка BMW, изменившая всё", "type": "lore/history"},
            {"id": "th-02", "topic": "E39 M5 — лучший спортивный седан всех времён?", "type": "lore/history"},
            {"id": "th-03", "topic": "E46 M3 CSL — 7:22 на Нюрбургринге в 2004", "type": "lore/history"},
            {"id": "th-04", "topic": "2002 Turbo — первый турбо BMW (1973)", "type": "lore/history"},
            {"id": "th-05", "topic": "M1 Procar — суперкар от BMW и Ламбретти", "type": "lore/history"},
            {"id": "th-06", "topic": "E28 M5 — оригинальная M-пятёрка", "type": "lore/history"},
            {"id": "th-07", "topic": "E36 M3 — доступная классика. Стоит ли брать?", "type": "polls/debates"},
            {"id": "th-08", "topic": "Z3 M Coupe — 'тапок' Кларксона", "type": "lore/history"},
            {"id": "th-09", "topic": "BMW 507 — красота 50-х, которую любил Элвис", "type": "lore/history"},
            {"id": "th-10", "topic": "E60 M5 V10 — безумный S85 и его проблемы", "type": "lore/history"},
        ],
    },
    "Freaky Friday": {
        "day": 4,
        "emoji": "🤪",
        "content_type": "polls/debates",
        "topics": [
            {"id": "ff-01", "topic": "Alpina B3 vs M340i — что выбрать?", "type": "polls/debates"},
            {"id": "ff-02", "topic": "AC Schnitzer — тюнинг со вкусом или переплата?", "type": "polls/debates"},
            {"id": "ff-03", "topic": "M Performance выхлоп — стоит ли $5000?", "type": "polls/debates"},
            {"id": "ff-04", "topic": "Individual цвета — топ-5 самых редких", "type": "lore/history"},
            {"id": "ff-05", "topic": "Чип-тюнинг B58 — Stage 1, 2, 3", "type": "DIY/how-to"},
            {"id": "ff-06", "topic": "Bagged BMW — искусство или издевательство?", "type": "polls/debates"},
            {"id": "ff-07", "topic": "Widebody BMW — за и против", "type": "polls/debates"},
            {"id": "ff-08", "topic": "BMW на Tokyo Auto Salon — лучшие проекты", "type": "news+reaction"},
            {"id": "ff-09", "topic": "Daytona Violet — храбрость или безумие?", "type": "polls/debates"},
            {"id": "ff-10", "topic": "Alpina vs M — два пути BMW", "type": "polls/debates"},
        ],
    },
    "Spotlight Saturday": {
        "day": 5,
        "emoji": "🔦",
        "content_type": "news+reaction",
        "topics": [
            {"id": "ss-01", "topic": "BMW iX M60 — электрический M-SUV", "type": "news+reaction"},
            {"id": "ss-02", "topic": "BMW X5 M Competition — SUV-монстр 635 л.с.", "type": "news+reaction"},
            {"id": "ss-03", "topic": "BMW 3 серии G20 — бестселлер нового поколения", "type": "news+reaction"},
            {"id": "ss-04", "topic": "BMW i4 M50 — электромобиль с ///M характером", "type": "news+reaction"},
            {"id": "ss-05", "topic": "BMW Z4 M40i — последний родстер?", "type": "news+reaction"},
            {"id": "ss-06", "topic": "BMW X3 M Competition — компактный SUV-ракета", "type": "news+reaction"},
            {"id": "ss-07", "topic": "BMW 7 серии G70 — флагман нового поколения", "type": "news+reaction"},
            {"id": "ss-08", "topic": "BMW i7 M70 — роскошь будущего", "type": "news+reaction"},
        ],
    },
    "Sunday Drive": {
        "day": 6,
        "emoji": "🛣️",
        "content_type": "lore/history",
        "topics": [
            {"id": "sd-01", "topic": "Нюрбургринг — дом BMW. История и рекорды.", "type": "lore/history"},
            {"id": "sd-02", "topic": "M5 на Нюрбургринге — lap guide от Маши", "type": "lore/history"},
            {"id": "sd-03", "topic": "Лучшие дороги для BMW в Европе", "type": "lore/history"},
            {"id": "sd-04", "topic": "BMW Driving Experience — стоит ли ехать?", "type": "polls/debates"},
            {"id": "sd-05", "topic": "Зимний дрифт на M5 — инструкция от выжившего", "type": "DIY/how-to"},
            {"id": "sd-06", "topic": "M-цвета: синий, фиолетовый, красный — что они значат", "type": "lore/history"},
            {"id": "sd-07", "topic": "BMW и кино — самые эпичные погони", "type": "lore/history"},
            {"id": "sd-08", "topic": "BMW клубы — зачем вступать и что дают", "type": "news+reaction"},
        ],
    },
}


class TopicManager:
    """Manages topic scheduling and selection."""

    def __init__(self) -> None:
        self._schedule = self._load_schedule()

    def _load_schedule(self) -> dict[str, dict[str, Any]]:
        """Load topic schedule from JSON or use defaults."""
        if TOPIC_SCHEDULE_PATH.exists():
            try:
                with open(TOPIC_SCHEDULE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load topic schedule: %s, using defaults", exc)
        return THEME_TOPICS

    def get_current_theme(self) -> dict[str, Any] | None:
        """Get the current theme based on the day of week."""
        now = datetime.now(timezone.utc)
        day = now.weekday()

        for theme_name, theme_data in self._schedule.items():
            if theme_data.get("day") == day:
                # Pick a topic
                topics = theme_data.get("topics", [])
                if topics:
                    # Vary by week number
                    week = now.isocalendar()[1]
                    idx = (week + now.day) % len(topics)
                    topic_data = topics[idx]
                    return {
                        "name": theme_name,
                        "emoji": theme_data.get("emoji", "🚗"),
                        "description": theme_data.get("description", ""),
                        "default_type": theme_data.get("content_type", "news+reaction"),
                        "topic": topic_data.get("topic", theme_name),
                        "topic_id": topic_data.get("id", ""),
                        "content_type": topic_data.get("type", theme_data.get("content_type")),
                    }
                return {
                    "name": theme_name,
                    "emoji": theme_data.get("emoji", "🚗"),
                    "default_type": theme_data.get("content_type", "news+reaction"),
                }
        return None

    def get_random_topic(self, content_type: str | None = None) -> dict[str, Any] | None:
        """Get a random topic from any theme day."""
        all_topics: list[dict[str, Any]] = []
        for theme_name, theme_data in self._schedule.items():
            for topic in theme_data.get("topics", []):
                if content_type is None or topic.get("type") == content_type:
                    all_topics.append({
                        **topic,
                        "theme": theme_name,
                        "emoji": theme_data.get("emoji", "🚗"),
                    })

        if all_topics:
            return random.choice(all_topics)
        return None

    def get_topics_for_theme(self, theme_name: str) -> list[dict[str, Any]]:
        """Get all topics for a specific theme."""
        theme = self._schedule.get(theme_name, {})
        return theme.get("topics", [])

    def get_all_themes(self) -> dict[str, dict[str, Any]]:
        """Return all theme definitions."""
        return self._schedule.copy()

    def save_schedule(self) -> None:
        """Save the current schedule to JSON."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(TOPIC_SCHEDULE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._schedule, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.error("Failed to save topic schedule: %s", exc)
