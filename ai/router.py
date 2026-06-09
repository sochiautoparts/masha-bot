"""AI Router — Multi-provider routing for masha-bot.

Routes AI requests through ProviderManager with automatic failover:
1. Pollinations (gen API with key → legacy free API)
2. Cloudflare Workers AI (free, 10k req/day/account)

Masha persona, BMW-focused system prompts, and content generation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from typing import Any, Optional

from .providers.base import AIResponse
from .providers.pollinations_provider import PollinationsProvider, CHAT_MODELS, IMAGE_MODELS
from .providers.cloudflare_provider import CloudflareProvider, CF_TEXT_MODEL
from .providers.provider_manager import ProviderManager

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
    """Routes AI requests for masha-bot content generation with multi-provider failover."""

    def __init__(self, provider: PollinationsProvider, cloudflare: CloudflareProvider | None = None) -> None:
        self.provider = provider
        self._manager = ProviderManager(
            pollinations=provider,
            cloudflare=cloudflare,
        )
        self._cache: dict[str, AIResponse] = {}

    @property
    def manager(self) -> ProviderManager:
        """Access the ProviderManager for direct provider calls."""
        return self._manager

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
        """Send a chat request through ProviderManager (Pollinations → Cloudflare)."""
        key = self._cache_key(messages, model)
        if use_cache and key in self._cache:
            cached = self._cache[key]
            cached.cached = True
            return cached

        response = await self._manager.chat(
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
        source_text: str = "",  # Accept but merge into context for backward compat
    ) -> AIResponse:
        """Generate a channel post for @bmw_mpower_club."""
        system = MASHA_SYSTEM_PROMPT + CHANNEL_PROMPT_SUFFIX

        character_note = ""
        if character_mix:
            character_note = f"\n\nВ этом посте участвуют: {character_mix}."

        # Merge source_text into context if provided (backward compatibility)
        full_context = context
        if source_text:
            full_context = f"{source_text}\n\n{context}" if context else source_text

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
{f"Контекст: {full_context}" if full_context else ""}
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
            image_resp = await self._manager.generate_image(
                prompt=img_prompt,
                width=1024,
                height=768,
                model="flux",
            )

        return text_resp, image_resp

    async def decode_vin(
        self,
        user_id: int = 0,
        vin_code: str = "",
        extra_context: str = "",
    ) -> AIResponse:
        """Decode a VIN code with BMW-specific expertise."""
        context_parts = []
        if extra_context:
            context_parts.append(extra_context)

        # BMW VIN prefix hints
        vin_upper = vin_code.upper().strip()
        if vin_upper.startswith("WBA"):
            context_parts.append("VIN начинается с WBA — это BMW!")
        elif vin_upper.startswith("WBS"):
            context_parts.append("VIN начинается с WBS — это BMW M-модель! ///M!")
        elif vin_upper.startswith("WBU"):
            context_parts.append("VIN начинается с WBU — это BMW Individual!")
        elif vin_upper.startswith("5U"):
            context_parts.append("VIN начинается с 5U — возможно BMW (US assembly)")

        context_str = "\n\n".join(context_parts) if context_parts else ""

        messages = [
            {"role": "system", "content": MASHA_SYSTEM_PROMPT + (
                "\n\nПользователь просит расшифровать VIN-код или номер кузова. "
                "Определи по VIN: марку, модель, поколение, год, тип кузова, двигатель, "
                "комплектацию если возможно. "
                "Если VIN начинается с WBA/WBS — это BMW, расскажи подробнее! "
                "Если не BMW — тоже расшифруй что сможешь. "
                "Пиши живо и экспертно, как BMW-энтузиастка. "
                "Предложи поискать запчасти на партнёрских сайтах (из контекста). "
                "НЕ придумывай данные которых нет — честно скажи если не уверен."
            )},
            {"role": "user", "content": (
                f"Расшифруй VIN/номер: {vin_code}\n"
                f"{f'Контекст: {context_str}' if context_str else ''}"
            )},
        ]

        return await self.chat(
            messages=messages,
            model=random.choice(["openai", "mistral-large", "deepseek"]),
            temperature=0.5,
            max_tokens=1500,
        )

    async def diagnose_car(
        self,
        user_id: int = 0,
        symptoms: str = "",
        extra_context: str = "",
    ) -> AIResponse:
        """Diagnose a car problem with BMW-specific expertise."""
        context_parts = []
        if extra_context:
            context_parts.append(extra_context)

        context_str = "\n\n".join(context_parts) if context_parts else ""

        messages = [
            {"role": "system", "content": MASHA_SYSTEM_PROMPT + (
                "\n\nПользователь описывает проблему с BMW. "
                "Дай пошаговую диагностику: возможные причины, как проверить каждую, "
                "что скорее всего, и что делать. "
                "Если можешь — укажи коды ошибок, OEM-номера запчастей, типичные проблемы для этой модели BMW. "
                "Если нужны запчасти — предложи партнёрские сайты (из контекста). "
                "Пиши живо и заботливо, как BMW-энтузиастка."
            )},
            {"role": "user", "content": (
                f"Проблема: {symptoms}\n"
                f"{f'Контекст: {context_str}' if context_str else ''}"
            )},
        ]

        return await self.chat(
            messages=messages,
            model=random.choice(["openai", "mistral-large", "deepseek"]),
            temperature=0.6,
            max_tokens=1500,
        )

    async def find_spare_part(
        self,
        user_id: int = 0,
        article: str = "",
        extra_context: str = "",
    ) -> AIResponse:
        """Help find a spare part with BMW-specific expertise."""
        context_parts = []
        if extra_context:
            context_parts.append(extra_context)

        context_str = "\n\n".join(context_parts) if context_parts else ""

        messages = [
            {"role": "system", "content": MASHA_SYSTEM_PROMPT + (
                "\n\nПользователь ищет запчасть для BMW. "
                "У тебя НЕТ доступа к каталогам запчастей — ты не можешь искать по VIN или артикулу напрямую. "
                "НЕ пытайся подобрать конкретную деталь по номеру — без каталогов это нереально. "
                "Вместо этого ОБЯЗАТЕЛЬНО предложи ТРИ партнёрских сайта (из контекста): "
                "1) Росско, 2) Autopiter, 3) AvtoALL. "
                "На всех трёх сайтах можно искать по VIN-коду и артикулу, есть чаты с подбором. "
                "Ссылки переданы в контексте — используй ИХ КАК ЕСТЬ. "
                "Если знаешь что за деталь — кратко объясни что это и для какого BMW подходит. "
                "Пиши живо и по-дружески, как BMW-энтузиастка."
            )},
            {"role": "user", "content": (
                f"Ищу запчасть: {article}\n"
                f"{f'Контекст: {context_str}' if context_str else ''}"
            )},
        ]

        return await self.chat(
            messages=messages,
            model=random.choice(["openai", "mistral-large", "deepseek"]),
            temperature=0.6,
            max_tokens=1500,
        )

    async def analyze_image(
        self,
        user_id: int = 0,
        image_base64: str = "",
        prompt: str = "",
        extra_context: str = "",
    ) -> AIResponse:
        """Analyze an image with BMW-specific expertise using vision-capable models.

        Tries Cloudflare Workers AI first (supports vision via image_url),
        then falls back to Pollinations vision models.
        """
        context_parts = []
        if extra_context:
            context_parts.append(extra_context)

        context_str = "\n\n".join(context_parts) if context_parts else ""

        system_content = MASHA_SYSTEM_PROMPT + (
            "\n\nПользователь отправил фото. Рассмотри изображение максимально внимательно.\n\n"
            "Если на фото BMW — определи: модель, поколение, год, тип кузова, "
            "цвет, состояние, двигатель если возможно. Укажи ориентировочную стоимость.\n\n"
            "Если на фото ЗАПЧАСТЬ — определи: что это за деталь, для какого BMW подходит. "
            "НЕ пытайся подобрать по артикулу — предложи поискать на партнёрских сайтах.\n\n"
            "Если на фото ДОКУМЕНТ на авто (ПТС, СТС) — "
            "считай данные: VIN, марку, модель, год, двигатель, мощность, объём. "
            "НИКОГДА не показывай ФИО и адрес — только технические данные.\n\n"
            "Если на фото ЭКРАН OBD-II сканера — считай и расшифруй коды ошибок.\n\n"
            "Если на фото ПОВРЕЖДЕНИЕ — опиши что видишь, возможные причины, стоимость ремонта.\n\n"
            "Если что-то другое — просто опиши что видишь."
        )

        # Build vision messages (OpenAI-compatible format with image_url)
        user_content: list[dict] | str
        if image_base64:
            user_content = [
                {"type": "text", "text": prompt or "Рассмотри это фото и расскажи что видишь"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
            ]
        else:
            user_content = prompt or "Рассмотри фото"

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

        if context_str:
            messages.append({"role": "user", "content": f"Дополнительный контекст:\n{context_str}"})

        # 1. Try Cloudflare Workers AI (supports vision natively)
        if self._manager.cloudflare and self._manager.cloudflare.is_available():
            try:
                response = await self._manager.cloudflare.chat(
                    messages=messages,
                    model=None,  # Use default CF vision model
                    temperature=0.5,
                    max_tokens=1500,
                )
                if response.ok:
                    return response
            except Exception as e:
                logger.debug(f"CF vision failed: {e}")

        # 2. Try Pollinations vision models
        vision_models = ["openai", "openai-large", "qwen", "llama", "mistral", "deepseek"]
        for model_name in vision_models[:3]:
            try:
                response = await self._manager.chat(
                    messages=messages,
                    model=model_name,
                    temperature=0.5,
                    max_tokens=1500,
                )
                if response.ok:
                    return response
            except Exception as e:
                logger.debug(f"Vision model {model_name} failed: {e}")
                continue

        # Fallback: text-only approach without image
        fallback_messages = [
            {"role": "system", "content": MASHA_SYSTEM_PROMPT + (
                "\n\nПользователь отправил фото, но vision-модели недоступны. "
                "Попроси пользователя описать проблему текстом."
            )},
            {"role": "user", "content": f"Пользователь отправил фото с подписью: {prompt[:200]}"},
        ]

        return await self.chat(
            messages=fallback_messages,
            model="openai",
            temperature=0.5,
            max_tokens=500,
        )

    def get_available_models(self) -> list[str]:
        """Return list of all available model names."""
        from .providers.pollinations_provider import (
            CHAT_MODELS, VISION_MODELS, CONTENT_MODELS,
            IMAGE_MODELS, REASONING_MODELS,
        )
        return list(set(CHAT_MODELS + VISION_MODELS + CONTENT_MODELS + IMAGE_MODELS + REASONING_MODELS))

    def get_model_categories(self) -> dict[str, list[str]]:
        """Return models grouped by category."""
        from .providers.pollinations_provider import (
            CHAT_MODELS, VISION_MODELS, CONTENT_MODELS,
            IMAGE_MODELS, REASONING_MODELS,
        )
        return {
            "chat": list(CHAT_MODELS),
            "reasoning": list(REASONING_MODELS),
            "vision": list(VISION_MODELS),
            "content": list(CONTENT_MODELS),
            "search": list(CHAT_MODELS),
            "image": list(IMAGE_MODELS),
            "cloudflare": [CF_TEXT_MODEL] if self._manager.cloudflare else [],
        }

    def is_available(self) -> bool:
        """Check if the AI router and its providers are available."""
        return self._manager.is_available()

    @property
    def primary(self) -> PollinationsProvider:
        """Alias for provider — backwards compatibility."""
        return self.provider

    @property
    def _primary(self) -> PollinationsProvider:
        """Alias for provider — backwards compatibility."""
        return self.provider

    def clear_cache(self) -> None:
        self._cache.clear()

    async def initialize(self) -> None:
        """Initialize the AI router (lazy setup). Called at bot startup."""
        providers = ["Pollinations"]
        if self._manager.cloudflare:
            providers.append(f"Cloudflare ({len(self._manager.cloudflare._accounts)} accounts)")
        logger.info("AI Router initialized (providers: %s)", ", ".join(providers))

    def get_provider_status(self) -> dict[str, Any]:
        """Get full provider status for monitoring."""
        return self._manager.get_status()


# ── Global singleton instance ────────────────────────────────────────────────

ai_router: Optional[AIRouter] = None


def _create_default_router() -> AIRouter:
    """Create the default AI router from environment config."""
    import os

    # Pollinations credentials
    api_key = os.getenv("POLLINATIONS_API_KEY", "")
    api_key_2 = os.getenv("POLLINATIONS_API_KEY_2", "")

    # Cloudflare Workers AI credentials (dual account)
    cf_account_1 = os.getenv("CF_ACCOUNT_ID_1", "")
    cf_token_1 = os.getenv("CF_API_TOKEN_1", "")
    cf_account_2 = os.getenv("CF_ACCOUNT_ID_2", "")
    cf_token_2 = os.getenv("CF_API_TOKEN_2", "")

    # Create Pollinations provider (with internal gen→legacy fallback)
    pollinations = PollinationsProvider(api_key=api_key, api_key_2=api_key_2)

    # Create Cloudflare provider (optional — works without it)
    cloudflare = None
    if cf_account_1 and cf_token_1:
        cloudflare = CloudflareProvider(
            account_id_1=cf_account_1,
            api_token_1=cf_token_1,
            account_id_2=cf_account_2 if cf_account_2 else "",
            api_token_2=cf_token_2 if cf_token_2 else "",
        )
        logger.info(
            "Cloudflare Workers AI configured (%d accounts)",
            len(cloudflare._accounts),
        )
    else:
        logger.info("Cloudflare Workers AI not configured (no CF_ACCOUNT_ID_1/CF_API_TOKEN_1)")

    return AIRouter(provider=pollinations, cloudflare=cloudflare)


def get_ai_router() -> AIRouter:
    """Get or create the global AI router singleton."""
    global ai_router
    if ai_router is None:
        ai_router = _create_default_router()
    return ai_router
