"""Persona management for masha-bot — character/tone + mood."""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..core.config import get_persona

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
PERSONA_STATE_PATH = DATA_DIR / "persona_state.json"

# ── Mood definitions ──────────────────────────────────────────────────────────

MOODS: dict[str, dict[str, Any]] = {
    "energetic": {
        "description": "Полна энергии, готова спорить и шутить",
        "temperature_modifier": 0.0,
        "prompt_suffix": "Ты сегодня в ударе! Энергичная, острая, готова к дискуссиям.",
    },
    "nostalgic": {
        "description": "Ностальгия по старым BMW, E30, E39, классика",
        "temperature_modifier": -0.1,
        "prompt_suffix": "Сегодня ты ностальгируешь по старым BMW. E30 M3, E39 M5... Были времена.",
    },
    "analytical": {
        "description": "Юридическая точность, факты и цифры",
        "temperature_modifier": -0.2,
        "prompt_suffix": "Сегодня ты в аналитическом режиме. Юридическая точность, только проверенные факты.",
    },
    "playful": {
        "description": "Игривая, шутит про Серёгу и Кинг Конга",
        "temperature_modifier": 0.1,
        "prompt_suffix": "Сегодня игривое настроение. Шутишь про коллег, цитируешь Кинг Конга и Доктора Ван Дамма.",
    },
    "passionate": {
        "description": "Страстная про M-division и Nürburgring",
        "temperature_modifier": 0.15,
        "prompt_suffix": "Сегодня ты особенно страстная про M-division. ///M — это религия. Нюрбургринг — дом.",
    },
    "skeptical": {
        "description": "Скептичная к новостям, требует доказательств",
        "temperature_modifier": -0.15,
        "prompt_suffix": "Скептичный режим. Не веришь на слово, требуешь доказательств. Бывший юрист просыпается.",
    },
}


class PersonaManager:
    """Manages Маша's persona, mood, and tone."""

    def __init__(self) -> None:
        self._state: dict[str, Any] = {}
        self._load_state()

    def _load_state(self) -> None:
        """Load persona state from JSON file."""
        if PERSONA_STATE_PATH.exists():
            try:
                with open(PERSONA_STATE_PATH, "r", encoding="utf-8") as f:
                    self._state = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load persona state: %s", exc)
                self._state = {}
        else:
            self._state = {
                "current_mood": "energetic",
                "mood_history": [],
                "last_post_character": "Маша",
                "post_count_today": 0,
                "last_reset_date": "",
            }

    def _save_state(self) -> None:
        """Save persona state to JSON file."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(PERSONA_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.error("Failed to save persona state: %s", exc)

    def get_current_mood(self) -> str:
        """Get or determine the current mood."""
        # Reset daily
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._state.get("last_reset_date") != today:
            self._state["post_count_today"] = 0
            self._state["last_reset_date"] = today
            self._rotate_mood()
            self._save_state()

        return self._state.get("current_mood", "energetic")

    def _rotate_mood(self) -> None:
        """Rotate mood based on day and randomness."""
        day = datetime.now(timezone.utc).weekday()

        # Theme-day mood suggestions
        day_moods: dict[int, str] = {
            0: "passionate",   # M-Monday
            1: "analytical",   # Tech Tuesday
            2: "playful",      # Workshop Wednesday
            3: "nostalgic",    # Throwback Thursday
            4: "playful",      # Freaky Friday
            5: "energetic",    # Spotlight Saturday
            6: "nostalgic",   # Sunday Drive
        }

        # 70% chance to follow day mood, 30% random
        if random.random() < 0.7:
            new_mood = day_moods.get(day, "energetic")
        else:
            new_mood = random.choice(list(MOODS.keys()))

        self._state["current_mood"] = new_mood
        self._state.setdefault("mood_history", []).append({
            "mood": new_mood,
            "date": datetime.now(timezone.utc).isoformat(),
        })

        # Keep history bounded
        if len(self._state.get("mood_history", [])) > 30:
            self._state["mood_history"] = self._state["mood_history"][-30:]

    def get_mood_prompt_suffix(self) -> str:
        """Get a prompt suffix based on the current mood."""
        mood = self.get_current_mood()
        mood_data = MOODS.get(mood, MOODS["energetic"])
        return mood_data.get("prompt_suffix", "")

    def get_temperature_modifier(self) -> float:
        """Get temperature modifier based on current mood."""
        mood = self.get_current_mood()
        mood_data = MOODS.get(mood, MOODS["energetic"])
        return mood_data.get("temperature_modifier", 0.0)

    def get_full_system_prompt(self) -> str:
        """Get the complete system prompt including mood."""
        persona = get_persona()
        base = persona.system_prompt
        mood_suffix = self.get_mood_prompt_suffix()
        channel_suffix = persona.channel_prompt_suffix

        return f"{base}\n\n{mood_suffix}{channel_suffix}"

    def record_post(self, character: str = "Маша") -> None:
        """Record that a post was made."""
        self._state["post_count_today"] = self._state.get("post_count_today", 0) + 1
        self._state["last_post_character"] = character
        self._save_state()

    def get_random_aside(self) -> str | None:
        """Get a random editorial aside from Маша's collection."""
        persona = get_persona()
        if persona.editorial_asides:
            return random.choice(persona.editorial_asides)
        return None

    def get_persona_info(self) -> dict[str, Any]:
        """Get current persona state info."""
        return {
            "mood": self.get_current_mood(),
            "mood_description": MOODS.get(self.get_current_mood(), {}).get("description", ""),
            "posts_today": self._state.get("post_count_today", 0),
            "last_character": self._state.get("last_post_character", "Маша"),
        }
