"""
Masha Persona Data — BMW M Expert Facts, Car Diagnostic Helpers, BMW Knowledge
"""

import re
from typing import List, Dict, Optional, Tuple


# ── Car brand database (BMW is primary, others kept for chat) ─────────────────

CAR_BRANDS = {
    "BMW": {"country": "Германия", "parent": "BMW Group", "popular_models": ["3 Series", "5 Series", "M3", "M5", "X3", "X5", "X7", "iX", "M2", "M4", "M8"]},
    "MINI": {"country": "Великобритания", "parent": "BMW Group", "popular_models": ["Cooper", "Countryman", "Clubman"]},
    "ROLLS-ROYCE": {"country": "Великобритания", "parent": "BMW Group", "popular_models": ["Phantom", "Ghost", "Cullinan", "Spectre"]},
    "ALPINA": {"country": "Германия", "parent": "BMW Group (sub-brand)", "popular_models": ["B5", "B7", "XB7", "B3"]},
    "AUDI": {"country": "Германия", "parent": "Volkswagen Group", "popular_models": ["A3", "A4", "A6", "Q5", "Q7", "e-tron"]},
    "MERCEDES-BENZ": {"country": "Германия", "parent": "Mercedes-Benz Group", "popular_models": ["C-Class", "E-Class", "S-Class", "GLC", "GLE", "AMG GT"]},
    "PORSCHE": {"country": "Германия", "parent": "Volkswagen Group", "popular_models": ["911", "Cayenne", "Macan", "Taycan", "Panamera"]},
    "VOLKSWAGEN": {"country": "Германия", "parent": "Volkswagen Group", "popular_models": ["Golf", "Tiguan", "Polo", "Touareg", "ID.4", "Passat"]},
    "TOYOTA": {"country": "Япония", "parent": "Toyota Motor", "popular_models": ["Camry", "RAV4", "Land Cruiser", "Corolla", "Highlander"]},
    "HONDA": {"country": "Япония", "parent": "Honda Motor", "popular_models": ["Civic", "Accord", "CR-V", "HR-V"]},
    "NISSAN": {"country": "Япония", "parent": "Nissan Motor", "popular_models": ["Qashqai", "X-Trail", "Juke", "Patrol"]},
    "LEXUS": {"country": "Япония", "parent": "Toyota", "popular_models": ["RX", "NX", "ES", "LX", "IS"]},
    "MAZDA": {"country": "Япония", "parent": "Mazda Motor", "popular_models": ["3", "6", "CX-5", "CX-9", "MX-5"]},
    "SUBARU": {"country": "Япония", "parent": "Subaru Corp", "popular_models": ["Forester", "Outback", "XV", "Impreza", "WRX"]},
    "HYUNDAI": {"country": "Южная Корея", "parent": "Hyundai Motor", "popular_models": ["Solaris", "Creta", "Tucson", "Santa Fe"]},
    "KIA": {"country": "Южная Корея", "parent": "Hyundai Motor", "popular_models": ["Rio", "Ceed", "Sportage", "Sorento", "K5"]},
    "FORD": {"country": "США", "parent": "Ford Motor", "popular_models": ["Focus", "Mustang", "Explorer", "F-150", "Bronco"]},
    "CHEVROLET": {"country": "США", "parent": "General Motors", "popular_models": ["Camaro", "Corvette", "Tahoe", "Traverse"]},
    "TESLA": {"country": "США", "parent": "Tesla Inc", "popular_models": ["Model 3", "Model Y", "Model S", "Model X", "Cybertruck"]},
    "VOLVO": {"country": "Швеция", "parent": "Geely", "popular_models": ["XC60", "XC90", "XC40", "S60"]},
    "JAGUAR": {"country": "Великобритания", "parent": "Tata Motors", "popular_models": ["F-Pace", "F-Type", "XE"]},
    "LAND ROVER": {"country": "Великобритания", "parent": "Tata Motors", "popular_models": ["Range Rover", "Defender", "Discovery", "Evoque"]},
    "RENAULT": {"country": "Франция", "parent": "Renault Group", "popular_models": ["Duster", "Arkana", "Logan", "Sandero"]},
    "SKODA": {"country": "Чехия", "parent": "Volkswagen Group", "popular_models": ["Octavia", "Kodiaq", "Karoq", "Superb"]},
    "PEUGEOT": {"country": "Франция", "parent": "Stellantis", "popular_models": ["208", "308", "3008", "5008"]},
    "CITROEN": {"country": "Франция", "parent": "Stellantis", "popular_models": ["C3", "C4", "C5 Aircross"]},
    "OPEL": {"country": "Германия", "parent": "Stellantis", "popular_models": ["Astra", "Mokka", "Crossland"]},
    "MITSUBISHI": {"country": "Япония", "parent": "Mitsubishi Motors", "popular_models": ["Outlander", "Pajero Sport", "Eclipse Cross"]},
    "SUZUKI": {"country": "Япония", "parent": "Suzuki Motor", "popular_models": ["Jimny", "Vitara", "S-Cross"]},
    "INFINITI": {"country": "Япония", "parent": "Nissan", "popular_models": ["Q50", "QX55", "QX60"]},
    "GENESIS": {"country": "Южная Корея", "parent": "Hyundai", "popular_models": ["G70", "G80", "GV70", "GV80"]},
    "JEEP": {"country": "США", "parent": "Stellantis", "popular_models": ["Wrangler", "Grand Cherokee", "Compass"]},
    "CHERY": {"country": "Китай", "parent": "Chery Automobile", "popular_models": ["Tiggo 4 Pro", "Tiggo 7 Pro", "Tiggo 8 Pro"]},
    "HAVAL": {"country": "Китай", "parent": "Great Wall Motors", "popular_models": ["H6", "Jolion", "F7", "Dargo"]},
    "GEELY": {"country": "Китай", "parent": "Geely Auto", "popular_models": ["Coolray", "Atlas Pro", "Monjaro"]},
    "CHANGAN": {"country": "Китай", "parent": "Changan Automobile", "popular_models": ["CS35 Plus", "CS55 Plus", "CS75", "UNI-V"]},
    "EXEED": {"country": "Китай", "parent": "Chery", "popular_models": ["TXL", "VX", "LX"]},
    "TANK": {"country": "Китай", "parent": "Great Wall Motors", "popular_models": ["300", "500", "700"]},
    "BYD": {"country": "Китай", "parent": "BYD Auto", "popular_models": ["Song", "Tang", "Han", "Seal", "Dolphin"]},
    "ZEEKR": {"country": "Китай", "parent": "Geely", "popular_models": ["001", "009", "X"]},
    "LI AUTO": {"country": "Китай", "parent": "Li Auto", "popular_models": ["L7", "L8", "L9"]},
    "NIO": {"country": "Китай", "parent": "NIO", "popular_models": ["ET7", "ES6", "ES8"]},
    "XPENG": {"country": "Китай", "parent": "XPeng", "popular_models": ["P7", "G6", "G9"]},
    "RIVIAN": {"country": "США", "parent": "Rivian", "popular_models": ["R1T", "R1S"]},
    "LUCID": {"country": "США", "parent": "Lucid Motors", "popular_models": ["Air", "Gravity"]},
    "FERRARI": {"country": "Италия", "parent": "Ferrari N.V.", "popular_models": ["296 GTB", "SF90", "Roma", "Purosangue"]},
    "LAMBORGHINI": {"country": "Италия", "parent": "Volkswagen Group", "popular_models": ["Huracán", "Urus", "Revuelto"]},
    "MASERATI": {"country": "Италия", "parent": "Stellantis", "popular_models": ["Ghibli", "Levante", "MC20"]},
    "BENTLEY": {"country": "Великобритания", "parent": "Volkswagen Group", "popular_models": ["Continental GT", "Flying Spur", "Bentayga"]},
    "BUGATTI": {"country": "Франция", "parent": "Volkswagen Group", "popular_models": ["Chiron", "Tourbillon"]},
    "MCLAREN": {"country": "Великобритания", "parent": "McLaren Group", "popular_models": ["720S", "Artura", "750S"]},
    "ASTON MARTIN": {"country": "Великобритания", "parent": "Aston Martin Lagonda", "popular_models": ["DB12", "Vantage", "DBX"]},
    "LOTUS": {"country": "Великобритания", "parent": "Geely", "popular_models": ["Emira", "Eletre", "Evija"]},
    "ALFA ROMEO": {"country": "Италия", "parent": "Stellantis", "popular_models": ["Giulia", "Stelvio", "Tonale"]},
    "FIAT": {"country": "Италия", "parent": "Stellantis", "popular_models": ["500", "Panda", "Tipo"]},
    "POLESTAR": {"country": "Швеция", "parent": "Geely/Volvo", "popular_models": ["2", "3", "4"]},
    "LADA (ВАЗ)": {"country": "Россия", "parent": "АвтоВАЗ", "popular_models": ["Granta", "Vesta", "Niva Travel"]},
    "UAZ": {"country": "Россия", "parent": "УАЗ", "popular_models": ["Patriot", "Hunter", "Profi"]},
    "GAZ": {"country": "Россия", "parent": "Группа ГАЗ", "popular_models": ["ГАЗель", "Соболь", "Валдай"]},
}


# ── Common OBD-II error codes (same for all cars) ─────────────────────────────

OBD2_CODES = {
    "P0010": "Неисправность цепи управления клапаном фаз газораспределения (ряд 1)",
    "P0011": "Положение распредвала — опережение зажигания / производительность (ряд 1)",
    "P0012": "Положение распредвала — задержка зажигания (ряд 1)",
    "P0013": "Цепь управления клапаном фаз газораспределения (ряд 1)",
    "P0020": "Неисправность цепи управления клапаном фаз газораспределения (ряд 2)",
    "P0030": "Цепь управления нагревателем датчика кислорода (ряд 1, датчик 1)",
    "P0031": "Низкий уровень сигнала цепи управления нагревателем HO2S (ряд 1, датчик 1)",
    "P0032": "Высокий уровень сигнала цепи управления нагревателем HO2S (ряд 1, датчик 1)",
    "P0100": "Неисправность цепи датчика массового расхода воздуха (MAF)",
    "P0101": "Диапазон/производительность датчика массового расхода воздуха",
    "P0102": "Низкий уровень сигнала датчика массового расхода воздуха",
    "P0103": "Высокий уровень сигнала датчика массового расхода воздуха",
    "P0110": "Неисправность цепи датчика температуры впускного воздуха (IAT)",
    "P0115": "Неисправность цепи датчика температуры охлаждающей жидкости (ECT)",
    "P0120": "Неисправность цепи датчика положения дроссельной заслонки/педали",
    "P0128": "Термостат — температура охлаждающей жидкости ниже порога регулирования",
    "P0130": "Неисправность цепи датчика кислорода (ряд 1, датчик 1)",
    "P0131": "Низкое напряжение цепи датчика кислорода (ряд 1, датчик 1)",
    "P0140": "Нет активности цепи датчика кислорода (ряд 1, датчик 2)",
    "P0170": "Неисправность топливной коррекции (ряд 1)",
    "P0171": "Система слишком бедная (ряд 1)",
    "P0172": "Система слишком богатая (ряд 1)",
    "P0174": "Система слишком бедная (ряд 2)",
    "P0175": "Система слишком богатая (ряд 2)",
    "P0190": "Неисправность цепи датчика давления топлива в рампе",
    "P0217": "Перегрев двигателя",
    "P0218": "Перегрев коробки передач",
    "P0230": "Неисправность первичной цепи топливного насоса",
    "P0234": "Перегрузка турбокомпрессора/компрессора",
    "P0235": "Неисправность цепи датчика А турбокомпрессора",
    "P0299": "Недостаточная производительность турбокомпрессора/компрессора",
    "P0300": "Обнаружены пропуски зажигания (случайные/множественные цилиндры)",
    "P0301": "Обнаружены пропуски зажигания в цилиндре 1",
    "P0302": "Обнаружены пропуски зажигания в цилиндре 2",
    "P0303": "Обнаружены пропуски зажигания в цилиндре 3",
    "P0304": "Обнаружены пропуски зажигания в цилиндре 4",
    "P0305": "Обнаружены пропуски зажигания в цилиндре 5",
    "P0306": "Обнаружены пропуски зажигания в цилиндре 6",
    "P0315": "Система изменения фаз газораспределения не обучена",
    "P0335": "Неисправность цепи датчика положения коленвала",
    "P0336": "Диапазон/производительность цепи датчика положения коленвала",
    "P0340": "Неисправность цепи датчика положения распредвала (ряд 1)",
    "P0341": "Диапазон/производительность датчика положения распредвала",
    "P0351": "Неисправность первичной/вторичной цепи катушки зажигания A",
    "P0365": "Неисправность цепи датчика положения распредвала (ряд 2)",
    "P0400": "Неисправность системы рециркуляции отработавших газов (EGR)",
    "P0401": "Недостаточный поток рециркуляции отработавших газов",
    "P0403": "Неисправность цепи управления клапаном EGR",
    "P0420": "Эффективность катализатора ниже порога (ряд 1)",
    "P0421": "Эффективность катализатора ниже порога (ряд 1, прогрев)",
    "P0430": "Эффективность катализатора ниже порога (ряд 2)",
    "P0441": "Некорректный поток системы улавливания паров топлива (EVAP)",
    "P0442": "Утечка в системе EVAP (малая)",
    "P0455": "Утечка в системе EVAP (большая)",
    "P0480": "Неисправность цепи управления вентилятором охлаждения 1",
    "P0500": "Неисправность датчика скорости автомобиля",
    "P0504": "Корреляция выключателя стоп-сигнала A/B",
    "P0562": "Низкое напряжение системы",
    "P0563": "Высокое напряжение системы",
    "P0600": "Неисправность канала связи CAN (последовательный)",
    "P0601": "Ошибка контрольной суммы внутренней памяти ECM",
    "P0606": "Неисправность процессора ECM/PCM",
    "P0607": "Неисправность модуля управления — производительность",
    "P0700": "Неисправность системы управления коробкой передач (запрос от TCM)",
    "P0705": "Неисправность цепи датчика диапазона коробки передач",
    "P0715": "Неисправность цепи датчика частоты вращения турбины/входного вала",
    "P0720": "Неисправность цепи датчика скорости выходного вала",
    "P0725": "Неисправность цепи датчика оборотов двигателя (вход TCM)",
    "P0730": "Некорректное передаточное число",
    "P0740": "Неисправность системы муфты гидротрансформатора",
    "P0750": "Неисправность соленоида переключения A",
    "P0753": "Электрическая неисправность соленоида переключения A",
    "P1101": "Диапазон/производительность датчика массового расхода воздуха",
    "P1345": "Неисправность VANOS (BMW-specific)",
    "P1347": "Неисправность VANOS, ряд 2 (BMW-specific)",
    "P1351": "Неисправность управления зажиганием, цилиндр 1 (BMW-specific)",
    "P1420": "Неисправность вторичной цепи клапана подачи воздуха",
    "P15A1": "Неисправность VANOS, впуск (BMW-specific)",
    "P15A2": "Неисправность VANOS, выпуск (BMW-specific)",
    "P1600": "Потеря питания ECM",
    "P2100": "Неисправность цепи привода дроссельной заслонки (открыта)",
    "P2101": "Диапазон/производительность цепи привода дроссельной заслонки",
    "P2110": "Система управления дроссельной заслонкой — принудительный ограничитель оборотов",
    "P2122": "Низкий уровень сигнала датчика положения педали D",
    "P2127": "Низкий уровень сигнала датчика положения педали E",
    "P2135": "Корреляция напряжения датчика положения дроссельной заслонки/педали",
    "P2177": "Система слишком бедная (ряд 1, кроме холостого хода)",
    "P2187": "Система слишком бедная (ряд 1, холостой ход)",
    "P2196": "Сигнал датчика кислорода застрял на богатой (ряд 1, датчик 1)",
    "P2270": "Сигнал датчика кислорода застрял на бедной (ряд 1, датчик 2)",
    "U0001": "Неисправность высокоскоростной шины CAN",
    "U0073": "Шина CAN A отключена",
    "U0100": "Потеря связи с ECM/PCM A",
    "U0101": "Потеря связи с TCM",
    "U0121": "Потеря связи с ABS",
    "U0140": "Потеря связи с BCM",
    "U0151": "Потеря связи с модулем подушки безопасности",
    "U0155": "Потеря связи с комбинацией приборов",
}


# ── Common car problems by symptom (with BMW-specific entries) ────────────────

SYMPTOM_DIAGNOSIS = {
    "engine_wont_start": {
        "symptoms": ["не заводится", "не запускается", "стартер крутит но не заводится", "машина не заводится"],
        "possible_causes": [
            "Разряжен аккумулятор",
            "Неисправность стартера",
            "Нет подачи топлива (топливный насос, фильтр, реле)",
            "Неисправность иммобилайзера",
            "Обрыв ремня ГРМ / цепи ГРМ (критично для N20/N55!)",
            "Неисправность датчика коленвала (ДПКВ)",
            "Нет искры (катушка, свечи, коммутатор)",
            "Загрязнение форсунок",
            "Низкая компрессия",
            # BMW-specific
            "Неисправность HPFP (N54/N55 — высокое давление топлива)",
            "Неисправность CAS-модуля (Car Access System — BMW)",
            "Отказ DME (ECU BMW)",
        ],
        "first_steps": [
            "Проверь напряжение аккумулятора (должно быть 12.4В+)",
            "Проверь, крутит ли стартер",
            "Слушай, гудит ли топливный насос при включении зажигания",
            "Проверь наличие искры на свече",
            "Считай коды ошибок OBD-II (ISTA/D для BMW)",
        ],
    },
    "engine_overheating": {
        "symptoms": ["перегревается", "кипит", "температура высокая", "стрелка в красной зоне", "перегрев"],
        "possible_causes": [
            "Низкий уровень охлаждающей жидкости",
            "Неисправность термостата (электронный на BMW!)",
            "Неисправность водяного насоса (помпы — электронасос на BMW)",
            "Пробитая прокладка ГБЦ",
            "Засорён радиатор",
            "Неисправность вентилятора охлаждения",
            "Воздушная пробка в системе охлаждения",
        ],
        "first_steps": [
            "Остановись и дай двигателю остыть (минимум 20-30 мин)",
            "Проверь уровень антифриза в расширительном бачке",
            "Осмотри на утечки под машиной",
            "Проверь, включается ли вентилятор",
            "Проверь, нагреваются ли оба патрубка радиатора (термостат)",
        ],
    },
    "check_engine": {
        "symptoms": ["чек", "check engine", "горит чек", "загорелся чек", "ошибка двигателя", "джеки чан"],
        "possible_causes": [
            "Неисправность датчика кислорода (лямбда-зонд)",
            "Снижение эффективности катализатора",
            "Пропуски зажигания (свечи, катушки — частая проблема BMW)",
            "Неисправность датчика MAF",
            "Утечка в системе EVAP (крышка бензобака!)",
            "Неисправность VANOS-соленоидов (BMW!)",
            "Проблемы с топливной системой",
            "Неисправность Valvetronic (BMW!)",
        ],
        "first_steps": [
            "Считай код ошибки OBD-II сканером (BimmerLink или ISTA/D)",
            "Проверь, плотно ли закручена крышка бензобака",
            "Обрати внимание на поведение: троит, теряет мощность, повышенный расход?",
            "Если код P0420 — скорее всего катализатор",
            "Если код P0300 — пропуски, проверь свечи и катушки (часто на BMW N-series)",
            "Если код P1345/P15A1/P15A2 — VANOS проблема (BMW-specific)",
        ],
    },
    "vanos_issues": {
        "symptoms": ["vanos", "ванос", "фазы газораспределения", "холостые обороты плавают", "prolonged crank"],
        "possible_causes": [
            "Неисправность VANOS-соленоидов (очень часто на N55/N54)",
            "Загрязнение VANOS-клапанов (отложения масла)",
            "Износ VANOS-узла (N54/N55 — проблема timing chain + VANOS)",
            "Неисправность датчиков распредвала (на BMW)",
            "Низкое давление масла (влияет на VANOS)",
        ],
        "first_steps": [
            "Считай ошибки — P1345, P15A1, P15A2 = VANOS",
            "Попробуй заменить VANOS-соленоиды (дешёвая диагностика)",
            "Проверь давление масла",
            "Проверь цепь ГРМ — на N55/N20 это может быть связано",
            "На N55: если соленоиды не помогают — возможна замена timing chain",
        ],
    },
    "timing_chain": {
        "symptoms": ["цепь грм", "timing chain", "стук цепи", "гремит цепь", "стучит на холодную"],
        "possible_causes": [
            "Износ направляющих цепи ГРМ (N20 — критичная проблема!)",
            "Износ натяжителя цепи (N55 — слабое место)",
            "Удлинение цепи (N20 — двигатель может разрушиться!)",
            "Crank hub slip на S55 (M3 F80, M4 F82)",
        ],
        "first_steps": [
            "СРОЧНО проверь — на N20 это может быть смертельно для двигателя",
            "Считай ошибки — проблемы с фазами = цепь",
            "Проверь натяжитель цепи",
            "На N20: если пробег >60k км — проверяй цепь ОБЯЗАТЕЛЬНО",
            "На S55: если теряется мощность — crank hub slip",
        ],
    },
    "hpfp_failure": {
        "symptoms": ["hpfp", "топливный насос высокое давление", "давление топлива", "long crank", "долго крутит"],
        "possible_causes": [
            "Отказ HPFP (High Pressure Fuel Pump — проблема N54!)",
            "Неисправность CP4 fuel pump (S63B44T0 — M5 F10, M6 F12)",
            "Загрязнение топливных форсунок",
            "Неисправность датчика давления топлива",
        ],
        "first_steps": [
            "Считай ошибки — P0190, P0087 = давление топлива",
            "Проверь давление топлива в рампе (ISTA/D)",
            "На N54: HPFP — известная проблема, многократные TSB",
            "На S63T0: CP4 pump может крошиться и попасть в форсунки!",
        ],
    },
    "oil_consumption": {
        "symptoms": ["ест масло", "расход масла", "масложор", "уходит масло", "дымит"],
        "possible_causes": [
            "Износ маслосъёмных колпачков (МСК)",
            "Износ поршневых колец",
            "Утечки через прокладки (клапанной крышки, ГБЦ, поддона)",
            "Неисправность системы вентиляции картера (PCV — valve cover на BMW)",
            "Турбина гонит масло",
            "Деформация ГБЦ",
            # BMW-specific
            "N63 — масложор это 'фича' (до 1л/1000км)",
            "B46/B48 — oil consumption на некоторых моторах",
            "S63B44T4 — расход масла считается нормой до 1л/1000км",
        ],
        "first_steps": [
            "Проверь уровень масла щупом (или через iDrive на BMW)",
            "Осмотри двигатель на подтёки масла",
            "Обрати внимание на цвет выхлопа: синий дым = масло",
            "Проверь крышку клапанов (на BMW PCV встроена в valve cover)",
            "Замерь компрессию",
        ],
    },
    "vibration": {
        "symptoms": ["вибрация", "трясёт", "дрожит", "вибрирует", "троит"],
        "possible_causes": [
            "Пропуски зажигания (свечи, катушки, форсунки — частая проблема BMW)",
            "Изношенные опоры двигателя (на BMW гидроопоры)",
            "Неисправность подушек КПП",
            "Дисбаланс колёс",
            "Изношенные ШРУСы",
            "Неисправность двухмассового маховика (BMW N-series)",
            "Загрязнение форсунок",
            "Проблемы с VANOS (BMW)",
        ],
        "first_steps": [
            "Определи, когда вибрация: на холостых, при движении, при торможении?",
            "Если на холостых — скорее всего опоры, пропуски или VANOS",
            "Если при скорости 80-120 — балансировка колёс",
            "Если при разгоне — ШРУС",
            "Считай ошибки OBD-II / ISTA/D",
        ],
    },
    "transmission_problems": {
        "symptoms": ["коробка", "кпп", "автомат", "мкпп", "не переключает", "пинается", "робот", "вариатор"],
        "possible_causes": [
            "Низкий уровень/износ масла в АКПП",
            "Неисправность соленоидов",
            "Износ фрикционов",
            "Неисправность мехатроника (ZF 8HP на BMW)",
            "Износ подшипников (МКПП)",
            "Износ сцепления (МКПП)",
            # BMW-specific
            "Adaptation needed ZF 8HP (M5 F90, M3 G80, etc.)",
            "Transfer case issues (M xDrive — M5 F90)",
        ],
        "first_steps": [
            "Проверь уровень и цвет масла в коробке (ZF lifetime? — нет, меняй!)",
            "Обрати внимание: толчки при переключении, пробуксовки, шумы?",
            "Считай ошибки OBD-II / TCM",
            "Проверь адаптации коробки (ZF 8HP — адаптация решает много проблем)",
        ],
    },
    "brake_problems": {
        "symptoms": ["тормоза", "скрипят", "биение при торможении", "мягкая педаль", "уводит"],
        "possible_causes": [
            "Износ тормозных колодок",
            "Деформация тормозных дисков",
            "Воздух в тормозной системе",
            "Неисправность тормозного цилиндра",
            "Подклинивание суппорта",
            "Износ тормозных шлангов",
            "Неисправность ABS",
        ],
        "first_steps": [
            "Проверь толщину тормозных колодок",
            "Осмотри тормозные диски на биение и борозды",
            "Проверь уровень тормозной жидкости",
            "Прокачай тормоза если педаль мягкая",
            "Проверь суппорты на подклинивание (часто на BMW)",
        ],
    },
    "suspension_noise": {
        "symptoms": ["стук", "грохот", "скрипит подвеска", "стучит на кочках", "гул"],
        "possible_causes": [
            "Износ стоек амортизаторов",
            "Износ шаровых опор",
            "Износ рулевых наконечников",
            "Износ ступичных подшипников (гул)",
            "Износ сайлентблоков",
            "Износ стабилизаторных втулок",
            "Ослабление креплений",
        ],
        "first_steps": [
            "Определи характер звука: стук, скрип, гул",
            "Стук на кочках — стойки/шаровые/сайлентблоки",
            "Гул, усиливающийся в поворотах — ступичный подшипник",
            "Скрип при повороте руля — рулевые наконечники/рулевая рейка",
            "Подними машину на подъёмнике и проверь люфты",
        ],
    },
    "electrical_problems": {
        "symptoms": ["электрика", "не горит", "предохранитель", "генератор", "аккумулятор разряжается", "короткое замыкание"],
        "possible_causes": [
            "Неисправность генератора (IBS-система на BMW)",
            "Утечка тока (часто на BMW — after-market устройства)",
            "Износ проводки",
            "Окисление контактов",
            "Неисправность блока BCM (FEM/ZGM на BMW)",
            "Неисправность реле",
            "Проблемы с CAS-модулем (BMW)",
        ],
        "first_steps": [
            "Замерь напряжение на АКБ: на заглушенной ~12.4В, на работающей 13.8-14.4В",
            "Если < 13.5В на работающей — генератор",
            "Замерь ток утечки (должно быть < 50мА)",
            "Проверь предохранители",
            "Проверь клеммы АКБ на окисление",
        ],
    },
}


# ── Helper functions ────────────────────────────────────────────────────────────

def lookup_obd2_code(code: str) -> Optional[str]:
    """Look up an OBD-II error code description."""
    code = code.upper().strip()
    if code in OBD2_CODES:
        return OBD2_CODES[code]
    match = re.match(r"[PBUCE]\d{3,4}", code)
    if match:
        code_key = match.group(0)
        if len(code_key) == 4:
            code_key = code_key[0] + "0" + code_key[1:]
        return OBD2_CODES.get(code_key)
    return None


def identify_car_brand(text: str) -> Optional[str]:
    """Identify a car brand from text. BMW is prioritized."""
    if not text or not isinstance(text, str):
        return None
    try:
        text_upper = text.upper()
    except (AttributeError, UnicodeDecodeError):
        return None

    # BMW-specific aliases — CHECK FIRST (prioritize BMW detection)
    bmw_aliases = [
        "BMW", "БМВ", "БЭХА", "БЭХУ", "БЕХА", "БЕХУ",
        "БИММЕР", "БИМЕР", "БАВАРЕЦ", "///M", "M POWER",
        "M-POWER", "M ПАСПОРТ", "M-ПАСПОРТ",
        # Engine codes that uniquely identify BMW
        "S63B44T4", "S63B44T0", "S68", "S58", "S55", "S65", "S85", "S62",
        "B58", "B48", "B46", "B38",
        "N54", "N55", "N63", "N20", "N52", "N53", "N47", "N57",
        # BMW-specific tech terms
        "VANOS", "VALVETRONIC", "ISTAP", "ISTA/D", "INPA", "E-SYS",
        "BIMMERCODE", "BIMMERLINK", "REALOEM",
    ]
    for alias in bmw_aliases:
        if alias in text_upper:
            return "BMW"

    # M model patterns
    if re.search(r'\bM[1-8]\b', text_upper):
        return "BMW"
    # X model patterns
    if re.search(r'\bX[1-7]\b', text_upper):
        # Could be BMW X-series
        if any(kw in text_upper for kw in ["DRIVE", "M SPORT", "M PERFORMANCE", "XDRIVE", "SERIES", "СЕРИЯ"]):
            return "BMW"

    # Standard brand detection
    for brand in CAR_BRANDS:
        if brand in text_upper:
            return brand

    # Common abbreviations and aliases
    aliases = {
        "ВАЗ": "LADA (ВАЗ)", "ЛADA": "LADA (ВАЗ)", "ЛАДА": "LADA (ВАЗ)",
        "МЕРСЕДЕС": "MERCEDES-BENZ", "МЕРИН": "MERCEDES-BENZ",
        "ФОЛЬКСВАГЕН": "VOLKSWAGEN", "ФВ": "VOLKSWAGEN", "ВАГ": "VOLKSWAGEN",
        "ЛАНД РОВЕР": "LAND ROVER", "РЕНДЖ РОВЕР": "LAND ROVER", "РЕНЖ": "LAND ROVER",
        "ПОРШ": "PORSCHE", "ПОРШЕ": "PORSCHE",
        "ШКОДА": "SKODA", "ШКОДУ": "SKODA",
        "ХЁНДАЙ": "HYUNDAI", "ХЕНДАЙ": "HYUNDAI", "ХЮНДАЙ": "HYUNDAI",
        "КИЯ": "KIA", "КИЮ": "KIA",
        "ТОЙОТА": "TOYOTA", "ТОЙОТУ": "TOYOTA",
        "МИЦУБИСИ": "MITSUBISHI", "МИЦУБИШИ": "MITSUBISHI",
        "СУБАРУ": "SUBARU",
        "ШЕВРОЛЕ": "CHEVROLET", "ШЕВРОЛЕТ": "CHEVROLET",
        "ФОРД": "FORD", "ФОРДА": "FORD",
        "РЕНО": "RENAULT", "РЕНОВ": "RENAULT",
        "ПЕЖО": "PEUGEOT",
        "СИТРОЕН": "CITROEN", "СИТРОЁН": "CITROEN",
        "ОПЕЛЬ": "OPEL", "ОПЕЛЯ": "OPEL",
        "УАЗ": "UAZ", "УАЗИК": "UAZ",
        "ГАЗЕЛЬ": "GAZ", "ГАЗ": "GAZ",
        "ХАВАЛ": "HAVAL",
        "ЧЕРИ": "CHERY",
        "ДЖИЛИ": "GEELY",
        "ЧАНГАН": "CHANGAN",
        "ТЕСЛА": "TESLA",
        "ЯГУАР": "JAGUAR",
        "ДЖИП": "JEEP",
        "АЛЬПИНА": "ALPINA",
    }
    for alias, brand in aliases.items():
        if alias in text_upper:
            return brand
    return None


def detect_symptoms(text: str) -> List[str]:
    """Detect car problem categories from user text."""
    text_lower = text.lower()
    detected = []
    for category, data in SYMPTOM_DIAGNOSIS.items():
        for symptom in data["symptoms"]:
            if symptom.lower() in text_lower:
                detected.append(category)
                break
    return detected


def detect_obd2_codes(text: str) -> List[str]:
    """Extract OBD-II codes from text."""
    pattern = r'[PBUCE]\d{3,4}'
    matches = re.findall(pattern, text.upper())
    normalized = []
    for m in matches:
        if len(m) == 4:
            m = m[0] + "0" + m[1:]
        if m in OBD2_CODES:
            normalized.append(m)
    return normalized


def is_part_number(text: str) -> bool:
    """Check if text looks like a part/article number (OEM number)."""
    text = text.strip()
    patterns = [
        r'^\d{4,10}$',
        r'^[A-Z]{2,3}\d{4,8}$',
        r'^[A-Z]{1,3}\d{3,6}[A-Z]?\d?$',
        r'^\d{3,6}[A-Z]{1,3}\d{0,4}$',
        r'^[A-Z0-9]{4,15}[-\s][A-Z0-9]{3,15}$',
        r'^[A-Z]{2}\d{9,12}$',
    ]
    for pattern in patterns:
        if re.match(pattern, text.upper()):
            return True
    return False


def extract_part_numbers(text: str) -> List[str]:
    """Extract possible part numbers from text."""
    words = text.replace(',', ' ').replace('.', ' ').split()
    parts = []
    for word in words:
        if is_part_number(word):
            parts.append(word.upper())
    return parts


def get_brand_info(brand: str) -> Optional[Dict]:
    """Get brand information by name."""
    return CAR_BRANDS.get(brand.upper())


def build_diagnostic_context(text: str) -> str:
    """Build additional context for AI when user describes a car problem."""
    context_parts = []

    # Detect car brand
    brand = identify_car_brand(text)
    if brand:
        info = get_brand_info(brand)
        if info:
            context_parts.append(f"Марка авто: {brand} ({info['country']}, холдинг: {info['parent']})")

    # BMW-specific context
    if brand == "BMW":
        try:
            from bot.bmw_knowledge import build_bmw_context
            bmw_ctx = build_bmw_context(text)
            if bmw_ctx:
                context_parts.append(bmw_ctx)
        except ImportError:
            pass

    # Detect symptoms
    symptoms = detect_symptoms(text)
    if symptoms:
        for symptom_cat in symptoms:
            data = SYMPTOM_DIAGNOSIS[symptom_cat]
            context_parts.append(f"Категория проблемы: {symptom_cat}")
            context_parts.append(f"Возможные причины: {', '.join(data['possible_causes'][:5])}")
            context_parts.append(f"Первые шаги: {', '.join(data['first_steps'][:3])}")

    # Detect OBD-II codes
    codes = detect_obd2_codes(text)
    if codes:
        for code in codes:
            desc = lookup_obd2_code(code)
            if desc:
                context_parts.append(f"Код ошибки {code}: {desc}")

    # Detect part numbers
    parts = extract_part_numbers(text)
    if parts:
        context_parts.append(f"Артикулы запчастей в запросе: {', '.join(parts)}")

    if context_parts:
        return "\n".join(context_parts)
    return ""


# ── Masha's signature phrases for different contexts ────────────────────────────

MASHA_PHRASES = {
    "greeting": [
        "Привет! 😊",
        "Хей, бимер!",
        "О, привет! ///M!",
        "Привет ☕",
        "Хей! Как дела?",
        "Привет! 😊 Что нового в BMW-мире?",
        "О! Привет 😊 Баварский привет!",
    ],
    "diagnostic_start": [
        "Так, давай разберёмся 🔧",
        "Понял, сейчас подумаем... VANOS тут ни при чём? 😏",
        "Блин, неприятно. Давай разбираться.",
        "Ок, рассказывай симптомы 🔧",
        "Сейчас разберёмся! Опиши подробнее.",
        "Так-так, что тут у нас... 🔍",
        "Щас всё проверю, погоди минутку 🔧",
        "Звучит знакомо... У BMW свои особенности 🏎️",
    ],
    "part_search": [
        "Ищу BMW-детали...",
        "Сейчас проверю 🔍",
        "Ищу, подожди чуть-чуть!",
        "Секунду 🔍",
        "Проверяю...",
        "Сейчас найду!",
        "Подбираю варианты для бимера... 🔍",
        "Ищу по магазинам, сек...",
        "Пушка! Сейчас проверю наличие 🔍",
    ],
    "news_comment": [
        "Пушка! 🔥",
        "///M! Вот это да!",
        "Заслуживает внимания!",
        "Бимер! Ничего себе!",
        "Интересная новость!",
        "Вот это да! 🔥",
        "Смотри, что нашла!",
        "M Power! 🔥",
    ],
    "thinking": [
        "Сейчас подумаю...",
        "Дай секунду...",
        "Минутку...",
        "Анализирую... как ISTA/D 😏",
        "Секунду, думаю...",
        "Готовлю ответ...",
        "Соображаю... как Серёга с S63 🧠",
        "Один момент...",
        "Сейчас соображу...",
        "Думаю-думаю... 🤔",
        "Сейчас всё разложу по полочкам...",
        "Сек, мысли собираю...",
        "Погоди, сейчас вспомню...",
        "Так, даю себе минутку подумать...",
    ],
}
