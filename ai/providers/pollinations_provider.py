"""Pollinations AI provider with dual-key failover for masha-bot.

Supports 60+ models with automatic failover, circuit breaking,
and content generation for the BMW-focused Telegram channel.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import random
import time
from typing import Any, Optional

import aiohttp

from .base import AIResponse, BaseAIProvider

logger = logging.getLogger(__name__)

# ── Model catalogues ──────────────────────────────────────────────────────────

CHAT_MODELS = [
    "openai", "openai-large", "openai-reasoning",
    "qwen-coder", "qwen", "llama", "llama-scale",
    "mistral", "mistral-large", "mistral-reasoning",
    "deepseek", "deepseek-r1", "deepseek-reasoner",
    "command-r",
    "unity", "midijourney", "rtist",
    "searchgpt", "p1", "evil",
    "claude-hybridspace", "sur", "sur-mistral",
    "pikachu", "planning", "bidara",
]

VISION_MODELS = [
    "openai", "openai-large", "qwen", "llama",
    "mistral", "mistral-large", "deepseek",
    "command-r", "sur",
]

CONTENT_MODELS = [
    "openai", "openai-large", "qwen", "llama",
    "mistral", "mistral-large", "deepseek",
    "deepseek-r1", "command-r",
]

IMAGE_MODELS = [
    "flux", "flux-realism", "flux-cablyai", "flux-anime",
    "flux-3d", "any-dark", "flux-pro",
]

REASONING_MODELS = [
    "openai-reasoning", "deepseek-r1", "deepseek-reasoner",
    "mistral-reasoning",
]

# ── Circuit breaker ───────────────────────────────────────────────────────────

class CircuitBreaker:
    """Simple circuit breaker for API endpoints."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time: float = 0.0
        self.state: str = "closed"  # closed | open | half-open

    @property
    def is_open(self) -> bool:
        if self.state == "open":
            if time.monotonic() - self.last_failure_time > self.recovery_timeout:
                self.state = "half-open"
                return False
            return True
        return False

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.warning(
                "Circuit breaker OPEN after %d failures", self.failure_count
            )


# ── Pollinations Provider ─────────────────────────────────────────────────────

class PollinationsProvider(BaseAIProvider):
    """Pollinations.ai provider with dual API key failover."""

    name = "pollinations"

    BASE_URL = "https://text.pollinations.ai"
    IMAGE_BASE_URL = "https://image.pollinations.ai"

    def __init__(
        self,
        api_key: str | None = None,
        api_key_2: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(api_key=api_key, **kwargs)
        self.api_key_2 = api_key_2
        self._keys: list[str] = []
        if api_key:
            self._keys.append(api_key)
        if api_key_2:
            self._keys.append(api_key_2)
        self._key_index = 0
        self._circuit = CircuitBreaker()
        self._model_circuits: dict[str, CircuitBreaker] = {}
        self._request_count = 0

    def _get_next_key(self) -> str | None:
        if not self._keys:
            return None
        key = self._keys[self._key_index % len(self._keys)]
        self._key_index += 1
        return key

    def _get_circuit(self, model: str) -> CircuitBreaker:
        if model not in self._model_circuits:
            self._model_circuits[model] = CircuitBreaker()
        return self._model_circuits[model]

    def _pick_model(self, model: str | None, pool: list[str]) -> str:
        if model and model in pool:
            return model
        if model:
            return model
        return random.choice(pool)

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=90),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        seed: int | None = None,
        json_mode: bool = False,
        private: bool = True,
        **kwargs: Any,
    ) -> AIResponse:
        """Send a chat completion request to Pollinations."""
        chosen_model = self._pick_model(model, CHAT_MODELS)
        circuit = self._get_circuit(chosen_model)

        if circuit.is_open and self._circuit.is_open:
            return AIResponse(
                error="All circuits open — service unavailable",
                provider=self.name,
                model=chosen_model,
            )

        start = time.monotonic()

        # Try with each key
        for attempt in range(len(self._keys) + 1):
            key = self._get_next_key()
            headers: dict[str, str] = {}
            if key:
                headers["Authorization"] = f"Bearer {key}"

            payload: dict[str, Any] = {
                "messages": messages,
                "model": chosen_model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "seed": seed or random.randint(1, 999999),
                "jsonMode": json_mode,
                "private": private,
            }

            try:
                session = self._get_session()
                url = f"{self.BASE_URL}/"
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        elapsed = (time.monotonic() - start) * 1000
                        circuit.record_success()
                        self._circuit.record_success()
                        self._request_count += 1
                        return AIResponse(
                            text=text,
                            model=chosen_model,
                            provider=self.name,
                            latency_ms=elapsed,
                        )
                    elif resp.status == 429:
                        logger.warning("Rate limited, switching key (attempt %d)", attempt)
                        continue
                    else:
                        body = await resp.text()
                        logger.error(
                            "Pollinations chat error %d: %s", resp.status, body[:200]
                        )
                        circuit.record_failure()
                        continue

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.error("Pollinations request failed: %s", exc)
                circuit.record_failure()
                continue

        # All keys failed — try a fallback model
        fallbacks = [m for m in CHAT_MODELS if m != chosen_model]
        if fallbacks:
            fb = random.choice(fallbacks[:3])
            logger.info("Trying fallback model: %s", fb)
            try:
                key = self._get_next_key()
                headers = {}
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                payload["model"] = fb
                session = self._get_session()
                async with session.post(
                    f"{self.BASE_URL}/", json=payload, headers=headers
                ) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        elapsed = (time.monotonic() - start) * 1000
                        return AIResponse(
                            text=text, model=fb, provider=self.name, latency_ms=elapsed
                        )
            except Exception as exc:
                logger.error("Fallback model also failed: %s", exc)

        return AIResponse(
            error=f"All attempts failed for model {chosen_model}",
            provider=self.name,
            model=chosen_model,
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        model: str | None = None,
        seed: int | None = None,
        nologo: bool = True,
        enhance: bool = True,
        **kwargs: Any,
    ) -> AIResponse:
        """Generate an image via Pollinations image API."""
        chosen_model = self._pick_model(model, IMAGE_MODELS)
        start = time.monotonic()

        # Build the URL directly (Pollinations image API is GET-based)
        params: dict[str, Any] = {
            "width": width,
            "height": height,
            "model": chosen_model,
            "nologo": str(nologo).lower(),
            "enhance": str(enhance).lower(),
            "seed": seed or random.randint(1, 999999),
        }
        # Encode prompt in URL path
        from urllib.parse import quote
        encoded_prompt = quote(prompt, safe="")
        url = f"{self.IMAGE_BASE_URL}/prompt/{encoded_prompt}"

        for attempt in range(len(self._keys) + 1):
            key = self._get_next_key()
            try:
                session = self._get_session()
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        img_bytes = await resp.read()
                        if len(img_bytes) < 1000:
                            logger.warning("Image too small (%d bytes), retrying", len(img_bytes))
                            continue
                        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                        elapsed = (time.monotonic() - start) * 1000
                        # Also construct the direct URL for reference
                        direct_url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
                        return AIResponse(
                            image_b64=img_b64,
                            image_url=direct_url,
                            model=chosen_model,
                            provider=self.name,
                            latency_ms=elapsed,
                        )
                    elif resp.status == 429:
                        logger.warning("Image rate limited, switching key (attempt %d)", attempt)
                        continue
                    else:
                        body = await resp.text()
                        logger.error("Image generation error %d: %s", resp.status, body[:200])
                        continue
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.error("Image request failed: %s", exc)
                continue

        return AIResponse(
            error="Image generation failed after all attempts",
            provider=self.name,
            model=chosen_model,
            latency_ms=(time.monotonic() - start) * 1000,
        )
