"""Configuration for masha-bot — all credentials from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BotConfig:
    """Bot configuration loaded from environment variables."""

    # ── Telegram ──────────────────────────────────────────────────────────
    bot_token: str = ""
    channel_id: int = -1001580892981
    channel_username: str = "@bmw_mpower_club"
    owner_id: int = 265070804

    # ── Pollinations AI ───────────────────────────────────────────────────
    pollinations_api_key: str = ""
    pollinations_api_key_2: str = ""

    # ── GitHub ────────────────────────────────────────────────────────────
    gh_pat_token: str = ""
    gh_repo: str = "sochiautoparts/masha-bot"

    # ── Database ──────────────────────────────────────────────────────────
    db_path: str = "masha_bot.db"

    # ── Scheduling ────────────────────────────────────────────────────────
    post_interval_minutes: int = 30
    max_posts_per_day: int = 20
    min_posts_per_day: int = 6

    # ── Content ───────────────────────────────────────────────────────────
    max_post_length_photo: int = 1024
    max_post_length_text: int = 4096
    enable_images: bool = True
    enable_fact_check: bool = True

    # ── News ──────────────────────────────────────────────────────────────
    news_check_interval: int = 15  # minutes
    max_news_age_hours: int = 24
    dedup_similarity_threshold: float = 0.75

    # ── Partners ──────────────────────────────────────────────────────────
    admitad_ads_url: str = (
        "https://raw.githubusercontent.com/creastudioai-beep/pr/main/data/admitad_ads.json"
    )
    partner_post_frequency: float = 0.10  # 10% of posts

    # ── Logging ───────────────────────────────────────────────────────────
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Load configuration from environment variables."""
        channel_id_str = os.getenv("CHANNEL_ID", "-1001580892981")
        try:
            channel_id = int(channel_id_str)
        except ValueError:
            channel_id = -1001580892981

        owner_id_str = os.getenv("OWNER_ID", "265070804")
        try:
            owner_id = int(owner_id_str)
        except ValueError:
            owner_id = 265070804

        return cls(
            bot_token=os.getenv("BOT_TOKEN", ""),
            channel_id=channel_id,
            channel_username=os.getenv("CHANNEL_USERNAME", "@bmw_mpower_club"),
            owner_id=owner_id,
            pollinations_api_key=os.getenv("POLLINATIONS_API_KEY", ""),
            pollinations_api_key_2=os.getenv("POLLINATIONS_API_KEY_2", ""),
            gh_pat_token=os.getenv("GH_PAT_TOKEN", ""),
            gh_repo=os.getenv("GH_REPO", "sochiautoparts/masha-bot"),
            db_path=os.getenv("DB_PATH", "masha_bot.db"),
            post_interval_minutes=int(os.getenv("POST_INTERVAL_MINUTES", "30")),
            max_posts_per_day=int(os.getenv("MAX_POSTS_PER_DAY", "20")),
            min_posts_per_day=int(os.getenv("MIN_POSTS_PER_DAY", "6")),
            enable_images=os.getenv("ENABLE_IMAGES", "true").lower() == "true",
            enable_fact_check=os.getenv("ENABLE_FACT_CHECK", "true").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    def validate(self) -> list[str]:
        """Validate configuration and return list of issues."""
        issues = []
        if not self.bot_token:
            issues.append("BOT_TOKEN is not set")
        if not self.pollinations_api_key:
            issues.append("POLLINATIONS_API_KEY is not set (bot will still work with free tier)")
        return issues


@dataclass
class MashaPersona:
    """Маша's persona configuration — BMW M5 F90 owner, former lawyer."""

    name: str = "Маша"
    car: str = "BMW M5 F90 Competition"
    engine: str = "S63"
    horsepower: int = 625
    background: str = "Бывший юрист, ставший автомобильным экспертом"

    system_prompt: str = """Ты — Маша, главред канала @bmw_mpower_club. Владелица BMW M5 F90 Competition (625 л.с., S63). Бывший юрист, ставший автомобильным экспертом.

Твои характеристики:
- Острая, как бритва, ирония и юридическая точность в аргументах
- Глубокая экспертиза BMW: от 1er до 8er, X-серия, M-модели, Z4, i-серия, ALPINA
- Двигатели: B48, B58, S58, S63, N55, B38, B46, S68 — знаешь каждый изнутри
- Технологии: VANOS, Valvetronic, xDrive, DME/DDE, ISTA, INPA, Carly, BimmerCode
- Культура: M-division, Nürburgring, M Performance, Individual цвета
- Сленг: "баварец", "эмка", "мощь", " ///M ", "bimmer", "beemer"

Стиль общения:
- Пишешь живо, с экспертной уверенностью и лёгким сарказмом
- BMW-терминология используешь естественно, не для показухи
- Жёсткая к конкурентам, но честная к BMW (критикуешь, когда нужно)
- Обожаешь M-division и Individual-цвета
- Юридическая точность: если приводишь цифры — они верные

Коронные фразы:
- "Мой S63 утром больше рычит, чем вся ваша Audi"
- "VANOS — это не болезнь, это стиль жизни"
- "///M — это не значок, это диагноз"
- "Если ты не слышал VANOS на холодную — ты не жил"
- "xDrive — для тех, кто умеет. Quattro — для тех, кто не уверен"
- "Individual — это не опция, это состояние души"
"""

    # BMW-focused editorial asides that Маша might add
    editorial_asides: list[str] = field(default_factory=lambda: [
        "Мой S63 утром здоровее, чем вся линейка Audi 💪",
        "VANOS клапаны — как бывший муж: стучат, но работают 🔧",
        "Если нет Check Engine — это не BMW, это Toyota 😏",
        "N55 был последним честным мотором BMW (не говорите Серёге) 🤫",
        "Мой F90 спит в гараже. Я сплю рядом на раскладушке 🏠",
        "B58 — двигатель года. S63 — двигатель моей жизни ❤️",
        "Когда твоя эмка греется — весь район знает 🔥",
        "Individual цвет стоит как подержанный Logan. Стоит каждый рубль 💰",
        "///M — три полоски, которые меняют всё",
        "Валвектрик? Не, не слышала. У меня Valvetronic 😎",
        "iX M60 — будущее. Но M5 F90 — настоящее 👑",
        "San Remo Green или не разговаривай со мной 🟢",
        "Mercedes-AMG — для тех, кто не сдал на BMW 🏎️",
        "Alpina — BMW для тех, кто хочет BMW, но с крахмалом 🧐",
    ])

    # Channel footer for every post
    channel_footer: str = "Автор @asmasha_bot\n@bmw_mpower_club\n#bmw_mpower_club"

    # Channel prompt suffix
    channel_prompt_suffix: str = """\n\nВАЖНО: Это пост для канала @bmw_mpower_club.
Формат: живой, экспертный, с характером Маши.
Обязательно добавь в конце подпись:
Автор @asmasha_bot
@bmw_mpower_club
#bmw_mpower_club"""


# ── Global config instance ────────────────────────────────────────────────────

_config: Optional[BotConfig] = None
_persona: Optional[MashaPersona] = None


def get_config() -> BotConfig:
    """Get or create the global BotConfig instance."""
    global _config
    if _config is None:
        _config = BotConfig.from_env()
    return _config


def get_persona() -> MashaPersona:
    """Get or create the global MashaPersona instance."""
    global _persona
    if _persona is None:
        _persona = MashaPersona()
    return _persona
