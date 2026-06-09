"""
Chat Handler — Main user interaction with AI, web search, partner links,
BMW car diagnostics, spare part search, VIN decoding, photo analysis,
and personalized communication with Masha's BMW-expert persona.
"""

import re
import logging
import base64
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
    check_rate_limit,
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
from ai.router import get_ai_router
ai_router = get_ai_router()
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
    """Handle photo messages — analyze with vision AI."""
    if not await _check_user(message):
        return

    is_group = message.chat.type in ("group", "supergroup")

    if is_group:
        caption = message.caption or ""
        simple_prompt = (
            f"Кто-то прислал фото в группе. "
            f"{'С подписью: ' + caption[:100] if caption else 'Без подписи.'} "
            f"Напиши короткий комментарий (до 200 символов) как BMW-эксперт. "
            f"Без анализа фото — просто живой комментарий."
        )
        try:
            response = await ai_router.chat(
                user_id=message.from_user.id,
                message=simple_prompt,
                route_type="comment",
                save_history=False,
                use_cache=False,
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

                response = await ai_router.analyze_image(
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
    """Handle voice messages — transcribe and process."""
    if not await _check_user(message):
        return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    await message.answer("Слушаю... 🎧")

    voice = message.voice
    text = await process_voice_message(message.bot, voice.file_id)

    if text and not text.startswith("Не удалось"):
        await _process_text_message(message, text)
    else:
        await message.answer(text)


# ── Main text message handler ─────────────────────────────────────────────────

@chat_router.message(F.text)
async def handle_text(message: Message):
    """Handle text messages — main interaction point."""
    if not await _check_user(message):
        return

    text = message.text.strip()
    if not text:
        return

    await _process_text_message(message, text)


async def _process_text_message(message: Message, text: str):
    """Core message processing with AI, search, diagnostics, parts, VIN, and personalization."""
    import random
    user_id = message.from_user.id
    chat_mode = await get_chat_mode(user_id)

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    text_lower = text.lower()
    if any(kw in text_lower for kw in ["запчаст", "деталь", "артикул", "купить", "найти запчас", "подобрать", "vin", "вин"]):
        thinking_msg = random.choice(MASHA_PHRASES["part_search"])
    elif any(kw in text_lower for kw in ["стучит", "не работает", "горит", "ошибка", "чек", "перегрев", "не заводит", "троит", "вибрац", "vanos"]):
        thinking_msg = random.choice(MASHA_PHRASES["diagnostic_start"])
    else:
        thinking_msg = random.choice(MASHA_PHRASES["thinking"])
    status_msg = await message.answer(thinking_msg)

    # ── Build extra context ────────────────────────────────────────────────

    extra_context_parts = []
    collected_partner_links = []

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
        
        response = await ai_router.decode_vin(
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
        except Exception as e:
            logger.debug(f"Partner links error: {e}")

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

    try:
        if is_diagnostic:
            response = await ai_router.diagnose_car(
                user_id=user_id,
                symptoms=text,
                extra_context=extra_context,
            )
        elif is_spare_part_query:
            response = await ai_router.find_spare_part(
                user_id=user_id,
                article=text.strip(),
                extra_context=extra_context,
            )
        else:
            response = await ai_router.chat(
                user_id=user_id,
                message=text,
                extra_context=extra_context,
            )
    except Exception as e:
        logger.error(f"AI router error: {e}")
        await message.reply("Ой, что-то я зависла 😅 Попробуй ещё раз!")
        return

    await _send_response(message, response, status_msg, collected_partner_links)


# ── Response formatting ────────────────────────────────────────────────────────

async def _send_response(message: Message, response, status_msg: Message = None, partner_links: list = None):
    """Send AI response to user with partner link formatting."""
    if not response or response.error or not response.text:
        await message.answer("Не получилось ответить 😅 Попробуй ещё раз!")
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
        return

    text = response.text
    text = _clean_markdown(text)
    text = _replace_plain_urls_with_affiliate(text)

    # Add partner links section
    if partner_links:
        text = _clean_raw_partner_urls(text, partner_links)
        partner_section = _format_partner_links_section(partner_links)
        if partner_section:
            text = text.rstrip() + "\n\n" + partner_section

    # Delete thinking status
    if status_msg:
        try:
            await status_msg.delete()
        except Exception:
            pass

    # Split if too long
    is_group = message.chat.type in ("group", "supergroup")
    max_chars = GROUP_MAX_CHARS if is_group else CHAT_MAX_CHARS

    if len(text) <= max_chars:
        await message.answer(text)
    elif len(text) <= config.TELEGRAM_TEXT_LIMIT:
        await message.answer(text)
    else:
        chunks = _split_message(text, max_length=config.TELEGRAM_TEXT_LIMIT)
        for chunk in chunks:
            await message.answer(chunk)


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
    """Replace plain partner site URLs with affiliate goto_links."""
    try:
        for site_domain in ["rossko.ru", "autopiter.ru", "avtoall.ru"]:
            prog = partner_manager.get_by_site(site_domain)
            if prog and prog.goto_link:
                # Don't replace if already has the affiliate link
                if prog.goto_link not in text:
                    text = text.replace(f"https://{site_domain}", prog.goto_link)
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
