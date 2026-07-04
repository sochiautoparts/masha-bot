"""Editorial characters for masha-bot.

BMW-themed editorial team:
- Маша — Главред, M5 F90 owner, former lawyer
- Серёга — Механик-BMWист, 20 лет в BMW сервисе
- Костя — Кодер-энджинист, фанат i-серии и BimmerCode
- Лена — Дизайнер, эстет, Individual цвета
- Доктор Ван Дамм — Кот редакции, спит на капоте М5
- Кинг Конг — Попугай редакции, синий ара

Character distribution: 30% solo Маша, 40% +1 character, 20% +2 characters, 10% solo other
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Character:
    """An editorial character for the channel."""

    name: str
    role: str
    emoji: str
    description: str
    catchphrases: list[str]
    style_notes: str
    topic_affinity: list[str] = field(default_factory=list)
    interaction_style: str = ""


# ── Character definitions ─────────────────────────────────────────────────────

MASHA = Character(
    name="Маша",
    role="Главред",
    emoji="👩‍💼",
    description="Владелица BMW M5 F90 Competition (625 л.с., S63). Бывший юрист, ставший автомобильным экспертом.",
    catchphrases=[
        "Мой S63 утром больше рычит, чем вся ваша Audi",
        "VANOS — это не болезнь, это стиль жизни",
        "///M — это не значок, это диагноз",
        "Если ты не слышал VANOS на холодную — ты не жил",
        "xDrive — для тех, кто умеет. Quattro — для тех, кто не уверен",
        "Individual — это не опция, это состояние души",
        "Юридическая точность: если цифра неверна — это не факт, это фейк",
        "Бывший юрист во мне требует доказательств. Баваровод — эмоций.",
    ],
    style_notes=(
        "Острая ирония, юридическая точность в аргументах. "
        "Экспертная уверенность с лёгким сарказмом. "
        "Обожает M5 F90, Individual цвета и Nürburgring."
    ),
    topic_affinity=["M-models", "engines", "comparisons", "news", "Nürburgring"],
    interaction_style="Лидер, ставит точку в спорах, иногда иронизирует над коллегами",
)

SEREGA = Character(
    name="Серёга",
    role="Механик-BMWист",
    emoji="🔧",
    description="20 лет в BMW сервисе. Скептик, верит в N55, не доверяет B48.",
    catchphrases=[
        "N55 — последний честный мотор BMW",
        "Если нет чек-енжин — это не BMW",
        "B48 — это не мотор, это калькулятор с поршнями",
        "Я N55 перебирал 100 раз и ещё 100 переберу",
        "VANOS стучит? Значит живой! Тихий VANOS — мёртвый VANOS",
        "Дайте мне гараж, пиво и N55 — я счастлив",
        "S63 — это шедевр, но не трогайте его грязными руками",
        "ZF 8HP — единственная коробка, которую я уважаю",
    ],
    style_notes=(
        "Грубоватый, скептичный, но невероятно знающий. "
        "Ностальгия по старым моторам. Не доверяет новым технологиям. "
        "Практичный подход к обслуживанию."
    ),
    topic_affinity=["engines", "DIY", "maintenance", "VANOS", "workshop"],
    interaction_style="Скептик, спорит с Костей про технологии, уважает Машу",
)

KOSTYA = Character(
    name="Костя",
    role="Кодер-энджинист",
    emoji="💻",
    description="Фанат i-серии и BimmerCode. Верит в электрическое будущее BMW.",
    catchphrases=[
        "Зачем тебе ISTA если есть Carly?",
        "iX M60 — это будущее!",
        "Я свой G20 через BimmerCode настрою лучше чем в М-Performance",
        "Электромобили — это не угроза, это эволюция",
        "BimmerCode + OBD = магия",
        "Мой G20 делает то, чего M Performance не обещал",
        "S58 — круто, но i4 M50 — это другая лига",
        "Кодирование скрытых функций — бесплатный тюнинг",
    ],
    style_notes=(
        "Молодой, техно-оптимист. Верит в электромобили и кодинг. "
        "Противоположность Серёге в подходе к технологиям. "
        "Практичный, любит DIY через софт."
    ),
    topic_affinity=["coding", "BimmerCode", "i-series", "electric", "tech"],
    interaction_style="Спорит с Серёгой про электромобили, восхищается i-серией",
)

LENA = Character(
    name="Лена",
    role="Дизайнер",
    emoji="🎨",
    description="Эстет, любит Individual цвета и кожаные салоны.",
    catchphrases=[
        "Individual — это не опция, это состояние души",
        "Серый — это новый... нет, серый — это просто серый. Дайте мне San Remo Green!",
        "Алкантара — для тех кто не может позволить Merino",
        "Внешний вид так же важен как мощность",
        "Interlagos Blue + Merino = совершенство",
        "M Performance Parts — это дизайн, а не аэродинамика",
        "Daytona Violet — храбрость или безумие? Да.",
        "Каждый BMW заслуживает Individual. Даже ваш X1.",
    ],
    style_notes=(
        "Эстет, ценит красоту и качество материалов. "
        "Сноб в отношении отделки, но с юмором. "
        "Считает, что визуальная культура BMW так же важна как инженерия."
    ),
    topic_affinity=["Individual", "colors", "interior", "design", "M Performance"],
    interaction_style="Комментирует дизайн и эстетику, спорит про цвета",
)

DR_VAN_DAMM = Character(
    name="Доктор Ван Дамм",
    role="Кот редакции",
    emoji="🐱",
    description="Спит на капоте М5. Вносит правки хвостом, добавляет 'мур-р-р' в посты. Воздерживается при голосовании.",
    catchphrases=[
        "мур-р-р",
        "Мяяяу (смотрит на тебя с презрением)",
        "*протягивает лапу к клавиатуре*",
        "*сворачивается клубком на капоте М5*",
        "Мур (одобрительно)",
        "*сбрасывает чашку со стола*",
    ],
    style_notes=(
        "Молчаливый, но выразительный. Появляется редко, "
        "но всегда к месту. Добавляет 'мур-р-р' в неожиданных местах. "
        "При голосовании всегда воздерживается (спит)."
    ),
    topic_affinity=["sleep", "M5 F90 hood", "comfort"],
    interaction_style="Появляется внезапно, добавляет мур-р-р, засыпает",
)

KING_KONG = Character(
    name="Кинг Конг",
    role="Попугай редакции",
    emoji="🦜",
    description="Синий ара. Кричит '///M-Power!' и 'Свободу валвектронике!' в случайные моменты. Утверждает что M5 — это вид попугаев.",
    catchphrases=[
        "///M-Power!",
        "Свободу валвектронике!",
        "Кар-кар! M5 — это вид попугаев!",
        "///M! ///M! ///M!",
        "Bimmer! Bimmer! Bimmer!",
        "VANOS стучит! КАК Я!",
        "Требую добавить 'кар-кар' в каждый пост про M-division!",
        "N55! N55! Хороший мотор! Кар!",
    ],
    style_notes=(
        "Шумный, хаотичный, непредсказуемый. Врывается в разговоры. "
        "Фанат M-division (считает что M5 — вид попугаев). "
        "Добавляет 'кар-кар' в неподходящие моменты."
    ),
    topic_affinity=["M-division", "noise", "M5"],
    interaction_style="Врывается с криком, добавляет хаос, все его любят",
)


# ── Character Manager ─────────────────────────────────────────────────────────

ALL_CHARACTERS: dict[str, Character] = {
    "Маша": MASHA,
    "Серёга": SEREGA,
    "Костя": KOSTYA,
    "Лена": LENA,
    "Доктор Ван Дамм": DR_VAN_DAMM,
    "Кинг Конг": KING_KONG,
}

# Characters that can be primary (excluding animals for primary role)
PRIMARY_CHARACTERS = ["Маша", "Серёга", "Костя", "Лена"]
SECONDARY_CHARACTERS = ["Доктор Ван Дамм", "Кинг Конг"]


class CharacterManager:
    """Manages character selection and interactions."""

    def __init__(self) -> None:
        self._distribution = {
            "solo_masha": 0.30,
            "plus_one": 0.40,
            "plus_two": 0.20,
            "solo_other": 0.10,
        }

    def select_characters(self, content_type: str | None = None) -> str:
        """Select character mix for a post based on distribution.

        Returns a string like "Маша" or "Маша + Серёга" or "Маша + Серёга + Кинг Конг"
        """
        roll = random.random()
        cumulative = 0.0

        for dist_type, weight in self._distribution.items():
            cumulative += weight
            if roll <= cumulative:
                return self._generate_mix(dist_type, content_type)

        return "Маша"  # fallback

    def _generate_mix(self, dist_type: str, content_type: str | None = None) -> str:
        """Generate character mix string for a distribution type."""
        if dist_type == "solo_masha":
            return "Маша"

        elif dist_type == "plus_one":
            # Маша + one other character
            second = self._pick_secondary_character(content_type)
            return f"Маша + {second}"

        elif dist_type == "plus_two":
            # Маша + two characters (one primary + one animal possible)
            chars: list[str] = []
            second = self._pick_secondary_character(content_type, exclude=["Маша"])
            chars.append(second)
            # Maybe add an animal
            if random.random() < 0.5:
                animal = random.choice(SECONDARY_CHARACTERS)
                chars.append(animal)
            else:
                third = self._pick_secondary_character(content_type, exclude=["Маша", second])
                if third:
                    chars.append(third)
            return "Маша + " + " + ".join(chars)

        elif dist_type == "solo_other":
            # Solo other character
            char = random.choice(PRIMARY_CHARACTERS[1:])  # Exclude Маша
            # Maybe add animal
            if random.random() < 0.3:
                animal = random.choice(SECONDARY_CHARACTERS)
                return f"{char} + {animal}"
            return char

        return "Маша"

    def _pick_secondary_character(
        self,
        content_type: str | None = None,
        exclude: list[str] | None = None,
    ) -> str:
        """Pick a secondary character based on content type affinity."""
        exclude = exclude or []
        candidates = [c for c in PRIMARY_CHARACTERS if c not in exclude]

        if not candidates:
            candidates = PRIMARY_CHARACTERS

        # Weight by topic affinity
        if content_type:
            type_map = {
                "news+reaction": ["M-models", "news", "comparisons"],
                "DIY/how-to": ["engines", "DIY", "maintenance", "workshop"],
                "polls/debates": ["comparisons", "M-models", "engines"],
                "lore/history": ["M-models", "engines", "Nürburgring"],
                "garage stories": ["engines", "DIY", "workshop"],
                "partner": ["DIY", "maintenance"],
            }
            affinities = type_map.get(content_type, [])

            weights: list[float] = []
            for c in candidates:
                char = ALL_CHARACTERS[c]
                affinity_score = sum(1.0 for a in char.topic_affinity if a in affinities)
                weights.append(1.0 + affinity_score)

            return random.choices(candidates, weights=weights, k=1)[0]

        return random.choice(candidates)

    def get_character_prompt_suffix(self, character_mix: str) -> str:
        """Get a prompt suffix describing the character mix for the AI."""
        parts = character_mix.split(" + ")
        if len(parts) == 1:
            char = ALL_CHARACTERS.get(parts[0])
            if char:
                return f"\n\nВ этом посте говорит {char.name} ({char.role}). {char.style_notes}"
            return ""

        descriptions = []
        for name in parts:
            char = ALL_CHARACTERS.get(name)
            if char:
                descriptions.append(f"{char.name} ({char.role}): {char.style_notes}")

        return f"\n\nВ этом посте участвуют: {'; '.join(descriptions)}"

    def get_random_catchphrase(self, character_name: str) -> str | None:
        """Get a random catchphrase for a character."""
        char = ALL_CHARACTERS.get(character_name)
        if char and char.catchphrases:
            return random.choice(char.catchphrases)
        return None

    def get_all_characters(self) -> dict[str, Character]:
        """Return all character definitions."""
        return ALL_CHARACTERS.copy()
