"""Community source for masha-bot — subscriber questions, polls."""

from __future__ import annotations

import logging
import random
from typing import Any, Optional

from ..database import Database

logger = logging.getLogger(__name__)

# ── Default community poll templates ──────────────────────────────────────────

DEFAULT_POLLS: list[dict[str, Any]] = [
    {
        "id": "poll-001",
        "question": "M3 Competition xDrive или RWD?",
        "options": ["xDrive — мощь в любой погоде", "RWD — чистый драйв", "У меня M3 Competition xDrive и я жалею", "Доктор Ван Дамм воздерживается (спит)"],
        "content_type": "polls/debates",
    },
    {
        "id": "poll-002",
        "question": "N55 или B58?",
        "options": ["N55 — последний честный мотор (Серёга)", "B58 — надёжность нового поколения", "S58 — и точка", "Кинг Конг: N55! N55! Кар!"],
        "content_type": "polls/debates",
    },
    {
        "id": "poll-003",
        "question": "Individual или M Performance?",
        "options": ["Individual — уникальность (Лена)", "M Performance — спорт", "Оба — если позволяет бюджет", "Доктор Ван Дамм: мур-р-р (воздержался)"],
        "content_type": "polls/debates",
    },
    {
        "id": "poll-004",
        "question": "Лучшая M-модель всех времён?",
        "options": ["E30 M3 — классика", "E46 M3 CSL — трек-легенда", "M5 F90 CS — современный король", "M4 CSL — последняя CSL", "Кинг Конг: M5 — это вид попугаев!"],
        "content_type": "polls/debates",
    },
    {
        "id": "poll-005",
        "question": "BMW i4 M50 или M340i?",
        "options": ["i4 M50 — будущее уже здесь (Костя)", "M340i — бензин не подведёт (Серёга)", "Подожду i5 M60", "Доктор Ван Дамм спит на обоих"],
        "content_type": "polls/debates",
    },
    {
        "id": "poll-006",
        "question": "Какой Individual цвет ваш фаворит?",
        "options": ["San Remo Green — элегантность (Лена)", "Interlagos Blue — глубина", "Daytona Violet — безумие", "Austin Yellow — дерзость", "Кинг Конг: Синий! Как я!"],
        "content_type": "polls/debates",
    },
    {
        "id": "poll-007",
        "question": "ZF 8HP или M-DCT (DKG)?",
        "options": ["ZF 8HP — универсальность и комфорт", "M-DCT — чистый спорт и скорость", "Ручная — только ручная! (но где найти?)", "Доктор Ван Дамм: лапой по клавиатуре"],
        "content_type": "polls/debates",
    },
    {
        "id": "poll-008",
        "question": "Alpina или M?",
        "options": ["M — трек и мощь", "Alpina — роскошь и эксклюзив", "M Performance — золотая середина", "Кинг Конг: ///M-Power! Кар-кар!"],
        "content_type": "polls/debates",
    },
    {
        "id": "poll-009",
        "question": "Лучший BMW SUV?",
        "options": ["X5 M — классика M-SUV", "XM — гибрид будущего", "X3 M — компактная мощь", "Alpina XB7 — роскошь", "Кинг Конг: M5 — это тоже SUV! Кар!"],
        "content_type": "polls/debates",
    },
    {
        "id": "poll-010",
        "question": "Ваш следующий BMW будет...",
        "options": ["M3 G80 — спортивный седан мечты", "i4 M50 — электромобиль с характером", "X5 M — семейная мощь", "M2 G87 — чистый драйв", "Доктор Ван Дамм: *спит на капоте M5*"],
        "content_type": "polls/debates",
    },
]


class CommunitySource:
    """Manages community-driven content: subscriber questions and polls."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._pending_questions: list[dict[str, Any]] = []
        self._polls = DEFAULT_POLLS

    async def get_pending(self) -> dict[str, Any] | None:
        """Get a pending community item (question or poll)."""
        # Try subscriber questions first
        if self._pending_questions:
            question = self._pending_questions.pop(0)
            return {
                "topic": question.get("text", ""),
                "content_type": "news+reaction",
                "context": f"Вопрос подписчика: {question.get('text', '')}",
                "source": "community",
            }

        # Otherwise, maybe suggest a poll
        if random.random() < 0.3:  # 30% chance
            poll = random.choice(self._polls)
            return {
                "topic": poll.get("question", ""),
                "content_type": "polls/debates",
                "context": f"Опрос: {poll.get('question', '')}. Варианты: {', '.join(poll.get('options', []))}",
                "source": "community",
            }

        return None

    def add_subscriber_question(self, user_id: int, text: str) -> None:
        """Add a question from a subscriber."""
        self._pending_questions.append({
            "user_id": user_id,
            "text": text,
            "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        })
        logger.info("New subscriber question from %d: %s", user_id, text[:50])

    def get_random_poll(self) -> dict[str, Any] | None:
        """Get a random poll template."""
        if self._polls:
            return random.choice(self._polls)
        return None

    def get_poll_count(self) -> int:
        """Get the number of available polls."""
        return len(self._polls)
