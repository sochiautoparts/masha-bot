"""Provider Manager — intelligent multi-provider failover for masha-bot.

Manages the provider failover chain:
1. Pollinations (gen API with key → legacy free API)
2. Cloudflare Workers AI (free, 10k req/day per account)
3. Hugging Face Spaces (free, unlimited)

Automatically switches providers based on:
- Availability (circuit breaker state)
- Error patterns (auth failures → switch provider)
- Health checks
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from .base import AIResponse, BaseAIProvider
from .pollinations_provider import PollinationsProvider
from .cloudflare_provider import CloudflareProvider
from .huggingface_provider import HuggingFaceProvider

logger = logging.getLogger(__name__)


class ProviderManager:
    """Manages AI providers with automatic failover.

    Provider chain for TEXT:
    1. Pollinations (gen API with key → legacy free)
    2. Cloudflare Workers AI

    Provider chain for IMAGES:
    1. Pollinations (gen API → legacy free with retry)
    2. Cloudflare Workers AI (Stable Diffusion XL)
    3. Hugging Face Spaces (free Inference API + Gradio Spaces)
    """

    def __init__(
        self,
        pollinations: PollinationsProvider,
        cloudflare: CloudflareProvider | None = None,
        huggingface: HuggingFaceProvider | None = None,
    ) -> None:
        self.pollinations = pollinations
        self.cloudflare = cloudflare
        self.huggingface = huggingface or HuggingFaceProvider()
        self._cf_fallback_count = 0
        self._hf_fallback_count = 0
        self._total_requests = 0
        self._last_provider: str = ""

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> AIResponse:
        """Send a chat request with provider failover.

        Chain: Pollinations → Cloudflare
        """
        self._total_requests += 1

        # 1. Try Pollinations (which has its own internal gen→legacy fallback)
        try:
            result = await self.pollinations.chat(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            if result.ok:
                self._last_provider = result.provider or "pollinations"
                return result

            logger.warning(
                "Pollinations chat failed: %s, trying Cloudflare",
                result.error or "unknown error",
            )
        except Exception as exc:
            logger.error("Pollinations chat exception: %s", exc)

        # 2. Try Cloudflare Workers AI
        if self.cloudflare and self.cloudflare.is_available():
            try:
                # Map Pollinations model names to CF models
                cf_model = self._map_model_to_cf(model)
                result = await self.cloudflare.chat(
                    messages=messages,
                    model=cf_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                if result.ok:
                    self._cf_fallback_count += 1
                    self._last_provider = "cloudflare"
                    logger.info("Cloudflare fallback succeeded (model=%s)", cf_model)
                    return result

                logger.warning("Cloudflare chat failed: %s", result.error or "unknown")
            except Exception as exc:
                logger.error("Cloudflare chat exception: %s", exc)

        # 3. Try HuggingFace (text chat)
        if self.huggingface and self.huggingface.is_available():
            try:
                result = await self.huggingface.chat(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                if result.ok:
                    self._last_provider = "huggingface"
                    logger.info("HuggingFace fallback succeeded (text)")
                    return result
            except Exception as exc:
                logger.debug("HuggingFace chat exception: %s", exc)

        # All providers failed
        return AIResponse(
            error="All AI providers failed (Pollinations + Cloudflare + HuggingFace)",
            provider="none",
            model=model or "unknown",
        )

    async def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        model: str | None = None,
        **kwargs: Any,
    ) -> AIResponse:
        """Generate an image with provider failover.

        Chain: Pollinations (gen→legacy with retry) → Cloudflare (SDXL) → HuggingFace
        """
        self._total_requests += 1

        # 1. Try Pollinations (which has internal gen→legacy fallback with retry)
        try:
            result = await self.pollinations.generate_image(
                prompt=prompt,
                width=width,
                height=height,
                model=model,
                **kwargs,
            )
            if result.ok:
                self._last_provider = result.provider or "pollinations"
                return result
            logger.warning("Pollinations image generation failed: %s", result.error or "unknown")
        except Exception as exc:
            logger.error("Pollinations image generation failed: %s", exc)

        # 2. Try Cloudflare Workers AI (Stable Diffusion XL)
        # Add delay before switching providers to avoid rapid-fire rate limits
        await asyncio.sleep(3)
        if self.cloudflare and self.cloudflare.is_available():
            try:
                result = await self.cloudflare.generate_image(
                    prompt=prompt,
                    width=width,
                    height=height,
                    model=None,  # Use default CF image model
                )
                if result.ok:
                    self._last_provider = "cloudflare"
                    logger.info("Cloudflare image fallback succeeded")
                    return result

                logger.warning("Cloudflare image generation failed: %s", result.error or "unknown")
            except Exception as exc:
                logger.error("Cloudflare image generation exception: %s", exc)

        # 3. Try Hugging Face Spaces (free, always available)
        if self.huggingface and self.huggingface.is_available():
            try:
                result = await self.huggingface.generate_image(
                    prompt=prompt,
                    width=width,
                    height=height,
                    model=model,
                )
                if result.ok:
                    self._hf_fallback_count += 1
                    self._last_provider = "huggingface"
                    logger.info("HuggingFace image fallback succeeded")
                    return result

                logger.warning("HuggingFace image generation failed: %s", result.error or "unknown")
            except Exception as exc:
                logger.error("HuggingFace image generation exception: %s", exc)

        # All providers failed
        return AIResponse(
            error="Image generation failed on all providers (Pollinations + Cloudflare + HuggingFace)",
            provider="none",
            model=model or "unknown",
        )

    def _map_model_to_cf(self, model: str | None) -> str | None:
        """Map Pollinations model names to Cloudflare equivalents."""
        if not model:
            return None  # Use CF default

        mapping = {
            "openai": "mistral",
            "openai-large": "mistral",
            "mistral": "mistral",
            "mistral-large": "mistral",
            "deepseek": "deepseek",
            "deepseek-r1": "deepseek",
            "llama": "llama",
            "qwen-coder": "mistral",
            "searchgpt": "mistral",
            "sur": "mistral",
        }
        return mapping.get(model)

    def is_available(self) -> bool:
        """Check if any provider is available."""
        return True  # At least legacy Pollinations should always work

    async def close(self) -> None:
        """Clean up all provider resources."""
        await self.pollinations.close()
        if self.cloudflare:
            await self.cloudflare.close()
        if self.huggingface:
            await self.huggingface.close()

    def get_status(self) -> dict[str, Any]:
        """Get full provider status."""
        status = {
            "total_requests": self._total_requests,
            "cf_fallback_count": self._cf_fallback_count,
            "hf_fallback_count": self._hf_fallback_count,
            "last_provider": self._last_provider,
            "pollinations": self.pollinations.get_status(),
        }
        if self.cloudflare:
            status["cloudflare"] = self.cloudflare.get_status()
        else:
            status["cloudflare"] = {"available": False, "reason": "not configured"}
        if self.huggingface:
            status["huggingface"] = self.huggingface.get_status()
        else:
            status["huggingface"] = {"available": False, "reason": "not configured"}
        return status
