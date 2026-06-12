"""Telegram channel manager for masha-bot.

Handles posting to @bmw_mpower_club channel with:
- Message and photo sending
- Deduplication (semantic and exact)
- BMW-specific validation
- Image pipeline (download → AI generate)
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from ..database import Database
from ..core.config import get_config, get_persona

logger = logging.getLogger(__name__)


class ChannelManager:
    """Manages posting to the @bmw_mpower_club Telegram channel."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.config = get_config()
        self._bot_session: Optional[aiohttp.ClientSession] = None

    def _get_bot_session(self) -> aiohttp.ClientSession:
        """Get or create the bot API session."""
        if self._bot_session is None or self._bot_session.closed:
            self._bot_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
            )
        return self._bot_session

    async def _call_bot_api(
        self,
        method: str,
        payload: dict[str, Any],
        files: dict[str, bytes] | None = None,
    ) -> dict[str, Any] | None:
        """Call the Telegram Bot API."""
        url = f"https://api.telegram.org/bot{self.config.bot_token}/{method}"
        session = self._get_bot_session()

        try:
            if files:
                # Multipart form data for file uploads
                data = aiohttp.FormData()
                for key, value in payload.items():
                    if isinstance(value, (dict, list)):
                        data.add_field(key, __import__("json").dumps(value))
                    else:
                        data.add_field(key, str(value))
                for key, file_bytes in files.items():
                    data.add_field(
                        key,
                        file_bytes,
                        filename="photo.jpg",
                        content_type="image/jpeg",
                    )
                async with session.post(url, data=data) as resp:
                    result = await resp.json()
            else:
                async with session.post(url, json=payload) as resp:
                    result = await resp.json()

            if result.get("ok"):
                return result.get("result")
            else:
                logger.error(
                    "Telegram API error: %s", result.get("description", "unknown")
                )
                return None

        except Exception as exc:
            logger.error("Telegram API call failed: %s", exc)
            return None

    async def send_message(self, text: str) -> dict[str, Any] | None:
        """Send a text message to the channel."""
        if not self._validate_post_text(text):
            logger.warning("Post text validation failed")
            return None

        payload = {
            "chat_id": self.config.channel_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        result = await self._call_bot_api("sendMessage", payload)

        if result:
            message_id = result.get("message_id", 0)
            # Record in database
            post_id = await self.db.add_channel_post(
                message_id=message_id,
                text=text,
                has_image=False,
            )
            # Add fingerprint for dedup
            fingerprint = hashlib.sha256(text.encode()).hexdigest()[:16]
            await self.db.add_fingerprint(fingerprint, post_id=post_id, text_hash=fingerprint)

            logger.info("Posted message %d to channel", message_id)
            return {"message_id": message_id, "post_id": post_id}

        return None

    async def send_photo(
        self,
        text: str,
        image: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Send a photo with caption to the channel."""
        if not self._validate_post_text(text):
            logger.warning("Post text validation failed")
            return None

        # Get image bytes
        image_bytes = await self._get_image_bytes(image)
        if not image_bytes:
            # Fall back to text-only post
            logger.warning("Failed to get image, falling back to text post")
            return await self.send_message(text)

        payload = {
            "chat_id": self.config.channel_id,
            "caption": text,  # Already enforced by formatter/channel.py
            "parse_mode": "HTML",
        }

        result = await self._call_bot_api("sendPhoto", payload, files={"photo": image_bytes})

        if result:
            message_id = result.get("message_id", 0)
            image_url = image.get("image_url", "")
            post_id = await self.db.add_channel_post(
                message_id=message_id,
                text=text,
                has_image=True,
                image_url=image_url,
            )
            fingerprint = hashlib.sha256(text.encode()).hexdigest()[:16]
            await self.db.add_fingerprint(fingerprint, post_id=post_id, text_hash=fingerprint)

            logger.info("Posted photo %d to channel", message_id)
            return {"message_id": message_id, "post_id": post_id}

        return None

    async def send_poll(
        self,
        question: str,
        options: list[str],
        context: str = "",
    ) -> dict[str, Any] | None:
        """Send a poll to the channel."""
        payload = {
            "chat_id": self.config.channel_id,
            "question": question[:300],  # Telegram limit
            "options": [opt[:100] for opt in options[:10]],  # Max 10 options, 100 chars each
            "is_anonymous": True,
            "type": "regular",
        }

        result = await self._call_bot_api("sendPoll", payload)

        if result:
            message_id = result.get("message_id", 0)
            poll_text = f"Poll: {question}"
            post_id = await self.db.add_channel_post(
                message_id=message_id,
                text=poll_text,
                content_type="polls/debates",
            )

            logger.info("Posted poll %d to channel", message_id)
            return {"message_id": message_id, "post_id": post_id}

        return None

    async def is_duplicate(self, text: str) -> bool:
        """Check if a post would be a duplicate."""
        # Check exact hash
        if await self.db.is_duplicate_post(text):
            return True

        # Semantic dedup: check for very similar recent posts
        recent = await self.db.get_recent_posts(limit=10)
        text_words = set(text.lower().split())
        for post in recent:
            post_words = set(post.get("text", "").lower().split())
            if not text_words or not post_words:
                continue
            intersection = text_words & post_words
            union = text_words | post_words
            jaccard = len(intersection) / len(union) if union else 0
            if jaccard > self.config.dedup_similarity_threshold:
                logger.info("Semantic duplicate detected (jaccard=%.2f)", jaccard)
                return True

        return False

    def _validate_post_text(self, text: str) -> bool:
        """Validate post text for BMW-specific rules."""
        if not text:
            return False

        if len(text) < 50:
            logger.warning("Post too short: %d chars", len(text))
            return False

        # Check for required footer
        if "@asmasha_bot" not in text:
            logger.warning("Post missing bot attribution")
            return False

        if "@bmw_mpower_club" not in text:
            logger.warning("Post missing channel tag")
            return False

        return True

    async def _get_image_bytes(self, image: dict[str, Any]) -> bytes | None:
        """Get image bytes from various sources."""
        # Try base64 first
        if image.get("image_b64"):
            try:
                return base64.b64decode(image["image_b64"])
            except Exception as exc:
                logger.warning("Failed to decode base64 image: %s", exc)

        # Try URL download
        if image.get("image_url"):
            try:
                session = self._get_bot_session()
                async with session.get(image["image_url"]) as resp:
                    if resp.status == 200:
                        img_bytes = await resp.read()
                        if len(img_bytes) > 1000:
                            return img_bytes
            except Exception as exc:
                logger.warning("Failed to download image: %s", exc)

        return None

    async def get_channel_info(self) -> dict[str, Any] | None:
        """Get channel information."""
        payload = {"chat_id": self.config.channel_id}
        return await self._call_bot_api("getChat", payload)

    async def get_post_views(self, message_id: int) -> int:
        """Get view count for a channel post."""
        # Note: This requires the bot to be an admin in the channel
        try:
            result = await self._call_bot_api(
                "getMessage",
                {"chat_id": self.config.channel_id, "message_id": message_id},
            )
            # Views are not directly available via Bot API for channels
            # We'd need to use the views from forwarded messages
            return 0
        except Exception:
            return 0

    async def close(self) -> None:
        """Clean up resources."""
        if self._bot_session and not self._bot_session.closed:
            await self._bot_session.close()
            self._bot_session = None
