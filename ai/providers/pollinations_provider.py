"""Pollinations AI provider with dual-key failover + legacy fallback for masha-bot.

Priority chain:
1. gen.pollinations.ai (new API, with API key) — best models, credit-based
2. text.pollinations.ai (legacy API, no key) — free, rate-limited (1 req/IP)
3. image.pollinations.ai (legacy image API, no key) — free, rate-limited

When API keys run out or credits depleted, automatically falls back to
legacy free endpoints without Authorization header.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

from .base import AIResponse, BaseAIProvider

logger = logging.getLogger(__name__)

# ── Model catalogues ──────────────────────────────────────────────────────────

# Best models for masha-bot content (tested, good Russian support)
CHAT_MODELS = [
    "openai", "openai-large",
    "qwen-coder", "qwen",
    "llama", "llama-scale",
    "mistral", "mistral-large",
    "deepseek", "deepseek-r1", "deepseek-reasoner",
    "command-r",
    "searchgpt", "sur",
]

# Models that work well on legacy free API (text.pollinations.ai)
LEGACY_CHAT_MODELS = [
    "openai", "mistral", "deepseek", "llama",
    "qwen", "mistral-large", "qwen-coder", "command-r",
]

VISION_MODELS = [
    "openai", "openai-large", "qwen", "llama",
    "mistral", "mistral-large", "deepseek",
    "command-r",
]

CONTENT_MODELS = [
    "openai", "openai-large", "qwen", "llama",
    "mistral", "mistral-large", "deepseek",
    "deepseek-r1", "command-r",
]

IMAGE_MODELS = [
    "flux", "flux-realism", "flux-cablyai",
    "flux-3d", "flux-pro",
]

REASONING_MODELS = [
    "openai-reasoning", "deepseek-r1", "deepseek-reasoner",
    "mistral-reasoning",
]

# ── API endpoints ─────────────────────────────────────────────────────────────

GEN_BASE_URL = "https://gen.pollinations.ai"        # New API (requires key)
LEGACY_TEXT_URL = "https://text.pollinations.ai"     # Legacy free API
LEGACY_IMAGE_URL = "https://image.pollinations.ai"   # Legacy free image API


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
    """Pollinations.ai provider with triple-fallback:

    1. gen.pollinations.ai/v1 (with API key, credit-based)
    2. text.pollinations.ai (legacy, free, no key, rate-limited)
    3. For images: image.pollinations.ai (legacy, free, no key)
    """

    name = "pollinations"

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
        self._legacy_circuit = CircuitBreaker(failure_threshold=3, recovery_timeout=120.0)
        self._model_circuits: dict[str, CircuitBreaker] = {}
        self._request_count = 0
        self._legacy_request_count = 0
        self._gen_fail_count = 0  # Track gen API failures for smart fallback
        self._last_time = time.monotonic()  # For latency calculation

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

    def _should_try_legacy_first(self) -> bool:
        """If gen API has been failing consistently, try legacy first."""
        return self._gen_fail_count >= 3

    # ── TEXT: gen.pollinations.ai (with key) ─────────────────────────────────

    async def _chat_gen_api(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
        seed: int | None,
        json_mode: bool,
    ) -> AIResponse | None:
        """Try new gen.pollinations.ai API with API key. Returns None on failure."""
        if not self._keys:
            return None

        url = f"{GEN_BASE_URL}/v1/chat/completions"
        start = time.monotonic()

        for attempt in range(len(self._keys)):
            key = self._get_next_key()
            if not key:
                continue

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            }
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            try:
                session = self._get_session()
                async with session.post(url, json=payload, headers=headers) as resp:
                    elapsed = (time.monotonic() - start) * 1000

                    if resp.status == 200:
                        data = await resp.json()
                        content = ""
                        if "choices" in data and data["choices"]:
                            msg = data["choices"][0].get("message", {})
                            content = msg.get("content", "")

                        if content:
                            self._gen_fail_count = 0  # Reset on success
                            return AIResponse(
                                text=content,
                                model=model,
                                provider=self.name,
                                latency_ms=elapsed,
                            )
                        logger.warning("gen.pollinations.ai returned empty content")
                        continue

                    elif resp.status == 429:
                        logger.warning("gen.pollinations.ai rate limited (attempt %d)", attempt)
                        continue

                    elif resp.status == 402:
                        body = await resp.text()
                        logger.warning("gen.pollinations.ai insufficient balance: %s", body[:100])
                        self._gen_fail_count += 1
                        continue  # Try next key or fallback

                    elif resp.status == 401:
                        body = await resp.text()
                        logger.warning("gen.pollinations.ai auth error: %s", body[:100])
                        self._gen_fail_count += 1
                        continue  # Key invalid, try next

                    else:
                        body = await resp.text()
                        logger.error("gen.pollinations.ai error %d: %s", resp.status, body[:200])
                        continue

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.error("gen.pollinations.ai request failed: %s", exc)
                continue

        self._gen_fail_count += 1
        return None

    # ── TEXT: text.pollinations.ai (legacy, free, no key) ────────────────────

    async def _chat_legacy_api(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AIResponse | None:
        """Try legacy text.pollinations.ai API (free, no key). Returns None on failure."""
        if self._legacy_circuit.is_open:
            logger.debug("Legacy API circuit breaker is OPEN, skipping")
            return None

        # Use legacy-compatible model
        legacy_model = model
        if model not in LEGACY_CHAT_MODELS:
            legacy_model = random.choice(LEGACY_CHAT_MODELS)
            logger.info("Model %s not in legacy pool, using %s instead", model, legacy_model)

        url = f"{LEGACY_TEXT_URL}/"
        payload = {
            "messages": messages,
            "model": legacy_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "seed": random.randint(1, 999999),
            "private": True,
        }
        # NO Authorization header — this is the free anonymous endpoint
        headers = {"Content-Type": "application/json"}

        start = time.monotonic()
        try:
            session = self._get_session()
            async with session.post(url, json=payload, headers=headers) as resp:
                elapsed = (time.monotonic() - start) * 1000

                if resp.status == 200:
                    text = await resp.text()
                    if text and len(text) > 5:
                        self._legacy_circuit.record_success()
                        self._legacy_request_count += 1
                        logger.info("Legacy API success: model=%s, %dms", legacy_model, round(elapsed))
                        return AIResponse(
                            text=text,
                            model=legacy_model,
                            provider=f"{self.name}-legacy",
                            latency_ms=elapsed,
                        )
                    logger.warning("Legacy API returned empty/short response")
                    self._legacy_circuit.record_failure()
                    return None

                elif resp.status == 429:
                    logger.warning("Legacy API rate limited")
                    self._legacy_circuit.record_failure()
                    return None

                else:
                    body = await resp.text()
                    logger.error("Legacy API error %d: %s", resp.status, body[:200])
                    self._legacy_circuit.record_failure()
                    return None

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.error("Legacy API request failed: %s", exc)
            self._legacy_circuit.record_failure()
            return None

    # ── Main chat method with triple fallback ────────────────────────────────

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
        """Send a chat completion request with automatic fallback.

        Priority: gen.pollinations.ai (with key) → legacy text.pollinations.ai (free)
        """
        chosen_model = self._pick_model(model, CHAT_MODELS)
        circuit = self._get_circuit(chosen_model)

        if circuit.is_open and self._circuit.is_open:
            # All circuits open — try legacy directly
            logger.warning("All circuits open, trying legacy API directly")
            result = await self._chat_legacy_api(messages, chosen_model, temperature, max_tokens)
            if result:
                return result
            return AIResponse(
                error="All circuits open and legacy API unavailable",
                provider=self.name,
                model=chosen_model,
            )

        start = time.monotonic()

        # Decide order: if gen API has been failing, try legacy first
        if self._should_try_legacy_first():
            logger.info("Gen API failing (%d consecutive failures), trying legacy first", self._gen_fail_count)
            legacy_result = await self._chat_legacy_api(messages, chosen_model, temperature, max_tokens)
            if legacy_result:
                return legacy_result
            # Legacy failed too, try gen API anyway
            gen_result = await self._chat_gen_api(messages, chosen_model, temperature, max_tokens, seed, json_mode)
            if gen_result:
                return gen_result
        else:
            # Normal: try gen API first, then legacy
            gen_result = await self._chat_gen_api(messages, chosen_model, temperature, max_tokens, seed, json_mode)
            if gen_result:
                circuit.record_success()
                self._circuit.record_success()
                self._request_count += 1
                return gen_result

            # Gen API failed — try legacy
            logger.info("Gen API failed, falling back to legacy API for model %s", chosen_model)
            legacy_result = await self._chat_legacy_api(messages, chosen_model, temperature, max_tokens)
            if legacy_result:
                circuit.record_success()  # Still counts as success via fallback
                return legacy_result

            circuit.record_failure()

        # All failed — try a fallback model on legacy
        fallbacks = [m for m in LEGACY_CHAT_MODELS if m != chosen_model]
        if fallbacks:
            for fb in fallbacks[:2]:
                logger.info("Trying fallback model on legacy: %s", fb)
                result = await self._chat_legacy_api(messages, fb, temperature, max_tokens)
                if result:
                    return result

        return AIResponse(
            error=f"All attempts failed for model {chosen_model}",
            provider=self.name,
            model=chosen_model,
            latency_ms=(time.monotonic() - start) * 1000,
        )

    # ── Image generation with fallback ───────────────────────────────────────

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
        """Generate an image with fallback from gen API to legacy API.

        Priority: gen.pollinations.ai (with key) → image.pollinations.ai (free)
        """
        chosen_model = self._pick_model(model, IMAGE_MODELS)
        start = time.monotonic()

        # 1. Try gen API with key first (if keys available and not consistently failing)
        if self._keys and not self._should_try_legacy_first():
            gen_result = await self._generate_image_gen_api(prompt, width, height, chosen_model, seed)
            if gen_result:
                return gen_result

        # 2. Fallback to legacy image API (free, no key)
        logger.info("Trying legacy image API for model %s", chosen_model)
        legacy_result = await self._generate_image_legacy(prompt, width, height, chosen_model, seed, nologo, enhance)
        if legacy_result:
            return legacy_result

        # 3. If legacy also failed, try gen API even if it was failing
        if self._keys:
            gen_result = await self._generate_image_gen_api(prompt, width, height, chosen_model, seed)
            if gen_result:
                return gen_result

        return AIResponse(
            error="Image generation failed on all endpoints",
            provider=self.name,
            model=chosen_model,
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def _generate_image_gen_api(
        self,
        prompt: str,
        width: int,
        height: int,
        model: str,
        seed: int | None,
    ) -> AIResponse | None:
        """Generate image via gen.pollinations.ai with API key."""
        url = f"{GEN_BASE_URL}/v1/images/generations"
        start = time.monotonic()

        for attempt in range(len(self._keys)):
            key = self._get_next_key()
            if not key:
                continue

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            }
            payload = {
                "model": model,
                "prompt": prompt,
                "size": f"{width}x{height}",
                "nologo": True,
                "enhance": True,
            }

            try:
                session = self._get_session()
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "data" in data and data["data"]:
                            d = data["data"][0]
                            b64 = d.get("b64_json", "")
                            img_url = d.get("url", "")
                            if b64 or img_url:
                                elapsed = (time.monotonic() - start) * 1000
                                return AIResponse(
                                    image_b64=b64 or None,
                                    image_url=img_url or None,
                                    model=model,
                                    provider=self.name,
                                    latency_ms=elapsed,
                                )
                    elif resp.status in (402, 401):
                        body = await resp.text()
                        logger.debug("Gen image API auth/balance error %d: %s", resp.status, body[:100])
                        continue
                    else:
                        body = await resp.text()
                        logger.debug("Gen image API error %d: %s", resp.status, body[:100])
                        continue
            except Exception as exc:
                logger.debug("Gen image API failed: %s", exc)
                continue

        return None

    async def _generate_image_legacy(
        self,
        prompt: str,
        width: int,
        height: int,
        model: str,
        seed: int | None,
        nologo: bool,
        enhance: bool,
    ) -> AIResponse | None:
        """Generate image via legacy image.pollinations.ai (free, no key).

        Implements retry with backoff because the legacy API has a per-IP
        rate limit (1 concurrent request). Retries up to 3 times with delays.
        """
        encoded_prompt = quote(prompt, safe="")
        url = f"{LEGACY_IMAGE_URL}/prompt/{encoded_prompt}"

        params: dict[str, Any] = {
            "width": width,
            "height": height,
            "model": model,
            "nologo": str(nologo).lower(),
            "enhance": str(enhance).lower(),
            "seed": seed or random.randint(1, 999999),
        }

        # Wait before first attempt to let any existing queue clear
        await asyncio.sleep(5)
        
        max_retries = 5
        for attempt in range(max_retries):
            start = time.monotonic()
            try:
                session = self._get_session()
                # NO Authorization header — free anonymous endpoint
                async with session.get(url, params=params) as resp:
                    elapsed = (time.monotonic() - start) * 1000

                    if resp.status == 200:
                        img_bytes = await resp.read()
                        if len(img_bytes) > 1000:
                            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                            direct_url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
                            logger.info("Legacy image API success: model=%s, %d bytes, %dms",
                                        model, len(img_bytes), round(elapsed))
                            return AIResponse(
                                image_b64=img_b64,
                                image_url=direct_url,
                                model=model,
                                provider=f"{self.name}-legacy",
                                latency_ms=elapsed,
                            )
                        logger.warning("Legacy image too small (%d bytes)", len(img_bytes))
                        return None

                    elif resp.status in (402, 429):
                        # 402 = queue full for IP, 429 = rate limited
                        # Both are temporary — retry with long backoff (GitHub Actions shared IPs need more time)
                        if attempt < max_retries - 1:
                            wait = 10 * (attempt + 1)  # 10s, 20s, 30s, 40s, 50s
                            logger.warning(
                                "Legacy image API rate limited (status %d, attempt %d/%d), waiting %ds",
                                resp.status, attempt + 1, max_retries, wait,
                            )
                            await asyncio.sleep(wait)
                            # Change seed to avoid cache
                            params["seed"] = random.randint(1, 999999)
                            continue
                        else:
                            logger.warning("Legacy image API rate limited after %d retries", max_retries)
                            return None

                    else:
                        body = await resp.text()
                        logger.error("Legacy image API error %d: %s", resp.status, body[:200])
                        return None

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.error("Legacy image request failed: %s", exc)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                return None

        return None

    # ── Status and health ────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check if provider is available (either gen or legacy)."""
        return True  # Always available — at least legacy should work

    def get_status(self) -> dict[str, Any]:
        """Get provider status info."""
        return {
            "provider": self.name,
            "gen_api_keys": len(self._keys),
            "gen_fail_count": self._gen_fail_count,
            "gen_prefer_legacy": self._should_try_legacy_first(),
            "gen_requests": self._request_count,
            "legacy_requests": self._legacy_request_count,
            "legacy_circuit": self._legacy_circuit.state,
            "main_circuit": self._circuit.state,
        }
