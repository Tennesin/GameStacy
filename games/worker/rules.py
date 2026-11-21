"""Правила и константы симулятора рабочего. Всё, что хочется подкрутить, лежит здесь.
Единицы: деньги целые («монеты»), расход и прирост показателей считаются «в час»,
за квант (5 минут) применяется 1/12 часового значения."""

# ---------- Время ----------
QUANTUM_MINUTES = 5

# ---------- Старт нового игрока ----------
START_ENERGY = 100.0
START_HEALTH = 100.0
START_HAPPINESS = 60.0
START_REPUTATION = 20.0
START_MONEY = 400
START_HOUSING = "room"
START_FOOD = "regular"

# ---------- Энергия ----------
FREE_ENERGY_RATE = 1.0            # свободен: +1%/ч
SLEEP_ENERGY_RATE = 12.0          # сон: +12%/ч (с жильём умножается)
SLEEP_AVAILABLE_BELOW = 50.0      # спать по своей воле можно, когда энергия ниже
ENERGY_FORCED = 15.0              # ниже: принудительный выход со смены и сон
ENERGY_FORCED_WAKE = 50.0         # принудительный сон длится, пока энергия не достигнет
ZERO_ENERGY_HEALTH_LOSS = 2.0     # при нулевой энергии здоровье дополнительно -2%/ч

# ---------- Здоровье ----------
FREE_HEALTH_RATE = 0.25           # свободен: +0.25%/ч
SLEEP_HEALTH_RATE = 1.5           # сон: +1.5%/ч (с жильём умножается)
HEALTH_SLOW = 65.0                # ниже: энергия тратится вдвое быстрее, восстанавливается вдвое медленнее
LOW_HEALTH_DRAIN_MULT = 2.0
LOW_HEALTH_REGEN_MULT = 0.5
HEALTH_WARN = 75.0                # только предупреждение в тексте, механики нет
HEALTH_CAREER_BLOCK = 25.0        # ниже: карьера и казино заблокированы, смена прерывается
HEALTH_HOSPITAL = 5.0             # ниже: принудительная больница

# ---------- Счастье ----------
HAPPY_SPRING = 0.04               # доля разницы с «базой» жилья, которая закрывается за час
HAPPY_MULT_MIN = 0.7              # множитель продуктивности при счастье 0%
HAPPY_MULT_MAX = 1.1              # при 100% (при 50% равен 1.0)

# ---------- Жильё и питание ----------
HOUSING = {
    "bunk": {"title": "Койко-место", "rent": 60, "base": 40.0, "sleep": 0.85, "heal": 0.8},
    "room": {"title": "Комната", "rent": 110, "base": 50.0, "sleep": 1.0, "heal": 1.0},
    "studio": {"title": "Студия", "rent": 170, "base": 55.0, "sleep": 1.07, "heal": 1.1},
    "flat": {"title": "Квартира", "rent": 240, "base": 60.0, "sleep": 1.15, "heal": 1.2},
    "loft": {"title": "Лофт", "rent": 520, "base": 68.0, "sleep": 1.25, "heal": 1.35},
    "penthouse": {"title": "Пентхаус", "rent": 1200, "base": 76.0, "sleep": 1.35, "heal": 1.5},
}
FOOD = {
    "regular": {"title": "Обычное", "cost": 70},
}

# ---------- Суточные события (в полночь по Ташкенту) ----------
UNPAID_HAPPINESS_LOSS = 5.0       # не хватило на платежи: счастье -5 (раз в сутки)
UNPAID_HEALTH_LOSS = 2.0          # и здоровье -2
BENEFIT_AMOUNT = 100              # пособие
BENEFIT_BELOW_MONEY = 180         # выдаётся, если денег меньше этого и здоровье ниже HEALTH_CAREER_BLOCK

# ---------- Долг ----------
DEBT_RATE = 0.01                  # +1% в сутки
DEBT_CAP_MULT = 2.0               # рост процентов останавливается на удвоенной сумме основного долга
DEBT_PRESSURE_MAX = 10
DEBT_PAY_MIN_SHARE = 0.10         # платёж от 10% долга останавливает рост за сутки и снимает давление
DEBT_PAY_PRESSURE_DROP = 2
DEBT_PRESSURE_HAPPY = 0.15        # счастье -0.15*P %/ч
DEBT_PRESSURE_REP = 0.1           # репутация -0.1*P в сутки

# ---------- Больница ----------
HOSPITAL_FULL_COST = 800
HOSPITAL_FULL_HOURS = 10
HOSPITAL_FULL_HEALTH = 100.0
HOSPITAL_LIGHT_COST = 500
HOSPITAL_LIGHT_HOURS = 7
HOSPITAL_LIGHT_HEALTH = 75.0

# ---------- Зарплата, срывы, репутация ----------
MIN_PAY_MINUTES = 15              # меньше отработано: оплаты нет
ABORT_PAY_VOLUNTARY = 0.7         # добровольный уход со смены
ABORT_PAY_FORCED = 0.8            # принудительный (энергия или здоровье)
REP_PER_HOUR = 0.25               # репутация за отработанный час
REP_SHIFT_MAX = 3.0               # но не больше за смену
REP_ABORT_VOLUNTARY = 2.0
REP_ABORT_FORCED = 1.0

# ---------- НДФЛ (недельный, ступенчатый) ----------
# (верхняя граница ступени, ставка); None - без верхней границы
TAX_BRACKETS = [(1500, 0.10), (3500, 0.15), (6000, 0.20), (9000, 0.27), (None, 0.35)]

# ---------- Заморозка ----------
FREEZE_MIN_HOURS = 24             # раньше разморозить нельзя
FREEZE_COOLDOWN_DAYS = 7          # после разморозки повторная заморозка недоступна

# ---------- Интерфейс ----------
SLEEP_CHOICES_HOURS = (1, 2, 4, 6, 8)
EVENTS_SHOWN = 6                  # сколько событий «пока вас не было» показывать

# ---------- Образование (ступени и курсы) ----------
EDUCATION = {
    "school": {"title": "Школа", "level": 1},
    "college": {"title": "Колледж", "level": 2},
    "higher": {"title": "Высшее образование", "level": 3},
    "course_acc": {"title": "Курс «Бухгалтерия»", "level": 0},
    "course_med": {"title": "Курс «Медицина»", "level": 0},
}

# ---------- Профессии ----------
PROFESSIONS = {
    "loader": {"title": "Грузчик", "edu": (), "min_rep": 0, "kinds": ("once", "perm"), "wage": 35,
               "shift": (4, 10), "energy": -8.0, "health": -1.2, "happy": -0.4,
               "commute": (20, 40), "fare": 8, "off": (1, 2)},
    "miner": {"title": "Шахтёр", "edu": (), "min_rep": 10, "kinds": ("once", "perm"), "wage": 48,
              "shift": (6, 10), "energy": -9.0, "health": -1.8, "happy": -0.6,
              "commute": (40, 70), "fare": 15, "off": (1, 2)},
    "courier": {"title": "Курьер", "edu": ("school",), "min_rep": 5, "kinds": ("once", "perm"), "wage": 38,
                "shift": (2, 8), "energy": -5.5, "health": -0.4, "happy": -0.2,
                "commute": (10, 30), "fare": 0, "off": (0, 1)},
    "cashier": {"title": "Кассир", "edu": ("school",), "min_rep": 10, "kinds": ("once", "perm"), "wage": 42,
                "shift": (4, 8), "energy": -4.0, "health": -0.2, "happy": -0.5,
                "commute": (15, 35), "fare": 8, "off": (1, 2)},
    "clerk": {"title": "Офисный клерк", "edu": ("college",), "min_rep": 25, "kinds": ("perm",), "wage": 62,
              "shift": (4, 8), "energy": -3.5, "health": -0.3, "happy": -0.5,
              "commute": (25, 50), "fare": 10, "off": (2, 2), "weekend": True},
    "accountant": {"title": "Бухгалтер", "edu": ("college", "course_acc"), "min_rep": 40, "kinds": ("perm",),
                   "wage": 85, "shift": (4, 8), "energy": -3.5, "health": -0.3, "happy": -0.7,
                   "commute": (25, 50), "fare": 10, "off": (2, 2), "weekend": True},
    "engineer": {"title": "Инженер", "edu": ("higher",), "min_rep": 55, "kinds": ("perm",), "wage": 120,
                 "shift": (4, 10), "energy": -4.5, "health": -0.2, "happy": 0.2,
                 "commute": (25, 60), "fare": 12, "off": (1, 2)},
    "doctor": {"title": "Врач", "edu": ("higher", "course_med"), "min_rep": 75, "kinds": ("perm",),
               "wage": 190, "shift": (6, 12), "energy": -6.5, "health": -0.3, "happy": -0.9,
               "commute": (20, 40), "fare": 12, "off": (0, 1)},
}

# ---------- Рынок вакансий ----------
MARKET_MIN = 7
MARKET_MAX = 14
MARKET_WINDOW_HOURS = 3           # окна обновления по Ташкенту
PERMANENT_ENABLED = False         # постоянные вакансии
PERM_SHARE = 0.4                  # доля постоянных в цели рынка
ONCE_LIFE_HOURS = (2, 6)          # срок жизни разовой вакансии
PERM_LIFE_HOURS = (24, 72)        # постоянной
ONCE_WAGE_MULT = 1.10             # разовая платит больше постоянной
HEAVY_PAY = 0.10                  # «тяжесть» d от -1 до +1
HEAVY_LOAD = 0.15
EDU_WEIGHTS = {0: 100, 1: 50, 2: 20, 3: 8}
COURSE_WEIGHT_MULT = 0.5          # каждый курс умножает вес
MAX_SHIFT_HOURS = 12
SHIFT_STEP_MIN = 15               # длина смены кратна 15 минутам
MIN_START_ENERGY = 30.0           # меньше энергии - на смену не выйти
COMMUTE_ENERGY_RATE = -4.0        # энергия в час на дороге

# ---------- Опыт по профессиям ----------
EXP_MAX_LEVEL = 30
EXP_BASE_HOURS = 3.0              # уровень N требует BASE + STEP * N часов
EXP_STEP_HOURS = 0.5
EXP_ABORT_MULT = 0.5              # при срыве опыт вдвое меньше
EXP_WAGE_STEP = 0.025             # уровни 1, 4, 7...: +2.5% к зарплате
EXP_NEG_STEP = 0.015              # уровни 2, 5, 8...: +1.5% сопротивления негативному
EXP_ENERGY_STEP = 0.02            # уровни 3, 6, 9...: +2% сопротивления расходу энергии

# ---------- Баффы ----------
RES_ENERGY_CAP = 0.6              # суммарное сопротивление расходу энергии (опыт + бафф) не выше
BUFF_MAX_HOURS = 96               # продление баффа повторным курсом не дальше этого срока

# ---------- Переезд ----------
HOUSING_MOVE_DAYS = 2             # плата за переезд: столько суток аренды НОВОГО жилья
HOUSING_MOVE_COOLDOWN_HOURS = 24  # между переездами

# ---------- Рынок (магазины) ----------
ERRAND_QUANTA = 1                 # дорога в одну сторону (кванты по 5 минут)
ERRAND_ENERGY_RATE = -3.0         # энергия в час на дороге в магазин
DEFAULT_DAILY_LIMIT = 6           # покупок одного товара за игровые сутки (если у товара нет своего лимита)
# cats: какие категории товаров продаёт площадка; markup: наценка
VENUES = {
    "food": {"title": "Продукты", "emoji": "🥖", "cats": ("food",), "markup": 1.0},
    "pharm": {"title": "Аптека", "emoji": "💊", "cats": ("pharm",), "markup": 1.0},
    "super": {"title": "Супермаркет", "emoji": "🛍", "cats": ("food", "pharm"), "markup": 1.10},
}
# energy / health / happy: мгновенное изменение показателя; limit: покупок за сутки
GOODS = {
    "snack": {"title": "Перекус", "cat": "food", "price": 12, "energy": 3},
    "lunch": {"title": "Обед", "cat": "food", "price": 40, "energy": 8, "happy": 2},
    "choc": {"title": "Шоколад", "cat": "food", "price": 18, "energy": 1, "happy": 3},
    "fruit": {"title": "Фрукты", "cat": "food", "price": 30, "energy": 2, "health": 1},
    "energy_drink": {"title": "Энергетик", "cat": "food", "price": 120, "energy": 15, "health": -3, "limit": 2},
    "vitamins": {"title": "Витамины", "cat": "pharm", "price": 25, "health": 2},
    "medicine": {"title": "Лекарство", "cat": "pharm", "price": 60, "health": 10, "limit": 4},
    "strong_med": {"title": "Сильный препарат", "cat": "pharm", "price": 200, "health": 25, "limit": 2},
    "sedative": {"title": "Успокоительное", "cat": "pharm", "price": 45, "happy": 6, "limit": 3},
}

# ---------- Развлечения ----------
LEISURE_MIN_ENERGY = 25.0         # меньше энергии - на досуг не пойти
# Убывающая отдача за игровые сутки по одному занятию: (до какого часа, множитель); None - дальше
DIM_TIERS = ((2.0, 1.0), (3.0, 0.5), (None, 0.2))
# cost: за час; hours: допустимая длительность; energy/health/happy: влияние в час
ENTERTAINMENT = {
    "park": {"title": "Прогулка в парке", "cost": 0, "hours": (1, 2, 3), "energy": -1.0, "health": 0.3, "happy": 4.0},
    "cinema": {"title": "Кино", "cost": 35, "hours": (2,), "energy": -0.5, "health": 0.0, "happy": 7.0},
    "restaurant": {"title": "Ресторан", "cost": 150, "hours": (1, 2), "energy": 2.0, "health": 0.0, "happy": 8.0},
    "gym": {"title": "Спортзал", "cost": 40, "hours": (1, 2), "energy": -5.0, "health": 1.2, "happy": 3.0},
    "spa": {"title": "СПА-салон", "cost": 350, "hours": (1, 2, 3), "energy": 3.0, "health": 1.5, "happy": 9.0},
    "concert": {"title": "Концерт", "cost": 500, "hours": (3,), "energy": -1.5, "health": 0.0, "happy": 11.0},
}

# ---------- Больница: добровольные курсы ----------
BUFF_KINDS = {"stamina": "Выносливость", "learning": "Концентрация", "recovery": "Восстановление"}
# cost: цена; power: сила (доля); hours: сколько действует после курса; lesson: длительность занятия, часов
COURSES = {
    "stamina_1": {"title": "Дыхательная гимнастика", "kind": "stamina", "cost": 300, "power": 0.08, "hours": 12, "lesson": 1},
    "stamina_2": {"title": "Йога", "kind": "stamina", "cost": 900, "power": 0.15, "hours": 24, "lesson": 2},
    "stamina_3": {"title": "Спортивная подготовка", "kind": "stamina", "cost": 2400, "power": 0.25, "hours": 48, "lesson": 3},
    "learning_1": {"title": "Техника запоминания", "kind": "learning", "cost": 300, "power": 0.10, "hours": 12, "lesson": 1},
    "learning_2": {"title": "Скорочтение", "kind": "learning", "cost": 900, "power": 0.20, "hours": 24, "lesson": 2},
    "learning_3": {"title": "Интенсив «Фокус»", "kind": "learning", "cost": 2400, "power": 0.35, "hours": 48, "lesson": 3},
    "recovery_1": {"title": "Основы здорового сна", "kind": "recovery", "cost": 300, "power": 0.15, "hours": 12, "lesson": 1},
    "recovery_2": {"title": "Курс реабилитации", "kind": "recovery", "cost": 900, "power": 0.30, "hours": 24, "lesson": 2},
    "recovery_3": {"title": "Санаторная программа", "kind": "recovery", "cost": 2400, "power": 0.50, "hours": 48, "lesson": 3},
}
COURSE_ENERGY_RATE = -2.0         # энергия в час во время занятия
COURSE_MIN_ENERGY = 25.0          # меньше энергии - на курс не пойти