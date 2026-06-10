"""Analytics tracker for masha-bot — metrics collection."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..database import Database

logger = logging.getLogger(__name__)


class AnalyticsTracker:
    """Tracks post and content metrics for masha-bot."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def track_post(
        self,
        content_type: str,
        source: str,
        character_mix: str,
        message_id: int | None = None,
    ) -> None:
        """Track a published post."""
        logger.info(
            "Post tracked: type=%s source=%s chars=%s msg=%s",
            content_type, source, character_mix, message_id,
        )

    async def track_error(
        self,
        stage: str,
        error: str,
        context: str = "",
    ) -> None:
        """Track an error during content pipeline."""
        logger.error("Pipeline error at %s: %s (%s)", stage, error, context)

    async def get_daily_summary(self) -> dict[str, Any]:
        """Get today's analytics summary."""
        posts_count = await self.db.get_posts_today_count()
        return {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "posts_published": posts_count,
        }

    async def get_weekly_summary(self) -> dict[str, Any]:
        """Get weekly analytics summary."""
        stats = await self.db.get_posts_stats(days=7)
        return stats
