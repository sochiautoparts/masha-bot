"""Weekly analytics reporter for masha-bot."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..database import Database
from ..analytics.tracker import AnalyticsTracker

logger = logging.getLogger(__name__)


class AnalyticsReporter:
    """Generates weekly analytics reports for masha-bot."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.tracker = AnalyticsTracker(db=db)

    async def generate_weekly_report(self) -> str:
        """Generate a weekly report text for the owner."""
        stats = await self.db.get_posts_stats(days=7)
        daily = stats.get("daily_stats", [])

        lines = [
            "📊 Недельный отчёт masha-bot (@bmw_mpower_club)",
            f"Период: {stats.get('period_days', 7)} дней",
            "=" * 40,
        ]

        total_posts = 0
        total_with_image = 0

        for day in daily:
            date = day.get("date", "?")
            count = day.get("post_count", 0)
            with_img = day.get("with_image", 0)
            total_posts += count
            total_with_image += with_img
            lines.append(f"{date}: {count} постов ({with_img} с фото)")

        lines.append("=" * 40)
        lines.append(f"Всего постов за неделю: {total_posts}")
        lines.append(f"С фото: {total_with_image}")
        lines.append(f"Среднее в день: {total_posts / 7:.1f}")

        # Add suggestions
        if total_posts < 40:
            lines.append("\n⚠️ Меньше 6 постов в день — рассмотрите увеличение")
        elif total_posts > 140:
            lines.append("\n⚠️ Больше 20 постов в день — слишком активно?")

        return "\n".join(lines)
