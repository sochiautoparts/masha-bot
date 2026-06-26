"""
masha-bot entry point — BMW-focused Telegram bot for @bmw_mpower_club.

Based on asya-bot architecture, reworked for BMW content.
Uses Pollinations AI, SQLite with aiosqlite, and GitHub Actions for scheduling.

Features:
- aiogram 3.x Telegram Bot framework
- Pollinations AI as primary provider (dual-key failover)
- SQLite with aiosqlite for persistence
- Background tasks: news fetching, channel posting
- Singleton lock to prevent duplicate instances
- Two modes: interactive (long polling) and single (one-shot for Actions)
"""

import asyncio
import faulthandler
import logging
import os
import random
import signal
import sys
import time
import fcntl
from pathlib import Path

# Enable faulthandler for C-level crash diagnostics (segfaults in llama-cpp)
faulthandler.enable()

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.core.config import config, persona
from bot.database import init_db, cleanup_old_fingerprints, add_chat_message, load_topic_registry
from bot.partners import partner_manager
from ai.router import get_ai_router
from news import run_news_cycle
from channel import channel_manager

# ── Logging setup ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("masha.main")

# Reduce noisy loggers
for noisy in ["aiogram.event", "httpx", "httpcore", "aiosqlite"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Singleton Lock ─────────────────────────────────────────────────────────────

class SingletonLock:
    """File-based lock to prevent multiple bot instances."""

    def __init__(self, lock_file: str):
        self.lock_file = lock_file
        self._lock_fd = None

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns True if successful."""
        try:
            os.makedirs(os.path.dirname(self.lock_file) or ".", exist_ok=True)
            self._lock_fd = open(self.lock_file, "w")
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
            return True
        except (IOError, OSError):
            if self._lock_fd:
                self._lock_fd.close()
                self._lock_fd = None
            return False

    def release(self) -> None:
        """Release the lock."""
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
                os.unlink(self.lock_file)
            except (IOError, OSError):
                pass
            self._lock_fd = None


# ── Background Tasks ───────────────────────────────────────────────────────────

class BackgroundTasks:
    """Manages background tasks for news and channel posting."""

    def __init__(self, bot: Bot):
        self.bot = bot
        self._running = False
        self._tasks: list = []
        self._greeting_sent = False

    async def start(self) -> None:
        """Start all background tasks."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._morning_greeting(), name="morning_greeting"),
            asyncio.create_task(self._news_fetcher(), name="news_fetcher"),
            asyncio.create_task(self._channel_poster(), name="channel_poster"),
        ]
        logger.info("Background tasks started")

    async def stop(self) -> None:
        """Stop all background tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("Background tasks stopped")

    async def _morning_greeting(self) -> None:
        """Send a natural greeting to the owner — like a living person, not a bot.
        Only sends ONCE per startup. Short and varied. Has a 4-hour cooldown
        to prevent spam on frequent restarts."""
        if self._greeting_sent:
            return

        await asyncio.sleep(15)  # Wait a bit after startup
        self._greeting_sent = True

        # Cooldown: don't send if one was sent recently (within 4 hours)
        try:
            cooldown_file = "/tmp/masha_last_greeting"
            if os.path.exists(cooldown_file):
                with open(cooldown_file, "r") as f:
                    last_greeting_time = float(f.read().strip())
                if time.time() - last_greeting_time < 14400:  # 4 hours
                    logger.info("Greeting cooldown active — skipping")
                    return
        except Exception:
            pass

        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            hour = datetime.now(ZoneInfo("Europe/Moscow")).hour

            if 5 <= hour < 12:
                greetings = [
                    "Утро! М5 прогрета ☕",
                    "Доброе утро! S63 рычит ☀️",
                    "Проснулась, кофе, BMW-новости ☕",
                    "Утро! ///M-Power! 🏎️",
                ]
            elif 12 <= hour < 18:
                greetings = [
                    "Привет! 😊",
                    "День! Свежие BMW-новости 📰",
                    "Хей! M-division на связи 🔥",
                    "На связи! Что нового у баварцев? 🏎️",
                ]
            elif 18 <= hour < 23:
                greetings = [
                    "Вечер! 🌆",
                    "Привет! M5 остывает после дня 🌆",
                    "Вечер! BMW-новости смотрю 📰",
                ]
            else:
                greetings = [
                    "Ночной режим 🌙",
                    "Не спится? M5 тоже 🌙",
                    "Совиный режим — Nürburgring по ночам лучше 🌙",
                ]

            greeting = random.choice(greetings)
            if config.OWNER_ID:
                await self.bot.send_message(config.OWNER_ID, greeting)
                try:
                    await add_chat_message(config.OWNER_ID, "assistant", greeting)
                except Exception as e:
                    logger.debug(f"Could not save greeting to chat history: {e}")
                try:
                    with open("/tmp/masha_last_greeting", "w") as f:
                        f.write(str(time.time()))
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Morning greeting error: {e}")

    async def _news_fetcher(self) -> None:
        """Periodically fetch news from RSS sources and cleanup old data."""
        await asyncio.sleep(30)

        cycle_count = 0
        while self._running:
            try:
                count = await run_news_cycle()
                if count > 0:
                    logger.info(f"News fetcher: {count} new items")

                # Cleanup old fingerprints every 12 cycles (~6 hours)
                cycle_count += 1
                if cycle_count % 12 == 0:
                    removed = await cleanup_old_fingerprints(max_age_days=7)
                    if removed > 0:
                        logger.info(f"Cleaned up {removed} old post fingerprints")
                    # v16: Also prune posted_urls entries older than 30 days
                    try:
                        from bot.database import cleanup_posted_urls
                        pruned = await cleanup_posted_urls(max_age_days=30)
                        if pruned > 0:
                            logger.info(f"Cleaned up {pruned} old posted_urls entries (>30 days)")
                    except Exception as e:
                        logger.debug(f"posted_urls cleanup skipped: {e}")
                    # v18.2: Prune old chat_history (>30 days OR >50 per chat)
                    # Prevents unbounded DB growth from group/private conversations.
                    try:
                        from bot.database import cleanup_old_chat_history
                        removed_ch = await cleanup_old_chat_history(max_age_days=30, keep_per_chat=50)
                        if removed_ch > 0:
                            logger.info(f"Cleaned up {removed_ch} old chat_history rows")
                    except Exception as e:
                        logger.debug(f"chat_history cleanup skipped: {e}")

                # Auto-refresh partner data every 6 hours
                if cycle_count % 12 == 0:
                    try:
                        await partner_manager.maybe_refresh()
                    except Exception as e:
                        logger.debug(f"Partner data refresh skipped: {e}")
            except Exception as e:
                logger.error(f"News fetcher error: {e}")

            # Wait for next cycle
            interval = config.NEWS_INTERVAL_MINUTES * 60
            for _ in range(interval):
                if not self._running:
                    break
                await asyncio.sleep(1)

    async def _channel_poster(self) -> None:
        """Post to channel — 4 news + 1 partner per hourly cycle.
        
        Each 60-min cycle publishes 5 posts:
          Posts 1-4: DIFFERENT news items (each with dedup checks)
          Post 5: partner content (if interval allows)
          
        Between posts: 2-4 min gap to avoid Telegram rate limits.
        Tracks tried titles per cycle to avoid re-selecting the same article.
        """
        await asyncio.sleep(30)
        
        # 4 news posts per hour + 1 partner per cycle.
        # The 48h TTL dedup (posted_urls table) recycles old articles after 48h,
        # so 4 news/hour = 96/day is sustainable — the bmw-news.json source is
        # refreshed hourly and the multi-source fallback (RSS + web search)
        # guarantees a non-empty pool.
        logger.info("Channel poster started — 4 news + 1 partner per hourly cycle")

        consecutive_empty_cycles = 0
        NEWS_POSTS_PER_CYCLE = 4

        while self._running:
            posts_this_cycle = 0
            tried_titles_this_cycle = []  # Track titles tried this cycle
            logger.info(f"Channel poster: starting new hourly cycle (consecutive_empty={consecutive_empty_cycles})")
            
            # ── Phase 1: 4 news posts ──
            for post_num in range(NEWS_POSTS_PER_CYCLE):
                try:
                    posted = await channel_manager.run_scheduled_post(
                        exclude_titles=tried_titles_this_cycle
                    )
                    if posted:
                        posts_this_cycle += 1
                        # Record the posted title so we don't try it again this cycle
                        if isinstance(posted, dict) and posted.get("title"):
                            tried_titles_this_cycle.append(posted["title"])
                        logger.info(f"Channel poster: news post {post_num + 1}/{NEWS_POSTS_PER_CYCLE} published")
                    else:
                        logger.info(f"Channel poster: news post {post_num + 1}/{NEWS_POSTS_PER_CYCLE} returned False")
                except Exception as e:
                    logger.error(f"Channel poster error (news post {post_num + 1}): {e}", exc_info=True)
                
                # Gap between news posts: 2-4 minutes
                if post_num < NEWS_POSTS_PER_CYCLE - 1:
                    gap = random.randint(120, 240)
                    logger.info(f"Waiting {gap}s before next news post")
                    for _ in range(gap):
                        if not self._running:
                            break
                        await asyncio.sleep(1)
            
            # ── Phase 2: 1 partner post ──
            try:
                partner_posted = await channel_manager.post_partner_content()
                if partner_posted:
                    posts_this_cycle += 1
                    logger.info("Channel poster: partner post published")
                else:
                    logger.info("Channel poster: partner post skipped (interval or no content)")
            except Exception as e:
                logger.error(f"Channel poster error (partner post): {e}", exc_info=True)
            
            if posts_this_cycle > 0:
                logger.info(f"Channel poster hourly cycle complete: {posts_this_cycle} posts published")
                consecutive_empty_cycles = 0
            else:
                consecutive_empty_cycles += 1
                if consecutive_empty_cycles == 3 and self.bot:
                    try:
                        await self.bot.send_message(
                            chat_id=config.OWNER_ID,
                            text=f"⚠️ Маша: 3 часа подряд без постов в канал. Возможна проблема с контентом или дедупликацией. Проверь логи."
                        )
                    except Exception:
                        pass

            # Wait for next hourly cycle
            interval = config.CHANNEL_POST_INTERVAL_MINUTES * 60
            for _ in range(interval):
                if not self._running:
                    break
                await asyncio.sleep(1)


# ── Main Entry Point ──────────────────────────────────────────────────────────

async def main():
    """Main entry point for Masha Bot."""
    # Check bot token
    if not config.BOT_TOKEN:
        logger.critical("BOT_TOKEN not set! Exiting.")
        sys.exit(1)

    # Acquire singleton lock
    lock = SingletonLock(config.LOCK_FILE)
    if not lock.acquire():
        logger.warning("Another instance is running, exiting.")
        sys.exit(0)

    # Check mode from environment
    mode = os.getenv("MASHA_BOT_MODE", "interactive").lower()

    # Create bot
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Delete webhook to ensure polling works
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook deleted, polling mode ready")
    except Exception as e:
        logger.warning(f"Could not delete webhook: {e}")

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Load topic registry from DB
    try:
        from bot.content_engine import _topic_registry
        loaded_registry = await load_topic_registry()
        if loaded_registry:
            import bot.content_engine as ce
            ce._topic_registry = loaded_registry
            logger.info(f"Topic registry loaded: {len(loaded_registry)} topics from DB")
        else:
            logger.info("Topic registry empty — first run or all topics expired")
    except Exception as e:
        logger.warning(f"Could not load topic registry from DB: {e}")

    # Initialize AI router
    await get_ai_router().initialize()
    logger.info("AI Router initialized")

    # Load partner programs
    try:
        partner_count = await partner_manager.load_async()
        logger.info(f"Partner programs loaded: {partner_count}")
    except Exception as e:
        logger.warning(f"Could not load partner programs: {e}")

    # Set bot on channel manager
    channel_manager.set_bot(bot)

    # Load recently posted titles into semantic dedup
    try:
        await channel_manager.load_recent_semantic_data()
    except Exception as e:
        logger.warning(f"Could not load semantic dedup data: {e}")

    if mode == "single":
        # Single-cycle mode for GitHub Actions
        logger.info("=== Masha Bot Starting (single-cycle mode) ===")
        try:
            posted = await channel_manager.run_scheduled_post()
            logger.info(f"Single cycle result: {'posted' if posted else 'no post'}")
        except Exception as e:
            logger.error(f"Single cycle error: {e}")
        finally:
            try:
                await bot.session.close()
            except Exception:
                pass
            lock.release()
        return

    # Interactive mode — long polling
    # Set up dispatcher
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Include all handler routers.
    # IMPORTANT: admin_router MUST be registered BEFORE chat_router.
    # chat_router has a catch-all `@chat_router.message(F.text)` handler that
    # matches every text message — including slash commands. If chat_router is
    # registered first, ALL admin commands (/admin, /status, /post, /switch, ...)
    # are swallowed by the catch-all and routed to the AI as ordinary text,
    # silently breaking the entire admin panel. Registering admin_router first
    # lets its Command(...) handlers take precedence.
    try:
        from bot.handlers.admin import admin_router
        from bot.handlers.chat import chat_router
        from bot.handlers.inline import inline_router
        dp.include_router(admin_router)
        dp.include_router(chat_router)
        dp.include_router(inline_router)
        logger.info("Handler routers included successfully (admin -> chat -> inline)")
    except Exception as e:
        logger.critical(f"Failed to include handler routers: {e}")
        raise

    # Start background tasks
    bg_tasks = BackgroundTasks(bot)

    async def on_startup():
        """Startup callback — start background tasks."""
        await bg_tasks.start()

    async def on_shutdown():
        """Shutdown callback — stop background tasks."""
        await bg_tasks.stop()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Run polling
    logger.info("=== Masha Bot Starting (v13.0 — LOCAL-FIRST Multi-Provider, BMW News from sochiautoparts/nws) ===")
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        await bg_tasks.stop()
        lock.release()
        try:
            await bot.session.close()
        except Exception:
            pass
        logger.info("=== Masha Bot Stopped ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        sys.exit(code)
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)
