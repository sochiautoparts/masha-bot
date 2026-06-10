"""Evergreen content source for masha-bot.

Buffer of pre-made BMW content for when no fresh news is available.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..database import Database

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
EVERGREEN_PATH = DATA_DIR / "evergreen_pool.json"


class EvergreenSource:
    """Manages the evergreen content buffer."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._pool: list[dict[str, Any]] = []
        self._load_pool()

    def _load_pool(self) -> None:
        """Load evergreen content from JSON file."""
        if EVERGREEN_PATH.exists():
            try:
                with open(EVERGREEN_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._pool = data.get("evergreen_pool", [])
                logger.info("Loaded %d evergreen items", len(self._pool))
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed to load evergreen pool: %s", exc)
                self._pool = []
        else:
            logger.warning("Evergreen pool file not found at %s", EVERGREEN_PATH)
            self._pool = []

    async def get_next(self) -> dict[str, Any] | None:
        """Get the next unused evergreen content item."""
        available = []
        for item in self._pool:
            item_id = item.get("id", "")
            if not await self.db.is_evergreen_used(item_id):
                available.append(item)

        if not available:
            # Reset evergreen items used more than 30 days ago
            logger.info("All evergreen items used, consider adding more")
            # Try items not used in the last 30 days
            available = self._pool

        if available:
            item = random.choice(available)
            return {
                "topic": item.get("topic", ""),
                "content_type": item.get("content_type", "lore/history"),
                "context": item.get("context", ""),
                "character_mix": item.get("character_hint", "Маша"),
                "evergreen_id": item.get("id", ""),
                "source": "evergreen",
            }

        return None

    async def mark_used(self, evergreen_id: str, post_id: int | None = None) -> None:
        """Mark an evergreen item as used."""
        await self.db.mark_evergreen_used(evergreen_id, post_id)

    def get_available_count(self) -> int:
        """Get the number of available evergreen items."""
        return len(self._pool)

    def add_item(self, item: dict[str, Any]) -> None:
        """Add a new item to the evergreen pool."""
        self._pool.append(item)
        self._save_pool()

    def _save_pool(self) -> None:
        """Save the current pool to JSON file."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(EVERGREEN_PATH, "w", encoding="utf-8") as f:
                json.dump({"evergreen_pool": self._pool}, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.error("Failed to save evergreen pool: %s", exc)
