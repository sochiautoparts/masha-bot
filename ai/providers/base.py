"""Base AI provider interface for masha-bot."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AIResponse:
    """Standardized response from any AI provider."""

    text: str = ""
    model: str = ""
    provider: str = ""
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: Optional[str] = None
    raw_response: Optional[dict[str, Any]] = None
    latency_ms: float = 0.0
    cached: bool = False
    image_url: Optional[str] = None
    image_b64: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text or self.image_url or self.image_b64)

    def __bool__(self) -> bool:
        return self.ok


class BaseAIProvider(ABC):
    """Abstract base class for AI providers."""

    name: str = "base"

    def __init__(self, api_key: str | None = None, **kwargs: Any) -> None:
        self.api_key = api_key
        self._session: Any = None

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> AIResponse:
        """Send a chat completion request."""
        ...

    @abstractmethod
    async def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        model: str | None = None,
        **kwargs: Any,
    ) -> AIResponse:
        """Generate an image from a text prompt."""
        ...

    async def close(self) -> None:
        """Clean up resources."""
        if self._session and hasattr(self._session, "close"):
            await self._session.close()
            self._session = None
