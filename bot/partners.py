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
        self.id: str = data.get("id", data.get("name", "unknown"))
        self.name: str = data.get("name", "Unknown")
        self.description: str = data.get("description", "")
        self.url: str = data.get("url", "")
        self.affiliate_url: str = data.get("affiliate_url", data.get("goto_link", data.get("url", "")))
        self.goto_link: str = data.get("goto_link", self.affiliate_url)
        self.logo_url: str = data.get("logo_url", "")
        self.categories: list[str] = data.get("categories", [])
        self.category: str = data.get("category", "general")
        self.promo_text: str = data.get("promo_text", "")
        self.promo_code: str = data.get("promo_code", "")
        self.discount: str = data.get("discount", "")
        self.site: str = data.get("site", "")
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

    def __init__(self) -> None:
        self.config = get_config()
        self._programs: list[PartnerProgram] = []
        self._loaded = False
        self._last_partner_post_time: float = 0

    @property
    def programs(self) -> list[PartnerProgram]:
        """Get loaded partner programs."""
        return self._programs

    def load(self) -> int:
        """Synchronous load from local JSON (fallback). Returns count."""
        try:
            import os
            ads_file = self.config.ADMITAD_ADS_FILE
            if os.path.exists(ads_file):
                with open(ads_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw_programs = data if isinstance(data, list) else data.get("programs", [])
                self._programs = [PartnerProgram(raw) for raw in raw_programs]
                self._loaded = True
                return len(self._programs)
        except Exception as exc:
            logger.warning("Failed to load partner data from file: %s", exc)
        return 0

    async def load_async(self) -> int:
        """Load partner programs from admitad_ads.json URL."""
        if self._loaded:
            return len(self._programs)

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
        return len(self._programs)

    async def maybe_refresh(self) -> None:
        """Refresh partner data if enough time has passed."""
        if not self._loaded:
            await self.load_async()

    def should_post_partner(self) -> bool:
        """Check if we should post a partner post this cycle."""
        import time
        interval = self.config.PARTNER_POST_INTERVAL_HOURS * 3600
        return (time.time() - self._last_partner_post_time) >= interval

    def mark_posted(self) -> None:
        """Mark that a partner post was just made."""
        self._last_partner_post_time = time.time()

    def get_random_program(self, category: str = "") -> PartnerProgram | None:
        """Get a random partner program, optionally filtered by category."""
        if not self._programs:
            return None
        candidates = self._programs
        if category:
            candidates = [p for p in self._programs if category.lower() in p.category.lower() or category.lower() in p.name.lower()]
        if not candidates:
            candidates = self._programs
        return random.choice(candidates) if candidates else None

    def get_by_site(self, site: str) -> PartnerProgram | None:
        """Get partner program by site domain."""
        for p in self._programs:
            if site.lower() in (p.site or "").lower() or site.lower() in (p.name or "").lower():
                return p
        return None

    def get_primary_parts_links(self) -> list[dict[str, str]]:
        """Get primary parts links (Rossko, Autopiter, AvtoALL)."""
        links = []
        for name in PARTNER_ORDER:
            for p in self._programs:
                if name.lower() in p.name.lower():
                    links.append({"name": p.name, "url": p.goto_link or p.affiliate_url})
                    break
        return links

    def format_primary_parts_links(self) -> str:
        """Format primary parts links as text for AI context."""
        links = self.get_primary_parts_links()
        if not links:
            return ""
        lines = []
        for link in links:
            lines.append(f"- {link['name']}: {link['url']}")
        return "Партнёрские ссылки для запчастей:\n" + "\n".join(lines)

    def get_all_relevant_links(self, query: str = "", max_programs: int = 5) -> list[dict[str, str]]:
        """Get all relevant partner links for a query."""
        return self.get_primary_parts_links()[:max_programs]

    async def generate_partner_post_content(self, program: PartnerProgram | None = None) -> str:
        """Generate a partner post text with Маша's voice."""
        if not program:
            program = self.get_random_program()
        if not program:
            return ""
        return self._build_partner_text(program)

    def _build_partner_text(self, program: PartnerProgram) -> str:
        """Build a partner post text in Маша's voice."""
        templates = [
            (
                f"🔧 Запчасти для вашего BMW — вопрос не эстетики, а выживания. "
                f"Особенно если у вас N55\n\n"
                f"{program.name} — {program.description}\n\n"
                f"{'Промокод: ' + program.promo_code + ' — скидка ' + program.discount if program.promo_code else 'Скидки до ' + program.discount if program.discount else 'Специальные цены для владельцев BMW'}\n\n"
                f"👉 {program.affiliate_url}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
            (
                f"Серёга говорит: 'Оригинал или хороший аналог — третьего не дано'. "
                f"В этом он прав (редко, но бывает)\n\n"
                f"{program.name} — {program.description}\n\n"
                f"{'Промокод: ' + program.promo_code + ' — ' + program.discount if program.promo_code else 'Акции и скидки на автозапчасти'}\n\n"
                f"👉 {program.affiliate_url}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
            (
                f"Ваш BMW заслуживает лучших запчастей. "
                f"Даже Доктор Ван Дамм одобряет\n\n"
                f"{program.name} — {program.description}\n\n"
                f"{'Промокод: ' + program.promo_code + ' — ' + program.discount if program.promo_code else 'Выгодные цены на всё'}\n\n"
                f"👉 {program.affiliate_url}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
        ]

        return random.choice(templates)


# ── Global singleton instance ────────────────────────────────────────────────

partner_manager = PartnerManager()
