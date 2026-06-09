"""Content orchestration pipeline for masha-bot.

Coordinates the flow: source → generate → validate → publish.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any, Optional

from ..core.config import get_config, get_persona
from ..generation.persona import PersonaManager
from ..generation.writer import ContentWriter
from ..generation.fact_checker import BMWFactChecker
from ..generation.image_gen import ImageGenerator
from ..publishing.telegram import ChannelManager
from ..publishing.formatter import PostFormatter
from ..sources.rss_fetcher import BMWRSSFetcher
from ..sources.evergreen import EvergreenSource
from ..sources.community import CommunitySource
from ..knowledge.topics import TopicManager
from ..knowledge.characters import CharacterManager
from ..partners import PartnerManager
from ..analytics.tracker import AnalyticsTracker
from ..database import Database

logger = logging.getLogger(__name__)


class ContentPipeline:
    """Orchestrates content generation and publishing for masha-bot."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.config = get_config()
        self.persona = get_persona()

        # Initialize components
        self.rss_fetcher = BMWRSSFetcher(db=db)
        self.evergreen = EvergreenSource(db=db)
        self.community = CommunitySource(db=db)
        self.topic_manager = TopicManager()
        self.character_manager = CharacterManager()
        self.persona_manager = PersonaManager()
        self.writer = ContentWriter()
        self.fact_checker = BMWFactChecker()
        self.image_gen = ImageGenerator()
        self.formatter = PostFormatter()
        self.channel = ChannelManager(db=db)
        self.partners = PartnerManager(db=db)
        self.tracker = AnalyticsTracker(db=db)

    async def run_cycle(self) -> dict[str, Any]:
        """Run one content cycle: source → generate → publish."""
        result: dict[str, Any] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "started",
            "post_published": False,
            "content_type": None,
            "source": None,
            "errors": [],
        }

        try:
            # 1. Check if we should post today
            posts_today = await self.db.get_posts_today_count()
            if posts_today >= self.config.max_posts_per_day:
                result["status"] = "skipped_daily_limit"
                logger.info("Daily post limit reached: %d", posts_today)
                return result

            # 2. Source content
            content_item = await self._source_content()
            if not content_item:
                result["status"] = "no_content_available"
                logger.info("No content available for this cycle")
                return result

            result["source"] = content_item.get("source", "unknown")
            result["content_type"] = content_item.get("content_type", "unknown")

            # 3. Select characters
            character_mix = self.character_manager.select_characters()
            content_item["character_mix"] = character_mix

            # 4. Generate post
            post_data = await self._generate_post(content_item)
            if not post_data:
                result["status"] = "generation_failed"
                result["errors"].append("Post generation returned empty")
                return result

            # 5. Fact-check if enabled
            if self.config.enable_fact_check and content_item.get("content_type") in (
                "news+reaction", "lore/history"
            ):
                validated = await self._validate_post(post_data)
                if not validated:
                    result["status"] = "fact_check_failed"
                    result["errors"].append("Post failed fact-check validation")
                    return result

            # 6. Format and publish
            published = await self._publish_post(post_data, content_item)
            if published:
                result["post_published"] = True
                result["status"] = "published"
                result["post_id"] = published.get("message_id")
                await self.tracker.track_post(
                    content_type=content_item.get("content_type", "unknown"),
                    source=content_item.get("source", "unknown"),
                    character_mix=character_mix,
                    message_id=published.get("message_id"),
                )
            else:
                result["status"] = "publish_failed"

        except Exception as exc:
            logger.exception("Pipeline error: %s", exc)
            result["status"] = "error"
            result["errors"].append(str(exc))

        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        return result

    async def _source_content(self) -> dict[str, Any] | None:
        """Try to source content in priority order."""
        # Priority 1: Urgent BMW news
        news = await self.rss_fetcher.fetch_urgent()
        if news:
            news["source"] = "rss_urgent"
            return news

        # Priority 2: Theme day content
        theme = self.topic_manager.get_current_theme()
        if theme:
            theme_content = await self.rss_fetcher.fetch_for_theme(theme)
            if theme_content:
                theme_content["source"] = "rss_theme"
                theme_content["content_type"] = theme.get("default_type", "news+reaction")
                return theme_content
            # Use theme topic directly
            return {
                "topic": theme.get("topic", theme.get("name", "BMW")),
                "context": theme.get("description", ""),
                "content_type": theme.get("default_type", "news+reaction"),
                "source": "theme_day",
            }

        # Priority 3: Regular BMW news
        regular_news = await self.rss_fetcher.fetch_latest()
        if regular_news:
            regular_news["source"] = "rss_regular"
            return regular_news

        # Priority 4: Community questions/polls
        community_item = await self.community.get_pending()
        if community_item:
            community_item["source"] = "community"
            return community_item

        # Priority 5: Evergreen buffer
        evergreen_item = await self.evergreen.get_next()
        if evergreen_item:
            evergreen_item["source"] = "evergreen"
            return evergreen_item

        return None

    async def _generate_post(self, content_item: dict[str, Any]) -> dict[str, Any] | None:
        """Generate a post from content item data."""
        topic = content_item.get("topic", "")
        context = content_item.get("context", "")
        content_type = content_item.get("content_type", "news+reaction")
        character_mix = content_item.get("character_mix", "Маша")

        # Add persona mood
        mood = self.persona_manager.get_current_mood()

        post_data = await self.writer.generate(
            topic=topic,
            context=context,
            content_type=content_type,
            character_mix=character_mix,
            mood=mood,
        )

        if not post_data:
            return None

        # Generate image if needed
        if self.config.enable_images and content_type in (
            "news+reaction", "lore/history", "garage stories", "partner"
        ):
            image = await self.image_gen.generate(
                topic=topic,
                content_type=content_type,
            )
            if image:
                post_data["image"] = image

        post_data["content_type"] = content_type
        post_data["character_mix"] = character_mix
        return post_data

    async def _validate_post(self, post_data: dict[str, Any]) -> bool:
        """Validate post with fact checker."""
        text = post_data.get("text", "")
        if not text:
            return False

        # Extract claims for fact checking
        checks = await self.fact_checker.check_post(text)
        if not checks:
            return True

        # Block posts with incorrect facts
        for check in checks:
            if check.get("verdict") == "incorrect":
                logger.warning(
                    "Post blocked by fact-check: %s — %s",
                    check.get("claim", ""),
                    check.get("explanation", ""),
                )
                return False

        return True

    async def _publish_post(
        self, post_data: dict[str, Any], content_item: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Format and publish the post."""
        text = post_data.get("text", "")
        image = post_data.get("image")

        # Format the post
        formatted = self.formatter.format_post(
            text=text,
            content_type=post_data.get("content_type", "news+reaction"),
            has_image=bool(image),
        )

        # Check dedup
        is_dup = await self.channel.is_duplicate(formatted)
        if is_dup:
            logger.info("Post rejected as duplicate")
            return None

        # Publish
        if image:
            result = await self.channel.send_photo(
                text=formatted,
                image=image,
            )
        else:
            result = await self.channel.send_message(text=formatted)

        return result
