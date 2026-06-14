"""Masha Bot Configuration — @asmasha_bot

Re-exports from bot.config to avoid config drift between modules.
Internal modules can import from either bot.config or bot.core.config.
"""

from bot.config import BotConfig, MashaPersona, config, persona, NewsSource, NewsConfig, news_config  # noqa: F401


def get_config() -> BotConfig:
    """Return the singleton BotConfig instance."""
    return config


def get_persona() -> MashaPersona:
    """Return the singleton MashaPersona instance."""
    return persona
