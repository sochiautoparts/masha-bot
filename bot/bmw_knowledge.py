"""
BMW Knowledge Base — Complete BMW model range, engines, problems, culture, slang.
Used as context for AI generation and news filtering.
This is Masha's UNIQUE differentiator from Asya.
"""

from typing import Dict, List, Optional


# ── BMW Model Range ─────────────────────────────────────────────────────────────

BMW_MODELS = {
    "1 Series": {
        "generations": {
            "E81/E82/E87/E88": {"years": "2004-2013", "engines": ["N46", "N52", "N54", "N55", "N47"], "body": "hatchback/coupe/convertible"},
            "F20/F21": {"years": "2011-2019", "engines": ["N13", "N20", "N55", "B38", "B48", "B47"], "body": "hatchback"},
            "F40": {"years": "2019-present", "engines": ["B38", "B48", "B46"], "body": "hatchback"},
        },
        "m_model": "M135i/M140i (F20), M135i (F40)",
    },
    "2 Series": {
        "generations": {
            "F22/F23": {"years": "2013-2021", "engines": ["N20", "N55", "B48", "B58"], "body": "coupe/convertible"},
            "G42": {"years": "2021-present", "engines": ["B48", "B58"], "body": "coupe"},
            "F44": {"years": "2019-present", "engines": ["B38", "B48", "B46"], "body": "Gran Coupe"},
            "F87": {"years": "2016-2021", "engines": ["N55", "S55"], "body": "M2/M2 Competition/M2 CS"},
            "G87": {"years": "2022-present", "engines": ["S58"], "body": "M2"},
        },
        "m_model": "M2 (F87), M2 Competition (F87), M2 CS (F87), M2 (G87)",
    },
    "3 Series": {
        "generations": {
            "E90/E91/E92/E93": {"years": "2005-2013", "engines": ["N46", "N52", "N53", "N54", "N55", "N57", "M57"], "body": "sedan/wagon/coupe/convertible"},
            "F30/F31/F34/F35": {"years": "2012-2019", "engines": ["N20", "N26", "N55", "B48", "B58", "B47", "B38"], "body": "sedan/wagon/Gran Turismo"},
            "G20/G21": {"years": "2018-present", "engines": ["B48", "B58", "B46", "B38", "B47"], "body": "sedan/wagon"},
        },
        "m_model": "M3 (E90), M3 (F80), M3 (G80)",
    },
    "4 Series": {
        "generations": {
            "F32/F33/F36": {"years": "2013-2020", "engines": ["N20", "N55", "B48", "B58", "B47"], "body": "coupe/convertible/Gran Coupe"},
            "G22/G23/G26": {"years": "2020-present", "engines": ["B48", "B58", "B46"], "body": "coupe/convertible/Gran Coupe"},
        },
        "m_model": "M4 (F82/F83), M4 Competition (F82), M4 (G82/G83)",
    },
    "5 Series": {
        "generations": {
            "E60/E61": {"years": "2003-2010", "engines": ["N52", "N53", "N54", "N55", "N62", "N63", "M57", "M5X"], "body": "sedan/wagon"},
            "F10/F11": {"years": "2010-2017", "engines": ["N20", "N55", "N63", "B48", "B58", "B47", "N57"], "body": "sedan/wagon"},
            "G30/G31": {"years": "2017-2023", "engines": ["B48", "B58", "N63", "B57", "B46"], "body": "sedan/wagon"},
            "G60/G61": {"years": "2023-present", "engines": ["B48", "B58", "B46"], "body": "sedan/wagon"},
        },
        "m_model": "M5 (E60), M5 (F10), M5 (F90), M5 (G90)",
    },
    "7 Series": {
        "generations": {
            "F01/F02": {"years": "2008-2015", "engines": ["N52", "N55", "N63", "N74", "N57"], "body": "sedan"},
            "G11/G12": {"years": "2015-2022", "engines": ["B48", "B58", "N63", "N74", "B57"], "body": "sedan"},
            "G70": {"years": "2022-present", "engines": ["B58", "S68", "B44"], "body": "sedan"},
        },
        "m_model": "M760Li (G12), M7 (G70 — not official M)",
    },
    "8 Series": {
        "generations": {
            "G15/G14/G16": {"years": "2018-present", "engines": ["B58", "N63", "S63"], "body": "coupe/convertible/Gran Coupe"},
        },
        "m_model": "M8 (G15), M8 Competition (G15)",
    },
    "X1": {
        "generations": {
            "E84": {"years": "2009-2015", "engines": ["N46", "N20", "N52", "N47"], "body": "SAV"},
            "F48": {"years": "2015-2022", "engines": ["B38", "B48", "B46", "B47"], "body": "SAV"},
            "U11": {"years": "2022-present", "engines": ["B38", "B48", "B46"], "body": "SAV"},
        },
    },
    "X2": {
        "generations": {
            "F39": {"years": "2017-2023", "engines": ["B38", "B48", "B46", "B47"], "body": "SAC"},
            "U10": {"years": "2023-present", "engines": ["B38", "B48", "B46"], "body": "SAC"},
        },
        "m_model": "M35i (F39), M35i (U10)",
    },
    "X3": {
        "generations": {
            "E83": {"years": "2003-2010", "engines": ["N46", "N52", "N54", "M57"], "body": "SAV"},
            "F25": {"years": "2010-2017", "engines": ["N20", "N55", "N57", "B47"], "body": "SAV"},
            "G01": {"years": "2017-2024", "engines": ["B48", "B58", "B46", "B47", "B38"], "body": "SAV"},
            "G45": {"years": "2024-present", "engines": ["B48", "B58", "B46"], "body": "SAV"},
        },
        "m_model": "X3 M (F97), X3 M Competition (F97)",
    },
    "X4": {
        "generations": {
            "F26": {"years": "2014-2018", "engines": ["N20", "N55", "B48", "B58", "B47"], "body": "SAC"},
            "G02": {"years": "2018-present", "engines": ["B48", "B58", "B46", "B47"], "body": "SAC"},
        },
        "m_model": "X4 M (G06), X4 M Competition (G06)",
    },
    "X5": {
        "generations": {
            "E70": {"years": "2006-2013", "engines": ["N52", "N55", "N63", "M57", "M5X"], "body": "SAV"},
            "F15": {"years": "2013-2018", "engines": ["N20", "N55", "N63", "N57", "B47"], "body": "SAV"},
            "G05": {"years": "2018-present", "engines": ["B48", "B58", "N63", "S68", "B57", "B46"], "body": "SAV"},
        },
        "m_model": "X5 M (F85), X5 M Competition (F85), X5 M (G05 LCI)",
    },
    "X6": {
        "generations": {
            "E71": {"years": "2008-2014", "engines": ["N55", "N63", "M57", "S63"], "body": "SAC"},
            "F16": {"years": "2014-2019", "engines": ["N20", "N55", "N63", "N57", "B47"], "body": "SAC"},
            "G06": {"years": "2019-present", "engines": ["B58", "N63", "S63", "S68", "B46"], "body": "SAC"},
        },
        "m_model": "X6 M (F86), X6 M Competition (F86), X6 M (G06)",
    },
    "X7": {
        "generations": {
            "G07": {"years": "2018-present", "engines": ["B58", "N63", "S68", "B57"], "body": "SAV"},
        },
        "m_model": "Alpina XB7 (G07)",
    },
    "iX": {
        "generations": {
            "I20": {"years": "2021-present", "engines": ["electric"], "body": "SAV"},
        },
    },
    "Z4": {
        "generations": {
            "E89": {"years": "2009-2016", "engines": ["N20", "N52", "N54", "N55"], "body": "roadster"},
            "G29": {"years": "2018-present", "engines": ["B48", "B58"], "body": "roadster"},
        },
    },
}


# ── BMW M Models — Detailed ──────────────────────────────────────────────────────

BMW_M_MODELS = {
    "M2 (F87)": {"engine": "N55 (2016-2018), S55 (2018-2021)", "power": "370-410 hp (N55), 405-450 hp (S55)", "transmission": "6MT / 7DCT", "drive": "RWD"},
    "M2 CS (F87)": {"engine": "S55", "power": "450 hp", "transmission": "6MT / 7DCT", "drive": "RWD"},
    "M2 (G87)": {"engine": "S58", "power": "460-473 hp", "transmission": "6MT / 8AT", "drive": "RWD"},
    "M3 (E90)": {"engine": "S65 V8", "power": "420 hp", "transmission": "6MT / 7DCT", "drive": "RWD", "known_issues": "rod bearings, throttle actuators, idle actuators"},
    "M3 (F80)": {"engine": "S55", "power": "425-493 hp", "transmission": "6MT / 7DCT", "drive": "RWD", "known_issues": "crank hub (CCT), rod bearings, oil cooler"},
    "M3 (G80)": {"engine": "S58", "power": "473-503 hp", "transmission": "6MT / 8AT", "drive": "RWD/xDrive", "known_issues": "fuel system, early DME updates"},
    "M4 (F82/F83)": {"engine": "S55", "power": "425-493 hp", "transmission": "6MT / 7DCT", "drive": "RWD", "known_issues": "crank hub, rod bearings"},
    "M4 (G82/G83)": {"engine": "S58", "power": "473-503 hp", "transmission": "6MT / 8AT", "drive": "RWD/xDrive"},
    "M5 (E39)": {"engine": "S62 V8", "power": "400 hp", "transmission": "6MT", "drive": "RWD", "known_issues": "VANOS, rod bearings, MAF, cooling", "legend": "The greatest M5 ever made"},
    "M5 (E60)": {"engine": "S85 V10", "power": "500 hp", "transmission": "7SMG / 6MT", "drive": "RWD", "known_issues": "rod bearings, VANOS, throttle actuators, SMG pump"},
    "M5 (F10)": {"engine": "S63B44T0", "power": "560-575 hp", "transmission": "7DCT", "drive": "RWD", "known_issues": "turbo wastegate rattle, oil consumption, CP4 fuel pump"},
    "M5 (F90)": {"engine": "S63B44T4", "power": "600-617 hp", "transmission": "8AT (ZF 8HP M)", "drive": "M xDrive (RWD/4WD/4WD Sport)", "known_issues": "transfer case, oil consumption, brake wear"},
    "M5 (G90)": {"engine": "S68 + e-motor (plug-in hybrid)", "power": "727 hp", "transmission": "8AT", "drive": "M xDrive", "weight": "~2435 kg", "note": "Heavy hybrid — controversial"},
    "M8 (G15)": {"engine": "S63B44T4", "power": "600-617 hp", "transmission": "8AT", "drive": "M xDrive"},
    "X3 M (F97)": {"engine": "S58", "power": "473-503 hp", "transmission": "8AT", "drive": "M xDrive"},
    "X4 M (G06)": {"engine": "S58", "power": "473-503 hp", "transmission": "8AT", "drive": "M xDrive"},
    "X5 M (F85)": {"engine": "S63B44T0", "power": "575 hp", "transmission": "8AT", "drive": "M xDrive"},
    "X5 M (G05 LCI)": {"engine": "S68", "power": "617 hp", "transmission": "8AT", "drive": "M xDrive"},
    "X6 M (F86)": {"engine": "S63B44T0", "power": "575 hp", "transmission": "8AT", "drive": "M xDrive"},
    "X6 M (G06)": {"engine": "S63B44T4 / S68", "power": "600-617 hp", "transmission": "8AT", "drive": "M xDrive"},
}


# ── BMW Engines — Technical Reference ────────────────────────────────────────────

BMW_ENGINES = {
    # Modular family (B-series)
    "B38": {"type": "I3", "displacement": "1.5L", "aspiration": "turbo", "power": "109-140 hp", "cars": "1er F20/F40, X1, X2, 3er G20 (base)", "reliability": "good"},
    "B46": {"type": "I4", "displacement": "2.0L", "aspiration": "turbo", "power": "184-255 hp", "cars": "330i, X3 sDrive, 530i", "reliability": "good", "issues": "oil consumption, coolant loss"},
    "B48": {"type": "I4", "displacement": "2.0L", "aspiration": "turbo", "power": "184-255 hp", "cars": "330i, 430i, 530i, X3, X5 (base)", "reliability": "good"},
    "B58": {"type": "I6", "displacement": "3.0L", "aspiration": "turbo", "power": "320-382 hp", "cars": "M340i, M440i, M550i, 740i, X3 M40i, X5 xDrive40i, Supra", "reliability": "excellent — one of the best modern BMW engines", "note": "Ward's 10 Best Engines"},
    # N-series (previous generation)
    "N20": {"type": "I4", "displacement": "2.0L", "aspiration": "turbo", "power": "181-241 hp", "cars": "328i, 428i, X1, X3, 528i", "reliability": "poor", "issues": "timing chain (fatal!), oil pump, turbo wastegate"},
    "N54": {"type": "I6", "displacement": "3.0L", "aspiration": "twin-turbo", "power": "300-335 hp", "cars": "335i, 135i, 535i, Z4 35i", "reliability": "legendary but problematic", "issues": "HPFP, injectors, turbos, wastegate, oil cooler", "culture": "The tuning king — 500+ hp easy, but at what cost?"},
    "N55": {"type": "I6", "displacement": "3.0L", "aspiration": "twin-scroll turbo", "power": "300-320 hp", "cars": "335i, 435i, 135i, M2 (F87), 535i, X5 35i", "reliability": "moderate", "issues": "timing chain, valve cover, oil filter housing, VANOS"},
    "N63": {"type": "V8", "displacement": "4.4L", "aspiration": "twin-turbo", "power": "400-523 hp", "cars": "550i, 650i, 750i, X5 50i, X6 50i, M550i", "reliability": "poor (early), improved later", "issues": "valve stem seals, timing chain, turbo oil feed, CP4 fuel pump"},
    # S-series (M engines)
    "S55": {"type": "I6", "displacement": "3.0L", "aspiration": "twin-turbo", "power": "425-493 hp", "cars": "M3 F80, M4 F82, M2 Competition/CS", "reliability": "good", "issues": "crank hub (CCT), rod bearings (preventive)"},
    "S58": {"type": "I6", "displacement": "3.0L", "aspiration": "twin-turbo", "power": "473-503 hp", "cars": "M3 G80, M4 G82, M2 G87, X3 M, X4 M", "reliability": "excellent so far"},
    "S63B44T0": {"type": "V8", "displacement": "4.4L", "aspiration": "twin-turbo", "power": "560-575 hp", "cars": "M5 F10, M6 F12, X5 M F85, X6 M F86", "issues": "turbo wastegate, oil consumption, CP4 fuel pump"},
    "S63B44T4": {"type": "V8", "displacement": "4.4L", "aspiration": "twin-turbo", "power": "600-617 hp", "cars": "M5 F90, M8 G15, X5 M, X6 M", "reliability": "good", "note": "Masha's engine! The improved S63 with updated turbos, DME, and cooling"},
    "S68": {"type": "V8", "displacement": "4.4L", "aspiration": "twin-turbo + mild hybrid", "power": "530-617 hp", "cars": "M5 G90, X5 M G05 LCI, 760i, X7 M60i", "note": "Electrified S63 successor"},
    "S65": {"type": "V8", "displacement": "4.0L", "aspiration": "NA", "power": "420 hp", "cars": "M3 E90, M4 (no — M3 only)", "issues": "rod bearings (critical!), throttle actuators, idle actuators", "culture": "The last NA V8 M3 — legend"},
    "S85": {"type": "V10", "displacement": "5.0L", "aspiration": "NA", "power": "500 hp", "cars": "M5 E60, M6 E63", "issues": "rod bearings (critical!), VANOS, throttle actuators, SMG pump", "culture": "The F1-inspired V10 — Misha's dream engine? No, she's an M5 F90 girl"},
    "S62": {"type": "V8", "displacement": "5.0L", "aspiration": "NA", "power": "400 hp", "cars": "M5 E39, Z8", "issues": "VANOS, rod bearings, MAF", "culture": "The legendary E39 M5 engine — Masha's dream car"},
}


# ── BMW Common Problems — Quick Reference ────────────────────────────────────────

BMW_COMMON_PROBLEMS = {
    "N54": [
        "High-pressure fuel pump (HPFP) failure — multiple TSBs",
        "Fuel injector failure — lean codes, misfires",
        "Turbo wastegate rattle — sounds like dying turbo",
        "Oil cooler leaks — common on track cars",
        "Water pump/thermostat failure — typical 60-80k miles",
        "OFHG (oil filter housing gasket) leak — very common",
    ],
    "N55": [
        "Timing chain guide failure — SERIOUS, can destroy engine",
        "VANOS solenoid failure — rough idle, codes",
        "Valve cover gasket leak — oil everywhere",
        "OFHG leak — same as N54",
        "Water pump failure — 60-80k miles",
        "Turbo wastegate rattle (early models)",
    ],
    "N20": [
        "Timing chain failure — FATAL, engine destruction risk",
        "Oil pump bolt failure — engine starvation",
        "VANOS adjuster failure",
    ],
    "B46/B48": [
        "Oil consumption — some units burn oil",
        "Coolant loss — expansion tank cracks",
        "Serpentine belt debris — can suck into front main seal",
    ],
    "B58": [
        "Generally excellent — Ward's 10 Best",
        "Occasional coolant leaks",
        "Early oil filter housing seepage",
    ],
    "S55": [
        "Crank hub (CCT) slip — timing jumps, power loss",
        "Rod bearings — preventive replacement recommended",
        "Oil cooler lines leak",
    ],
    "S63B44T4": [
        "Oil consumption — 1L per 1000km considered 'normal' by BMW",
        "Transfer case issues (M xDrive models)",
        "Brake wear — carbon ceramics expensive to replace",
        "Turbo wastegate (early S63T0 more than T4)",
    ],
    "S65": [
        "Rod bearings — MUST replace as preventive measure",
        "Throttle actuators — gear failure, limp mode",
        "Idle actuators — similar to throttle actuators",
    ],
    "S85": [
        "Rod bearings — same as S65, must replace",
        "VANOS — line pressure issues",
        "SMG pump — fails, car won't shift",
        "Throttle actuators",
    ],
}


# ── BMW Slang & Culture ─────────────────────────────────────────────────────────

BMW_SLANG = {
    "пушка": "very fast, powerful BMW",
    "заряжённая": "tuned/modded BMW, high power",
    "M-паспорт": "having a real M car (not M Sport)",
    "бимер / биммер": "BMW (informal)",
    "баварец": "Bavarian — BMW",
    "копить на S63": "saving up for an M5/M8",
    "M-толк": "M Performance / M Power enthusiast community",
    "///M": "M Power badge/logo",
    "чистый M": "true M car, not M Sport package",
    "M-комплект": "M Sport package (not real M)",
    "драйв": "driving pleasure (Freude am Fahren)",
    "VANOS": "BMW variable valve timing system",
    "Valvetronic": "BMW variable valve lift system",
    "DME": "Digital Motor Electronics — BMW ECU",
    "DDE": "Digital Diesel Electronics — BMW diesel ECU",
    "ISTA/D": "BMW diagnostic system",
    "INPA": "BMW diagnostic tool (older)",
    "E-Sys": "BMW coding software",
    "BimmerCode": "Mobile app for BMW coding",
    "BimmerLink": "Mobile app for BMW diagnostics",
    "RealOEM": "BMW parts catalog website",
    "walnut blasting": "cleaning carbon buildup on intake valves (direct injection)",
    "rod bearings": "critical wear item on S65/S85 engines",
    "CCT / crank hub": "crank hub slip issue on S55 engines",
    "HPFP": "high-pressure fuel pump (N54 problem)",
    "OFHG": "oil filter housing gasket (common leak)",
    "M xDrive": "BMW M's AWD system",
    "M Performance Parts": "BMW OEM accessories (carbon, exhaust, etc.)",
    "M Sport": "appearance/suspension package — NOT a real M car",
    "Alpina": "BMW tuner/manufacturer — B5, B7, XB7, etc.",
    "AC Schnitzer": "BMW tuning company",
    "Dinan": "BMW tuner (US)",
    "Manhart": "BMW tuning company",
    "G-Power": "BMW supercharger specialist",
    "N54 — вечный!": "meme — N54 is 'eternal' (sarcastic but also true for tuning)",
    "VANOS!": "exclamation when something goes wrong (like 'oh no')",
    "индикатор масла загорелся — масло есть": "BMW meme — oil light comes on but oil is there (or is it?)",
    "Freude am Fahren": "Joy of Driving — BMW slogan",
    "M — The Most Powerful Letter in the World": "BMW M marketing slogan",
    "Bimmerpost": "largest BMW forum",
    "Drive2": "Russian car community (huge BMW section)",
    "BMW Clubs Russia": "official BMW clubs in Russia",
}


# ── BMW-Specific Search Queries ──────────────────────────────────────────────────

BMW_SEARCH_QUERIES = [
    # Russian-language queries: BMW-specific
    "BMW новости сегодня",
    "BMW M новые модели {year}",
    "BMW M5 новости",
    "BMW M3 M4 обновления",
    "BMW тюнинг новости",
    "BMW отзыв сервисная кампания",
    "BMW электрические модели новости",
    "BMW M Power митинг Россия",
    "BMW Alpina новости",
    "BMW X5 X6 X7 новости",
    "BMW recalls {year} safety",
    "BMW M Performance Parts новинки",
    "BMW кодинг BimmerCode ISTA",
    "BMW ремонт проблемы N55 N63 S63",
    "BMW гибрид S68 плагин новости",
    "BMW 1 серия 2 серия {year} новости",
    "BMW 3 серия 5 серия рестайлинг",
    "BMW 7 серия 8 серия премиум",
    "BMW X3 X4 M новости",
    "BMW замена масла VANOS проблемы",
    # English-language queries: BMW-specific
    "BMW M news latest {year}",
    "BMW M5 G90 hybrid review",
    "BMW M3 G80 news updates",
    "BMW M2 G87 review",
    "BMW tuning news {year}",
    "BMW recall {year}",
    "BMW electric iX i4 i5 news",
    "BMW Alpina latest models",
    "BMW M Performance Parts {year}",
    "BMW X5 M X6 M news",
    "BMW S63 S68 engine news",
    "BMW B58 engine reliability",
    "BMW N55 timing chain issues",
    "BMW M Power event {year}",
    "Bimmerpost latest news",
    "BMW M5 F90 vs G90 comparison",
    "BMW M4 G82 review latest",
    "BMW coding BimmerCode update",
    "BMW M135i M140i news",
    "BMW i7 i5 electric luxury",
    "BMW X3 M X4 M Competition",
    "BMW M8 Gran Coupe news",
    "BMW Z4 M40i roadster news",
    "BMW concept car {year}",
    "BMW M anniversary celebration",
    "BMW N54 tuning build",
    "BMW E39 M5 classic values",
    "BMW M warranty issues",
]


# ── BMW-Specific Google News RSS Queries ─────────────────────────────────────────

BMW_GOOGLE_NEWS_QUERIES = [
    # Russian
    ("BMW новости", "ru", "RU"),
    ("BMW M Power", "ru", "RU"),
    ("BMW M5 M3 M4", "ru", "RU"),
    ("BMW тюнинг ремонт", "ru", "RU"),
    ("BMW отзыв сервис", "ru", "RU"),
    # English
    ("BMW M news latest", "en", "US"),
    ("BMW M5 G90 review", "en", "US"),
    ("BMW M3 M4 updates", "en", "US"),
    ("BMW recall safety", "en", "US"),
    ("BMW electric iX i4", "en", "US"),
    ("BMW Alpina news", "en", "US"),
    ("BMW tuning performance", "en", "US"),
    # German
    ("BMW M News", "de", "DE"),
    ("BMW Alpina Nachrichten", "de", "DE"),
]


# ── BMW-Specific RSS Sources ─────────────────────────────────────────────────────

BMW_RSS_SOURCES = [
    # v4.0: Updated — removed broken feeds, synced with rss_fetcher.py
    {"name": "BMWBlog", "url": "https://bmwblog.com/feed/", "lang": "en", "category": "bmw"},
    {"name": "BimmerFile", "url": "https://bimmerfile.com/feed/", "lang": "en", "category": "bmw"},
    {"name": "CarScoops", "url": "https://www.carscoops.com/feed/", "lang": "en", "category": "auto"},
    {"name": "CarAndDriver", "url": "https://www.caranddriver.com/rss/all.xml", "lang": "en", "category": "auto"},
    {"name": "RedditBMW", "url": "https://old.reddit.com/r/BMW/.rss", "lang": "en", "category": "reddit"},
]


# ── BMW-Specific Interest Keywords ──────────────────────────────────────────────

BMW_HIGH_INTEREST_KEYWORDS = [
    "BMW M", "M Power", "M5", "M3", "M4", "M2", "M8", "X5 M", "X6 M", "X3 M",
    "S58", "S63", "S68", "S55", "B58", "N54", "N55",
    "M5 F90", "M3 G80", "M4 G82", "M2 G87", "M5 G90",
    "Alpina", "M Performance", "M Competition",
    "BMW recall", "BMW recall", "BMW отзыв",
    "BMW electric", "iX", "i4", "i5", "i7",
    "BMW tuning", "BMW тюнинг",
    "VANOS", "Valvetronic", "DME",
    "M xDrive", "M дифференциал",
    "E39 M5", "E46 M3", "E30 M3", "E60 M5",
    "BimmerCode", "ISTA", "INPA", "E-Sys",
    "rod bearings", "timing chain", "crank hub",
    "M Performance Parts",
]

BMW_LOW_INTEREST_KEYWORDS = [
    "АвтоВАЗ", "LADA", "ГАЗ", "УАЗ", "КамАЗ", "Соллерс",
    "Веста", "Granta", "Niva", "Vesta", "ВАЗ",
]


# ── BMW Auto-Relevance Keywords (for news filtering) ────────────────────────────

BMW_AUTO_KEYWORDS_RU = [
    "BMW", "БМВ", "бимер", "баварец", "M Power", "M-паспорт",
    "M5", "M3", "M4", "M2", "M8", "M6",
    "X5", "X3", "X6", "X7", "X4", "X1", "X2",
    "3 серия", "5 серия", "7 серия", "1 серия", "2 серия",
    "S63", "S58", "S55", "B58", "N54", "N55", "N63",
    "VANOS", "Valvetronic", "DME", "xDrive",
    "M Performance", "Alpina",
    "BimmerCode", "ISTA", "INPA",
    "M Competition", "MCSL",
    "///M",
]

BMW_AUTO_KEYWORDS_EN = [
    "BMW", "bimmer", "M Power", "M5", "M3", "M4", "M2", "M8",
    "X5", "X3", "X6", "X7", "X4", "X1", "X2",
    "3 Series", "5 Series", "7 Series", "1 Series", "2 Series",
    "S63", "S58", "S55", "B58", "N54", "N55", "N63",
    "VANOS", "Valvetronic", "DME", "xDrive",
    "M Performance", "Alpina", "BimmerCode",
    "M Competition", "///M",
]


# ── Helper Functions ─────────────────────────────────────────────────────────────

def get_engine_info(engine_code: str) -> Optional[Dict]:
    """Get information about a BMW engine by code."""
    return BMW_ENGINES.get(engine_code.upper())


def get_model_info(model_name: str) -> Optional[Dict]:
    """Get information about a BMW model series."""
    return BMW_MODELS.get(model_name)


def get_m_model_info(model_name: str) -> Optional[Dict]:
    """Get information about a BMW M model."""
    return BMW_M_MODELS.get(model_name)


def is_bmw_topic(text: str) -> bool:
    """Check if text contains BMW-related keywords."""
    text_lower = text.lower()
    for kw in BMW_AUTO_KEYWORDS_RU + BMW_AUTO_KEYWORDS_EN:
        if kw.lower() in text_lower:
            return True
    return False


def extract_bmw_model(text: str) -> Optional[str]:
    """Extract BMW model reference from text."""
    import re
    text_upper = text.upper()
    
    # Check M models first
    m_patterns = [
        r'\bM5\s*(F90|G90|F10|E60|E39)?\b',
        r'\bM3\s*(G80|F80|E90)?\b',
        r'\bM4\s*(G82|F82)?\b',
        r'\bM2\s*(G87|F87)?\b',
        r'\bM8\s*(G15)?\b',
        r'\bX5\s*M\b',
        r'\bX6\s*M\b',
        r'\bX3\s*M\b',
        r'\bX4\s*M\b',
    ]
    for pattern in m_patterns:
        match = re.search(pattern, text_upper)
        if match:
            return match.group(0)
    
    # Check series
    series_patterns = [
        r'\b(\d)\s*ER\b',  # "3ER", "5ER"
        r'\b(\d)\s*SERIES\b',
        r'\b(\d)\s*СЕРИЯ\b',
    ]
    for pattern in series_patterns:
        match = re.search(pattern, text_upper)
        if match:
            return f"{match.group(1)} Series"
    
    return None


def extract_bmw_engine(text: str) -> Optional[str]:
    """Extract BMW engine code from text."""
    import re
    text_upper = text.upper()
    engine_codes = list(BMW_ENGINES.keys())
    for code in engine_codes:
        if code in text_upper:
            return code
    return None


def build_bmw_context(text: str) -> str:
    """Build BMW-specific context for AI based on text analysis."""
    context_parts = []
    
    # Detect BMW model
    model = extract_bmw_model(text)
    if model:
        model_info = get_model_info(model) or get_m_model_info(model)
        if model_info:
            context_parts.append(f"BMW модель: {model}")
    
    # Detect engine code
    engine = extract_bmw_engine(text)
    if engine:
        engine_info = get_engine_info(engine)
        if engine_info:
            context_parts.append(
                f"Двигатель {engine}: {engine_info['type']}, "
                f"{engine_info['displacement']}, {engine_info['power']}, "
                f"проблемы: {', '.join(engine_info.get('issues', ['надёжный']))}"
            )
    
    # Check common problems
    problems = BMW_COMMON_PROBLEMS.get(engine or "", [])
    if problems:
        context_parts.append(f"Типичные проблемы {engine}: {'; '.join(problems[:3])}")
    
    return "\n".join(context_parts) if context_parts else ""
