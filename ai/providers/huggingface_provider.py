"""Hugging Face Spaces image generation provider for masha-bot.

Uses free Hugging Face Spaces API (gradio_client) for image generation.
Multiple model endpoints for failover:
1. black-forest-labs/FLUX.1-schnell — fast, free
2. stabilityai/stable-diffusion-xl-base-1.0 — SDXL
3. playgroundai/playground-v2.5-1024px-aesthetic — aesthetic
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import random
import time
from typing import Any, Optional

import aiohttp

from .base import AIResponse, BaseAIProvider

logger = logging.getLogger(__name__)

# ── Free Hugging Face Spaces for image generation ──────────────────────────────
# These are public Spaces that accept API calls for free
HF_IMAGE_ENDPOINTS = [
    {
        "name": "FLUX-schnell",
        "url": "https://black-forest-labs-flux-1-schnell.hf.space",
        "api_path": "/api/predict",
        "method": "post_json",
    },
    {
        "name": "stable-diffusion-xl",
        "url": "https://stabilityai-stable-diffusion-xl-base-1-0.hf.space",
        "api_path": "/api/predict",
        "method": "post_json",
    },
]

# Alternative: Use direct inference API (free tier)
HF_INFERENCE_API = "https://api-inference.huggingface.co/models"


class HuggingFaceProvider(BaseAIProvider):
    """Hugging Face Spaces provider for free image generation.

    Uses multiple free endpoints with failover:
    1. Hugging Face Inference API (free tier, no key needed for some models)
    2. Gradio Spaces API (free, no key needed)
    """

    name = "huggingface"

    def __init__(self, api_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(api_key=api_key, **kwargs)
        self._request_count = 0
        self._error_count = 0

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120),
            )
        return self._session

    async def chat(self, **kwargs) -> AIResponse:
        """Not supported — this provider is image-only."""
        return AIResponse(error="HuggingFace provider is image-only", provider=self.name)

    async def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        model: str | None = None,
        seed: int | None = None,
        **kwargs: Any,
    ) -> AIResponse:
        """Generate image using Hugging Face free endpoints.

        Priority: HF Inference API → Gradio Spaces
        """
        start = time.monotonic()

        # 1. Try HF Inference API (free models, no key for some)
        result = await self._try_inference_api(prompt, model)
        if result and result.ok:
            return result

        # 2. Try Gradio Spaces
        for endpoint in HF_IMAGE_ENDPOINTS:
            result = await self._try_gradio_space(prompt, endpoint)
            if result and result.ok:
                return result

        return AIResponse(
            error="HuggingFace image generation failed on all endpoints",
            provider=self.name,
            model=model or "unknown",
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def _try_inference_api(
        self, prompt: str, model: str | None = None
    ) -> AIResponse | None:
        """Try Hugging Face Inference API for image generation.

        Uses free models that don't require an API key.
        """
        # Free models on HF Inference API
        models = [
            "stabilityai/stable-diffusion-xl-base-1.0",
            "runwayml/stable-diffusion-v1-5",
            "stabilityai/stable-diffusion-2-1",
        ]

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for hf_model in models:
            url = f"{HF_INFERENCE_API}/{hf_model}"
            payload = {"inputs": prompt}

            try:
                session = self._get_session()
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        content_type = resp.headers.get("content-type", "")
                        if "image" in content_type:
                            img_bytes = await resp.read()
                            if len(img_bytes) > 5000:
                                img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                                self._request_count += 1
                                logger.info(
                                    "HF Inference API success: model=%s, %d bytes",
                                    hf_model, len(img_bytes),
                                )
                                return AIResponse(
                                    image_b64=img_b64,
                                    model=hf_model,
                                    provider=self.name,
                                )
                    elif resp.status == 503:
                        # Model is loading — wait and retry once
                        body = await resp.text()
                        logger.debug("HF model %s loading, waiting: %s", hf_model, body[:100])
                        await asyncio.sleep(15)
                        async with session.post(url, json=payload, headers=headers) as resp2:
                            if resp2.status == 200:
                                ct2 = resp2.headers.get("content-type", "")
                                if "image" in ct2:
                                    img_bytes = await resp2.read()
                                    if len(img_bytes) > 5000:
                                        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                                        self._request_count += 1
                                        return AIResponse(
                                            image_b64=img_b64,
                                            model=hf_model,
                                            provider=self.name,
                                        )
                    elif resp.status == 429:
                        logger.debug("HF Inference API rate limited for %s", hf_model)
                        continue
                    else:
                        body = await resp.text()
                        logger.debug("HF Inference API error %d for %s: %s", resp.status, hf_model, body[:100])
                        continue
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.debug("HF Inference API request failed for %s: %s", hf_model, exc)
                continue

        return None

    async def _try_gradio_space(
        self, prompt: str, endpoint: dict
    ) -> AIResponse | None:
        """Try a Gradio Space API endpoint for image generation."""
        url = f"{endpoint['url']}{endpoint['api_path']}"
        payload = {"data": [prompt]}

        try:
            session = self._get_session()
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "data" in data and data["data"]:
                        result = data["data"][0]
                        # Result may be a URL or base64
                        if isinstance(result, str):
                            if result.startswith("http"):
                                # Download the image
                                try:
                                    async with session.get(result) as img_resp:
                                        if img_resp.status == 200:
                                            img_bytes = await img_resp.read()
                                            if len(img_bytes) > 5000:
                                                img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                                                self._request_count += 1
                                                return AIResponse(
                                                    image_b64=img_b64,
                                                    model=endpoint["name"],
                                                    provider=self.name,
                                                )
                                except Exception:
                                    pass
                            elif result.startswith("data:image"):
                                # Data URI
                                parts = result.split(",", 1)
                                if len(parts) == 2:
                                    img_b64 = parts[1]
                                    if len(img_b64) > 1000:
                                        self._request_count += 1
                                        return AIResponse(
                                            image_b64=img_b64,
                                            model=endpoint["name"],
                                            provider=self.name,
                                        )
                    else:
                        logger.debug("Gradio Space %s returned 200 but no valid image data", endpoint["name"])
                elif resp.status == 429:
                    logger.debug("Gradio Space %s rate limited", endpoint["name"])
                else:
                    logger.debug("Gradio Space %s error: %d", endpoint["name"], resp.status)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.debug("Gradio Space %s failed: %s", endpoint["name"], exc)

        return None

    def is_available(self) -> bool:
        """Always available — free tier."""
        return True

    def get_status(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "requests": self._request_count,
            "errors": self._error_count,
            "available": True,
        }
