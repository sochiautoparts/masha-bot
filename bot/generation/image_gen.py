"""AI image generation via Pollinations for masha-bot.

Generates BMW-themed images for channel posts using the
Pollinations image API with flux models.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Optional

from ...ai.router import AIRouter
from ...ai.providers.pollinations_provider import PollinationsProvider, IMAGE_MODELS
from ...bot.core.config import get_config

logger = logging.getLogger(__name__)

# ── Image prompt templates ────────────────────────────────────────────────────

BMW_IMAGE_TEMPLATES: dict[str, list[str]] = {
    "news+reaction": [
        "BMW {model} in dramatic studio lighting, professional automotive photography, dark background, 4k",
        "BMW {model} driving on mountain road, golden hour, cinematic, wide angle, 4k",
        "BMW {model} front view, aggressive stance, urban environment, professional photo, 4k",
    ],
    "DIY/how-to": [
        "BMW {model} engine bay detailed shot, professional photography, clean garage, 4k",
        "BMW {model} under the hood, close-up of engine components, workshop lighting, 4k",
        "BMW engine parts on workbench, organized, professional automotive photography, 4k",
    ],
    "polls/debates": [
        "Two BMW models side by side comparison, split view, professional automotive photography, 4k",
        "BMW {model} and competitor, dramatic comparison, track setting, 4k",
    ],
    "lore/history": [
        "Classic BMW {model} vintage shot, nostalgic film look, 4k",
        "BMW {model} historic racing, vintage motorsport photography, 4k",
        "Classic BMW {model} in original setting, period-correct background, 4k",
    ],
    "garage stories": [
        "BMW in professional workshop, mechanic working, warm lighting, 4k",
        "BMW {model} on lift in garage, tools around, authentic atmosphere, 4k",
    ],
    "partner": [
        "BMW {model} with premium accessories, lifestyle shot, professional, 4k",
        "BMW parts and accessories display, professional product photography, 4k",
    ],
}

DEFAULT_MODELS_FOR_IMAGES = [
    "BMW M5 F90 Competition",
    "BMW M3 G80",
    "BMW M4 G82",
    "BMW X5 M Competition",
    "BMW i4 M50",
    "BMW iX M60",
    "BMW M2 G87",
]


class ImageGenerator:
    """Generates BMW-themed images for channel posts."""

    def __init__(self) -> None:
        self._router: Optional[AIRouter] = None

    def _get_router(self) -> AIRouter:
        if self._router is None:
            config = get_config()
            provider = PollinationsProvider(
                api_key=config.pollinations_api_key,
                api_key_2=config.pollinations_api_key_2,
            )
            self._router = AIRouter(provider=provider)
        return self._router

    async def generate(
        self,
        topic: str,
        content_type: str = "news+reaction",
        model: str | None = None,
    ) -> dict[str, Any] | None:
        """Generate a BMW-themed image for a post.

        Returns dict with 'image_b64' and 'image_url' or None.
        """
        try:
            # Build the image prompt
            prompt = self._build_prompt(topic, content_type, model)

            # Generate image via Pollinations
            router = self._get_router()
            img_prompt = await router.generate_image_prompt(topic)
            if img_prompt:
                prompt = img_prompt

            response = await router.manager.generate_image(
                prompt=prompt,
                width=1024,
                height=768,
                model=random.choice(IMAGE_MODELS[:3]),
            )

            if not response.ok:
                logger.warning("Image generation failed: %s", response.error)
                return None

            return {
                "image_b64": response.image_b64,
                "image_url": response.image_url,
                "model": response.model,
                "prompt": prompt,
            }

        except Exception as exc:
            logger.error("Image generation error: %s", exc)
            return None

    def _build_prompt(
        self,
        topic: str,
        content_type: str,
        specific_model: str | None = None,
    ) -> str:
        """Build an image generation prompt."""
        bmw_model = specific_model or random.choice(DEFAULT_MODELS_FOR_IMAGES)

        templates = BMW_IMAGE_TEMPLATES.get(content_type, BMW_IMAGE_TEMPLATES["news+reaction"])
        template = random.choice(templates)

        prompt = template.format(model=bmw_model)

        # Add topic-specific details
        topic_lower = topic.lower()
        if "nürburgring" in topic_lower or "нюрбургринг" in topic_lower:
            prompt += ", Nürburgring Nordschleife track, racing atmosphere"
        elif "electric" in topic_lower or "электр" in topic_lower or topic_lower.startswith("i"):
            prompt += ", electric vehicle, futuristic, clean energy"
        elif "classic" in topic_lower or "e30" in topic_lower or "e46" in topic_lower:
            prompt += ", vintage feel, classic BMW styling"
        elif "m performance" in topic_lower:
            prompt += ", M Performance parts, aggressive styling"
        elif "individual" in topic_lower:
            prompt += ", BMW Individual, premium paint, luxury detail"

        return prompt

    async def generate_from_url(self, image_url: str) -> dict[str, Any] | None:
        """Download and process an image from a URL."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status == 200:
                        import base64
                        img_bytes = await resp.read()
                        if len(img_bytes) < 1000:
                            return None
                        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                        return {
                            "image_b64": img_b64,
                            "image_url": image_url,
                            "source": "url",
                        }
        except Exception as exc:
            logger.error("Image download error: %s", exc)
        return None

    async def close(self) -> None:
        """Clean up resources."""
        if self._router:
            await self._router.provider.close()
