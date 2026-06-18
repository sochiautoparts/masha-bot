"""
Inline Handler — @asmasha_bot inline mode.
Users can type @asmasha_bot <query> in any chat to get BMW-expert responses.
Supports: general questions, VIN decoding, BMW diagnostics, spare part search.
"""

import logging
import hashlib
from typing import Optional, List

from aiogram import Router, F, types
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    InputTextMessageContent,
    ChosenInlineResult,
)

from bot.config import config, persona
from bot.masha_data import (
    is_part_number, identify_car_brand, detect_symptoms,
    detect_obd2_codes, lookup_obd2_code,
)
from bot.partners import partner_manager
from ai.router import get_ai_router
from ai.providers.provider_manager import ROUTE_CHAT, ROUTE_FUNCTION

logger = logging.getLogger("masha.handlers.inline")

inline_router = Router()

# ── VIN detection for inline ─────────────────────────────────────────────────

import re

_VIN_PATTERN = re.compile(r'\b[A-HJ-NPR-Z0-9]{17}\b', re.IGNORECASE)


def _detect_vin_inline(text: str) -> Optional[str]:
    """Detect a VIN code in inline query text."""
    match = _VIN_PATTERN.search(text.upper())
    if match:
        vin = match.group(0)
        if len(vin) == 17 and vin[8] in '0123456789X':
            return vin
    return None


def _detect_query_type(text: str) -> str:
    """Detect what type of query this is for inline mode."""
    text_lower = text.lower().strip()

    # VIN decoding
    if _detect_vin_inline(text) or any(kw in text_lower for kw in ["vin", "вин", "vin-код", "вин код"]):
        return "vin"

    # OBD-II codes
    if detect_obd2_codes(text):
        return "obd2"

    # Spare parts
    if is_part_number(text.strip()) or any(kw in text_lower for kw in [
        "запчасть", "артикул", "купить деталь", "номер детали", "oem",
    ]):
        return "parts"

    # Diagnostics
    if detect_symptoms(text) or any(kw in text_lower for kw in [
        "не заводится", "стук", "вибрация", "перегрев", "чек", "check engine",
        "диагностика", "поломка", "проблема с", "горит", "vanos",
    ]):
        return "diagnostic"

    # Default: general chat
    return "general"


# ── Inline query handler ──────────────────────────────────────────────────────

@inline_router.inline_query()
async def handle_inline_query(inline_query: InlineQuery):
    """Handle inline queries — @asmasha_bot <query>."""
    query = inline_query.query.strip()

    if not query:
        results = [
            InlineQueryResultArticle(
                id="help",
                title="Маша — BMW-эксперт",
                description="Введите вопрос, VIN-код, артикул запчасти или опишите проблему",
                input_message_content=InputTextMessageContent(
                    message_text=(
                        "🏎️ Маша — ваш BMW-эксперт!\n\n"
                        "Я могу помочь с:\n"
                        "• Расшифровкой VIN — отправьте VIN (WBA/WBS = BMW!)\n"
                        "• Диагностикой BMW — опишите проблему\n"
                        "• Поиском запчастей — укажите артикул\n"
                        "• Вопросами о BMW и M Power — просто спросите\n\n"
                        "Напишите @asmasha_bot и ваш вопрос!"
                    ),
                ),
                thumbnail_url="https://cdn-icons-png.flaticon.com/128/3097/3097180.png",
            ),
        ]
        await inline_query.answer(results, cache_time=30)
        return

    query_type = _detect_query_type(query)

    try:
        response, partner_links = await _generate_inline_response(query, query_type)

        if response.error or not response.text:
            results = [
                InlineQueryResultArticle(
                    id="error",
                    title="Не удалось получить ответ",
                    description="Попробуйте переформулировать вопрос",
                    input_message_content=InputTextMessageContent(
                        message_text=f"🏎️ Вопрос: {query}\n\n⚠️ Не удалось получить ответ от Маши. Напишите в личку @asmasha_bot",
                    ),
                ),
            ]
            await inline_query.answer(results, cache_time=10)
            return

        reply_text = _clean_markdown(response.text)
        reply_text = _replace_plain_urls_with_affiliate_inline(reply_text)

        # Append partner links section so the published inline message carries
        # the correct goto_links from partners.json (used EXACTLY as-is).
        if partner_links:
            section = _format_inline_partner_links(partner_links)
            if section:
                reply_text = reply_text.rstrip() + "\n\n" + section

        if len(reply_text) > 4000:
            reply_text = reply_text[:3997] + "..."

        results = _build_inline_results(query, reply_text, query_type)

        await inline_query.answer(results, cache_time=60, is_personal=True)

    except Exception as e:
        logger.error(f"Inline query error: {e}")
        results = [
            InlineQueryResultArticle(
                id="error",
                title="Ошибка обработки",
                description="Попробуйте ещё раз",
                input_message_content=InputTextMessageContent(
                    message_text=f"🏎️ Вопрос: {query}\n\n⚠️ Произошла ошибка. Напишите в личку @asmasha_bot",
                ),
            ),
        ]
        await inline_query.answer(results, cache_time=10)


async def _generate_inline_response(query: str, query_type: str):
    """Generate AI response for inline query.

    Returns a tuple: (AIResponse, partner_links) where partner_links is a list
    of (name, goto_link) tuples to be appended to the inline result so the
    bot monetizes parts/VIN/diagnostic queries even in inline mode.
    """
    inline_user_id = 0

    # ── Build partner context + collect links for the dialogue ──────────
    # Inline mode previously had ZERO partner integration. Now we inject the
    # full partner context (goto_links from partners.json) for relevant query
    # types so the AI can reference them naturally, AND we append a clean
    # "🔗 Где искать:" section to the published inline message.
    partner_context_parts: list[str] = []
    partner_links: list[tuple[str, str]] = []
    try:
        await partner_manager.maybe_refresh()
        if query_type in ("vin", "parts", "diagnostic", "obd2"):
            # Auto-related queries → inject primary parts links + cross-category context
            try:
                primary = partner_manager.format_primary_parts_links()
                if primary:
                    partner_context_parts.append(primary)
            except Exception:
                pass
            try:
                ctx = partner_manager.generate_partner_context(query, max_programs=3)
                if ctx:
                    partner_context_parts.append(ctx)
            except Exception:
                pass
            try:
                for pl in partner_manager.get_all_relevant_links(query, max_programs=5):
                    partner_links.append((pl["name"], pl["url"]))
            except Exception:
                pass
        else:
            # General chat → only add partners if the query actually mentions
            # shopping/parts/travel/tools (context-aware, not spammy)
            q_lower = query.lower()
            shopping_kw = [
                "запчаст", "деталь", "купить", "заказать", "артикул", "масло",
                "фильтр", "колодки", "шины", "диски", "инструмент", "аренд",
                "прокат", "билет", "страхов", " vin", "вин",
            ]
            if any(kw in q_lower for kw in shopping_kw):
                try:
                    ctx = partner_manager.generate_partner_context(query, max_programs=2)
                    if ctx:
                        partner_context_parts.append(ctx)
                except Exception:
                    pass
                try:
                    for pl in partner_manager.get_all_relevant_links(query, max_programs=3):
                        partner_links.append((pl["name"], pl["url"]))
                except Exception:
                    pass
    except Exception as e:
        logger.debug(f"Inline partner context error: {e}")

    partner_context = "\n\n".join(p for p in partner_context_parts if p)

    if query_type == "vin":
        vin_code = _detect_vin_inline(query) or query.strip()
        response = await get_ai_router().decode_vin(
            user_id=inline_user_id,
            vin_code=vin_code,
            extra_context=partner_context,
        )
        return response, partner_links

    elif query_type == "diagnostic":
        response = await get_ai_router().diagnose_car(
            user_id=inline_user_id,
            symptoms=query,
            extra_context=partner_context,
        )
        return response, partner_links

    elif query_type == "parts":
        response = await get_ai_router().find_spare_part(
            user_id=inline_user_id,
            article=query.strip(),
            extra_context=partner_context,
        )
        return response, partner_links

    else:
        messages = [
            {"role": "system", "content": "Ты Маша, BMW-эксперт. Отвечай кратко и живо."},
        ]
        if partner_context:
            messages.append({"role": "user", "content": f"Контекст:\n{partner_context}"})
        messages.append({"role": "user", "content": query})
        response = await get_ai_router().chat(
            messages=messages,
            use_cache=True,
            max_tokens=1000,
            route_type=ROUTE_CHAT,
        )
        return response, partner_links


def _build_inline_results(query: str, reply_text: str, query_type: str) -> List:
    """Build inline query results with contextual titles."""
    results = []

    title_map = {
        "vin": "🔓 Расшифровка VIN (BMW: WBA/WBS)",
        "diagnostic": "🔧 Диагностика BMW",
        "parts": "🔍 Запчасть для BMW",
        "obd2": "📊 Код ошибки",
        "general": "🏎️ Ответ Маши",
    }

    desc_map = {
        "vin": f"Расшифровка: {query[:30]}",
        "diagnostic": "Возможные причины и рекомендации",
        "parts": f"Информация по: {query[:30]}",
        "obd2": "Описание ошибки и решения",
        "general": f"Ответ на: {query[:40]}",
    }

    main_id = hashlib.md5(f"main_{query}".encode()).hexdigest()[:16]

    results.append(
        InlineQueryResultArticle(
            id=main_id,
            title=title_map.get(query_type, "🏎️ Ответ Маши"),
            description=desc_map.get(query_type, ""),
            input_message_content=InputTextMessageContent(
                message_text=reply_text,
            ),
            thumbnail_url="https://cdn-icons-png.flaticon.com/128/3097/3097180.png",
        )
    )

    # Short version
    short_text = reply_text
    if len(short_text) > 200:
        break_pos = short_text.rfind('\n', 0, 200)
        if break_pos < 100:
            break_pos = short_text.rfind('. ', 0, 200)
        if break_pos < 100:
            break_pos = 197
        short_text = short_text[:break_pos].rstrip() + "..."

    short_id = hashlib.md5(f"short_{query}".encode()).hexdigest()[:16]

    results.append(
        InlineQueryResultArticle(
            id=short_id,
            title="📝 Краткий ответ",
            description=short_text[:80],
            input_message_content=InputTextMessageContent(
                message_text=short_text,
            ),
        )
    )

    # VIN detailed option
    if query_type == "vin":
        detail_id = hashlib.md5(f"detail_{query}".encode()).hexdigest()[:16]
        results.append(
            InlineQueryResultArticle(
                id=detail_id,
                title="📋 Подробная расшифровка VIN",
                description="BMW: модель, год, двигатель, комплектация",
                input_message_content=InputTextMessageContent(
                    message_text=reply_text,
                ),
            )
        )

    return results


@inline_router.chosen_inline_result()
async def handle_chosen_inline_result(chosen: ChosenInlineResult):
    """Log when a user selects an inline result."""
    logger.info(
        f"Inline result chosen: query='{chosen.query}', "
        f"result_id='{chosen.result_id}', "
        f"user={chosen.from_user.id}"
    )


def _format_inline_partner_links(links: list) -> str:
    """Format partner links as a clean section for inline results.

    Uses the goto_link from partners.json EXACTLY as-is (no modifications).
    """
    if not links:
        return ""
    # De-duplicate by name while preserving order
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for name, url in links:
        if name not in seen and url:
            seen.add(name)
            unique.append((name, url))
    if not unique:
        return ""
    lines = ["🔗 Где искать:"]
    for name, url in unique[:4]:
        lines.append(f"• {name}: {url}")
    return "\n".join(lines)


def _replace_plain_urls_with_affiliate_inline(text: str) -> str:
    """Replace plain partner site URLs with affiliate goto_links for ALL partners.

    Iterates the full partner site_map (all campaigns from partners.json),
    so any plain merchant URL the AI emits is rewritten to the correct
    goto_link from the source.
    """
    try:
        for site_domain, prog in partner_manager._site_map.items():
            if not prog.goto_link or prog.goto_link in text:
                continue
            for variant in (f"https://{site_domain}", f"http://{site_domain}",
                            f"https://www.{site_domain}", f"http://www.{site_domain}"):
                if variant in text:
                    text = text.replace(variant, prog.goto_link)
                    break
    except Exception:
        pass
    return text


def _clean_markdown(text: str) -> str:
    """Remove markdown formatting for inline responses."""
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'```[\s\S]*?```', lambda m: m.group(0).strip('`').strip(), text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*]\s+', '— ', text, flags=re.MULTILINE)
    return text
