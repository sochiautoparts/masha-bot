"""BMW Knowledge Base for masha-bot.

Three levels of BMW knowledge:
- Level 1: Model range, engines, series
- Level 2: Technical depth (VANOS, DME, ISTA, Valvetronic, xDrive, etc.)
- Level 3: Culture/slang (M-division history, Nürburgring, M colors, Individual, ///M logo)
"""

from __future__ import annotations

from typing import Any

# ══════════════════════════════════════════════════════════════════════════════
# LEVEL 1: Model Range, Engines, Series
# ══════════════════════════════════════════════════════════════════════════════

BMW_SERIES: dict[str, dict[str, Any]] = {
    "1er": {
        "name": "1 Series",
        "code": "F40",
        "body": "Hatchback",
        "years": "2019–present",
        "drive": "FWD / xDrive",
        "engines": ["B38", "B46", "B48"],
        "note": "Перешёл на передний привод с F40",
    },
    "2er": {
        "name": "2 Series",
        "variants": {
            "Coupe": {"code": "G42", "years": "2021–present", "drive": "RWD / xDrive", "engines": ["B46", "B48", "B58"]},
            "Gran Coupe": {"code": "F44", "years": "2019–present", "drive": "FWD / xDrive", "engines": ["B38", "B46", "B48"]},
            "Active Tourer": {"code": "U06", "years": "2021–present", "drive": "FWD / xDrive", "engines": ["B38", "B48"]},
        },
    },
    "3er": {
        "name": "3 Series",
        "code": "G20",
        "body": "Sedan",
        "years": "2018–present",
        "drive": "RWD / xDrive",
        "engines": ["B38", "B46", "B48", "B58"],
        "note": "Бестселлер BMW, икона спортивных седанов",
    },
    "4er": {
        "name": "4 Series",
        "variants": {
            "Coupe": {"code": "G22", "years": "2020–present", "engines": ["B46", "B48", "B58"]},
            "Convertible": {"code": "G23", "years": "2020–present", "engines": ["B46", "B48", "B58"]},
            "Gran Coupe": {"code": "G26", "years": "2021–present", "engines": ["B46", "B48", "B58"]},
        },
    },
    "5er": {
        "name": "5 Series",
        "code": "G60",
        "body": "Sedan",
        "years": "2023–present",
        "drive": "RWD / xDrive",
        "engines": ["B48", "B58", "S58"],
        "note": "Бизнес-седан нового поколения, также i5",
    },
    "6er": {
        "name": "6 Series",
        "variants": {
            "GT": {"code": "G32", "years": "2017–present", "engines": ["B48", "B58"]},
        },
        "note": "В основном GT, купе заменено 8er",
    },
    "7er": {
        "name": "7 Series",
        "code": "G70",
        "body": "Sedan",
        "years": "2022–present",
        "drive": "RWD / xDrive",
        "engines": ["B58", "S68", "electric"],
        "note": "Флагман, также i7 полностью электрический",
    },
    "8er": {
        "name": "8 Series",
        "variants": {
            "Coupe": {"code": "G15", "years": "2018–present", "engines": ["B58", "S63"]},
            "Gran Coupe": {"code": "G16", "years": "2019–present", "engines": ["B58", "S63"]},
            "Convertible": {"code": "G14", "years": "2018–present", "engines": ["B58", "S63"]},
        },
    },
    "X1": {"code": "U11", "years": "2022–present", "drive": "FWD / xDrive / sDrive", "engines": ["B38", "B46", "B48"], "note": "Также iX1"},
    "X2": {"code": "U10", "years": "2023–present", "drive": "FWD / xDrive", "engines": ["B38", "B46", "B48"], "note": "Также iX2"},
    "X3": {"code": "G45", "years": "2024–present", "drive": "RWD / xDrive", "engines": ["B48", "B58", "S58"], "note": "Также iX3"},
    "X4": {"code": "G02", "years": "2018–present", "drive": "xDrive", "engines": ["B48", "B58", "S58"]},
    "X5": {"code": "G05", "years": "2018–present", "drive": "xDrive", "engines": ["B58", "S68"], "note": "Также X5 M Competition с S68"},
    "X6": {"code": "G06", "years": "2019–present", "drive": "xDrive", "engines": ["B58", "S68"]},
    "X7": {"code": "G07", "years": "2018–present", "drive": "xDrive", "engines": ["B58", "S68"], "note": "Трёхрядный SUV, также Alpina XB7"},
    "XM": {"code": "G09", "years": "2022–present", "drive": "xDrive", "engines": ["S68 + electric"], "note": "M-гибрид, первый standalone M SUV"},
    "Z4": {"code": "G29", "years": "2018–present", "drive": "RWD", "engines": ["B48", "B58"], "note": "Последний родстер BMW"},
    "iX": {"code": "I20", "years": "2021–present", "drive": "AWD", "engines": ["electric"], "note": "Электрический SUV, также iX M60"},
    "i4": {"code": "G26 BEV", "years": "2021–present", "drive": "RWD / AWD", "engines": ["electric"], "note": "Электрический Gran Coupe, также i4 M50"},
    "i5": {"code": "G60 BEV", "years": "2023–present", "drive": "RWD / AWD", "engines": ["electric"], "note": "Также i5 M60"},
    "i7": {"code": "G70 BEV", "years": "2022–present", "drive": "AWD", "engines": ["electric"], "note": "Электрический флагман, i7 M70"},
    "iX1": {"code": "U11 BEV", "years": "2022–present", "drive": "AWD", "engines": ["electric"]},
    "iX2": {"code": "U10 BEV", "years": "2023–present", "drive": "AWD", "engines": ["electric"]},
    "iX3": {"code": "G08 BEV", "years": "2020–present", "drive": "RWD", "engines": ["electric"]},
}

BMW_M_MODELS: dict[str, dict[str, Any]] = {
    "M2": {"code": "G87", "years": "2022–present", "engine": "S58", "hp": 460, "drive": "RWD / xDrive", "body": "Coupe"},
    "M3": {"code": "G80", "years": "2020–present", "engine": "S58", "hp": 473, "hp_comp": 503, "drive": "RWD / xDrive", "body": "Sedan"},
    "M3 CS": {"code": "G80 CS", "years": "2023–present", "engine": "S58", "hp": 543, "drive": "xDrive", "body": "Sedan"},
    "M4": {"code": "G82", "years": "2020–present", "engine": "S58", "hp": 473, "hp_comp": 503, "drive": "RWD / xDrive", "body": "Coupe"},
    "M4 CSL": {"code": "G82 CSL", "years": "2022", "engine": "S58", "hp": 543, "drive": "RWD", "body": "Coupe", "note": "Limited edition"},
    "M5": {"code": "F90", "years": "2017–2024", "engine": "S63", "hp": 600, "hp_comp": 625, "drive": "xDrive", "body": "Sedan"},
    "M5 CS": {"code": "F90 CS", "years": "2021–2022", "engine": "S63", "hp": 635, "drive": "xDrive", "body": "Sedan", "note": "Limited edition"},
    "M5 G90": {"code": "G90", "years": "2024–present", "engine": "S68 + electric", "hp": 717, "drive": "xDrive", "body": "Sedan", "note": "Hybrid M5"},
    "M8": {"code": "G15/G16", "years": "2019–present", "engine": "S63", "hp": 600, "hp_comp": 617, "drive": "xDrive", "body": "Coupe/GC/Convert"},
    "X3 M": {"code": "F97", "years": "2019–present", "engine": "S58", "hp": 473, "hp_comp": 503, "drive": "xDrive"},
    "X4 M": {"code": "F98", "years": "2019–present", "engine": "S58", "hp": 473, "hp_comp": 503, "drive": "xDrive"},
    "X5 M": {"code": "F95", "years": "2019–present", "engine": "S68", "hp": 617, "hp_comp": 635, "drive": "xDrive"},
    "X6 M": {"code": "F96", "years": "2019–present", "engine": "S68", "hp": 617, "hp_comp": 635, "drive": "xDrive"},
    "XM": {"code": "G09", "years": "2022–present", "engine": "S68 + electric", "hp": 644, "hp_label": 738, "drive": "xDrive", "note": "Label version is the top"},
    "i4 M50": {"code": "G26 BEV M", "years": "2021–present", "engine": "electric", "hp": 536, "drive": "AWD", "note": "Электрическая M"},
    "iX M60": {"code": "I20 M", "years": "2022–present", "engine": "electric", "hp": 610, "drive": "AWD", "note": "Электрическая M SUV"},
    "i5 M60": {"code": "G60 BEV M", "years": "2023–present", "engine": "electric", "hp": 590, "drive": "AWD"},
    "i7 M70": {"code": "G70 BEV M", "years": "2023–present", "engine": "electric", "hp": 650, "drive": "AWD"},
}

BMW_ALPINA_MODELS: dict[str, dict[str, Any]] = {
    "Alpina B3": {"code": "G20", "engine": "B58 upgraded", "hp": 462, "note": "Турбо-заряженная 3er"},
    "Alpina B4": {"code": "G22", "engine": "B58 upgraded", "hp": 462, "note": "Турбо-заряженная 4er"},
    "Alpina B5": {"engine": "V8 twin-turbo", "hp": 600, "note": "Турбо-заряженная 5er"},
    "Alpina B7": {"code": "G12/G70", "engine": "V8 twin-turbo", "hp": 600, "note": "Турбо-заряженная 7er"},
    "Alpina B8": {"code": "G16", "engine": "V8 twin-turbo", "hp": 612, "note": "Турбо-заряженная 8er GC"},
    "Alpina XB7": {"code": "G07", "engine": "V8 twin-turbo", "hp": 621, "note": "Турбо-заряженный X7"},
}

BMW_ENGINES: dict[str, dict[str, Any]] = {
    "B38": {"type": "I3 turbo", "displacement": "1.5L", "hp_range": "109–140", "note": "Базовый 3-цилиндровый, 1er/2er/X1/X2"},
    "B46": {"type": "I4 turbo", "displacement": "2.0L", "hp_range": "184–255", "note": "Базовый 4-цилиндровый, множественные модели"},
    "B48": {"type": "I4 turbo", "displacement": "2.0L", "hp_range": "184–302", "note": "Улучшенный 4-цилиндр, широкий диапазон"},
    "B58": {"type": "I6 turbo", "displacement": "3.0L", "hp_range": "322–382", "note": "Легендарная рядная шестёрка, замена N55"},
    "S58": {"type": "I6 twin-turbo", "displacement": "3.0L", "hp_range": "473–543", "note": "M-версия B58, M3/M4/X3M/X4M"},
    "S63": {"type": "V8 twin-turbo", "displacement": "4.4L", "hp_range": "600–635", "note": "M-версия V8, M5 F90/M8/X5M/X6M"},
    "S68": {"type": "V8 twin-turbo mild hybrid", "displacement": "4.4L", "hp_range": "523–635", "note": "Новое поколение V8 с мягким гибридом, X5M/X6M/M5 G90"},
    "N55": {"type": "I6 turbo", "displacement": "3.0L", "hp_range": "302–335", "note": "Легендарный предшественник B58, один турбокомпрессор TwinPower"},
    "N20": {"type": "I4 turbo", "displacement": "2.0L", "hp_range": "181–245", "note": "Предшественник B46/B48, проблемы с цепью ГРМ"},
}

# ══════════════════════════════════════════════════════════════════════════════
# LEVEL 2: Technical Depth
# ══════════════════════════════════════════════════════════════════════════════

BMW_TECH: dict[str, dict[str, Any]] = {
    "VANOS": {
        "full_name": "Variable Nockenwellensteuerung",
        "description": "Система изменения фаз газораспределения BMW",
        "how_it_works": "Масляные клапаны управляют положением распредвалов для оптимального наполнения цилиндров",
        "common_issues": "Стук на холодную (VANOS rattle), износ соленоидов, утечки масла",
        "models_affected": "Все BMW с VANOS (M50TU и новее)",
        "fun_fact": "VANOS стук — это как татуировка: у каждого баваровода есть история",
    },
    "Valvetronic": {
        "full_name": "BMW Valvetronic",
        "description": "Система бесступенчатого изменения высоты подъёма впускных клапанов",
        "how_it_works": "Эксцентриковый вал управляет подъёмом клапанов, заменяя дроссельную заслонку",
        "common_issues": "Износ эксцентрикового вала, проблемы с моторчиком Valvetronic",
        "models_affected": "N-серия двигателей (N46, N52, N53, N54, N55, N62 и др.)",
        "fun_fact": "Valvetronic — причина, по которой BMW не нужны дроссельные заслонки (почти)",
    },
    "xDrive": {
        "full_name": "BMW xDrive",
        "description": "Система полного привода BMW",
        "how_it_works": "Межосевой дифференциал с электронным управлением, 40/60 по умолчанию (задний.bias)",
        "common_issues": "Проблемы с раздаткой (VTG), замена масла каждые 60к км",
        "models_affected": "Большинство моделей BMW с xDrive",
        "fun_fact": "xDrive = RWD пока не понадобится AWD. Это не Quattro.",
    },
    "DME": {
        "full_name": "Digital Motor Electronics",
        "description": "Электронный блок управления двигателем BMW",
        "how_it_works": "Управляет впрыском, зажиганием, VANOS, Valvetronic, турбонагнетателями",
        "common_issues": "Проблемы с прошивкой, отказ реле DME (N54/N55)",
        "models_affected": "Все современные BMW",
    },
    "DDE": {
        "full_name": "Digital Diesel Electronics",
        "description": "Электронный блок управления дизельным двигателем BMW",
        "note": "Аналог DME для дизелей",
    },
    "ISTA": {
        "full_name": "Integrated Service Technical Application",
        "description": "Официальная диагностическая система BMW",
        "how_it_works": "Полная диагностика, кодирование, программирование всех систем BMW",
        "alternatives": "INPA (старая), E-Sys (кодирование), Carly, BimmerCode (мобильные)",
        "fun_fact": "ISTA — это как法律 для юриста: без него ты не специалист",
    },
    "INPA": {
        "full_name": "Interpreter for Test Procedures",
        "description": "Старая диагностическая программа BMW",
        "note": "Ещё используется энтузиастами, но официально заменена ISTA",
    },
    "DKG": {
        "full_name": "Doppelkupfungsgetriebe",
        "description": "Роботизированная КПП с двойным сцеплением BMW (M-DCT)",
        "how_it_works": "Два сцепления для мгновенного переключения, одно для чётных, другое для нечётных передач",
        "note": "Устанавливалась на M3 E9X, M5 F10, 135i и др.",
    },
    "ZF 8HP": {
        "full_name": "ZF 8-Speed Automatic",
        "description": "8-ступенчатый автомат ZF, используется в большинстве BMW",
        "how_it_works": "Планетарная КПП с гидротрансформатором, быстрое переключение",
        "note": "Одна из лучших автоматических КПП в мире",
        "fun_fact": "ZF 8HP настолько хороша, что даже Rolls-Royce использует её",
    },
    "DSC": {
        "full_name": "Dynamic Stability Control",
        "description": "Система динамической стабилизации BMW",
        "modes": "DSC ON, DTC (Dynamic Traction Control), DSC OFF",
        "fun_fact": "DTC — это режим 'я знаю что делаю, но не уверен'",
    },
    "Carly": {
        "description": "Мобильное приложение для диагностики и кодирования BMW",
        "features": "Диагностика, кодирование, used car check",
        "note": "Популярная альтернатива ISTA для простых задач",
    },
    "BimmerCode": {
        "description": "Приложение для кодирования BMW",
        "features": "Кодирование скрытых функций, настройка параметров",
        "note": "Любимый инструмент Кости-кодера",
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# LEVEL 3: Culture & Slang
# ══════════════════════════════════════════════════════════════════════════════

BMW_CULTURE: dict[str, dict[str, Any]] = {
    "M-division": {
        "description": "BMW M GmbH — подразделение высокопроизводительных автомобилей BMW",
        "founded": "1972",
        "founder": "Jochen Neerpasch",
        "headquarters": "Мюнхен, Германия",
        "philosophy": "M — это не просто мощность, это управляемость, баланс, эмоции",
        "logo_meaning": "Три полоски M: синяя (BMW), фиолетовая (переход), красная (Motorsport/Motul)",
        "history": "Начало в моторспорте → первые дорожные M-машины → мировое доминирование",
    },
    "Nürburgring": {
        "description": "Nürburgring Nordschleife — 'Зелёный ад', тестовый трек BMW",
        "length": "20.832 км",
        "turns": "73 (33 левых, 40 правых)",
        "bmw_records": [
            "M5 CS F90: 7:29.57",
            "M8 Gran Coupe: 7:30.00",
            "M4 CSL: 7:20.00",
            "XM Label: 7:55",
        ],
        "fun_fact": "BMW тестирует каждую M-модель на Нюрбургринге — это обязательный ритуал",
    },
    "M_colors": {
        "description": "Фирменные цвета M-division: синий, фиолетовый (или тёмно-синий), красный",
        "blue": "BMW",
        "purple": "Переход / Trademark",
        "red": "Motorsport / Motul partnership",
        "note": "Эти три полоски — один из самых узнаваемых автомобильных символов в мире",
    },
    "Individual": {
        "description": "BMW Individual — программа индивидуализации BMW",
        "features": "Эксклюзивные цвета, кожа Merino, отделка ручной работы",
        "popular_colors": [
            "San Remo Green",
            "Interlagos Blue",
            "Long Beach Blue",
            "Austin Yellow",
            "Macau Blue",
            "Imola Red",
            "Laguna Seca Blue",
            "Daytona Violet",
        ],
        "fun_fact": "Individual цвет может добавить $5,000-15,000 к цене, но это того стоит",
    },
    "///M logo": {
        "description": "Три цветные полоски — символ M-division",
        "meaning": "Синий = BMW, Красный = Motorsport/Motul, Фиолетовый = Переход",
        "usage": "///M пишется именно так — три слеша и M",
        "note": "В сообществе используется как символ принадлежности к M-культуре",
    },
    "bimmer_vs_beemer": {
        "description": "Разница между Bimmer и Beemer",
        "bimmer": "Автомобили BMW",
        "beemer": "Мотоциклы BMW",
        "note": "Путать их — ошибка новичка. Истинные фанаты знают разницу.",
    },
}

BMW_SLANG: dict[str, str] = {
    "баварец": "BMW (неформальное, ласковое)",
    "эмка": "BMW M-модель (M3, M4, M5 и т.д.)",
    "мощь": "Мощность, потенциал двигателя (иронично-уважительно)",
    "///M": "Символ M-division, три слеша + M",
    "bimmer": "Автомобиль BMW (не мотоцикл!)",
    "beemer": "Мотоцикл BMW (не автомобиль!)",
    "VANOS rattle": "Характерный стук VANOS на холодную — 'приветствие' BMW",
    "чек-енжин": "Check Engine — 'лучший друг' владельца BMW",
    "M-tax": "Наценка на всё, что с шильдиком M",
    "bimmer tax": "Наценка на запчасти BMW",
    "кольцевоз": "Частый гость Нюрбургринга",
    "заварка": "Дифференциал повышенного трения (welded diff)",
    "тапок": "BMW Z3 M Coupe (Rooney Shoe / Clown Shoe)",
    "пылесос": "BMW S1000RR (мотоцикл)",
    "правый руль": "Не относится к BMW, но иногда шутят про 'правый привод к правой жизни'",
}

BMW_INDIVIDUAL_COLORS: list[dict[str, str]] = [
    {"name": "San Remo Green", "code": "417", "era": "Current", "vibe": "Элегантный зелёный, фаворит Маши"},
    {"name": "Interlagos Blue", "code": "417/A46", "era": "E9X M3", "vibe": "Глубокий синий с фиолетовым отливом"},
    {"name": "Long Beach Blue", "code": "E46/E92", "era": "E46 M3 CSL / E92 M3", "vibe": "Яркий синий, легенда"},
    {"name": "Austin Yellow", "code": "B44", "era": "F8X M3/M4", "vibe": "Ядовито-жёлтый, любишь или ненавидишь"},
    {"name": "Macau Blue", "code": "250", "era": "E34 M5", "vibe": "Классический тёмно-синий"},
    {"name": "Imola Red", "code": "405/A44", "era": "E39 M5 / E46 M3", "vibe": "Агрессивный красный, трек-день"},
    {"name": "Laguna Seca Blue", "code": "448", "era": "E46 M3", "vibe": "Невероятно яркий синий"},
    {"name": "Daytona Violet", "code": "C10", "era": "E36 M3 / G80 M3", "vibe": "Фиолетовый для смелых"},
    {"name": "Sakhir Orange", "code": "S04", "era": "F80/F82 M3/M4", "vibe": "Оранжевый M-Performance"},
    {"name": "Portimao Blue", "code": "C2E", "era": "G80/G82 M3/M4", "vibe": "Современный синий M"},
]

# ── Validation helpers ─────────────────────────────────────────────────────────

def is_valid_bmw_model(name: str) -> bool:
    """Check if a BMW model name is valid (exists in the knowledge base)."""
    name_lower = name.lower().strip()
    # Check M models
    for key in BMW_M_MODELS:
        if key.lower() == name_lower:
            return True
    # Check series
    for key in BMW_SERIES:
        if key.lower().replace(" ", "") == name_lower.replace(" ", ""):
            return True
    # Check common codes
    all_codes = []
    for v in BMW_M_MODELS.values():
        if "code" in v:
            all_codes.append(v["code"].lower())
    for v in BMW_SERIES.values():
        if "code" in v:
            all_codes.append(v["code"].lower())
    return name_lower in all_codes


def is_valid_bmw_engine(code: str) -> bool:
    """Check if a BMW engine code is valid."""
    return code.upper() in BMW_ENGINES


def get_engine_for_model(model: str) -> list[str]:
    """Get the engine(s) associated with a BMW model."""
    model_lower = model.lower().strip()

    # Check M models
    for key, val in BMW_M_MODELS.items():
        if key.lower() == model_lower:
            engine = val.get("engine", "")
            return [engine] if engine else []

    # Check series
    for key, val in BMW_SERIES.items():
        if key.lower().replace(" ", "") == model_lower.replace(" ", ""):
            return val.get("engines", [])

    return []


def validate_hp_for_model(model: str, claimed_hp: int) -> dict[str, Any]:
    """Validate if a claimed HP figure is realistic for a BMW model."""
    model_lower = model.lower().strip()

    # Check M models
    for key, val in BMW_M_MODELS.items():
        if key.lower() == model_lower:
            hp = val.get("hp", 0)
            hp_comp = val.get("hp_comp", hp)
            hp_label = val.get("hp_label", hp_comp)
            max_hp = max(hp, hp_comp, hp_label)

            if claimed_hp <= 0:
                return {"valid": False, "reason": "HP must be positive"}
            if claimed_hp > max_hp * 1.15:  # Allow 15% for tuning
                return {
                    "valid": False,
                    "reason": f"{model} stock HP is {hp}-{hp_label}, claimed {claimed_hp} seems too high",
                    "actual_max": max_hp,
                }
            return {
                "valid": True,
                "reason": f"HP claim within expected range for {model}",
                "actual_range": f"{hp}-{hp_label}",
            }

    # Check regular models
    for key, val in BMW_SERIES.items():
        if key.lower().replace(" ", "") == model_lower.replace(" ", ""):
            engines = val.get("engines", [])
            max_hp = 0
            for eng_code in engines:
                if eng_code in BMW_ENGINES:
                    hp_range = BMW_ENGINES[eng_code].get("hp_range", "0-0")
                    try:
                        top = int(hp_range.split("–")[-1])
                        max_hp = max(max_hp, top)
                    except (ValueError, IndexError):
                        pass
            if max_hp > 0 and claimed_hp > max_hp * 1.2:
                return {
                    "valid": False,
                    "reason": f"HP claim {claimed_hp} seems high for {model} (expected max ~{max_hp})",
                }
            return {"valid": True, "reason": "Within plausible range"}

    return {"valid": None, "reason": f"Model {model} not found in knowledge base"}


# ── Known AI hallucinations about BMW ─────────────────────────────────────────

BMW_HALLUCINATIONS: list[dict[str, str]] = [
    {"myth": "BMW M7", "truth": "Не существует M7. Есть Alpina B7 или M760i (не M-division)"},
    {"myth": "BMW M1 modern", "truth": "Современного M1 нет. Оригинальный M1 (1978-1981) — единственный"},
    {"myth": "BMW V10 in M5 F90", "truth": "F90 использует S63 V8 twin-turbo. V10 S85 был в E60 M5"},
    {"myth": "N54 in M3 E92", "truth": "E92 M3 использует S65 V8, а не N54. N54 в 335i/135i"},
    {"myth": "BMW V12 in 7 Series current", "truth": "Нынешний G70 7er не имеет V12. Последний V12 был M760i G12"},
    {"myth": "M3 only comes in sedan", "truth": "M3 G80 — седан, M4 G82 — купе. Но E92 M3 был купе"},
    {"myth": "All M cars are manual", "truth": "Современные M — только автомат (ZF 8HP) или M-DCT"},
    {"myth": "xDrive makes BMW slower", "truth": "M5 F90 xDrive быстрее RWD версии (0-100 в 3.3 vs 3.5 сек)"},
    {"myth": "B58 is unreliable", "truth": "B58 — один из самых надёжных двигателей BMW за последние годы"},
    {"myth": "BMW i8 is an M car", "truth": "i8 — это плагин-гибрид спорткар, не M-модель"},
]
