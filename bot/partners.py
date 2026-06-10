"""
Admitad Partner Program Integration v3.0 — Masha Bot Edition

Loads partner data from remote admitad_ads.json (updateable file!).
Uses goto_link EXACTLY as-is — no subid additions, no modifications.
The goto_links are ready for both posts and user dialogs.

Key features from Asya-bot integration:
- Downloads admitad_ads.json from remote GitHub URL
- Auto-refreshes every 6 hours (file is updateable!)
- Uses goto_link EXACTLY as provided — NO subid additions
- Regional filtering by allowed_regions
- For article searches, modifies ulp parameter in goto_link
- Proper formatting: "Name (category description): goto_link"
- BMW-friendly category descriptions
- Keyword matching across categories
- Partner context generation for AI

Masha's BMW persona voice preserved in all templates.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse

import httpx

from .core.config import get_config, get_persona

logger = logging.getLogger(__name__)

# Remote admitad_ads.json URL (updateable file!)
ADMITAD_JSON_URL = (
    "https://raw.githubusercontent.com/creastudioai-beep/pr/main/data/admitad_ads.json"
)
ADMITAD_LOCAL_CACHE = "data/admitad_ads.json"
ADMITAD_REFRESH_INTERVAL = 6 * 3600  # Refresh every 6 hours

# Default region for partner filtering
DEFAULT_REGION = "RU"

# ── Partner order priority ────────────────────────────────────────────────────

PARTNER_ORDER = ["Rossko", "Autopiter", "AvtoALL"]


class PartnerProgram:
    """Represents a single partner program from admitad."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self.id: str = str(data.get("id", data.get("name", "unknown")))
        self.name: str = data.get("name", "Unknown")
        self.slug: str = data.get("slug", "")
        self.description: str = data.get("ad_text", data.get("description", ""))[:300]
        self.ad_text: str = data.get("ad_text", "")
        self.url: str = data.get("site_url", data.get("url", ""))
        self.site_url: str = data.get("site_url", data.get("site", ""))
        self.affiliate_url: str = data.get(
            "goto_link", data.get("affiliate_url", data.get("url", ""))
        )
        self.goto_link: str = data.get("goto_link", self.affiliate_url)
        self.logo_url: str = data.get(
            "image_url", data.get("logo", data.get("image", data.get("logo_url", "")))
        )
        self.image_url: str = data.get("image_url", data.get("image", self.logo_url))
        self.image: str = data.get(
            "image", data.get("image_url", data.get("logo", data.get("brand_logo", "")))
        )
        self.categories: list[str] = (
            [data.get("category", "general")]
            if data.get("category")
            else data.get("categories", [])
        )
        self.category: str = data.get("category", "general")
        self.category_name: str = data.get("category_name", "")
        self.promo_text: str = data.get("ad_text", data.get("promo_text", ""))
        self.promo_code: str = data.get("promo_code", "")
        self.discount: str = data.get("discount", "")
        self.allowed_regions: list[str] = data.get("allowed_regions", [])
        self.rating: str = data.get("rating", "")
        self.raw = data
        self.bmw_relevant: bool = self._check_bmw_relevance()

    # ── Region / category / keyword matching ────────────────────────────────

    def has_region(self, region: str = DEFAULT_REGION) -> bool:
        """Check if program is available in a region.

        Empty allowed_regions = available everywhere.
        "00" in allowed_regions = worldwide.
        """
        if not self.allowed_regions:
            return True
        region_upper = region.upper()
        if "00" in self.allowed_regions:
            return True
        return region_upper in [r.upper() for r in self.allowed_regions]

    def has_category(self, category: str) -> bool:
        """Check if program belongs to a category."""
        cat_lower = category.lower()
        if cat_lower == self.category.lower():
            return True
        if cat_lower in self.category_name.lower():
            return True
        return False

    def matches_text(self, text: str) -> bool:
        """Check if text contains keywords related to this program."""
        text_lower = text.lower()
        # Check program name words
        name_words = [w.lower() for w in self.name.split() if len(w) > 3]
        for word in name_words:
            if word in text_lower:
                return True
        # Check category name words
        cat_words = [w.lower() for w in self.category_name.split() if len(w) > 3]
        for word in cat_words:
            if word in text_lower:
                return True
        # Check site_url domain
        if self.site_url:
            domain = urlparse(self.site_url).netloc.replace("www.", "")
            if domain and domain in text_lower:
                return True
        return False

    # ── Search URL generation ───────────────────────────────────────────────

    def get_search_url(self, query: str) -> str:
        """Get a search URL for this partner, using goto_link as base.

        If the goto_link has a ulp parameter (redirect URL), we modify it
        to include the search path. Otherwise, returns the goto_link as-is.
        """
        if not self.goto_link:
            return ""
        if not query:
            return self.goto_link

        try:
            parsed = urlparse(self.goto_link)
            params = parse_qs(parsed.query)

            if "ulp" in params and params["ulp"]:
                original_ulp = params["ulp"][0]
                search_url = self._build_search_url(original_ulp, query)
                if search_url != original_ulp:
                    new_params: dict[str, Any] = {}
                    for k, v_list in params.items():
                        if k == "ulp":
                            new_params[k] = search_url
                        else:
                            new_params[k] = v_list[0] if len(v_list) == 1 else v_list

                    new_query = urlencode(new_params, doseq=True)
                    return urlunparse(parsed._replace(query=new_query))
        except Exception as e:
            logger.debug("Error modifying goto_link for search: %s", e)

        return self.goto_link

    def _build_search_url(self, original_ulp: str, query: str) -> str:
        """Build a search URL by modifying the original redirect URL."""
        site_url = self.site_url.rstrip("/")
        query_encoded = quote_plus(query)

        search_patterns = {
            "rossko.ru": f"{site_url}/search?text={query_encoded}",
            "autopiter.ru": f"{site_url}/search?querystr={query_encoded}",
            "autopiter.kz": f"{site_url}/search?querystr={query_encoded}",
            "exist.ru": f"{site_url}/Price/?p={query_encoded}",
            "emex.ru": f"{site_url}/products?search={query_encoded}",
            "autodoc.ru": f"{site_url}/search?keyword={query_encoded}",
            "zzap.ru": f"{site_url}/search/?q={query_encoded}",
            "avtoall.ru": f"{site_url}/search/?q={query_encoded}",
            "aliexpress.ru": f"{site_url}/wholesale?SearchText={query_encoded}",
            "aliexpress.com": f"{site_url}/wholesale?SearchText={query_encoded}",
            "hyperauto.ru": f"{site_url}/search/?q={query_encoded}",
            "euro-diski.ru": f"{site_url}/search/?q={query_encoded}",
            "bs-tyres.ru": f"{site_url}/search/?q={query_encoded}",
            "koleso.ru": f"{site_url}/search/?q={query_encoded}",
            "avtocod.ru": f"{site_url}/search/?q={query_encoded}",
            "petrolplus.ru": f"{site_url}/search/?q={query_encoded}",
            "globaldrive.ru": f"{site_url}/search/?q={query_encoded}",
            "mirdvornikov.ru": f"{site_url}/search/?q={query_encoded}",
            "lukoil-shop.com": f"{site_url}/search/?q={query_encoded}",
        }

        for domain, pattern in search_patterns.items():
            if domain in self.site_url:
                return quote_plus(pattern)

        return original_ulp

    # ── Link formatting ─────────────────────────────────────────────────────

    def format_link(self, with_description: bool = True) -> str:
        """Format this partner's link for display.

        Uses goto_link EXACTLY as-is from the file.
        No subid additions — the link is ready to use!
        """
        if not self.goto_link:
            return ""
        if with_description and self.category_name:
            return f"{self.name} ({self.category_name}): {self.goto_link}"
        return f"{self.name}: {self.goto_link}"

    def format_link_with_search(self, query: str) -> str:
        """Format this partner's link with a search query.

        Modifies the ulp parameter in goto_link to include search.
        The base goto_link (with tracking) is preserved.
        """
        search_url = self.get_search_url(query)
        if not search_url:
            return ""
        if self.category_name:
            desc = self._get_category_description()
            return f"{self.name} ({desc}): {search_url}"
        return f"{self.name}: {search_url}"

    def _get_category_description(self) -> str:
        """Get a BMW-friendly description for this partner's category."""
        descriptions = {
            "autoparts": "профессиональный подбор запчастей",
            "tires": "шины и диски",
            "tools": "автоинструменты",
            "autoinsurance": "автострахование",
            "checkauto": "проверка авто",
            "autorent": "аренда авто",
            "coupons": "скидки и промокоды",
            "other": "рекомендую",
        }
        return descriptions.get(self.category, self.category_name or "рекомендую")

    # ── BMW relevance ───────────────────────────────────────────────────────

    def _check_bmw_relevance(self) -> bool:
        """Check if this partner is relevant to BMW owners."""
        relevant_keywords = [
            "автозапчасти", "запчасти", "auto parts",
            "масло", "oil", "фильтр", "filter",
            "тормоз", "brake", "BMW", "bmw",
            "авто", "car", "сервис", "service",
        ]
        combined = (
            f"{self.name} {self.description} {' '.join(self.categories)}"
        ).lower()
        return any(kw.lower() in combined for kw in relevant_keywords)


class PartnerManager:
    """Manages all partner programs — loading, matching, posting.

    v3.0: Downloads admitad_ads.json from remote URL, auto-refreshes.
    Uses goto_link EXACTLY as-is — NO subid additions!
    Masha BMW persona voice in all templates.
    """

    def __init__(self) -> None:
        self.config = get_config()
        self._programs: list[PartnerProgram] = []
        self._loaded = False
        self._last_load_time: float = 0
        self._last_partner_post_time: float = 0
        self._posted_today: int = 0
        self._day_start: float = 0
        # Site URL -> PartnerProgram mapping for fast lookup
        self._site_map: Dict[str, PartnerProgram] = {}

    @property
    def programs(self) -> list[PartnerProgram]:
        """Get loaded partner programs."""
        return self._programs

    # ── Loading ─────────────────────────────────────────────────────────────

    async def load_async(self) -> int:
        """Load partner programs — try remote first, then local cache."""
        count = await self._load_from_remote()
        if count > 0:
            return count
        return self._load_from_local()

    async def _load_from_remote(self) -> int:
        """Download admitad_ads.json from GitHub URL with httpx."""
        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True
            ) as client:
                response = await client.get(ADMITAD_JSON_URL)
                if response.status_code == 200:
                    data = response.json()
                    count = self._parse_programs(data)
                    if count > 0:
                        self._save_cache(data)
                        self._loaded = True
                        self._last_load_time = time.time()
                        logger.info(
                            "Loaded %d partner programs from remote URL", count
                        )
                        return count
        except Exception as e:
            logger.warning(
                "Failed to load admitad_ads.json from remote: %s", e
            )
        return 0

    def _load_from_local(self) -> int:
        """Load from local cache file."""
        for filepath in [ADMITAD_LOCAL_CACHE, "admitad_ads.json"]:
            path = Path(filepath)
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    count = self._parse_programs(data)
                    self._loaded = True
                    self._last_load_time = time.time()
                    logger.info(
                        "Loaded %d partner programs from local cache: %s",
                        count,
                        filepath,
                    )
                    return count
                except Exception as e:
                    logger.error("Error loading local admitad cache: %s", e)
        logger.warning("No admitad_ads.json found locally or remotely")
        self._loaded = True
        return 0

    def load(self, filepath: str = "") -> int:
        """Synchronous load from local file only. Returns count."""
        filepath = filepath or self.config.ADMITAD_ADS_FILE
        path = Path(filepath)
        if not path.exists():
            path = Path(ADMITAD_LOCAL_CACHE)
        if not path.exists():
            path = Path("admitad_ads.json")

        if not path.exists():
            logger.warning("Partner ads file not found")
            self._loaded = True
            return 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = self._parse_programs(data)
            self._loaded = True
            self._last_load_time = time.time()
            logger.info("Loaded %d partner programs from %s", count, path)
            return count
        except Exception as e:
            logger.error("Error loading partner ads: %s", e)
            self._loaded = True
            return 0

    def _parse_programs(self, data: Any) -> int:
        """Parse programs from JSON data and build site_map."""
        self._programs = []
        self._site_map = {}

        items: list = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("programs", data.get("items", data.get("results", [])))
            if not isinstance(items, list):
                items = []

        for item in items:
            prog = PartnerProgram(item)
            if prog.goto_link:
                self._programs.append(prog)
                if prog.site_url:
                    domain = urlparse(prog.site_url).netloc.replace("www.", "")
                    self._site_map[domain] = prog

        # Sort by partner order priority
        self._programs.sort(
            key=lambda p: PARTNER_ORDER.index(p.name)
            if p.name in PARTNER_ORDER
            else len(PARTNER_ORDER)
        )

        return len(self._programs)

    def _save_cache(self, data: Any) -> None:
        """Save data to local cache."""
        try:
            cache_path = Path(ADMITAD_LOCAL_CACHE)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.warning("Failed to save admitad cache: %s", e)

    # ── Loading lifecycle ───────────────────────────────────────────────────

    def ensure_loaded(self) -> None:
        """Load partner programs if not yet loaded."""
        if not self._loaded:
            self.load()

    async def maybe_refresh(self) -> None:
        """Refresh from remote if enough time has passed."""
        if not self._loaded or (
            time.time() - self._last_load_time > ADMITAD_REFRESH_INTERVAL
        ):
            await self.load_async()

    # ── Query helpers ───────────────────────────────────────────────────────

    def get_by_category(
        self, category: str, region: str = DEFAULT_REGION
    ) -> list[PartnerProgram]:
        """Get programs in a specific category and region."""
        self.ensure_loaded()
        return [
            p
            for p in self._programs
            if p.has_category(category) and p.has_region(region)
        ]

    def get_by_site(self, site: str) -> Optional[PartnerProgram]:
        """Get partner program by site domain."""
        self.ensure_loaded()
        if not site:
            return None

        # Try parsing as URL first
        domain = (
            urlparse(site).netloc.replace("www.", "") if site else ""
        )

        # If urlparse didn't extract a netloc (bare domain like "rossko.ru"),
        # treat the input itself as the domain
        if not domain and site:
            domain = site.replace("www.", "").rstrip("/")

        # Direct lookup via site_map
        result = self._site_map.get(domain)
        if result:
            return result

        # Fallback: partial match on domain keys
        for key, prog in self._site_map.items():
            if domain in key or key in domain:
                return prog

        # Legacy fallback: check name and site fields
        for p in self._programs:
            if site.lower() in (p.site_url or "").lower() or site.lower() in (p.name or "").lower():
                return p
        return None

    def get_all_categories(self) -> list[str]:
        """Get all available categories across programs."""
        self.ensure_loaded()
        cats: set[str] = set()
        for p in self._programs:
            if p.category:
                cats.add(p.category)
            if p.category_name:
                cats.add(p.category_name)
        return sorted(cats)

    def find_matching_programs(
        self, text: str, region: str = DEFAULT_REGION
    ) -> list[PartnerProgram]:
        """Find programs that match keywords in the text."""
        self.ensure_loaded()
        text_lower = text.lower()

        matches: list[PartnerProgram] = []
        # 1. Direct text matching
        for p in self._programs:
            if p.has_region(region) and p.matches_text(text):
                matches.append(p)

        # 2. Category keyword matching
        if not matches:
            category_keywords: dict[str, list[str]] = {
                "autoparts": [
                    "запчаст", "деталь", "артикул", "купить запчас", "купить детал",
                    "оригинал", "аналог", "замена", "подбор", "номер детал",
                    "oem", "оригинальн", "поиск запчас", "найти запчас",
                    "фильтр", "колодки", "свечи", "ремень", "прокладк",
                    "сальник", "подшипник", "амортизатор", "реле", "датчик",
                    "масло", "антифриз", "тормозн", "где купить",
                    "росско", "rossko", "autopiter", "автопитер",
                ],
                "tires": [
                    "шины", "диски", "резина", "колёса", "зимняя", "летняя",
                    "шипованные", "euro-diski", "bs-tyres",
                ],
                "tools": [
                    "инструмент", "ключ", "набор", "гараж", "домкрат", "avtoall",
                ],
                "autoinsurance": [
                    "страховка", "осаго", "каско", "страхование", "полис",
                    "petrolplus", "avtocod",
                ],
                "checkauto": [
                    "проверка", "вин", "vin", "история", "автокод", "пробить",
                    "hyperauto",
                ],
                "autorent": [
                    "аренда", "прокат", "рент", "арендовать", "напрокат",
                    "discovercars", "localrent",
                ],
                "coupons": [
                    "промокод", "скидк", "купон", "акция", "aliexpress",
                    "globaldrive", "koleso", "mirdvornikov", "raketa",
                ],
            }

            for cat, keywords in category_keywords.items():
                if any(kw in text_lower for kw in keywords):
                    cat_programs = self.get_by_category(cat, region)
                    matches.extend(cat_programs)
                    break

        return matches

    # ── Random / selection ──────────────────────────────────────────────────

    def get_random_program(
        self, category: str = "", region: str = DEFAULT_REGION
    ) -> Optional[PartnerProgram]:
        """Get a random partner program, optionally filtered by category."""
        self.ensure_loaded()
        if category:
            pool = self.get_by_category(category, region)
        else:
            pool = [p for p in self._programs if p.has_region(region)]
        if not pool:
            pool = self._programs
        return random.choice(pool) if pool else None

    # ── Posting ─────────────────────────────────────────────────────────────

    def should_post_partner(self) -> bool:
        """Check if we should post a partner post this cycle."""
        now = time.time()
        if now - self._day_start > 86400:
            self._day_start = now
            self._posted_today = 0

        if self._posted_today >= self.config.PARTNER_DAILY_LIMIT:
            return False

        interval = self.config.PARTNER_POST_INTERVAL_HOURS * 3600
        if now - self._last_partner_post_time < interval:
            return False

        return True

    def mark_posted(self) -> None:
        """Mark that a partner post was just made."""
        self._last_partner_post_time = time.time()
        self._posted_today += 1

    # ── Primary parts links ─────────────────────────────────────────────────

    def get_primary_parts_links(
        self, region: str = DEFAULT_REGION
    ) -> list[dict[str, str]]:
        """Get the THREE primary partner links for auto parts in strict order.

        Order: 1) Rossko, 2) Autopiter (RU), 3) AvtoALL
        These are the main links Masha gives in EVERY parts/VIN query.
        """
        self.ensure_loaded()
        links: list[dict[str, str]] = []

        primary_sites = [
            ("rossko.ru", "Росско", "профессиональный подбор запчастей для BMW"),
            ("autopiter.ru", "Autopiter", "крупнейший магазин автозапчастей в России"),
            ("avtoall.ru", "AvtoALL", "автотовары и запчасти"),
        ]

        for site_domain, display_name, description in primary_sites:
            prog = self.get_by_site(site_domain)
            if prog and prog.has_region(region):
                links.append(
                    {
                        "name": display_name,
                        "url": prog.goto_link,
                        "description": description,
                    }
                )

        return links

    def format_primary_parts_links(self, region: str = DEFAULT_REGION) -> str:
        """Format the three primary partner links with BMW descriptions.

        Returns a string like:
        ПАРТНЁРСКИЕ ССЫЛКИ ДЛЯ ЗАПЧАСТЕЙ (давай ВСЕГДА в этом порядке! ...):
        1. Росско (профессиональный подбор запчастей для BMW): https://...
        2. Autopiter (крупнейший магазин автозапчастей в России): https://...
        3. AvtoALL (автотовары и запчасти): https://...
        """
        links = self.get_primary_parts_links(region)
        if not links:
            return ""

        lines = [
            "ПАРТНЁРСКИЕ ССЫЛКИ ДЛЯ ЗАПЧАСТЕЙ (давай ВСЕГДА в этом порядке! Используй КАК ЕСТЬ, ничего не меняй!):",
        ]
        for i, link in enumerate(links, 1):
            lines.append(
                f"{i}. {link['name']} ({link['description']}): {link['url']}"
            )
        lines.append("")
        lines.append(
            "На всех трёх сайтах можно искать по VIN-коду и артикулу запчастей. Есть чаты с подбором запчастей."
        )

        return "\n".join(lines)

    # ── Travel / tools links (Masha-specific) ──────────────────────────────

    def get_travel_links(self, region: str = DEFAULT_REGION) -> list[dict[str, str]]:
        """Get travel-related partner links (Aviasales, Localrent, etc.)."""
        self.ensure_loaded()
        links: list[dict[str, str]] = []
        travel_keywords = [
            "авиа", "avi", "rent", "прокат", "аренд", "ticket", "билет",
            "отель", "hotel", "путешеств", "travel", "тур", "tour",
        ]
        for p in self._programs:
            if not p.has_region(region):
                continue
            if any(
                kw.lower() in p.name.lower() or kw.lower() in p.category.lower()
                for kw in travel_keywords
            ):
                links.append({"name": p.name, "url": p.goto_link or p.affiliate_url})
        return links[:5]

    def get_tools_links(self, region: str = DEFAULT_REGION) -> list[dict[str, str]]:
        """Get tools-related partner links."""
        self.ensure_loaded()
        links: list[dict[str, str]] = []
        tools_keywords = [
            "инструмент", "tool", "220", "всё инструмент", "ремонт", "оборудован",
            "garage", "гараж",
        ]
        for p in self._programs:
            if not p.has_region(region):
                continue
            if any(
                kw.lower() in p.name.lower() or kw.lower() in p.category.lower()
                for kw in tools_keywords
            ):
                links.append({"name": p.name, "url": p.goto_link or p.affiliate_url})
        return links[:5]

    # ── AI context generation ───────────────────────────────────────────────

    def generate_partner_context(
        self, text: str, max_programs: int = 3, region: str = DEFAULT_REGION
    ) -> str:
        """Generate context about matching partner programs for AI to reference
        naturally in its response.

        Uses goto_link from admitad_ads.json EXACTLY as-is.
        No subid additions — the link is ready!
        """
        self.ensure_loaded()

        # Find matching programs
        matches = self.find_matching_programs(text, region)
        if not matches:
            # Default: show autoparts programs for auto-related queries
            text_lower = text.lower()
            auto_keywords = [
                "запчаст", "деталь", "артикул", "купить", "подобрать",
                "масло", "фильтр", "колодки", "ремонт", "сто",
            ]
            if any(kw in text_lower for kw in auto_keywords):
                matches = self.get_by_category("autoparts", region)

        if not matches:
            return ""

        # Check if there's an article number for search-specific links
        article_match = re.search(
            r"\b([A-Z0-9]{4,}[-/]?[A-Z0-9]*)\b", text.upper()
        )
        article = article_match.group(1) if article_match else ""

        lines = [
            "Партнёрские ссылки (Вставь ЕСТЕСТВЕННО в ответ — как рекомендация, не как реклама!):",
        ]

        for p in matches[:max_programs]:
            if article:
                link = p.format_link_with_search(article)
            else:
                link = p.format_link(with_description=True)
            if link:
                lines.append(f"- {link}")

        # Also add Rossko specifically for auto parts queries
        text_lower = text.lower()
        parts_keywords = [
            "запчаст", "деталь", "артикул", "купить запчас", "подбор",
            "фильтр", "колодки", "свечи", "ремень", "масло",
        ]
        if any(kw in text_lower for kw in parts_keywords):
            rossko = self.get_by_site("rossko.ru")
            if rossko and rossko not in matches[:max_programs]:
                if article:
                    lines.append(f"- {rossko.format_link_with_search(article)}")
                else:
                    lines.append(f"- {rossko.format_link(with_description=True)}")

        if len(lines) <= 1:
            return ""

        lines.append("")
        lines.append(
            "ВАЖНО: Ссылки выше — ПАРТНЁРСКИЕ (goto_link из admitad_ads.json). "
            "Используй их КАК ЕСТЬ, ничего не добавляй и не меняй!"
        )

        return "\n".join(lines)

    # ── All relevant links (cross-category) ─────────────────────────────────

    def get_all_relevant_links(
        self, text: str, max_programs: int = 5, region: str = DEFAULT_REGION
    ) -> list[dict[str, str]]:
        """Get ALL relevant partner links across ALL categories for given text.

        Unlike get_primary_parts_links() which only returns autoparts, this method
        detects ALL relevant categories (autoparts, tires, tools, insurance, checkauto, etc.)
        and returns links from ALL matching categories.

        Returns list of dicts with 'name', 'url', 'description' keys.
        """
        self.ensure_loaded()
        links: list[dict[str, str]] = []
        seen_names: set[str] = set()
        text_lower = text.lower()

        # Detect ALL relevant categories based on keywords
        relevant_categories: set[str] = set()

        auto_keywords = [
            "запчаст", "деталь", "артикул", "купить запчас", "купить детал",
            "оригинал", "аналог", "замена", "подбор", "номер детал",
            "oem", "оригинальн", "поиск запчас", "найти запчас",
            "фильтр", "колодки", "свечи", "ремень", "прокладк",
            "сальник", "подшипник", "амортизатор", "реле", "датчик",
            "масло", "антифриз", "тормозн", "где купить",
            "росско", "rossko", "autopiter", "автопитер",
            "vin", "вин", "машина", "авто", "мотор", "двигатель",
            "ремонт", "поломк", "стучит", "диагност",
            "avtoall", "exist", "emex", "autodoc",
        ]
        tire_keywords = [
            "шины", "диски", "резина", "колёса", "зимняя", "летняя",
            "шипованные", "шиповк", "покрышк", "euro-diski", "bs-tyres",
            "сезонная смен", "переобув",
        ]
        tools_keywords = [
            "инструмент", "ключ", "набор", "гараж", "домкрат",
            "avtoall", "подъёмник", "станок",
        ]
        insurance_keywords = [
            "страховка", "осаго", "каско", "страхование", "полис",
            "petrolplus", "автострахов",
        ]
        checkauto_keywords = [
            "проверка", "вин", "vin", "история", "автокод", "пробить",
            "hyperauto", "проверить авто", "история автомобил",
        ]
        rent_keywords = [
            "аренда", "прокат", "рент", "арендовать", "напрокат",
            "discovercars", "localrent",
        ]

        if any(kw in text_lower for kw in auto_keywords):
            relevant_categories.add("autoparts")
        if any(kw in text_lower for kw in tire_keywords):
            relevant_categories.add("tires")
        if any(kw in text_lower for kw in tools_keywords):
            relevant_categories.add("tools")
        if any(kw in text_lower for kw in insurance_keywords):
            relevant_categories.add("autoinsurance")
        if any(kw in text_lower for kw in checkauto_keywords):
            relevant_categories.add("checkauto")
        if any(kw in text_lower for kw in rent_keywords):
            relevant_categories.add("autorent")

        # If no specific category detected, default to autoparts for car queries
        if not relevant_categories:
            car_kw = [
                "авто", "машина", "машин", "двигатель", "мотор", "car", "auto",
                "кузов", "ходов", "подвеск", "тормоз", "руль", "коробк",
            ]
            if any(kw in text_lower for kw in car_kw):
                relevant_categories.add("autoparts")
                relevant_categories.add("tires")
                relevant_categories.add("tools")
                relevant_categories.add("checkauto")

        # Collect programs from all relevant categories
        for cat in relevant_categories:
            cat_programs = self.get_by_category(cat, region)
            for p in cat_programs:
                if p.name not in seen_names and p.goto_link:
                    seen_names.add(p.name)
                    desc = p._get_category_description()
                    links.append(
                        {
                            "name": p.name,
                            "url": p.goto_link,
                            "description": f"{p.category_name} — {desc}",
                        }
                    )

        # Always ensure primary autoparts links are included if autoparts is relevant
        if "autoparts" in relevant_categories:
            primary_sites = ["rossko.ru", "autopiter.ru", "avtoall.ru"]
            for site in primary_sites:
                prog = self.get_by_site(site)
                if prog and prog.name not in seen_names and prog.goto_link:
                    seen_names.add(prog.name)
                    desc = prog._get_category_description()
                    links.append(
                        {
                            "name": prog.name,
                            "url": prog.goto_link,
                            "description": f"{prog.category_name} — {desc}",
                        }
                    )

        return links[:max_programs]

    # ── Partner post generation (Masha BMW voice) ──────────────────────────

    async def generate_partner_post_content(
        self, program: Optional[PartnerProgram] = None
    ) -> str:
        """Generate a partner post text with Маша's BMW voice."""
        if not program:
            program = self.get_random_program()
        if not program:
            return ""
        return self._build_partner_text(program)

    def _build_partner_text(self, program: PartnerProgram) -> str:
        """Build a partner post text in Маша's BMW voice."""
        link = program.goto_link  # Use goto_link as-is!
        cat_label = program.category_name or "авто"
        discount_text = (
            f"Промокод: {program.promo_code} — {program.discount}"
            if program.promo_code
            else f"Скидки до {program.discount}"
            if program.discount
            else "Специальные цены для владельцев BMW"
        )

        templates = [
            (
                f"🔧 Запчасти для вашего BMW — вопрос не эстетики, а выживания. "
                f"Особенно если у вас N55\n\n"
                f"{program.name} — {program.description}\n\n"
                f"{discount_text}\n\n"
                f"👉 {link}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
            (
                f"Серёга говорит: 'Оригинал или хороший аналог — третьего не дано'. "
                f"В этом он прав (редко, но бывает)\n\n"
                f"{program.name} — {cat_label}\n\n"
                f"{discount_text}\n\n"
                f"👉 {link}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
            (
                f"Ваш ///M заслуживает лучших запчастей. "
                f"Даже Доктор Ван Дамм одобряет\n\n"
                f"{program.name} — {cat_label}\n\n"
                f"{discount_text}\n\n"
                f"👉 {link}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
            (
                f"S63 утром здоровее, чем вся линейка конкурентов — "
                f"но только если кормишь его качественными запчастями\n\n"
                f"{program.name} — {cat_label}\n\n"
                f"{discount_text}\n\n"
                f"👉 {link}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
            (
                f"Редакция протестировала {program.name} — {cat_label}. "
                f"Вердикт: bimmer-одобрено ✓\n\n"
                f"{discount_text}\n\n"
                f"👉 {link}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
            (
                f"Пока Кинг Конг орёт '///M-Power!' с жёрдочки, "
                f"я нашла вам нормальный {cat_label.lower()}\n\n"
                f"{program.name} — проверено редакцией\n\n"
                f"{discount_text}\n\n"
                f"👉 {link}\n\n"
                f"Автор @asmasha_bot\n"
                f"@bmw_mpower_club\n"
                f"#bmw_mpower_club #bmwparts"
            ),
        ]

        return random.choice(templates)


# ── Global singleton instance ────────────────────────────────────────────────

partner_manager = PartnerManager()
