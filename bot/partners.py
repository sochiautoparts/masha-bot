"""Admitad partner integration for masha-bot.

Same partner system as Asya-bot:
- PartnerProgram class
- PartnerManager
- admitad_ads.json from same URL
- Rossko → Autopiter → AvtoALL order
- Same partner post templates but with Маша's voice
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

from .database import Database
from .core.config import get_config, get_persona

logger = logging.getLogger(__name__)

ADMITAD_ADS_URL = (
    "https://raw.githubusercontent.com/creastudioai-beep/pr/main/data/admitad_ads.json"
)

# ── Partner order priority ────────────────────────────────────────────────────

PARTNER_ORDER = ["Rossko", "Autopiter", "AvtoALL"]


class PartnerProgram:
    """Represents a single partner program."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.name: str = data.get("name", "Unknown")
        self.description: str = data.get("description", "")
        self.url: str = data.get("url", "")
        self.affiliate_url: str = data.get("affiliate_url", data.get("url", ""))
        self.logo_url: str = data.get("logo_url", "")
        self.categories: list[str] = data.get("categories", [])
        self.promo_text: str = data.get("promo_text", "")
        self.promo_code: str = data.get("promo_code", "")
        self.discount: str = data.get("discount", "")
        self.bmw_relevant: bool = self._check_bmw_relevance()

    def _check_bmw_relevance(self) -> bool:
        """Check if this partner is relevant to BMW owners."""
        relevant_keywords = [
            "автозапчасти", "запчасти", "auto parts",
            "масло", "oil", "фильтр", "filter",
            "тормоз", "brake", "BMW", "bmw",
            "авто", "car", "сервис", "service",
        ]
        combined = f"{self.name} {self.description} {' '.join(self.categories)}".lower()
        return any(kw.lower() in combined for kw in relevant_keywords)


class PartnerManager:
    """Manages partner programs and partner post generation."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.config = get_config()
        self._programs: list[PartnerProgram] = []
        self._loaded = False

    async def load_programs(self) -> None:
        """Load partner programs from admitad_ads.json."""
        if self._loaded:
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(ADMITAD_ADS_URL) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_programs = data if isinstance(data, list) else data.get("programs", [])

                        for raw in raw_programs:
                            program = PartnerProgram(raw)
                            self._programs.append(program)

                        # Sort by partner order priority
                        self._programs.sort(
                            key=lambda p: PARTNER_ORDER.index(p.name)
                            if p.name in PARTNER_ORDER
                            else len(PARTNER_ORDER)
                        )

                        logger.info("Loaded %d partner programs", len(self._programs))
                    else:
                        logger.warning("Failed to fetch partner data: status %d", resp.status)
        except Exception as exc:
            logger.error("Failed to load partner programs: %s", exc)

        self._loaded = True

    async def should_post_partner(self) -> bool:
        """Check if we should post a partner post this cycle."""
        posts_today = await self.db.get_posts_today_count()
        partner_today = await self.db.get_partner_posts_today()

        # 10% of posts should be partner content
        if posts_today == 0:
            return False

        partner_ratio = partner_today / posts_today
        return partner_ratio < self.config.partner_post_frequency

    async def generate_partner_post(self) -> dict[str, Any] | None:
        """Generate a partner post with Маша's voice."""
        await self.load_programs()

        if not self._programs:
            logger.warning("No partner programs available")
            return None

        # Prefer BMW-relevant programs
        bmw_programs = [p for p in self._programs if p.bmw_relevant]
        program = random.choice(bmw_programs) if bmw_programs else random.choice(self._programs)

        # Generate post text with Маша's voice
        text = self._build_partner_text(program)

        return {
            "text": text,
            "program_name": program.name,
            "affiliate_url": program.affiliate_url,
            "content_type": "partner",
        }

    def _build_partner_text(self, program: PartnerProgram) -> str:
        """Build a partner post text in Маша's voice."""
        persona = get_persona()

        templates = [
            (
                f"🔧 Запчасти для вашего BMW — вопрос не эстетики, а выживания. "
                f"Особенно если у вас N55 😏\n\n"
                f"🎁 {program.name} — {program.description}\n\n"
                f"{'Промокод: ' + program.promo_code + ' — скидка ' + program.discount if program.promo_code else 'Скидки до ' + program.discount if program.discount else 'Специальные цены для владельцев BMW'}\n\n"
                f"👉 {program.affiliate_url}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
            (
                f"Серёга говорит: 'Оригинал или хороший аналог — третьего не дано'. "
                f"В этом он прав (редко, но бывает 😅)\n\n"
                f"🔍 {program.name} — {program.description}\n\n"
                f"{'Промокод: ' + program.promo_code + ' — ' + program.discount if program.promo_code else 'Акции и скидки на автозапчасти'}\n\n"
                f"👉 {program.affiliate_url}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
            (
                f"Ваш BMW заслуживает лучших запчастей. "
                f"Даже Доктор Ван Дамм одобряет (мур-р-р) 🐱\n\n"
                f"✅ {program.name} — {program.description}\n\n"
                f"{'Промокод: ' + program.promo_code + ' — ' + program.discount if program.promo_code else 'Выгодные цены на всё'}\n\n"
                f"👉 {program.affiliate_url}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
        ]

        return random.choice(templates)

    def get_programs(self) -> list[PartnerProgram]:
        """Get all loaded partner programs."""
        return self._programs.copy()
