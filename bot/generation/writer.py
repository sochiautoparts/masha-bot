"""AI text generation via Pollinations for masha-bot.

Generates channel posts, reactions, guides, polls, and stories
using the Pollinations AI provider through the AIRouter.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Optional

from ...ai.router import AIRouter
from ...ai.providers.pollinations_provider import CHAT_MODELS, CONTENT_MODELS
from ...ai.providers.provider_manager import ROUTE_FUNCTION
from ...bot.core.config import get_persona
from ...bot.generation.persona import PersonaManager
from ...bot.knowledge.characters import CharacterManager, ALL_CHARACTERS

logger = logging.getLogger(__name__)


class ContentWriter:
    """Generates content for the @bmw_mpower_club channel."""

    def __init__(self) -> None:
        self._router = None
        self.persona_manager = PersonaManager()
        self.character_manager = CharacterManager()

    def _get_router(self):
        """Get the global AI router singleton."""
        from ai.router import get_ai_router
        return get_ai_router()

    async def generate(
        self,
        topic: str,
        context: str = "",
        content_type: str = "news+reaction",
        character_mix: str = "Маша",
        mood: str = "energetic",
    ) -> dict[str, Any] | None:
        """Generate a channel post."""
        try:
            # Build system prompt with persona + mood + characters
            system_prompt = self._build_system_prompt(character_mix, mood)

            # Build user prompt
            user_prompt = self._build_user_prompt(topic, context, content_type, character_mix)

            # Get temperature modifier from mood
            temp_mod = self.persona_manager.get_temperature_modifier()
            temperature = max(0.3, min(1.2, 0.8 + temp_mod))

            # Select model based on content type
            model = self._select_model(content_type)

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            response = await self._get_router().chat(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=1500,
                route_type=ROUTE_FUNCTION,
            )

            if not response.ok:
                logger.error("Content generation failed: %s", response.error)
                return None

            text = response.text.strip()

            # Clean up the text
            text = self._clean_text(text)

            if not text:
                logger.warning("Generated text is empty after cleanup")
                return None

            # Maybe add editorial aside
            if random.random() < 0.3:
                aside = self.persona_manager.get_random_aside()
                if aside:
                    text = f"{text}\n\n💬 {aside}"

            # Ensure footer is present
            text = self._ensure_footer(text)

            return {
                "text": text,
                "model": response.model,
                "provider": response.provider,
                "latency_ms": response.latency_ms,
                "character_mix": character_mix,
                "content_type": content_type,
            }

        except Exception as exc:
            logger.exception("Content generation error: %s", exc)
            return None

    def _build_system_prompt(self, character_mix: str, mood: str) -> str:
        """Build the complete system prompt."""
        persona = get_persona()
        base = persona.system_prompt
        mood_suffix = self.persona_manager.get_mood_prompt_suffix()
        char_suffix = self.character_manager.get_character_prompt_suffix(character_mix)
        channel_suffix = persona.channel_prompt_suffix

        return f"{base}\n\n{mood_suffix}{char_suffix}{channel_suffix}"

    def _build_user_prompt(
        self,
        topic: str,
        context: str,
        content_type: str,
        character_mix: str,
    ) -> str:
        """Build the user prompt for content generation."""
        type_instructions = {
            "news+reaction": (
                "Напиши новость с реакцией — экспертный комментарий с характером Маши. "
                "Начни с фактов, потом добавь мнение. Используй BMW-терминологию естественно."
            ),
            "DIY/how-to": (
                "Напиши практический гайд/совет по обслуживанию BMW. "
                "Пошагово, с конкретными моделями и двигателями. "
                "Если участвует Серёга — добавь его характерные фразы."
            ),
            "polls/debates": (
                "Создай опрос/дебаты на BMW-тематику. "
                "Заставь читателей выбрать сторону и аргументируй обе позиции. "
                "Если участвуют животные — они воздерживаются или кричат."
            ),
            "lore/history": (
                "Расскажи историю BMW — легендарные модели, двигатели, моменты. "
                "С ностальгией и экспертностью. Можно добавить малоизвестные факты."
            ),
            "garage stories": (
                "История из гаража — от Серёги-механика или Кости-кодера. "
                "Живая история с BMW-колоритом. Юмор + практическая польза."
            ),
            "partner": (
                "Партнёрский пост — рекомендации запчастей/аксессуаров для BMW. "
                "Полезный контент, не реклама. С конкретными примерами и моделями."
            ),
        }

        instruction = type_instructions.get(content_type, type_instructions["news+reaction"])

        prompt = f"""Тема: {topic}
{f"Контекст: {context}" if context else ""}
Тип контента: {content_type}
Участвуют: {character_mix}

{instruction}

Ограничения:
- Максимум 1024 символа (пост с фото) или 4096 (текстовый пост)
- Живой язык, не энциклопедия
- BMW-экспертность, не вода
- Подпись в конце: Автор @asmasha_bot
@bmw_mpower_club
#bmw_mpower_club
- Добавь релевантные хештеги: #bmw #bmwm #mpower и тематические"""

        return prompt

    def _select_model(self, content_type: str) -> str:
        """Select an AI model based on content type."""
        if content_type in ("DIY/how-to", "partner"):
            # Factual content — use more reliable models
            return random.choice(["openai", "mistral-large", "deepseek"])
        elif content_type in ("polls/debates", "garage stories"):
            # Creative content — use more creative models
            return random.choice(["openai-large", "mistral-large", "deepseek"])
        elif content_type == "lore/history":
            # History — needs accuracy + storytelling
            return random.choice(["openai", "openai-large", "deepseek"])
        else:
            # Default news+reaction
            return random.choice(CONTENT_MODELS[:5])

    def _clean_text(self, text: str) -> str:
        """Clean up generated text."""
        # Remove markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3]

        # Remove common AI artifacts
        text = text.strip()

        # Remove "Вот пост:" or similar prefixes
        for prefix in ["Вот пост:", "Вот пост", "Пост:", "Пост", "```markdown"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        return text

    def _ensure_footer(self, text: str) -> str:
        """Ensure the channel footer is present."""
        footer = "Автор @asmasha_bot\n@bmw_mpower_club\n#bmw_mpower_club"
        if footer not in text:
            # Remove any partial footer attempts
            for line in ["Автор @asmasha_bot", "@bmw_mpower_club", "#bmw_mpower_club"]:
                if line in text and footer not in text:
                    # Partial footer exists, rebuild
                    text = text.split("Автор @asmasha_bot")[0].strip()
            text = f"{text}\n\n{footer}"
        return text

    async def close(self) -> None:
        """Clean up resources — router is a global singleton, nothing to close here."""
        pass
