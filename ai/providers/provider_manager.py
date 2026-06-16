"""Provider Manager v3.0 — LOCAL-FIRST multi-provider failover for masha-bot.

Manages the provider failover chain with route-aware routing:

ROUTE STRATEGY (LOCAL-FIRST):
  CHAT route (user chats)    → Local → Pollinations(key) → Pollinations(free) → Cloudflare → HuggingFace
  COMMENT route (groups)     → Local → Pollinations(key) → Pollinations(free) → Cloudflare → HuggingFace
  FUNCTION route (posts,VIN) → Pollinations(key) → Pollinations(free) → Cloudflare → Local(fallback) → HuggingFace
  VISION tasks (photos)      → Pollinations vision → Cloudflare vision → (Local can't do vision)
  IMAGE generation           → Pollinations → Cloudflare → HuggingFace
  LOCAL-ONLY (last resort)   → Local model directly — when ALL cloud providers are down/exhausted

Level 0: Local Model (RuadaptQwen3-4B-Instruct GGUF, CPU) — chat & comments FIRST
  CHAT route local limit: 1024 tokens (fast user answers on CPU)
  COMMENT route local limit: 256 tokens (short group comments, must be fast)
  FUNCTION route local limit: 1024 tokens (fallback for posts, VIN, diagnostics)
  LOCAL-ONLY post limit: 1024 tokens (dedicated posting via local model)
Level 1: Pollinations (gen API with key → legacy free API)
Level 2: Cloudflare Workers AI (free, 10k req/day/account)
Level 3: HuggingFace Spaces (free, unlimited)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from .base import AIResponse, BaseAIProvider
from .local_provider import LocalProvider
from .pollinations_provider import PollinationsProvider
from .cloudflare_provider import CloudflareProvider
from .huggingface_provider import HuggingFaceProvider

logger = logging.getLogger(__name__)

# ── Route types for LOCAL-FIRST strategy ──

ROUTE_CHAT = "chat"           # User chat — Local first (saves cloud balance)
ROUTE_COMMENT = "comment"     # Group comments — Local first (short, cheap)
ROUTE_FUNCTION = "function"   # Posts, VIN, diagnostics — Cloud first (needs quality)
ROUTE_VISION = "vision"       # Photo analysis — Cloud only (local can't do vision)
ROUTE_IMAGE = "image"         # Image generation — Cloud only


class ProviderManager:
    """Manages AI providers with LOCAL-FIRST automatic failover.

    Provider chain for TEXT (route-aware):
    - CHAT/COMMENT: Local → Pollinations → Cloudflare → HuggingFace
    - FUNCTION: Pollinations → Cloudflare → Local(fallback) → HuggingFace
    - VISION: Pollinations vision → Cloudflare vision

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
        local: LocalProvider | None = None,
    ) -> None:
        self.pollinations = pollinations
        self.cloudflare = cloudflare
        self.huggingface = huggingface or HuggingFaceProvider()
        self.local = local
        self._cf_fallback_count = 0
        self._hf_fallback_count = 0
        self._local_fallback_count = 0
        self._total_requests = 0
        self._last_provider: str = ""

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        route_type: str = ROUTE_CHAT,
        **kwargs: Any,
    ) -> AIResponse:
        """Send a chat request with LOCAL-FIRST provider failover.

        Route strategy:
          CHAT/COMMENT → Local → Pollinations → Cloudflare → HuggingFace
          FUNCTION     → Pollinations → Cloudflare → Local(fallback) → HuggingFace
          VISION       → Pollinations vision → Cloudflare vision
        """
        self._total_requests += 1

        # ── CHAT / COMMENT route: Local FIRST ──
        if route_type in (ROUTE_CHAT, ROUTE_COMMENT):
            # Level 0: Try Local model first
            if self.local and self.local.is_available():
                try:
                    # Route-aware token limits for local model:
                    #   CHAT: up to 1024 (user conversations — local model is fast at this)
                    #   COMMENT: up to 256 (short group/channel comments — must be fast)
                    # Capped lower than cloud to keep CPU inference fast
                    local_max = min(max_tokens, 1024) if route_type == ROUTE_CHAT else min(max_tokens, 256)
                    if max_tokens > local_max:
                        logger.debug(
                            "Local model token limit: %d → %d (route=%s)",
                            max_tokens, local_max, route_type,
                        )
                    result = await self.local.chat(
                        messages=messages,
                        model="local-ruadapt-qwen3-4b",
                        temperature=temperature,
                        max_tokens=local_max,
                        **kwargs,
                    )
                    if result.ok:
                        self._last_provider = "local"
                        self._local_fallback_count += 1
                        logger.info("Local model responded (route=%s)", route_type)
                        return result
                    logger.debug("Local model failed: %s", result.error or "unknown")
                except Exception as exc:
                    logger.debug("Local model exception: %s", exc)

        # ── VISION route: Cloud only (local can't do vision) ──
        if route_type == ROUTE_VISION:
            return await self._chat_cloud_only(
                messages=messages, model=model,
                temperature=temperature, max_tokens=max_tokens, **kwargs,
            )

        # ── FUNCTION route: Cloud FIRST, Local as fallback ──
        # Also used as cloud fallback for CHAT/COMMENT routes
        # Level 1: Try Pollinations
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
                "Pollinations chat failed: %s, trying next provider",
                result.error or "unknown error",
            )
        except Exception as exc:
            logger.error("Pollinations chat exception: %s", exc)

        # Level 2: Try Cloudflare Workers AI
        if self.cloudflare and self.cloudflare.is_available():
            try:
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

        # Level 2.5: For FUNCTION routes — try Local as LAST fallback
        if route_type == ROUTE_FUNCTION and self.local and self.local.is_available():
            try:
                # FUNCTION route local limit: 1024 tokens
                # Local 4B model can generate decent short posts/diagnostics
                # 1024 tokens = ~700-900 Russian chars, good for Telegram media caption
                # On CPU ~5-10 tokens/sec → 1024 tokens = ~100-200s max generation time
                local_max = min(max_tokens, 1024)
                result = await self.local.chat(
                    messages=messages,
                    model="local-ruadapt-qwen3-4b",
                    temperature=temperature,
                    max_tokens=local_max,
                    **kwargs,
                )
                if result.ok:
                    self._last_provider = "local"
                    self._local_fallback_count += 1
                    logger.info("Local model fallback succeeded (FUNCTION route)")
                    return result
            except Exception as exc:
                logger.debug("Local model fallback exception: %s", exc)

        # Level 3: HuggingFace — image generation only, text chat NOT supported
        # HuggingFace Spaces are primarily for image generation.
        # Text chat always returns error ("HuggingFace provider is image-only"),
        # so skip for text routes to save time and avoid wasted requests.
        # if self.huggingface and self.huggingface.is_available():
        #     ... text chat attempt removed — HF is image-only ...

        # All providers failed
        return AIResponse(
            error="All AI providers failed (Local + Pollinations + Cloudflare + HuggingFace)",
            provider="none",
            model=model or "unknown",
        )

    async def chat_local_only(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.8,
    ) -> AIResponse:
        """Chat using ONLY the local model — bypasses all cloud providers.

        Used as a last-resort fallback when ALL cloud providers are unavailable
        or have exhausted their rate limits. This ensures the bot can ALWAYS
        generate content for the channel, even if quality is lower.

        The local model (RuadaptQwen3-4B-Instruct) is less creative than cloud models but
        can produce acceptable short posts with simplified prompts.

        Args:
            messages: Chat messages (should use simplified prompts for 4B model)
            max_tokens: Max tokens to generate (capped at 1024 for CPU speed)
            temperature: Sampling temperature (0.8 default for some creativity)

        Returns:
            AIResponse from local model, or error if local model is unavailable
        """
        if not self.local:
            return AIResponse(
                error="Local model not configured",
                provider="none",
                model="local-ruadapt-qwen3-4b",
            )

        if not self.local.is_available():
            # Try loading it one more time
            try:
                loaded = self.local._load_model()
                if not loaded:
                    return AIResponse(
                        error="Local model not available (load failed)",
                        provider="none",
                        model="local-ruadapt-qwen3-4b",
                    )
            except Exception as e:
                return AIResponse(
                    error=f"Local model load error: {e}",
                    provider="none",
                    model="local-ruadapt-qwen3-4b",
                )

        # Cap at 1024 tokens for CPU inference speed
        actual_max = min(max_tokens, 1024)

        try:
            result = await self.local.chat(
                messages=messages,
                model="local-ruadapt-qwen3-4b",
                temperature=temperature,
                max_tokens=actual_max,
            )
            if result.ok:
                self._last_provider = "local-only"
                self._local_fallback_count += 1
                logger.info(
                    "Local-ONLY model responded (%d chars, %d tokens requested)",
                    len(result.text), actual_max,
                )
            return result
        except Exception as exc:
            logger.error("Local-only chat exception: %s", exc)
            return AIResponse(
                error=f"Local-only chat failed: {exc}",
                provider="local",
                model="local-ruadapt-qwen3-4b",
            )

    async def _chat_cloud_only(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> AIResponse:
        """Cloud-only chat for VISION tasks (local model can't do vision)."""
        # Try Pollinations vision models
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
        except Exception as exc:
            logger.debug("Pollinations vision failed: %s", exc)

        # Try Cloudflare vision
        if self.cloudflare and self.cloudflare.is_available():
            try:
                result = await self.cloudflare.chat(
                    messages=messages,
                    model=None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                if result.ok:
                    self._last_provider = "cloudflare"
                    return result
            except Exception as exc:
                logger.debug("Cloudflare vision failed: %s", exc)

        return AIResponse(
            error="All vision providers failed",
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
        Local model is NOT used for image generation.
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
        # Local provider doesn't need async close

    def get_status(self) -> dict[str, Any]:
        """Get full provider status."""
        status = {
            "total_requests": self._total_requests,
            "cf_fallback_count": self._cf_fallback_count,
            "hf_fallback_count": self._hf_fallback_count,
            "local_fallback_count": self._local_fallback_count,
            "last_provider": self._last_provider,
            "pollinations": self.pollinations.get_status(),
        }
        if self.local:
            status["local"] = self.local.get_status()
        else:
            status["local"] = {"status": "not configured", "available": False}
        if self.cloudflare:
            status["cloudflare"] = self.cloudflare.get_status()
        else:
            status["cloudflare"] = {"available": False, "reason": "not configured"}
        if self.huggingface:
            status["huggingface"] = self.huggingface.get_status()
        else:
            status["huggingface"] = {"available": False, "reason": "not configured"}
        return status
