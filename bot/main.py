"""masha-bot entry point — BMW-focused Telegram bot for @bmw_mpower_club.

Based on asya-bot architecture but completely reworked for BMW content.
Uses Pollinations AI, SQLite, and GitHub Actions for scheduling.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── Logging setup ─────────────────────────────────────────────────────────────

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("masha-bot")

# ── Imports ───────────────────────────────────────────────────────────────────

from bot.core.config import get_config, BotConfig
from bot.core.pipeline import ContentPipeline
from bot.core.scheduler import Scheduler
from bot.database import Database
from bot.publishing.telegram import ChannelManager
from bot.publishing.formatter import PostFormatter
from bot.generation.writer import ContentWriter
from bot.generation.persona import PersonaManager
from bot.knowledge.characters import CharacterManager
from bot.partners import PartnerManager
from bot.analytics.tracker import AnalyticsTracker
from bot.analytics.reporter import AnalyticsReporter


# ── Singleton Lock ────────────────────────────────────────────────────────────

class SingletonLock:
    """Ensures only one instance of the bot runs at a time."""

    LOCK_FILE = "/tmp/masha-bot.lock"

    def __init__(self) -> None:
        self._lock_file: Any = None

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns True if successful."""
        try:
            self._lock_file = open(self.LOCK_FILE, "w")
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_file.write(str(os.getpid()))
            self._lock_file.flush()
            return True
        except (IOError, OSError):
            if self._lock_file:
                self._lock_file.close()
            return False

    def release(self) -> None:
        """Release the lock."""
        if self._lock_file:
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()
            except (IOError, OSError):
                pass
            try:
                os.unlink(self.LOCK_FILE)
            except OSError:
                pass


# ── Background Tasks ──────────────────────────────────────────────────────────

class BackgroundTasks:
    """Manages background async tasks."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []

    def add(self, coro: Any, name: str = "") -> asyncio.Task:
        """Add a background task."""
        task = asyncio.create_task(coro, name=name)
        self._tasks.append(task)
        return task

    async def wait_all(self) -> None:
        """Wait for all tasks to complete."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def cancel_all(self) -> None:
        """Cancel all tasks."""
        for task in self._tasks:
            if not task.done():
                task.cancel()


# ── Main Bot Class ────────────────────────────────────────────────────────────

class MashaBot:
    """Main masha-bot application."""

    def __init__(self) -> None:
        self.config = get_config()
        self.db = Database(db_path=self.config.db_path)
        self.scheduler = Scheduler()
        self.pipeline: Optional[ContentPipeline] = None
        self.channel: Optional[ChannelManager] = None
        self.formatter = PostFormatter()
        self.writer: Optional[ContentWriter] = None
        self.persona_manager = PersonaManager()
        self.character_manager = CharacterManager()
        self.partners: Optional[PartnerManager] = None
        self.tracker: Optional[AnalyticsTracker] = None
        self.reporter: Optional[AnalyticsReporter] = None
        self.background = BackgroundTasks()
        self._lock = SingletonLock()

    async def init(self) -> None:
        """Initialize all components."""
        # Validate config
        issues = self.config.validate()
        if issues:
            for issue in issues:
                logger.warning("Config issue: %s", issue)

        # Initialize database
        await self.db.init()

        # Initialize components that need DB
        self.pipeline = ContentPipeline(db=self.db)
        self.channel = ChannelManager(db=self.db)
        self.writer = ContentWriter()
        self.partners = PartnerManager(db=self.db)
        self.tracker = AnalyticsTracker(db=self.db)
        self.reporter = AnalyticsReporter(db=self.db)

        # Load partner programs
        await self.partners.load_programs()

        logger.info("masha-bot initialized successfully")

    async def run_cycle(self) -> dict[str, Any]:
        """Run one content cycle."""
        if not self.pipeline:
            return {"status": "error", "message": "Pipeline not initialized"}

        result = await self.pipeline.run_cycle()
        logger.info("Cycle result: %s", result.get("status", "unknown"))

        # Record persona state
        if result.get("post_published"):
            character = result.get("character_mix", "Маша")
            self.persona_manager.record_post(character)

        return result

    async def run_single_post(self) -> dict[str, Any]:
        """Run a single post generation and publishing cycle."""
        logger.info("Starting single post cycle...")

        try:
            await self.init()

            # Check if we should post
            posts_today = await self.db.get_posts_today_count()
            if not self.scheduler.should_post_now(posts_today, self.config.max_posts_per_day):
                logger.info("Not time to post or daily limit reached")
                return {"status": "skipped", "reason": "not_time_or_limit"}

            result = await self.run_cycle()
            return result

        except Exception as exc:
            logger.exception("Single post cycle error: %s", exc)
            return {"status": "error", "message": str(exc)}

    async def run_interactive(self) -> None:
        """Run the bot in interactive mode (long polling)."""
        logger.info("Starting masha-bot in interactive mode...")

        try:
            await self.init()

            # Import telegram bot libraries
            from telegram import Update
            from telegram.ext import (
                Application,
                CommandHandler,
                MessageHandler,
                filters,
            )

            app = Application.builder().token(self.config.bot_token).build()

            # Register handlers
            app.add_handler(CommandHandler("start", self._cmd_start))
            app.add_handler(CommandHandler("help", self._cmd_help))
            app.add_handler(CommandHandler("post", self._cmd_post))
            app.add_handler(CommandHandler("stats", self._cmd_stats))
            app.add_handler(CommandHandler("mood", self._cmd_mood))
            app.add_handler(CommandHandler("theme", self._cmd_theme))
            app.add_handler(CommandHandler("ask", self._cmd_ask))
            app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
            )

            logger.info("Bot started. Press Ctrl+C to stop.")
            await app.run_polling(allowed_updates=Update.ALL_TYPES)

        except ImportError:
            logger.error(
                "python-telegram-bot not installed. "
                "Install with: pip install python-telegram-bot"
            )
        except Exception as exc:
            logger.exception("Interactive mode error: %s", exc)

    # ── Bot command handlers ──────────────────────────────────────────────

    async def _cmd_start(self, update: Any, context: Any) -> None:
        """Handle /start command."""
        if not update.effective_user:
            return
        user = update.effective_user
        await self.db.add_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )
        await update.message.reply_text(
            f"Привет! Я Маша, главред @bmw_mpower_club 🏎️\n\n"
            f"BMW M5 F90 Competition — моя машина, а этот канал — мой дом.\n"
            f"Пишу про BMW, двигатели, M-division и всё баварское.\n\n"
            f"Команды:\n"
            f"/post — сгенерировать пост\n"
            f"/stats — статистика канала\n"
            f"/mood — моё настроение\n"
            f"/theme — тема дня\n"
            f"/ask <вопрос> — задать вопрос про BMW\n"
            f"/help — помощь"
        )

    async def _cmd_help(self, update: Any, context: Any) -> None:
        """Handle /help command."""
        await update.message.reply_text(
            "🔧 Команды masha-bot:\n\n"
            "/start — приветствие\n"
            "/post — сгенерировать пост\n"
            "/stats — статистика\n"
            "/mood — настроение Маши\n"
            "/theme — тема дня\n"
            "/ask <вопрос> — вопрос про BMW\n"
            "/help — эта справка\n\n"
            "Канал: @bmw_mpower_club"
        )

    async def _cmd_post(self, update: Any, context: Any) -> None:
        """Handle /post command — generate and publish a post."""
        user_id = update.effective_user.id if update.effective_user else 0

        # Only owner can trigger posts
        if user_id != self.config.owner_id:
            await update.message.reply_text("Извини, постить может только владелец бота 😏")
            return

        await update.message.reply_text("Генерирую пост... 🔧")

        result = await self.run_cycle()
        status = result.get("status", "unknown")

        if result.get("post_published"):
            await update.message.reply_text(
                f"✅ Пост опубликован! (message_id: {result.get('post_id', '?')})"
            )
        else:
            await update.message.reply_text(
                f"❌ Пост не опубликован: {status}"
            )

    async def _cmd_stats(self, update: Any, context: Any) -> None:
        """Handle /stats command."""
        summary = await self.tracker.get_daily_summary() if self.tracker else {}
        posts_today = summary.get("posts_published", 0)
        persona = self.persona_manager.get_persona_info()

        await update.message.reply_text(
            f"📊 Статистика @bmw_mpower_club:\n\n"
            f"Постов сегодня: {posts_today}\n"
            f"Настроение Маши: {persona.get('mood', '?')} ({persona.get('mood_description', '')})\n"
            f"Последний персонаж: {persona.get('last_character', '?')}"
        )

    async def _cmd_mood(self, update: Any, context: Any) -> None:
        """Handle /mood command."""
        persona = self.persona_manager.get_persona_info()
        mood = persona.get("mood", "energetic")
        desc = persona.get("mood_description", "")

        await update.message.reply_text(
            f"🎯 Настроение Маши: {mood}\n"
            f"{desc}\n\n"
            f"Мой S63 сегодня {'рычит' if mood == 'passionate' else 'мурлычет' if mood == 'nostalgic' else 'работает'} 💪"
        )

    async def _cmd_theme(self, update: Any, context: Any) -> None:
        """Handle /theme command."""
        theme = self.scheduler.get_current_theme()
        if theme:
            await update.message.reply_text(
                f"📅 Тема дня: {theme.get('emoji', '🚗')} {theme.get('name', '?')}\n"
                f"{theme.get('description', '')}\n\n"
                f"Тема: {theme.get('topic', 'не определена')}"
            )
        else:
            await update.message.reply_text("Сегодня нет особой темы — обычный BMW-день 🏎️")

    async def _cmd_ask(self, update: Any, context: Any) -> None:
        """Handle /ask command — answer BMW questions."""
        if not context.args:
            await update.message.reply_text("Задай вопрос: /ask <вопрос про BMW>")
            return

        question = " ".join(context.args)
        await update.message.reply_text(f"Думаю над: {question} 🤔")

        try:
            # Generate answer
            if self.writer:
                result = await self.writer.generate(
                    topic=question,
                    context="Вопрос подписчика",
                    content_type="news+reaction",
                    character_mix="Маша",
                    mood=self.persona_manager.get_current_mood(),
                )
                if result and result.get("text"):
                    await update.message.reply_text(result["text"][:4096])
                else:
                    await update.message.reply_text("Не смогла сформулировать ответ. VANOS барахлит 😅")
        except Exception as exc:
            logger.error("Ask command error: %s", exc)
            await update.message.reply_text("Ошибка при генерации ответа 😔")

    async def _handle_message(self, update: Any, context: Any) -> None:
        """Handle regular text messages."""
        if not update.effective_user or not update.message:
            return

        user = update.effective_user
        text = update.message.text or ""

        # Save to chat history
        await self.db.add_chat_message(
            user_id=user.id,
            chat_id=update.effective_chat.id if update.effective_chat else 0,
            role="user",
            content=text,
        )

        # Auto-respond to BMW-related messages
        text_lower = text.lower()
        bmw_keywords = ["bmw", "бмв", "бавар", "эмка", "///m", "mpower"]
        if any(kw in text_lower for kw in bmw_keywords):
            await update.message.reply_text(
                "Привет! Задай вопрос через /ask или просто спроси про BMW 😎🏎️"
            )

    async def cleanup(self) -> None:
        """Clean up resources."""
        self.background.cancel_all()

        if self.writer:
            await self.writer.close()
        if self.channel:
            await self.channel.close()
        if self.pipeline and self.pipeline.rss_fetcher:
            await self.pipeline.rss_fetcher.close()
        if self.pipeline and self.pipeline.fact_checker:
            await self.pipeline.fact_checker.close()
        if self.pipeline and self.pipeline.image_gen:
            await self.pipeline.image_gen.close()

        await self.db.close()
        self._lock.release()

        logger.info("masha-bot cleanup complete")


# ── CLI Entry Point ───────────────────────────────────────────────────────────

async def main() -> None:
    """Main entry point."""
    bot = MashaBot()

    # Acquire singleton lock
    if not bot._lock.acquire():
        logger.error("Another instance is already running. Exiting.")
        sys.exit(1)

    try:
        # Check mode from environment
        mode = os.getenv("MASHA_BOT_MODE", "single").lower()

        if mode == "interactive":
            await bot.run_interactive()
        elif mode == "single":
            result = await bot.run_single_post()
            logger.info("Single post result: %s", result)
        else:
            logger.warning("Unknown mode: %s, defaulting to single", mode)
            result = await bot.run_single_post()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        sys.exit(1)
    finally:
        await bot.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
