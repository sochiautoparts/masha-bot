"""Cloudflare Workers AI provider for masha-bot.

Uses @cf/mistralai/mistral-small-3.1-24b-instruct via OpenAI-compatible API.
Supports vision (image_url in messages), dual-account failover.
Free tier: 10,000 requests/day per account.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from typing import Any, Optional

import aiohttp

from .base import AIResponse, BaseAIProvider

logger = logging.getLogger(__name__)

# ── Cloudflare Workers AI model ────────────────────────────────────────────────

CF_TEXT_MODEL = "@cf/mistralai/mistral-small-3.1-24b-instruct"

# Fallback models if primary is unavailable
CF_TEXT_MODELS = [
    "@cf/mistralai/mistral-small-3.1-24b-instruct",
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
]


class CFAccount:
    """Single Cloudflare account with credentials."""

    def __init__(self, account_id: str, api_token: str) -> None:
        self.account_id = account_id
        self.api_token = api_token
        self._request_count = 0
        self._error_count = 0
        self._last_error_time: float = 0.0
        self._available: bool = True

    @property
    def base_url(self) -> str:
        return f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/v1"

    @property
    def is_available(self) -> bool:
        """Check if account is available (not rate-limited or errored out)."""
        if not self._available:
            # Re-enable after 5 minutes
            if time.monotonic() - self._last_error_time > 300:
                self._available = True
                self._error_count = 0
                logger.info("CF account %s re-enabled after cooldown", self.account_id[:8])
        return self._available

    def record_success(self) -> None:
        self._request_count += 1
        self._error_count = 0

    def record_failure(self) -> None:
        self._error_count += 1
        self._last_error_time = time.monotonic()
        if self._error_count >= 5:
            self._available = False
            logger.warning(
                "CF account %s disabled after %d errors",
                self.account_id[:8], self._error_count,
            )


class CloudflareProvider(BaseAIProvider):
    """Cloudflare Workers AI provider with dual-account failover.

    Uses OpenAI-compatible format:
    - POST /chat/completions for text
    - Supports vision via image_url in messages
    - 10,000 free requests/day per account
    """

    name = "cloudflare"

    def __init__(
        self,
        account_id_1: str = "",
        api_token_1: str = "",
        account_id_2: str = "",
        api_token_2: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(api_key=api_token_1, **kwargs)
        self._accounts: list[CFAccount] = []
        if account_id_1 and api_token_1:
            self._accounts.append(CFAccount(account_id_1, api_token_1))
        if account_id_2 and api_token_2:
            self._accounts.append(CFAccount(account_id_2, api_token_2))
        self._account_index = 0

    def _get_next_account(self) -> CFAccount | None:
        """Get next available account (round-robin with failover)."""
        available = [a for a in self._accounts if a.is_available]
        if not available:
            # All accounts are down — try first one anyway
            if self._accounts:
                logger.warning("All CF accounts marked unavailable, retrying first")
                self._accounts[0]._available = True
                return self._accounts[0]
            return None

        # Round-robin among available
        self._account_index %= len(available)
        account = available[self._account_index]
        self._account_index += 1
        return account

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> AIResponse:
        """Send a chat completion request to Cloudflare Workers AI.

        Supports vision via image_url in messages (OpenAI-compatible format).
        """
        chosen_model = model or CF_TEXT_MODEL
        # Map short names to full CF model IDs
        if not chosen_model.startswith("@cf/"):
            model_map = {
                "mistral": CF_TEXT_MODEL,
                "mistral-small": CF_TEXT_MODEL,
                "llama": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                "deepseek": "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
            }
            chosen_model = model_map.get(chosen_model, CF_TEXT_MODEL)

        # Try each account
        for _ in range(len(self._accounts)):
            account = self._get_next_account()
            if not account:
                break

            start = time.monotonic()

            # Build OpenAI-compatible payload
            payload: dict[str, Any] = {
                "model": chosen_model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {account.api_token}",
            }

            url = f"{account.base_url}/chat/completions"

            try:
                session = self._get_session()
                async with session.post(url, json=payload, headers=headers) as resp:
                    elapsed = (time.monotonic() - start) * 1000

                    if resp.status == 200:
                        data = await resp.json()
                        # OpenAI-compatible response format
                        content = ""
                        if "choices" in data and data["choices"]:
                            msg = data["choices"][0].get("message", {})
                            content = msg.get("content", "")
                        elif "result" in data:
                            # CF native format fallback
                            result = data["result"]
                            if isinstance(result, dict):
                                content = result.get("response", "")
                            elif isinstance(result, str):
                                content = result

                        if content:
                            account.record_success()
                            return AIResponse(
                                text=content,
                                model=chosen_model,
                                provider=self.name,
                                latency_ms=elapsed,
                            )
                        else:
                            logger.warning("CF returned empty content from %s", chosen_model)
                            account.record_failure()
                            continue

                    elif resp.status == 429:
                        logger.warning("CF rate limited on account %s", account.account_id[:8])
                        account.record_failure()
                        continue

                    elif resp.status == 401:
                        body = await resp.text()
                        logger.error("CF auth error on account %s: %s", account.account_id[:8], body[:200])
                        account.record_failure()
                        continue

                    else:
                        body = await resp.text()
                        logger.error("CF error %d: %s", resp.status, body[:200])
                        account.record_failure()
                        continue

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.error("CF request failed: %s", exc)
                if account:
                    account.record_failure()
                continue

        return AIResponse(
            error="All Cloudflare accounts failed",
            provider=self.name,
            model=chosen_model,
        )

    async def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        model: str | None = None,
        **kwargs: Any,
    ) -> AIResponse:
        """Cloudflare doesn't provide image generation for masha-bot.

        Returns error — image generation should use Pollinations.
        """
        return AIResponse(
            error="Cloudflare provider does not support image generation",
            provider=self.name,
            model=model or "none",
        )

    def is_available(self) -> bool:
        """Check if any CF account is available."""
        return bool(self._accounts) and any(a.is_available for a in self._accounts)

    def get_status(self) -> dict[str, Any]:
        """Get provider status info."""
        accounts = []
        for a in self._accounts:
            accounts.append({
                "account_id": a.account_id[:8] + "...",
                "available": a.is_available,
                "requests": a._request_count,
                "errors": a._error_count,
            })
        return {
            "provider": self.name,
            "accounts": accounts,
            "model": CF_TEXT_MODEL,
            "available": self.is_available(),
        }
