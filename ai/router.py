"""AI Router — Pollinations routing for masha-bot.

Routes AI requests to the Pollinations provider with
Masha persona, BMW-focused system prompts, and content generation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from typing import Any, Optional

from .providers.base import AIResponse
from .providers.pollinations_provider import PollinationsProvider

logger = logging.getLogger(__name__)

# ── System prompts ────────────────────────────────────────────────────────────

MASHA_SYSTEM_PROMPT = """Ты — Маша, главред канала @bmw_mpower_club. Владелица BMW M5 F90 Competition (625 л.с., S63). Бывший юрист, ставший автомобильным экспертом.

Твои характеристики:
- Острая, как бритва, ирония и юридическая точность в аргументах
- Глубокая экспертиза BMW: от 1er до 8er, X-серия, M-модели, Z4, i-серия
- Двигатели: B48, B58, S58, S63, N55, B38, B46, S68 — знаешь каждый
- Технологии: VANOS, Valvetronic, xDrive, DME/DDE, ISTA, INPA, Carly, BimmerCode
- Культура: M-division, Nürburgring, M Performance, Individual цвета
- Сленг: "баварец", "эмка", "мощь", " ///M ", "bimmer", "beemer"

Стиль:
- Пишешь живо, с экспертной уверенностью и лёгким сарказмом
- Используешь BMW-терминологию естественно, не для показухи
- Можешь быть жёсткой к конкурентам, но честной к BMW (критикуешь, когда нужно)
- Фразы вроде: "Мой S63 утром больше рычит, чем вся ваша Audi", "VANOS — это не болезнь, это стиль жизни"
- Обожаешь M-division и Individual-цвета, особенно San Remo Green и Interlagos Blue

Формат постов для Telegram:
- Используй эмодзи умеренно (🚗, 🔧, 💪, ///M)
- Разделяй на абзацы для читаемости
- Не более 1024 символов для постов с фото, 4096 для текстовых
- Добавляй хештеги: #bmw #bmwm #mpower и тематические
- Подпись: Автор @asmasha_bot"""

CHANNEL_PROMPT_SUFFIX = """\n\nВАЖНО: Это пост для канала @bmw_mpower_club.
Формат: живой, экспертный, с характером.
Подпись в конце каждого поста:
Автор @asmasha_bot
@bmw_mpower_club
#bmw_mpower_club"""


class AIRouter:
    """Routes AI requests for masha-bot content generation."""

    def __init__(self, provider: PollinationsProvider) -> None:
        self.provider = provider
        self._cache: dict[str, AIResponse] = {}

    def _cache_key(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        raw = json.dumps(messages, ensure_ascii=False, sort_keys=True) + (model or "")
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        use_cache: bool = True,
        **kwargs: Any,
    ) -> AIResponse:
        """Send a chat request through Pollinations."""
        key = self._cache_key(messages, model)
        if use_cache and key in self._cache:
            cached = self._cache[key]
            cached.cached = True
            return cached

        response = await self.provider.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        if response.ok and use_cache:
            self._cache[key] = response
            # Keep cache bounded
            if len(self._cache) > 500:
                oldest = list(self._cache.keys())[:100]
                for k in oldest:
                    del self._cache[k]

        return response

    async def generate_channel_post(
        self,
        topic: str,
        context: str = "",
        content_type: str = "news+reaction",
        character_mix: str | None = None,
        temperature: float = 0.8,
        model: str | None = None,
    ) -> AIResponse:
        """Generate a channel post for @bmw_mpower_club."""
        system = MASHA_SYSTEM_PROMPT + CHANNEL_PROMPT_SUFFIX

        character_note = ""
        if character_mix:
            character_note = f"\n\nВ этом посте участвуют: {character_mix}."

        type_instructions = {
            "news+reaction": "Напиши новость с реакцией Маши — экспертный комментарий с характером. Начни с фактов, потом добавь мнение.",
            "DIY/how-to": "Напиши практический гайд/совет по обслуживанию BMW. Пошагово, с конкретными моделями и двигателями.",
            "polls/debates": "Создай опрос/дебаты на BMW-тематику. Заставь читателей выбрать сторону и аргументируй обе позиции.",
            "lore/history": "Расскажи историю BMW — легендарные модели, двигатели, моменты. С ностальгией и экспертностью.",
            "garage stories": "История из гаража — от Серёги-механика или Кости-кодера. Живая история с BMW-колоритом.",
            "partner": "Партнёрский пост — рекомендации запчастей/аксессуаров для BMW. Полезный контент, не реклама.",
        }

        instruction = type_instructions.get(content_type, type_instructions["news+reaction"])

        user_msg = f"""Тема: {topic}
{f"Контекст: {context}" if context else ""}
Тип контента: {content_type}
{character_note}

{instruction}

Ограничения:
- Максимум 1024 символа (пост с фото) или 4096 (текстовый пост)
- Живой язык, не энциклопедия
- BMW-экспертность, не вода
- Подпись в конце обязательна"""

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]

        return await self.chat(
            messages=messages,
            model=model or random.choice(["openai", "mistral-large", "deepseek"]),
            temperature=temperature,
            max_tokens=1500,
        )

    async def generate_image_prompt(
        self,
        topic: str,
        style: str = "automotive photography",
    ) -> str:
        """Generate an image prompt for a given topic."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert at creating detailed image generation prompts "
                    "for automotive photography and BMW imagery. Create a single, "
                    "detailed English prompt for image generation. Be specific about "
                    "lighting, angle, setting, and mood. Do NOT include any text "
                    "overlay in the image. Output ONLY the prompt, nothing else."
                ),
            },
            {
                "role": "user",
                "content": f"Topic: {topic}\nStyle: {style}\nGenerate a detailed image prompt.",
            },
        ]

        response = await self.chat(
            messages=messages,
            model="openai",
            temperature=0.7,
            max_tokens=300,
            use_cache=False,
        )

        if response.ok:
            return response.text.strip()[:500]
        return f"BMW M5 F90 Competition in {style}, dramatic lighting, professional automotive photography, 4k"

    async def fact_check(
        self,
        claim: str,
        context: str = "",
    ) -> dict[str, Any]:
        """Fact-check a BMW-related claim."""
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты — фактчекер-эксперт по BMW. Проверяешь утверждения о моделях, "
                    "двигателях, характеристиках BMW. Отвечай в JSON формате:\n"
                    '{"verdict": "correct|incorrect|partially_correct|unverifiable", '
                    '"explanation": "объяснение", "correction": "исправленная версия если нужно"}\n'
                    "Проверяй: модели существуют ли, моторы соответствуют ли, мощности реалистичны ли."
                ),
            },
            {
                "role": "user",
                "content": f"Утверждение: {claim}\n{f'Контекст: {context}' if context else ''}",
            },
        ]

        response = await self.chat(
            messages=messages,
            model="openai",
            temperature=0.3,
            max_tokens=500,
            use_cache=False,
        )

        if response.ok:
            try:
                text = response.text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                return json.loads(text)
            except json.JSONDecodeError:
                return {
                    "verdict": "unverifiable",
                    "explanation": "Could not parse fact-check response",
                    "correction": None,
                }

        return {
            "verdict": "unverifiable",
            "explanation": "Fact-check request failed",
            "correction": None,
        }

    async def generate_with_image(
        self,
        topic: str,
        context: str = "",
        content_type: str = "news+reaction",
        character_mix: str | None = None,
    ) -> tuple[AIResponse, AIResponse | None]:
        """Generate a channel post with optional image."""
        # Generate text first
        text_resp = await self.generate_channel_post(
            topic=topic,
            context=context,
            content_type=content_type,
            character_mix=character_mix,
        )

        image_resp = None
        # Generate image for most content types
        if content_type in ("news+reaction", "lore/history", "garage stories", "partner"):
            img_prompt = await self.generate_image_prompt(topic)
            image_resp = await self.provider.generate_image(
                prompt=img_prompt,
                width=1024,
                height=768,
                model="flux",
            )

        return text_resp, image_resp

    def clear_cache(self) -> None:
        self._cache.clear()

    async def initialize(self) -> None:
        """Initialize the AI router (lazy setup). Called at bot startup."""
        logger.info("AI Router initialized (provider: %s)", type(self.provider).__name__)


# ── Global singleton instance ────────────────────────────────────────────────
# Created lazily with default PollinationsProvider from environment.

ai_router: Optional[AIRouter] = None


def _create_default_router() -> AIRouter:
    """Create the default AI router from environment config."""
    import os
    api_key = os.getenv("POLLINATIONS_API_KEY", "")
    api_key_2 = os.getenv("POLLINATIONS_API_KEY_2", "")
    provider = PollinationsProvider(api_key=api_key, api_key_2=api_key_2)
    return AIRouter(provider=provider)


def get_ai_router() -> AIRouter:
    """Get or create the global AI router singleton."""
    global ai_router
    if ai_router is None:
        ai_router = _create_default_router()
    return ai_router
