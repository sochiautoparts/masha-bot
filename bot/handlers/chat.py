"""
Chat Handler — Main user interaction with AI, web search, partner links,
BMW car diagnostics, spare part search, VIN decoding, photo analysis,
and personalized communication with Masha's BMW-expert persona.

v18: GROUP CONVERSATION SUPPORT — Маша replies to other participants in
group chats and channel comment threads when:
  - she is @mentioned (@asmasha_bot)
  - someone replies to one of her messages
  - the message is a comment on a channel post (discussion group) AND it's
    BMW-relevant (so she participates naturally in the conversation)
She applies her full BMW knowledge + the conversation context (last N
messages with author names from chat_history) to every reply.
"""

import re
import logging
import base64
import time
from typing import List, Optional

from aiogram import Router, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, PhotoSize, WebAppInfo
from aiogram.enums import ChatAction
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import config, persona
from bot.database import (
    get_or_create_user, is_user_blocked, add_chat_message,
    clear_chat_history, get_chat_mode, set_chat_mode,
    add_user_car, get_user_cars, delete_user_car, update_car_mileage,
    check_rate_limit, get_chat_history,
)
from bot.masha_data import (
    is_part_number, extract_part_numbers, identify_car_brand,
    detect_symptoms, detect_obd2_codes, lookup_obd2_code,
    build_diagnostic_context, MASHA_PHRASES,
)
from bot.web_search import web_search, search_spare_part, search_parts_by_vin, format_search_results
from bot.tech_docs import (
    search_part_by_article, search_diagnostic_code,
    search_repair_procedure, format_part_info, format_tech_context,
)
from bot.partners import partner_manager
from ai.router import get_ai_router, MASHA_SYSTEM_PROMPT
from ai.providers.provider_manager import ROUTE_CHAT, ROUTE_COMMENT, ROUTE_FUNCTION
from ai.voice import process_voice_message

logger = logging.getLogger("masha.handlers.chat")

chat_router = Router()

# ── Character limits for chat responses ──────────────────────────────────────
CHAT_MAX_CHARS = 1500
GROUP_MAX_CHARS = 600
COMMENT_MAX_CHARS = 300

# ── VIN / Body number detection ───────────────────────────────────────────────

_VIN_PATTERN = re.compile(r'\b[A-HJ-NPR-Z0-9]{17}\b', re.IGNORECASE)
_VIN_FLEX_PATTERN = re.compile(
    r'(?:VIN[-:]?\s*|вин[-:]?\s*|вин-код[-:]?\s*)?([A-HJ-NPR-Z0-9](?:[A-HJ-NPR-Z0-9\s\-]{14,22})[A-HJ-NPR-Z0-9])',
    re.IGNORECASE
)
_BODY_NUMBER_PATTERN = re.compile(
    r'(?:номер\s+кузова|кузовн?ой\s+номер|body\s*number|кузов)\s*[:\s]*([A-Z0-9\-/]{5,20})',
    re.IGNORECASE
)


def _detect_vin(text: str) -> Optional[str]:
    """Detect a VIN code (17 chars) in text."""
    match = _VIN_PATTERN.search(text.upper())
    if match:
        vin = match.group(0)
        if len(vin) == 17 and vin[8] in '0123456789X':
            return vin
    return None


def _detect_body_number(text: str) -> Optional[str]:
    """Detect a body number reference in text."""
    match = _BODY_NUMBER_PATTERN.search(text)
    if match:
        return match.group(1)
    return None


def _is_vin_query(text: str) -> bool:
    """Check if text is asking about VIN/body number decoding."""
    text_lower = text.lower()
    keywords = [
        "vin", "вин", "номер кузова", "кузовной номер", "расшифруй vin",
        "расшифруй вин", "пробей vin", "пробей вин", "декодировать vin",
        "vin код", "вин код", "vin-код", "вин-код",
        "что за vin", "что за вин", "какая машина vin", "какая машина вин",
        "какой автомобиль vin", "определи vin", "определи вин",
        "что за машина vin", "проверь vin", "проверь вин",
        "история vin", "история автомобиля", "пробить машину",
    ]
    return any(kw in text_lower for kw in keywords)


# ── Chat-context detection (Opt 6 / 7 / 8) ───────────────────────────────────
#
# Builds a list of context lines that tell the AI:
#   - whether it's replying in a private chat or a group/comment thread
#     (so it shapes the answer — short & punchy in groups, detailed in DMs);
#   - when the user's message is a reply to a channel post in the linked
#     discussion group, the ORIGINAL post text (so the reply is on-topic);
#   - when the chat is a forum-style supergroup, the active topic
#     (message_thread_id) so the AI is aware of the thread context.

def _detect_chat_context(message: Message) -> list[str]:
    """Detect chat-type / channel-comment / forum-topic context for the AI.

    Returns a list of short lines to be joined and prepended to extra_context.
    Never raises — always returns a (possibly empty) list.
    """
    parts: list[str] = []
    try:
        chat = message.chat
        chat_type = getattr(chat, "type", "private")

        # ── Opt 6: chat-type awareness ──
        if chat_type in ("group", "supergroup"):
            parts.append(
                "Отвечаешь в ГРУППЕ/комментариях — пиши КОРОТКО и живо "
                "(до ~600 символов), без длинных вступлений. Можно эмодзи."
            )
        else:
            parts.append(
                "Отвечаешь в ЛИЧКЕ — можно подробнее, развёрнуто и с примерами."
            )

        # ── Opt 8: forum topic awareness ──
        # Forum supergroups carry is_forum=True; each topic has its own
        # message_thread_id. The "General" topic uses thread_id == 1.
        is_forum = bool(getattr(chat, "is_forum", False))
        thread_id = getattr(message, "message_thread_id", None)
        if is_forum and thread_id:
            topic_name = _resolve_forum_topic_name(message, thread_id)
            if topic_name:
                parts.append(
                    f"Это форум-супергруппа, тема «{topic_name}» "
                    f"(thread_id={thread_id}). Отвечай по теме."
                )
            else:
                parts.append(
                    f"Это форум-супергруппа, тема thread_id={thread_id}. "
                    "Отвечай строго по теме обсуждения."
                )

        # ── Opt 7: channel-post comment detection ──
        # When a user replies to a channel post in the linked discussion group,
        # message.reply_to_message is the forwarded/sent copy of that channel
        # post. Use its text as context so the AI's reply is on-topic.
        reply = getattr(message, "reply_to_message", None)
        if reply is not None:
            original_text = _extract_original_post_text(reply)
            if original_text:
                snippet = original_text[:500].replace("\n", " ")
                parts.append(
                    "Это ответ (комментарий) на пост в канале. "
                    "Оригинальный пост:\n"
                    f"«{snippet}»\n"
                    "Отвечай по существу этого поста."
                )
    except Exception as e:
        logger.debug(f"_detect_chat_context error: {e}")

    return parts


def _resolve_forum_topic_name(message: Message, thread_id: int) -> str:
    """Best-effort resolve of a forum topic name by its thread_id.

    aiogram doesn't expose the topic list directly on a Message, so we return
    "" when unknown. A future enhancement could cache the forum's topic map via
    bot.get_forum_topic_icon_set / bot.get_chat. For now, empty is fine — the
    caller still passes thread_id to the AI.
    """
    # The "General" topic is conventionally thread_id == 1.
    if thread_id == 1:
        return "General"
    return ""


def _extract_original_post_text(reply: Message) -> str:
    """Extract the text of the original channel post a reply is attached to.

    Handles three cases in Telegram discussion groups:
      1. reply is a forwarded copy of the channel post → reply.text / caption
      2. reply was sent by the channel itself (reply.sender_chat is the channel)
      3. reply is from the bot (reply.from_user.is_bot) quoting a channel post
    Returns "" when the reply doesn't look like a channel-post reference.
    """
    try:
        # Case: the reply itself has text/caption
        candidate = (reply.text or reply.caption or "").strip()
        if not candidate:
            return ""
        # Heuristic: treat as a channel-post reference if the reply's sender is
        # the channel (sender_chat) or a bot (the bot reposting), OR if the chat
        # is a supergroup (discussion groups are supergroups).
        is_channel_sender = bool(getattr(reply, "sender_chat", None))
        is_bot_sender = bool(getattr(reply.from_user, "is_bot", False)) if reply.from_user else False
        is_supergroup = getattr(reply.chat, "type", "") == "supergroup"
        if is_channel_sender or is_bot_sender or is_supergroup:
            return candidate
    except Exception:
        pass
    return ""


# ════════════════════════════════════════════════════════════════════════════
# v18: GROUP CONVERSATION SUPPORT
# ════════════════════════════════════════════════════════════════════════════
# Маша участвует в беседах групп и комментариев к постам канала. Триггеры:
#   1. @asmasha_bot упоминание (или @asmasha_bot в entities)
#   2. Reply на сообщение Маши (reply_to_message.from_user.is_bot и это Маша)
#   3. В дискуссионной группе канала: BMW-релевантный комментарий к посту
#      (мягкий триггер — чтобы Маша естественно участвовала в обсуждении постов)
# Перед ответом Маша загружает последние N сообщений беседы из chat_history
# (с именами авторов) и применяет ВСЕ свои знания (BMW knowledge, partners,
# web search) к контексту. Rate-limit: 1 ответ/30сек на группу (анти-спам).

# Per-group rate limiting: {chat_id: last_reply_timestamp}
_group_reply_cooldown: dict[int, float] = {}
_GROUP_COOLDOWN_SECONDS = 12  # Маша отвечает не чаще чем раз в 12 сек на группу
_GROUP_HISTORY_LIMIT = 12     # сколько последних сообщений брать в контекст группы
_PRIVATE_HISTORY_LIMIT = 10   # v18.2: сколько последних сообщений брать в личке

# v18.1: Probabilities for casual participation (when not explicitly triggered).
# Маша участвует в беседе живо — не только по @mention, но и присоединяется к
# разговорам, реагирует на BMW-тематику, иногда вступает в обычную болтовню.
_PARTICIPATE_PROB_BMW = 0.55   # 55% — BMW-релевантное сообщение без reply → вступает
_PARTICIPATE_PROB_CASUAL = 0.18  # 18% — обычная болтовня без reply → иногда вступает

# BMW-relevance keywords for the soft group trigger (channel comments)
_BMW_TRIGGER_KEYWORDS = [
    "bmw", "бмв", "бимер", "баварец", "///m", "m power", "mpower",
    "m3", "m4", "m5", "m2", "m8", "x5", "x6", "x7", "x3", "x4",
    "s63", "s58", "s55", "b58", "n55", "b48", "n54", "s68",
    "vanos", "valvetronic", "xdrive", "alpina",
    "m5 f90", "e30", "e46", "e39", "f80", "f82", "g80", "g82", "g87",
    "ista", "bimmercode", "realoem", "inpa",
    "запчаст", "vin", "вин", "колодк", "фильтр", "масло",
    "ванос", "стучит", "троит", "чек", "diagnostic",
    "независ", "m performance", "individual",
    # v18.1: broader car/automotive terms so Маша вступает в авто-беседы
    "машина", "авто", "двигатель", "мотор", "коробка", "кузов",
    "тормоз", "подвеска", "руль", "турбина", "седан", "кроссовер",
    "пробег", "сервис", "ремонт", "сто", "оил", "дрифт", "трек",
]


def _is_group_chat(message: Message) -> bool:
    """True if the message is from a group or supergroup (not private, not channel)."""
    try:
        return getattr(message.chat, "type", "private") in ("group", "supergroup")
    except Exception:
        return False


def _is_mentioned(message: Message) -> bool:
    """True if Маша is @mentioned in the message text or entities."""
    text = message.text or message.caption or ""
    # 1. Plain-text mention
    if re.search(r'@asmasha_bot', text, re.IGNORECASE):
        return True
    # 2. Telegram entity mention (resolves username → bot id)
    try:
        for ent in message.entities or []:
            if ent.type == "mention":
                mention_text = text[ent.offset:ent.offset + ent.length]
                if mention_text.lower() == "@asmasha_bot":
                    return True
            if ent.type == "text_mention" and ent.user and ent.user.is_bot:
                # text_mention resolves to a user object; if it's a bot, likely us
                return True
    except Exception:
        pass
    return False


def _is_reply_to_masha(message: Message) -> bool:
    """True if the message is a reply to one of Маша's messages (not a channel post).

    Note: a reply to a channel-post COPY (sender_chat set) is NOT a reply to
    Маша — that's a channel-comment discussion and is handled by trigger 3
    (channel_comment_bmw). We only return True when the reply target was sent
    by a bot (Маша is the only active bot in our groups).
    """
    try:
        reply = getattr(message, "reply_to_message", None)
        if reply is None:
            return False
        # Reply target is from a bot — in our groups the only active bot is Маша
        if reply.from_user and getattr(reply.from_user, "is_bot", False):
            return True
    except Exception:
        pass
    return False


def _is_bmw_relevant(text: str) -> bool:
    """Soft BMW-relevance check for the channel-comment trigger."""
    if not text:
        return False
    t = text.lower()
    return any(kw in t for kw in _BMW_TRIGGER_KEYWORDS)


def _is_reply_in_conversation(message: Message) -> bool:
    """True if the message is a reply to ANY message in the group.

    v18.1: Маша присоединяется к ЛЮБОЙ ветке разговора — не только когда
    отвечают ей, но и когда кто-то отвечает другому участнику. Это делает
    её полноправным участником беседы, а не пассивным наблюдателем.
    """
    try:
        reply = getattr(message, "reply_to_message", None)
        if reply is None:
            return False
        # Any reply target (user, bot, or channel-post copy) counts as a
        # conversation thread Маша can join.
        return True
    except Exception:
        return False


def _group_should_reply(message: Message) -> tuple[bool, str]:
    """Decide whether Маша should reply in a group, and why.

    v18.1: Маша теперь активно участвует в беседах, а не только отвечает на
    прямые обращения. Триггеры (по приоритету):

      1. @asmasha_bot упоминание → ВСЕГДА отвечает (адресовано ей)
      2. Reply на ЛЮБОЕ сообщение в группе → ВСЕГДА отвечает (присоединяется
         к ветке разговора — даже если отвечают другому участнику)
      3. BMW/авто-релевантное сообщение (без reply) → 55% вероятность
         (вступает в BMW-обсуждение)
      4. Обычная болтовня (без reply, не BMW) → 18% вероятность
         (иногда вступает в casual-беседу — живой участник, не спамер)

    Rate-limit (12 сек на группу) защищает от спама: даже если триггер
    сработал, Маша не ответит дважды за 12 секунд.

    Returns (should_reply, reason). Reason is logged + used for analytics.
    """
    import random

    text = message.text or message.caption or ""

    # Trigger 1: explicit @mention — highest priority, always reply
    if _is_mentioned(message):
        return True, "mention"

    # Trigger 2: reply to ANY message in a conversation thread — always join.
    # v18.1: расширили с "reply to Маша" до "reply to anyone" — Маша теперь
    # участвует в беседах, а не только отвечает на обращения к ней.
    if _is_reply_in_conversation(message):
        # Sub-case: reply to Маша specifically (continue her own dialogue)
        if _is_reply_to_masha(message):
            return True, "reply_to_masha"
        # Reply to another participant → join the conversation
        return True, "conversation_join"

    # Trigger 3: BMW/auto-relevant standalone message → probabilistic
    if _is_bmw_relevant(text):
        if random.random() < _PARTICIPATE_PROB_BMW:
            return True, "bmw_chatter"
        return False, "bmw_skipped_prob"

    # Trigger 4: casual chatter → low probability (живое участие, не спам)
    if random.random() < _PARTICIPATE_PROB_CASUAL:
        return True, "casual_participation"

    return False, "no_trigger"


def _group_rate_limited(chat_id: int) -> bool:
    """True if Маша replied to this group too recently (anti-spam)."""
    now = time.time()
    last = _group_reply_cooldown.get(chat_id, 0)
    if now - last < _GROUP_COOLDOWN_SECONDS:
        return True
    _group_reply_cooldown[chat_id] = now
    return False


def _display_name(user) -> str:
    """Best-effort display name for a Telegram user (for the conversation transcript)."""
    try:
        first = getattr(user, "first_name", "") or ""
        last = getattr(user, "last_name", "") or ""
        username = getattr(user, "username", "") or ""
        if first and last:
            return f"{first} {last}"
        if first:
            return first
        if username:
            return f"@{username}"
        return "Участник"
    except Exception:
        return "Участник"


def _format_conversation_transcript(history: list[dict], current_author: str, current_text: str) -> str:
    """Format chat_history into a readable transcript for the AI.

    Each line: «Имя: текст». Маша's own previous replies are marked as «Маша:».
    The current message is appended as the last line so the AI sees it in
    context. Skips the thinking-status messages Маша posts («Слушаю...» etc.).
    """
    lines: list[str] = []
    _THINKING_PHRASES = (
        "секунд", "слушаю", "думаю", "ищу", "проверяю", "смотрю", "греди",
    )
    for row in history:
        role = row.get("role", "user")
        content = (row.get("content") or "").strip()
        if not content:
            continue
        # Skip Маша's short "thinking" status messages
        if role == "assistant" and len(content) < 40 and any(p in content.lower() for p in _THINKING_PHRASES):
            continue
        author = row.get("author_name") or ("Маша" if role == "assistant" else "Участник")
        lines.append(f"{author}: {content}")
    # Append the current message (not yet in DB when this is called)
    if current_text:
        lines.append(f"{current_author}: {current_text}")
    return "\n".join(lines)


# ── Gender detection from Russian first name ────────────────────────────────

MALE_NAME_ENDINGS = ("й", "ь", "н", "л", "р", "с", "т", "в", "к", "м", "г", "б", "д", "п", "з", "ж", "х")
FEMALE_NAME_ENDINGS = ("а", "я", "ия", "ья", "ина")

COMMON_MALE_NAMES = {
    "александр", "дмитрий", "максим", "сергей", "андрей", "алексей", "артём",
    "илья", "кирилл", "михаил", "никита", "матвей", "роман", "егор", "арсений",
    "иван", "денис", "евгений", "даниил", "тимур", "владимир", "олег", "павел",
}

COMMON_FEMALE_NAMES = {
    "анна", "мария", "ольга", "елена", "наталья", "татьяна", "ирина", "светлана",
    "екатерина", "юлия", "дарья", "алина", "вера", "полина", "кристина", "софия",
    "валерия", "марина", "людмила", "надежда", "настя", "анастасия",
    "виктория", "маргарита", "диана", "евгения", "алёна", "катерина",
}


def _guess_gender(first_name: str) -> str:
    """Guess gender from Russian first name."""
    if not first_name:
        return "unknown"
    name_lower = first_name.lower().strip()
    if name_lower in COMMON_MALE_NAMES:
        return "male"
    if name_lower in COMMON_FEMALE_NAMES:
        return "female"
    if name_lower.endswith(FEMALE_NAME_ENDINGS):
        if name_lower.endswith("ь"):
            pass
        else:
            return "female"
    if name_lower.endswith("й") or name_lower.endswith("ь"):
        return "male"
    return "unknown"


def _get_user_persona_context(message: Message) -> str:
    """Build a context string about the user for personalized communication."""
    parts = []
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
    username = message.from_user.username or ""

    if first_name:
        parts.append(f"Имя пользователя: {first_name}")
    if last_name:
        parts.append(f"Фамилия: {last_name}")
    if username:
        parts.append(f"Username: @{username}")

    gender = _guess_gender(first_name)
    if gender == "male":
        parts.append("Пол: скорее всего мужчина")
    elif gender == "female":
        parts.append("Пол: скорее всего женщина")

    if message.from_user.id == config.OWNER_ID:
        parts.append("Это владелец бота — общайся тепло и уважительно")

    if parts:
        return "Информация о пользователе для персонализации общения:\n" + "\n".join(parts)
    return ""


# ── Middleware-like: check user and log ─────────────────────────────────────────

async def _check_user(message: Message) -> bool:
    """Check if user is allowed to interact."""
    user = await get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
        language_code=message.from_user.language_code or "ru",
    )

    if await is_user_blocked(message.from_user.id):
        return False

    if not check_rate_limit(message.from_user.id):
        await message.answer("Ты слишком быстро пишешь! Дай мне секунду 🏎️")
        return False

    return True


# ── /start command ─────────────────────────────────────────────────────────────

@chat_router.message(CommandStart())
async def cmd_start(message: Message):
    """Handle /start command — greet like a BMW enthusiast."""
    if not await _check_user(message):
        return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    name = message.from_user.first_name or ""
    gender = _guess_gender(name)

    import random
    from datetime import datetime
    from zoneinfo import ZoneInfo
    hour = datetime.now(ZoneInfo("Europe/Moscow")).hour

    if name:
        if gender == "male":
            greets = [
                f"Привет, {name}! 😊 Маша тут. BMW — наша страсть!",
                f"Хей, {name}! ///M! Чем займёмся?",
                f"О, {name}! Привет! Баварский привет! 🏎️",
                f"Привет, {name}! 😊 Кофе уже пью, можно про BMW болтать",
            ]
        elif gender == "female":
            greets = [
                f"Привет, {name}! 😊 Мы с тобой обе понимаем толк в M Power!",
                f"Хей, {name}! Давай про BMW поболтаем!",
                f"Привет, {name}! 😊 Бимер-драйв! 🏎️",
            ]
        else:
            greets = [
                f"Привет, {name}! 😊 Masha тут!",
                f"Хей, {name}! BMW — наша страсть!",
            ]
    else:
        greets = [
            "Привет! 😊 Маша тут. BMW — наша страсть!",
            "Хей! ///M! Давай знакомиться!",
            "Привет! 😊 Пиши о чём хочешь, я BMW-эксперт!",
        ]

    welcome = random.choice(greets)
    await message.answer(welcome)


# ── /help command ──────────────────────────────────────────────────────────────

@chat_router.message(Command("help"))
async def cmd_help(message: Message):
    """Handle /help command — BMW-focused."""
    if not await _check_user(message):
        return

    help_text = (
        "Если что, я могу:\n\n"
        "🔧 Помочь с диагностикой BMW — расскажи, что с бимером, разберёмся вместе\n"
        "🔍 Подобрать запчасти — подскажу где искать по VIN и артикулу\n"
        "📊 Расшифровать VIN или номер кузова — WBA = BMW, WBS = BMW M!\n"
        "📸 Посмотреть фото — отправь, я расскажу что вижу\n"
        "💬 Просто поболтать — я люблю общаться про BMW и M Power!\n"
        "🚗 Сохранить твою машину — /mycar Марка Модель Год\n"
        "📱 Работаю в любом чате — набери @asmasha_bot и вопрос!\n\n"
        "Команды:\n"
        "/clear — начать с чистого листа\n"
        "/diagnostic — фокус на диагностике BMW\n"
        "/parts — ищем запчасти\n"
        "/normal — обычный режим\n"
        "/mycar — мои машины\n"
        "/delcar <номер> — удалить машину\n"
        "/mileage <номер> <км> — обновить пробег"
    )
    await message.answer(help_text)


# ── /clear command ─────────────────────────────────────────────────────────────

@chat_router.message(Command("clear"))
async def cmd_clear(message: Message):
    """Clear chat history."""
    if not await _check_user(message):
        return

    await clear_chat_history(message.from_user.id)
    await message.answer("Чистый лист! 😊 Начинаем заново ///M!")


# ── Mode commands ──────────────────────────────────────────────────────────────

@chat_router.message(Command("diagnostic"))
async def cmd_diagnostic(message: Message):
    """Switch to diagnostic mode."""
    if not await _check_user(message):
        return

    await set_chat_mode(message.from_user.id, "diagnostic")
    await message.answer(
        "Ок, режим диагностики BMW 🔧 Расскажи, что с бимером — разберёмся вместе!"
    )


@chat_router.message(Command("parts"))
async def cmd_parts(message: Message):
    """Switch to parts search mode."""
    if not await _check_user(message):
        return

    await set_chat_mode(message.from_user.id, "parts")
    await message.answer(
        "Ищем запчасти 🔍 Подскажу где искать — Росско, Autopiter, AvtoALL"
    )


@chat_router.message(Command("normal"))
async def cmd_normal(message: Message):
    """Switch to normal chat mode."""
    if not await _check_user(message):
        return

    await set_chat_mode(message.from_user.id, "normal")
    await message.answer("Обычный режим 😊 Пиши о чём хочешь!")


# ── /partners command ──────────────────────────────────────────────────────────

@chat_router.message(Command("partners"))
async def cmd_partners(message: Message):
    """Show available partner programs."""
    if not await _check_user(message):
        return

    await partner_manager.maybe_refresh()

    if not partner_manager.programs:
        await message.answer("Партнёрские программы пока не загружены 😅")
        return

    lines = ["🔧 Партнёры @bmw_mpower_club:\n"]
    for p in partner_manager.programs[:10]:
        url = p.goto_link or p.affiliate_url
        if url:
            lines.append(f"• {p.name}\n  👉 {url}")

    lines.append(f"\nВсего программ: {len(partner_manager.programs)}")
    await message.answer("\n".join(lines))


# ── /mycar command ─────────────────────────────────────────────────────────────

@chat_router.message(Command("mycar"))
async def cmd_mycar(message: Message):
    """Show user's saved cars or add a new one."""
    if not await _check_user(message):
        return

    args = message.text.split(maxsplit=1)

    if len(args) < 2:
        cars = await get_user_cars(message.from_user.id)
        if not cars:
            await message.answer(
                "У тебя пока нет сохранённых машин. Добавь:\n"
                "/mycar BMW M5 F90\n"
                "/mycar BMW 330i G20 B48 65000\n"
                "\nФормат: /mycar Марка Модель Год [Двигатель] [Пробег]"
            )
            return

        lines = ["🚗 Твои машины:"]
        for car in cars:
            car_info = f"  {car['brand']} {car['model']}"
            if car['year']:
                car_info += f" {car['year']}"
            if car['engine']:
                car_info += f", {car['engine']}"
            if car['mileage']:
                car_info += f", {car['mileage']} км"
            car_info += f" (#{car['id']})"
            lines.append(car_info)
            if car['vin']:
                lines.append(f"    VIN: {car['vin']}")

        lines.append("\nУдалить: /delcar <номер>")
        lines.append("Обновить пробег: /mileage <номер> <км>")
        await message.answer("\n".join(lines))
        return

    car_text = args[1].strip()
    parts = car_text.split()

    brand = parts[0] if len(parts) > 0 else ""
    model_name = parts[1] if len(parts) > 1 else ""
    year = 0
    engine = ""
    mileage = 0

    try:
        year = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    except (ValueError, IndexError):
        pass

    remaining = parts[3:] if year else parts[2:]
    for r in remaining:
        if r.isdigit() and len(r) >= 4:
            mileage = int(r)
        elif not engine:
            engine = r
        else:
            engine += f" {r}"

    car_id = await add_user_car(
        user_id=message.from_user.id,
        brand=brand,
        model=model_name,
        year=year,
        engine=engine,
        mileage=mileage,
    )

    await message.answer(f"Машина добавлена! {brand} {model_name} {year or ''} (#{car_id}) 🏎️")


# ── /delcar command ────────────────────────────────────────────────────────────

@chat_router.message(Command("delcar"))
async def cmd_delcar(message: Message):
    """Delete a car from user's profile."""
    if not await _check_user(message):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /delcar <номер машины>")
        return

    try:
        car_id = int(args[1])
    except ValueError:
        await message.answer("Нужно указать номер машины (число)")
        return

    deleted = await delete_user_car(car_id, message.from_user.id)
    if deleted:
        await message.answer("Машина удалена из профиля ✅")
    else:
        await message.answer("Не найдена такая машина в твоём профиле")


# ── /mileage command ──────────────────────────────────────────────────────────

@chat_router.message(Command("mileage"))
async def cmd_mileage(message: Message):
    """Update mileage for a saved car."""
    if not await _check_user(message):
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /mileage <номер машины> <пробег км>")
        return

    try:
        car_id = int(args[1])
        km = int(args[2])
    except ValueError:
        await message.answer("Нужно: номер машины и пробег (числа)")
        return

    updated = await update_car_mileage(car_id, message.from_user.id, km)
    if updated:
        await message.answer(f"Пробег обновлён: {km} км 📝")
    else:
        await message.answer("Не найдена такая машина")


# ── Photo handler ──────────────────────────────────────────────────────────────

@chat_router.message(F.photo)
async def handle_photo(message: Message):
    """Handle photo messages — analyze with vision AI.

    v18: In groups, photos are only handled when triggered (mention / reply to
    Маша / BMW-relevant caption under a channel post) — same gate as text
    messages. Prevents Маша from commenting on every photo in the group.
    """
    if not await _check_user(message):
        return

    is_group = message.chat.type in ("group", "supergroup")

    if is_group:
        # v18: Apply the same trigger gate as text messages
        should_reply, reason = _group_should_reply(message)
        # For photos, also allow the BMW-relevant caption to trigger (the
        # _is_bmw_relevant check in _group_should_reply already covers this
        # via the channel-comment path, but a plain @mention always works).
        if not should_reply:
            return  # Silent — don't comment on every group photo

        if _group_rate_limited(message.chat.id):
            return

        caption = message.caption or ""
        simple_prompt = (
            f"Кто-то прислал фото в группе. "
            f"{'С подписью: ' + caption[:100] if caption else 'Без подписи.'} "
            f"Напиши короткий комментарий (до 200 символов) как BMW-эксперт. "
            f"Без анализа фото — просто живой комментарий."
        )
        try:
            response = await get_ai_router().chat(
                messages=[
                    {"role": "system", "content": "Ты Маша, BMW-эксперт. Напиши короткий комментарий (до 200 символов) на фото в группе. Живо и с характером."},
                    {"role": "user", "content": simple_prompt},
                ],
                use_cache=False,
                max_tokens=300,
                route_type=ROUTE_COMMENT,
            )
            if response.text:
                reply_text = response.text[:COMMENT_MAX_CHARS]
                await message.reply(reply_text)
        except Exception as e:
            logger.debug(f"Group photo comment error: {e}")
        return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    photo: PhotoSize = message.photo[-1]

    caption = message.caption or ""
    if caption:
        prompt = caption
    else:
        prompt = (
            "Рассмотри это фото МАКСИМАЛЬНО внимательно и подробно:\n\n"
            "1. Если на фото BMW — определи: модель, поколение, год, тип кузова, "
            "цвет, состояние, двигатель если возможно. Укажи ориентировочную стоимость.\n\n"
            "2. Если на фото ЗАПЧАСТЬ — определи: что это за деталь, для какого BMW подходит, "
            "артикул (OEM-номер), если виден. Посоветуй где купить.\n\n"
            "3. Если на фото ДОКУМЕНТ на авто (ПТС, СТС) — "
            "считай ВСЕ данные: VIN, марку, модель, год, двигатель, мощность, объём. "
            "НИКОГДА не показывай ФИО владельца и адрес! Только технические данные.\n\n"
            "4. Если на фото ЭКРАН СКАНЕРА OBD-II — считай коды ошибок и расшифруй.\n\n"
            "5. Если на фото ПОВРЕЖДЕНИЕ/ПОЛОМКА — опиши что видишь, возможные причины, "
            "что делать и примерную стоимость ремонта.\n\n"
            "6. Если что-то другое — просто опиши что видишь.\n\n"
            "Пиши живо и заботливо, как BMW M-энтузиастка."
        )

    extra_context_parts = []
    user_context = _get_user_persona_context(message)
    if user_context:
        extra_context_parts.append(user_context)

    try:
        user_cars = await get_user_cars(message.from_user.id)
        if user_cars:
            car_lines = ["Машины пользователя:"]
            for car in user_cars[:3]:
                car_line = f"- {car['brand']} {car['model']}"
                if car['year']:
                    car_line += f" {car['year']}"
                if car['vin']:
                    car_line += f", VIN: {car['vin']}"
                car_lines.append(car_line)
            extra_context_parts.append("\n".join(car_lines))
    except Exception:
        pass

    # ── Partner context for photo analysis ───────────────────────────────
    # Photos are often of parts / VIN documents / damages. Inject the primary
    # parts links + cross-category partner context so the vision AI can
    # recommend the correct goto_links (from partners.json) in its answer.
    photo_partner_links: list = []
    try:
        await partner_manager.maybe_refresh()
        primary_links = partner_manager.format_primary_parts_links()
        if primary_links:
            extra_context_parts.append(primary_links)
        # Use the caption (or a generic parts hint) to find relevant partners
        partner_query = caption or "запчасть деталь vin"
        partner_ctx = partner_manager.generate_partner_context(partner_query, max_programs=3)
        if partner_ctx:
            extra_context_parts.append(partner_ctx)
        for pl in partner_manager.get_all_relevant_links(partner_query, max_programs=5):
            photo_partner_links.append((pl["name"], pl["url"]))
    except Exception as e:
        logger.debug(f"Photo partner context error: {e}")

    try:
        file_info = await message.bot.get_file(photo.file_id)
        if not file_info or not file_info.file_path:
            await message.answer("Не удалось скачать фото 😅 Попробуй ещё раз")
            return

        file_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_info.file_path}"

        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(file_url)
            if response.status_code == 200:
                image_base64 = base64.b64encode(response.content).decode("utf-8")

                media_type = "image/jpeg"
                if file_info.file_path.endswith(".png"):
                    media_type = "image/png"
                elif file_info.file_path.endswith(".webp"):
                    media_type = "image/webp"

                extra_context = "\n\n".join(extra_context_parts) if extra_context_parts else ""

                response = await get_ai_router().analyze_image(
                    user_id=message.from_user.id,
                    image_base64=image_base64,
                    prompt=prompt,
                    extra_context=extra_context,
                )

                if response.error or not response.text:
                    await message.answer("Ой, не получилось разглядеть фото 😅 Попробуй ещё раз!")
                    return

                reply_text = response.text
                reply_text = _clean_markdown(reply_text)
                reply_text = _replace_plain_urls_with_affiliate(reply_text)

                # Append the “🔗 Где искать:” section with correct goto_links
                if photo_partner_links:
                    reply_text = _clean_raw_partner_urls(reply_text, photo_partner_links)
                    partner_section = _format_partner_links_section(photo_partner_links)
                    if partner_section:
                        reply_text = reply_text.rstrip() + "\n\n" + partner_section

                if len(reply_text) <= config.TELEGRAM_TEXT_LIMIT:
                    await message.answer(reply_text)
                else:
                    chunks = _split_message(reply_text, max_length=config.TELEGRAM_TEXT_LIMIT)
                    for chunk in chunks:
                        await message.answer(chunk)
                return
            else:
                await message.answer("Не удалось скачать фото 😅 Попробуй ещё раз")
                return

    except Exception as e:
        logger.error(f"Photo processing error: {e}")
        await message.answer("Ой, что-то пошло не так с фото 😅 Напиши текстом, попробую помочь!")


# ── Voice message handler ─────────────────────────────────────────────────────

@chat_router.message(F.voice)
async def handle_voice(message: Message):
    """Handle voice messages — transcribe and process.

    v18: In groups, voice messages are only handled when triggered (mention /
    reply to Маша) — same gate as text messages.
    """
    if not await _check_user(message):
        return

    # v18: Group trigger gate — don't transcribe every voice in the group
    if _is_group_chat(message):
        should_reply, _ = _group_should_reply(message)
        if not should_reply:
            return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    await message.answer("Слушаю... 🎧")

    voice = message.voice
    text = await process_voice_message(message.bot, voice.file_id)

    if text and not text.startswith("Не удалось"):
        # In groups, route through the group message processor (conversation
        # context + BMW knowledge); in private, use the standard text flow.
        if _is_group_chat(message):
            await _process_group_message(message, text, "voice_trigger")
        else:
            await _process_text_message(message, text)
    else:
        await message.answer(text)


# ── Main text message handler ─────────────────────────────────────────────────

@chat_router.message(F.text)
async def handle_text(message: Message):
    """Handle text messages — main interaction point.

    v18: In GROUP chats, Маша replies ONLY when triggered (mention / reply to
    her / BMW-relevant channel-comment). In PRIVATE chat she replies to every
    message (existing behavior). Group replies load the conversation transcript
    from chat_history so Маша answers with full context of the discussion.
    """
    if not await _check_user(message):
        return

    text = message.text.strip()
    if not text:
        return

    # ── v18: GROUP trigger gate ──
    if _is_group_chat(message):
        should_reply, reason = _group_should_reply(message)
        if not should_reply:
            return  # Silent: don't reply to every group message (anti-spam)

        # Per-group rate limit (anti-spam): skip if we just replied here
        if _group_rate_limited(message.chat.id):
            logger.debug(
                f"Group rate-limited (chat_id={message.chat.id}, reason={reason})"
            )
            return

        logger.info(
            f"Group reply triggered: chat_id={message.chat.id} "
            f"user={message.from_user.id} reason={reason}"
        )
        await _process_group_message(message, text, reason)
        return

    # ── PRIVATE chat: existing behavior (reply to every message) ──
    await _process_text_message(message, text)


async def _process_group_message(message: Message, text: str, reason: str):
    """Process a group message that triggered a reply.

    v18: Loads the conversation transcript (last N messages with author names),
    injects BMW knowledge + partner links relevant to the topic, and calls the
    AI with a group-optimized system prompt. The reply is sent via _safe_reply
    (preserves forum thread + reply chain). Saves the user's message AND Маша's
    reply to chat_history keyed by chat_id so the next trigger sees the context.
    """
    import random
    chat_id = message.chat.id
    user = message.from_user
    author_name = _display_name(user)

    # Strip the @asmasha_bot mention from the text we send to the AI
    clean_text = re.sub(r'@asmasha_bot\s*', '', text, flags=re.IGNORECASE).strip()
    if not clean_text:
        clean_text = text  # keep original if stripping left it empty

    await message.bot.send_chat_action(chat_id, ChatAction.TYPING)

    # ── Load conversation transcript ──
    transcript = ""
    try:
        history = await get_chat_history(chat_id, limit=_GROUP_HISTORY_LIMIT)
        transcript = _format_conversation_transcript(history, author_name, clean_text)
    except Exception as e:
        logger.debug(f"Group history load error: {e}")
        transcript = f"{author_name}: {clean_text}"

    # ── Build context: chat-type awareness + original post (if comment) ──
    extra_context_parts: list[str] = []

    # Chat context (group/comment/forum) — short style guidance
    chat_ctx = _detect_chat_context(message)
    if chat_ctx:
        extra_context_parts.append("\n".join(chat_ctx))

    # User persona (name, gender) for personalized tone
    user_ctx = _get_user_persona_context(message)
    if user_ctx:
        extra_context_parts.append(user_ctx)

    # The trigger reason — so the AI knows WHY it's replying
    _reason_desc = {
        "mention": "Тебя @упомянули в группе. Ответь адресованно тому, кто упомянул.",
        "reply_to_masha": "Кто-то ответил на твоё сообщение. Продолжи диалог по существу.",
        "conversation_join": "Кто-то ответил другому участнику в беседе, и ты присоединяешься к разговору. Вступай естественно — как живой участник, добавляй своё экспертное мнение, шутку или вопрос по теме.",
        "channel_comment_bmw": "Это BMW-релевантный комментарий под постом канала. Ответь как эксперт, дополни или поправь, если нужно.",
        "bmw_chatter": "В группе обсуждают BMW/авто-тема — ты вступаешь в обсуждение как эксперт. Добавь ценность: факт, мнение, сравнение.",
        "casual_participation": "Обычная болтовня в группе — ты иногда вступаешь как живой участник. Коротко, с характером, без занудства. Можно просто пошутить или перекинуться парой слов.",
        "voice_trigger": "Голосовое сообщение в группе — ты расшифровала и отвечаешь на него.",
    }
    extra_context_parts.append(
        f"Почему ты отвечаешь: {_reason_desc.get(reason, 'Триггер группы')}"
    )

    # ── Apply BMW knowledge ──
    try:
        from bot.bmw_knowledge import build_bmw_context
        bmw_ctx = build_bmw_context(clean_text)
        if bmw_ctx:
            extra_context_parts.append(bmw_ctx)
    except Exception:
        pass

    # ── Partner links (for VIN/parts queries in the group) ──
    collected_partner_links: list[tuple[str, str]] = []
    try:
        await partner_manager.maybe_refresh()
        # If the message mentions parts/VIN, inject partner context
        text_lower = clean_text.lower()
        if any(kw in text_lower for kw in [
            "запчаст", "деталь", "артикул", "vin", "вин", "купить", "подобрать",
            "масло", "фильтр", "колодк", "ремень", "тормоз",
        ]):
            primary = partner_manager.format_primary_parts_links()
            if primary:
                extra_context_parts.append(primary)
            pctx = partner_manager.generate_partner_context(clean_text, max_programs=3)
            if pctx:
                extra_context_parts.append(pctx)
            for pl in partner_manager.get_all_relevant_links(clean_text, max_programs=4):
                collected_partner_links.append((pl["name"], pl["url"]))
    except Exception as e:
        logger.debug(f"Group partner context error: {e}")

    # ── Web search for diagnostic / parts questions (best-effort) ──
    try:
        if any(kw in clean_text.lower() for kw in [
            "стучит", "не работает", "горит", "ошибка", "чек", "троит",
            "запчаст", "артикул", "купить", "сколько стоит",
        ]):
            results = await web_search(clean_text, max_results=3)
            if results:
                extra_context_parts.append(
                    "Результаты поиска (используй если релевантно):\n"
                    + format_search_results(results, max_items=3)
                )
    except Exception as e:
        logger.debug(f"Group web search error: {e}")

    # ── Build the AI prompt ──
    # Group-optimized system prompt: short, conversational, uses transcript
    group_system = (
        MASHA_SYSTEM_PROMPT
        + "\n\nСЕЙЧАС ТЫ В ГРУППЕ/КОММЕНТАРИЯХ. Правила:\n"
        "- Отвечай КОРОТКО (до 500 символов), живо, как участник беседы.\n"
        "- Используй контекст беседы (переписка выше) — отвечай по существу, "
        "ссылайся на то, что говорили другие, если уместно.\n"
        "- Применяй ВСЮ свою BMW-экспертизу: модели, двигатели, технологии, "
        "сленг. Если вопрос про запчасти/VIN/диагностику — давай конкретику.\n"
        "- НЕ пиши подпись канала, НЕ используй формат поста. Это живой чат.\n"
        "- Можно эмодзи, можно сарказм, можно не согласиться с собеседником.\n"
        "- Если спрашивают про запчасти — упомяни партнёров (Росско, Autopiter, "
        "AvtoALL) и дай ссылку из контекста. Не спамь ссылками без повода.\n"
        "- Если ответ на вопрос есть в контексте беседы выше — не повторяйся, "
        "добавь новое.\n"
        "- Не извиняйся, не «как AI» — ты Маша, главред, владелица M5 F90."
    )

    extra_context = "\n\n".join(p for p in extra_context_parts if p)
    user_msg = (
        f"Переписка в группе (последние сообщения, oldest→newest):\n"
        f"{transcript}\n\n"
    )
    if extra_context:
        user_msg += f"Контекст для ответа:\n{extra_context}\n\n"
    user_msg += (
        f"Ответь на последнее сообщение от {author_name}. "
        f"Коротко, по делу, как BMW-эксперт в живой беседе."
    )

    chat_messages = [
        {"role": "system", "content": group_system},
        {"role": "user", "content": user_msg},
    ]

    # ── Save the user's message to chat_history BEFORE replying ──
    try:
        await add_chat_message(
            user_id=user.id,
            role="user",
            content=clean_text,
            chat_id=chat_id,
            author_name=author_name,
        )
    except Exception as e:
        logger.debug(f"Group: failed to save user message to history: {e}")

    # ── Call AI (ROUTE_COMMENT = local-first for fast group replies) ──
    try:
        response = await get_ai_router().chat(
            messages=chat_messages,
            use_cache=False,
            max_tokens=600,
            route_type=ROUTE_COMMENT,
        )
    except Exception as e:
        logger.error(f"Group AI error: {e}")
        try:
            await message.reply("Ой, зависла 😅 Попробуй ещё раз!")
        except Exception:
            pass
        return

    if not response or response.error or not response.text:
        logger.debug(f"Group AI empty response: {getattr(response, 'error_message', '?')}")
        return  # Silent — don't spam the group with error messages

    reply_text = _clean_markdown(response.text)
    reply_text = _replace_plain_urls_with_affiliate(reply_text)

    # Strip the @asmasha_bot mention from the reply (we know who's addressed)
    # and trim to group limit
    reply_text = reply_text.strip()
    if len(reply_text) > GROUP_MAX_CHARS:
        reply_text = reply_text[:GROUP_MAX_CHARS - 1].rstrip() + "…"

    # ── Append partner links section (short, for group) ──
    # Dedup and keep max 2 (group = compact)
    if collected_partner_links:
        seen = set()
        unique_links = []
        for name, url in collected_partner_links:
            if name not in seen and url:
                seen.add(name)
                unique_links.append((name, url))
        if unique_links:
            section_lines = ["🔗 Где искать:"]
            for name, url in unique_links[:2]:
                section_lines.append(f"• {name}: {url}")
            reply_text = reply_text.rstrip() + "\n\n" + "\n".join(section_lines)

    # ── Send the reply (preserves reply chain + forum thread) ──
    try:
        sent = await message.reply(reply_text)
    except Exception:
        try:
            sent = await message.answer(reply_text)
        except Exception as e:
            logger.error(f"Group reply send error: {e}")
            return

    # ── Save Маша's reply to chat_history ──
    try:
        await add_chat_message(
            user_id=0,  # bot's own messages — user_id 0 (Маша herself)
            role="assistant",
            content=reply_text,
            chat_id=chat_id,
            author_name="Маша",
        )
    except Exception as e:
        logger.debug(f"Group: failed to save Маша's reply to history: {e}")

    logger.info(
        f"Group reply sent: chat_id={chat_id} reason={reason} "
        f"len={len(reply_text)} partners={len(collected_partner_links)}"
    )


async def _process_text_message(message: Message, text: str):
    """Core message processing with AI, search, diagnostics, parts, VIN, and personalization."""
    import random
    user_id = message.from_user.id
    chat_mode = await get_chat_mode(user_id)

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # ── Detect chat context: group/private, channel-post comment, forum topic ──
    # This context is injected into the AI prompt so responses adapt to where
    # the conversation is happening (short & punchy in groups/comments, detailed
    # in private; aware of the original channel post when replying in a
    # discussion group; aware of the forum topic when in a forum supergroup).
    chat_context_parts = _detect_chat_context(message)

    text_lower = text.lower()
    if any(kw in text_lower for kw in ["запчаст", "деталь", "артикул", "купить", "найти запчас", "подобрать", "vin", "вин"]):
        thinking_msg = random.choice(MASHA_PHRASES["part_search"])
    elif any(kw in text_lower for kw in ["стучит", "не работает", "горит", "ошибка", "чек", "перегрев", "не заводит", "троит", "вибрац", "vanos"]):
        thinking_msg = random.choice(MASHA_PHRASES["diagnostic_start"])
    else:
        thinking_msg = random.choice(MASHA_PHRASES["thinking"])
    status_msg = await message.answer(thinking_msg)

    # ── Ensure partner data loaded ────────────────────────────────────────
    try:
        await partner_manager.maybe_refresh()
    except Exception as e:
        logger.debug(f"Partner refresh error: {e}")

    # ── Build extra context ────────────────────────────────────────────────

    extra_context_parts = []
    collected_partner_links = []

    # Chat-context (group/private, channel-post comment, forum topic) FIRST,
    # so the AI knows how to shape its answer before any other context.
    if chat_context_parts:
        extra_context_parts.append("\n".join(chat_context_parts))

    user_context = _get_user_persona_context(message)
    if user_context:
        extra_context_parts.append(user_context)

    try:
        user_cars = await get_user_cars(user_id)
        if user_cars:
            car_lines = ["Машины пользователя:"]
            for car in user_cars[:3]:
                car_line = f"- {car['brand']} {car['model']}"
                if car['year']:
                    car_line += f" {car['year']}"
                if car['engine']:
                    car_line += f", двигатель: {car['engine']}"
                if car['mileage']:
                    car_line += f", пробег: {car['mileage']} км"
                if car['vin']:
                    car_line += f", VIN: {car['vin']}"
                car_lines.append(car_line)
            extra_context_parts.append("\n".join(car_lines))
    except Exception as e:
        logger.debug(f"Error loading user cars: {e}")

    # Detect VIN
    vin_code = _detect_vin(text)
    body_number = _detect_body_number(text) if not vin_code else None
    is_vin_query = bool(vin_code) or bool(body_number) or _is_vin_query(text)

    if is_vin_query:
        vin_or_body = vin_code or body_number or text.strip()
        
        # BMW VIN info
        if vin_code and len(vin_code) == 17:
            vin_prefix = vin_code[:3].upper()
            if vin_prefix == "WBA":
                extra_context_parts.append("VIN начинается с WBA — это BMW!")
            elif vin_prefix == "WBS":
                extra_context_parts.append("VIN начинается с WBS — это BMW M-модель! ///M!")
        
        vin_search_context = ""
        if vin_code and len(vin_code) == 17:
            try:
                search_query = f"VIN {vin_code} расшифровка автомобиль характеристики"
                results = await web_search(search_query, max_results=3)
                if results:
                    vin_search_context = "Результаты поиска по VIN:\n" + format_search_results(results, max_items=3)
            except Exception as e:
                logger.debug(f"VIN web search error: {e}")
        
        primary_links_context = ""
        try:
            primary_links_context = partner_manager.format_primary_parts_links()
        except Exception as e:
            logger.debug(f"Primary links context error: {e}")
        
        all_context = extra_context_parts.copy()
        if vin_search_context:
            all_context.append(vin_search_context)
        if primary_links_context:
            all_context.append(primary_links_context)
        
        response = await get_ai_router().decode_vin(
            user_id=user_id,
            vin_code=vin_or_body,
            extra_context="\n".join(all_context),
        )
        vin_partner_links = []
        try:
            all_links_data = partner_manager.get_all_relevant_links(vin_or_body, max_programs=5)
            for pl in all_links_data:
                vin_partner_links.append((pl['name'], pl['url']))
        except Exception:
            try:
                primary_links_data = partner_manager.get_primary_parts_links()
                for pl in primary_links_data:
                    vin_partner_links.append((pl['name'], pl['url']))
            except Exception:
                pass
        await _send_response(message, response, status_msg, vin_partner_links)
        return

    # Detect car brand
    try:
        brand = identify_car_brand(text)
    except Exception as e:
        logger.debug(f"identify_car_brand error: {e}")
        brand = None
    if brand:
        from bot.masha_data import get_brand_info
        info = get_brand_info(brand)
        if info:
            extra_context_parts.append(f"Упомянута марка: {brand} ({info['country']}, холдинг: {info['parent']})")
        if brand == "BMW":
            try:
                from bot.bmw_knowledge import build_bmw_context
                bmw_ctx = build_bmw_context(text)
                if bmw_ctx:
                    extra_context_parts.append(bmw_ctx)
            except Exception:
                pass

    # Detect OBD-II codes
    obd_codes = detect_obd2_codes(text)
    if obd_codes:
        for code in obd_codes:
            desc = lookup_obd2_code(code)
            if desc:
                extra_context_parts.append(f"Код ошибки {code}: {desc}")

        for code in obd_codes[:2]:
            try:
                code_info = await search_diagnostic_code(code)
                if code_info.get("links"):
                    links_text = "\n".join(
                        f"- {l['title']}: {l['url']}" for l in code_info["links"][:3]
                    )
                    extra_context_parts.append(f"Подробности по ошибке {code}:\n{links_text}")
            except Exception as e:
                logger.error(f"Error searching diagnostic code: {e}")

    # Detect part numbers
    part_numbers = extract_part_numbers(text)
    is_part_query = bool(part_numbers) or is_part_number(text.strip()) or chat_mode == "parts"

    if is_part_query:
        try:
            primary_links = partner_manager.format_primary_parts_links()
            if primary_links:
                extra_context_parts.append(primary_links)
        except Exception as e:
            logger.debug(f"Primary links error: {e}")

    # Detect car symptoms
    symptoms = detect_symptoms(text)
    is_diagnostic = bool(symptoms) or chat_mode == "diagnostic"

    if symptoms:
        diag_context = build_diagnostic_context(text)
        if diag_context:
            extra_context_parts.append(diag_context)

    # Web search
    needs_search = (
        is_diagnostic or
        is_part_query or
        any(kw in text.lower() for kw in [
            "найди", "поиск", "ищи", "где купить", "сколько стоит",
            "новости", "что нового", "обзор", "сравни", "лучший",
            "рекомендуй", "посоветуй", "купить", "заказать",
            "запчаст", "деталь", "артикул", "оригинал", "аналог",
            "замена", "ремонт", "поломк", "стучит", "не работает",
            "горит", "ошибка", "код", "чек", "check",
            "цена", "стоимость", "подбор",
            "bmw", "бмв", "бимер", "m power", "vanos",
        ])
    )

    if needs_search:
        try:
            search_query = text
            if brand:
                search_query = f"{brand} {text}"

            text_lower = text.lower().strip()
            _SEARCH_QUERY_REWRITES = {
                "какие новости": "BMW автомобильные новости сегодня",
                "что нового": "BMW автоновости сегодня",
                "новости": "BMW автомобильные новости сегодня",
                "что нового у bmw": "BMW новости сегодня",
                "какие новости сегодня": "BMW автомобильные новости сегодня",
            }
            for vague, specific in _SEARCH_QUERY_REWRITES.items():
                if vague in text_lower and len(text_lower) < len(vague) + 15:
                    search_query = specific
                    break

            results = await web_search(search_query, max_results=5)
            if results:
                extra_context_parts.append("Результаты поиска:\n" + format_search_results(results, max_items=5))
        except Exception as e:
            logger.error(f"Web search error: {e}")

    # Spare part query
    is_spare_part_query = (
        any(kw in text.lower() for kw in [
            "запчаст", "деталь", "артикул", "купить запчас", "купить детал",
            "оригинал", "аналог", "замена", "подбор", "номер детал",
            "oem", "оригинальн", "цена", "стоимость", "скольк",
            "колодки", "фильтр", "свечи", "ремень", "амортизатор",
            "подшипник", "сальник", "прокладк", "датчик", "реле",
            "насос", "стойка", "шаровая", "наконечник", "сцепление",
            "где купить", "подобрать", "найти запчас",
            "bmw", "бмв", "бимер",
        ])
        or is_part_number(text.strip())
        or bool(part_numbers)
        or chat_mode == "parts"
    )

    if is_spare_part_query:
        try:
            primary_links = partner_manager.format_primary_parts_links()
            if primary_links and primary_links not in extra_context_parts:
                extra_context_parts.append(primary_links)
            try:
                all_links_data = partner_manager.get_all_relevant_links(text, max_programs=5)
                for pl in all_links_data:
                    collected_partner_links.append((pl['name'], pl['url']))
            except Exception:
                pass
            # Also generate AI-friendly partner context for natural link insertion
            try:
                partner_ctx = partner_manager.generate_partner_context(text, max_programs=3)
                if partner_ctx and partner_ctx not in extra_context_parts:
                    extra_context_parts.append(partner_ctx)
            except Exception as e:
                logger.debug(f"Partner context generation error: {e}")
        except Exception as e:
            logger.debug(f"Partner links error: {e}")

    # Travel queries — add travel partner links (Aviasales, Localrent)
    is_travel_query = any(kw in text_lower for kw in [
        "авиа", "билеты", "путешеств", "полёт", "рейс", "отель", "прокат", "аренд",
        "перелёт", "самолёт", "гостиниц", "тур", "vacation", "flight", "hotel",
    ])
    if is_travel_query:
        try:
            travel_links = partner_manager.get_travel_links()
            for pl in travel_links:
                collected_partner_links.append((pl['name'], pl['url']))
            if travel_links:
                travel_ctx = "\n".join(f"- {l['name']}: {l['url']}" for l in travel_links)
                extra_context_parts.append(f"Партнёрские ссылки для путешествий:\n{travel_ctx}")
        except Exception as e:
            logger.debug(f"Travel partner links error: {e}")

    # Tools queries — add tools partner links (VseInstrumenti, etc.)
    is_tools_query = any(kw in text_lower for kw in [
        "инструмент", "ключ", "гараж", "ремонт", "сервис", "оборудован",
        "toolbox", "garage", "tool",
    ])
    if is_tools_query:
        try:
            tools_links = partner_manager.get_tools_links()
            for pl in tools_links:
                collected_partner_links.append((pl['name'], pl['url']))
            if tools_links:
                tools_ctx = "\n".join(f"- {l['name']}: {l['url']}" for l in tools_links)
                extra_context_parts.append(f"Партнёрские ссылки для инструментов:\n{tools_ctx}")
        except Exception as e:
            logger.debug(f"Tools partner links error: {e}")

    # General shopping queries — add relevant partner links
    is_shopping_query = any(kw in text_lower for kw in [
        "купить", "заказать", "цена", "стоимость", "магазин",
    ])
    if is_shopping_query and not is_spare_part_query and not is_travel_query and not is_tools_query:
        try:
            primary_links = partner_manager.get_primary_parts_links()
            for pl in primary_links[:3]:
                collected_partner_links.append((pl['name'], pl['url']))
        except Exception as e:
            logger.debug(f"Shopping partner links error: {e}")

    # BMW knowledge context
    try:
        from bot.bmw_knowledge import build_bmw_context
        bmw_ctx = build_bmw_context(text)
        if bmw_ctx and bmw_ctx not in extra_context_parts:
            extra_context_parts.append(bmw_ctx)
    except Exception:
        pass

    # Route to AI
    extra_context = "\n\n".join(extra_context_parts) if extra_context_parts else ""

    # v18.2: Load conversation history so Маша REMEMBERS the discussion.
    # In PRIVATE chat, history is keyed by user_id (1-on-1 with Маша).
    # In GROUP, history is keyed by chat_id (handled by _process_group_message).
    # Both paths now load recent turns and inject them into the AI prompt.
    conversation_history: list[dict] = []
    try:
        history_chat_id = message.chat.id  # works for both private (id=user_id) and group
        history_rows = await get_chat_history(history_chat_id, limit=_PRIVATE_HISTORY_LIMIT)
        # Filter out thinking-status messages Маша posted ("Слушаю..." etc.)
        _THINKING_PHRASES = ("секунд", "слушаю", "думаю", "ищу", "проверяю", "смотрю", "греди")
        for row in history_rows:
            role = row.get("role", "user")
            content = (row.get("content") or "").strip()
            if not content:
                continue
            if role == "assistant" and len(content) < 40 and any(p in content.lower() for p in _THINKING_PHRASES):
                continue
            conversation_history.append({"role": role, "content": content})
    except Exception as e:
        logger.debug(f"Could not load conversation history: {e}")

    # v18.2: Save the user's message to chat_history BEFORE calling the AI,
    # so if the AI is slow, the message is still recorded.
    is_group_msg = message.chat.type in ("group", "supergroup")
    try:
        await add_chat_message(
            user_id=user_id,
            role="user",
            content=text,
            chat_id=message.chat.id,
            author_name=_display_name(message.from_user) if is_group_msg else "",
        )
    except Exception as e:
        logger.debug(f"Could not save user message to history: {e}")

    try:
        if is_diagnostic:
            # Inject conversation history into extra_context for diagnostic route
            diag_context = extra_context
            if conversation_history:
                hist_text = "\n".join(
                    f"{'Маша' if m['role']=='assistant' else 'Пользователь'}: {m['content'][:200]}"
                    for m in conversation_history[-6:]
                )
                diag_context = f"Предыдущая переписка:\n{hist_text}\n\n{extra_context}"
            response = await get_ai_router().diagnose_car(
                user_id=user_id,
                symptoms=text,
                extra_context=diag_context,
            )
        elif is_spare_part_query:
            parts_context = extra_context
            if conversation_history:
                hist_text = "\n".join(
                    f"{'Маша' if m['role']=='assistant' else 'Пользователь'}: {m['content'][:200]}"
                    for m in conversation_history[-6:]
                )
                parts_context = f"Предыдущая переписка:\n{hist_text}\n\n{extra_context}"
            response = await get_ai_router().find_spare_part(
                user_id=user_id,
                article=text.strip(),
                extra_context=parts_context,
            )
        else:
            # Build messages list for chat — INCLUDE conversation history
            # so Маша remembers what was discussed before.
            chat_messages = [
                {"role": "system", "content": MASHA_SYSTEM_PROMPT},
            ]
            # Inject conversation history as alternating user/assistant turns
            for hist_msg in conversation_history:
                chat_messages.append(hist_msg)
            if extra_context:
                chat_messages.append({"role": "user", "content": f"Контекст:\n{extra_context}"})
            chat_messages.append({"role": "user", "content": text})

            # Use ROUTE_COMMENT for group/supergroup messages (Local-first for faster responses)
            # Use ROUTE_CHAT for private messages (full cloud pipeline)
            route = ROUTE_COMMENT if is_group_msg else ROUTE_CHAT

            response = await get_ai_router().chat(
                messages=chat_messages,
                use_cache=False,  # v18.2: disable cache — history makes each request unique
                route_type=route,
            )
    except Exception as e:
        logger.error(f"AI router error: {e}")
        await message.reply("Ой, что-то я зависла 😅 Попробуй ещё раз!")
        return

    # v18.2: Save Маша's reply to chat_history so the NEXT message sees it.
    if response and response.text:
        try:
            # Strip partner links section from the saved copy (it's appended
            # later by _send_response; we want to store the core reply)
            saved_reply = response.text
            await add_chat_message(
                user_id=0,  # Маша's own messages
                role="assistant",
                content=saved_reply[:2000],  # cap stored length
                chat_id=message.chat.id,
                author_name="Маша",
            )
        except Exception as e:
            logger.debug(f"Could not save Маша's reply to history: {e}")

    await _send_response(message, response, status_msg, collected_partner_links)


# ── Response formatting ────────────────────────────────────────────────────────

async def _send_response(message: Message, response, status_msg: Message = None, partner_links: list = None):
    """Send AI response to user with partner link formatting.

    In groups/comments, partner links are sent as a SEPARATE reply so they
    always remain visible (never cut off by the group char budget).
    In private chat, partner links are appended inline after the answer.
    Forum topics: replies are sent into the correct message_thread_id so they
    land in the same topic the user wrote from.
    """
    if not response or response.error or not response.text:
        await _safe_reply(message, "Не получилось ответить 😅 Попробуй ещё раз!")
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
        return

    text = response.text
    text = _clean_markdown(text)
    text = _replace_plain_urls_with_affiliate(text)

    is_group = message.chat.type in ("group", "supergroup")
    max_chars = GROUP_MAX_CHARS if is_group else CHAT_MAX_CHARS

    # Delete thinking status before sending
    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass

    partner_section = ""
    if partner_links:
        text = _clean_raw_partner_urls(text, partner_links)
        partner_section = _format_partner_links_section(partner_links)

    if is_group and partner_section:
        # ── Group/comment path: send the answer, then partner links as a
        # separate reply so they survive the group char budget and stay
        # cleanly readable in the comment thread. ──
        if len(text) > max_chars:
            text = _truncate_at_boundary(text, max_chars)
        if text.strip():
            await _safe_reply(message, text)
        await _safe_reply(message, partner_section)
        return

    # ── Private chat path: append partner section inline ──
    if partner_section:
        text = text.rstrip() + "\n\n" + partner_section

    if len(text) <= max_chars:
        await _safe_reply(message, text)
    elif len(text) <= config.TELEGRAM_TEXT_LIMIT:
        await _safe_reply(message, text)
    else:
        chunks = _split_message(text, max_length=config.TELEGRAM_TEXT_LIMIT)
        for chunk in chunks:
            await _safe_reply(message, chunk)


async def _safe_reply(message: Message, text: str) -> None:
    """Send a message that lands in the same forum topic / thread as `message`.

    Uses message.reply() so the reply quote links back to the user's message
    AND the message_thread_id is preserved in forum supergroups. For private
    chats (no thread), reply() behaves identically to answer().
    Falls back to message.answer() if reply() fails.
    """
    try:
        await message.reply(text)
    except Exception:
        try:
            await message.answer(text)
        except Exception as e:
            logger.debug(f"_safe_reply failed: {e}")


def _truncate_at_boundary(text: str, max_chars: int) -> str:
    """Truncate text at the last sentence/line boundary before max_chars."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Prefer breaking on a newline, then on a sentence end
    pos = cut.rfind('\n')
    if pos < max_chars // 2:
        pos = cut.rfind('. ')
    if pos < max_chars // 2:
        pos = max_chars
    return cut[:pos].rstrip() + "…"


def _format_partner_links_section(links: list) -> str:
    """Format partner links as a clean section."""
    if not links:
        return ""
    lines = ["\n🔗 Где искать:"]
    for name, url in links[:3]:
        lines.append(f"• {name}: {url}")
    return "\n".join(lines)


def _clean_raw_partner_urls(text: str, links: list) -> str:
    """Remove raw affiliate URLs from AI text that will be re-added cleanly."""
    for name, url in links:
        # Remove raw URLs that the AI might have included
        if url in text:
            text = text.replace(url, "")
    return text


def _replace_plain_urls_with_affiliate(text: str) -> str:
    """Replace plain partner site URLs with affiliate goto_links for ALL partners.

    Iterates the full partner site_map (every campaign from partners.json), so
    any plain merchant URL the AI emits — be it autoparts, tires, tools,
    travel (aviasales/localrent), insurance, etc. — is rewritten to the correct
    goto_link from the source. Uses goto_link EXACTLY as-is, no modifications.
    """
    try:
        for site_domain, prog in partner_manager._site_map.items():
            if not prog.goto_link or prog.goto_link in text:
                continue
            for variant in (
                f"https://{site_domain}",
                f"http://{site_domain}",
                f"https://www.{site_domain}",
                f"http://www.{site_domain}",
            ):
                if variant in text:
                    text = text.replace(variant, prog.goto_link)
                    break
    except Exception:
        pass
    return text


def _clean_markdown(text: str) -> str:
    """Remove markdown formatting for Telegram."""
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'```[\s\S]*?```', lambda m: m.group(0).strip('`').strip(), text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*]\s+', '— ', text, flags=re.MULTILINE)
    return text


def _split_message(text: str, max_length: int = 4096) -> List[str]:
    """Split long text into Telegram-compatible chunks."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        split_pos = text.rfind('\n', 0, max_length)
        if split_pos < max_length // 2:
            split_pos = text.rfind('. ', 0, max_length)
        if split_pos < max_length // 2:
            split_pos = max_length

        chunks.append(text[:split_pos].rstrip())
        text = text[split_pos:].lstrip()

    return chunks
