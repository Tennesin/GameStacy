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
START_MONEY = 500
START_HOUSING = "room"
START_FOOD = "regular"

# ---------- Энергия ----------
FREE_ENERGY_RATE = 1.0            # свободен: +1%/ч
SLEEP_ENERGY_RATE = 12.0          # сон: +12%/ч (с жильём умножается)
SLEEP_AVAILABLE_BELOW = 70.0      # спать по своей воле можно, когда энергия ниже
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
    "bunk": {"title": "Койко-место", "rent": 50, "base": 40.0, "sleep": 0.85, "heal": 0.8, "fridge": 6},
    "dorm": {"title": "Общежитие", "rent": 75, "base": 45.0, "sleep": 0.92, "heal": 0.9, "fridge": 8},
    "room": {"title": "Комната", "rent": 100, "base": 50.0, "sleep": 1.0, "heal": 1.0, "fridge": 10},
    "studio": {"title": "Студия", "rent": 145, "base": 55.0, "sleep": 1.07, "heal": 1.1, "fridge": 12},
    "flat": {"title": "Квартира", "rent": 200, "base": 60.0, "sleep": 1.15, "heal": 1.2, "fridge": 14},
    "loft": {"title": "Лофт", "rent": 325, "base": 68.0, "sleep": 1.25, "heal": 1.35, "fridge": 16},
    "house": {"title": "Дом", "rent": 500, "base": 72.0, "sleep": 1.3, "heal": 1.4, "fridge": 18},
    "penthouse": {"title": "Пентхаус", "rent": 1000, "base": 76.0, "sleep": 1.35, "heal": 1.5, "fridge": 20},
}
FOOD = {
    "regular": {"title": "Обычное", "cost": 50},
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
# Сколько пунктов на одной странице постраничных списков (список выбора вмещает не больше 25)
PAGE_SIZES = {"venue": 8, "kitchen": 6, "ent": 7, "edu": 6}

# ---------- Образование (ступени и курсы) ----------
# level: вес в генераторе вакансий; hours: сколько часов учёбы до получения;
# fee: стоимость часа (списывается поминутно); need: что нужно получить заранее
EDUCATION = {
    "school": {"title": "Школа", "level": 1, "hours": 18, "fee": 25, "need": ()},
    "college": {"title": "Колледж", "level": 2, "hours": 36, "fee": 50, "need": ("school",)},
    "higher": {"title": "Высшее образование", "level": 3, "hours": 48, "fee": 100, "need": ("college",)},
    "course_acc": {"title": "Курс «Бухгалтерия»", "level": 0, "hours": 18, "fee": 65, "need": ("school",)},
    "course_med": {"title": "Курс «Медицина»", "level": 0, "hours": 24, "fee": 125, "need": ("college",)},
    "course_driver": {"title": "Курс «Вождение»", "level": 0, "hours": 14, "fee": 55, "need": ("school",)},
    "course_weld": {"title": "Курс «Сварка»", "level": 0, "hours": 20, "fee": 70, "need": ("school",)},
    "course_prog": {"title": "Курс «Программирование»", "level": 0, "hours": 30, "fee": 90, "need": ("college",)},
}
EDU_SESSION_HOURS = (1, 2, 4)     # длительность одного занятия на выбор
EDU_MIN_ENERGY = 25.0             # меньше энергии - на занятие не прийти
EDU_ENERGY_RATE = -3.0            # энергия в час во время учёбы
EDU_HAPPY_RATE = -0.3             # счастье в час во время учёбы

# ---------- Профессии ----------
PROFESSIONS = {
    "loader": {"title": "Грузчик", "edu": (), "min_rep": 0, "kinds": ("once", "perm"), "wage": 42,
               "shift": (4, 10), "energy": -8.0, "health": -1.2, "happy": -0.4,
               "commute": (5, 15), "fare": 8, "off": (1, 2)},
    "miner": {"title": "Шахтёр", "edu": (), "min_rep": 10, "kinds": ("once", "perm"), "wage": 55,
              "shift": (6, 10), "energy": -9.0, "health": -1.8, "happy": -0.6,
              "commute": (15, 35), "fare": 15, "off": (1, 2)},
    "courier": {"title": "Курьер", "edu": ("school",), "min_rep": 5, "kinds": ("once", "perm"), "wage": 44,
                "shift": (2, 8), "energy": -5.5, "health": -0.4, "happy": -0.2,
                "commute": (5, 10), "fare": 0, "off": (0, 1)},
    "cashier": {"title": "Кассир", "edu": ("school",), "min_rep": 10, "kinds": ("once", "perm"), "wage": 48,
                "shift": (4, 8), "energy": -4.0, "health": -0.2, "happy": -0.5,
                "commute": (15, 35), "fare": 8, "off": (1, 2)},
    "clerk": {"title": "Офисный клерк", "edu": ("college",), "min_rep": 25, "kinds": ("perm",), "wage": 66,
              "shift": (4, 8), "energy": -3.5, "health": -0.3, "happy": -0.5,
              "commute": (20, 40), "fare": 10, "off": (2, 2), "weekend": True},
    "accountant": {"title": "Бухгалтер", "edu": ("college", "course_acc"), "min_rep": 40, "kinds": ("once", "perm"),
                   "wage": 85, "shift": (4, 8), "energy": -3.5, "health": -0.3, "happy": -0.7,
                   "commute": (15, 35), "fare": 10, "off": (2, 2), "weekend": True},
    "engineer": {"title": "Инженер", "edu": ("higher",), "min_rep": 55, "kinds": ("perm",), "wage": 120,
                 "shift": (4, 10), "energy": -4.5, "health": -0.2, "happy": 0.2,
                 "commute": (20, 30), "fare": 12, "off": (1, 2)},
    "doctor": {"title": "Врач", "edu": ("higher", "course_med"), "min_rep": 75, "kinds": ("perm",),
               "wage": 190, "shift": (6, 12), "energy": -6.5, "health": -0.3, "happy": -0.9,
               "commute": (15, 25), "fare": 12, "off": (0, 1)},
    "janitor": {"title": "Дворник", "edu": (), "min_rep": 0, "kinds": ("once", "perm"), "wage": 36,
                "shift": (3, 6), "energy": -3.0, "health": -0.3, "happy": -0.3,
                "commute": (5, 10), "fare": 0, "off": (1, 2), "weight": 0.5},
    "guard": {"title": "Охранник", "edu": ("school",), "min_rep": 8, "kinds": ("perm",), "wage": 52,
              "shift": (8, 12), "energy": -2.5, "health": -0.1, "happy": -0.8,
              "commute": (10, 25), "fare": 8, "off": (2, 3)},
    "driver": {"title": "Водитель", "edu": ("school", "course_driver"), "min_rep": 15,
               "kinds": ("once", "perm"), "wage": 62, "shift": (4, 10), "energy": -4.0, "health": -0.2,
               "happy": -0.3, "commute": (10, 20), "fare": 0, "off": (1, 2)},
    "welder": {"title": "Сварщик", "edu": ("college", "course_weld"), "min_rep": 30,
               "kinds": ("once", "perm"), "wage": 92, "shift": (4, 10), "energy": -6.0, "health": -0.9,
               "happy": -0.2, "commute": (15, 30), "fare": 10, "off": (1, 2)},
    "nurse": {"title": "Медсестра", "edu": ("college", "course_med"), "min_rep": 45, "kinds": ("perm",),
              "wage": 100, "shift": (6, 12), "energy": -5.0, "health": -0.3, "happy": -0.6,
              "commute": (15, 30), "fare": 10, "off": (1, 2)},
    "teacher": {"title": "Учитель", "edu": ("higher",), "min_rep": 60, "kinds": ("perm",), "wage": 105,
                "shift": (4, 8), "energy": -3.5, "health": -0.1, "happy": 0.1,
                "commute": (15, 30), "fare": 10, "off": (2, 2), "weekend": True},
    "programmer": {"title": "Программист", "edu": ("higher", "course_prog"), "min_rep": 65,
                   "kinds": ("perm",), "wage": 150, "shift": (4, 10), "energy": -3.0, "health": -0.2,
                   "happy": 0.3, "commute": (5, 10), "fare": 0, "off": (1, 2)},
}

# ---------- Рынок вакансий ----------
MARKET_MIN = 7
MARKET_MAX = 14
MARKET_WINDOW_HOURS = 3           # окна обновления по Ташкенту
PERMANENT_ENABLED = True          # постоянные вакансии
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

# ---------- Постоянная работа ----------
JOB_FIRE_ABSENCES = 3             # столько прогулов - увольнение
JOB_ABSENCE_REP = 3.0             # репутация за каждый прогул
JOB_HIRE_COOLDOWN_HOURS = 12      # после ухода (увольнение или по желанию) новую постоянную не взять
JOB_WEEK_BONUS = 0.10             # недельная премия: доля от недельного брутто по контракту

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
    "food": {"title": "Продукты", "emoji": "🥖", "cats": ("food", "ingr"), "markup": 1.0},
    "pharm": {"title": "Аптека", "emoji": "💊", "cats": ("pharm",), "markup": 1.0},
    "super": {"title": "Супермаркет", "emoji": "🛍", "cats": ("food", "pharm", "ingr"), "markup": 1.10},
}
# energy / health / happy: мгновенное изменение показателя; limit: покупок за сутки
GOODS = {
    "snack": {"title": "Перекус", "cat": "food", "price": 12, "energy": 3},
    "sandwich": {"title": "Бутерброд", "cat": "food", "price": 20, "energy": 5, "happy": 1},
    "coffee": {"title": "Кофе", "cat": "food", "price": 22, "energy": 5, "happy": 1},
    "juice": {"title": "Сок", "cat": "food", "price": 24, "energy": 2, "health": 1, "happy": 1},
    "lunch": {"title": "Обед", "cat": "food", "price": 40, "energy": 8, "happy": 2},
    "choc": {"title": "Шоколад", "cat": "food", "price": 18, "energy": 1, "happy": 3},
    "fruit": {"title": "Фрукты", "cat": "food", "price": 30, "energy": 2, "health": 1},
    "energy_drink": {"title": "Энергетик", "cat": "food", "price": 120, "energy": 15, "health": -3, "limit": 2},
    "vitamins": {"title": "Витамины", "cat": "pharm", "price": 25, "health": 2},
    "bandage": {"title": "Бинты", "cat": "pharm", "price": 30, "health": 4, "limit": 3},
    "painkiller": {"title": "Обезболивающее", "cat": "pharm", "price": 55, "health": 7, "happy": 1, "limit": 3},
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
    "library": {"title": "Библиотека", "cost": 10, "hours": (1, 2, 3), "energy": -0.5, "health": 0.0, "happy": 3.0},
    "bowling": {"title": "Боулинг", "cost": 90, "hours": (1, 2), "energy": -2.0, "health": 0.3, "happy": 6.0},
    "pool": {"title": "Бассейн", "cost": 70, "hours": (1, 2), "energy": -4.0, "health": 1.0, "happy": 4.0},
    "fishing": {"title": "Рыбалка", "cost": 0, "hours": (2, 3, 4), "energy": -1.0, "health": 0.4, "happy": 3.5},
    "theatre": {"title": "Театр", "cost": 300, "hours": (2, 3), "energy": -0.5, "health": 0.0, "happy": 9.5},
    "bar": {"title": "Бар", "cost": 200, "hours": (1, 2, 3), "energy": 0.0, "health": -0.8, "happy": 7.0},
    "amusement": {"title": "Парк аттракционов", "cost": 400, "hours": (3, 4), "energy": -3.0, "health": 0.0,
                  "happy": 12.0},
}

# ---------- Больница: добровольные курсы ----------
BUFF_KINDS = {"stamina": "Выносливость", "learning": "Концентрация", "recovery": "Восстановление"}
# cost: цена; power: сила (доля); hours: сколько действует после курса; lesson: длительность занятия, часов
COURSES = {
    "stamina_1": {"title": "Дыхательная гимнастика", "kind": "stamina", "cost": 300, "power": 0.08, "hours": 12, "lesson": 1},
    "stamina_2": {"title": "Йога", "kind": "stamina", "cost": 900, "power": 0.15, "hours": 24, "lesson": 2},
    "stamina_3": {"title": "Спортивная подготовка", "kind": "stamina", "cost": 2400, "power": 0.25, "hours": 48, "lesson": 3},
    "stamina_4": {"title": "Марафонская подготовка", "kind": "stamina", "cost": 5000, "power": 0.35, "hours": 72,
                  "lesson": 4},
    "learning_1": {"title": "Техника запоминания", "kind": "learning", "cost": 300, "power": 0.10, "hours": 12, "lesson": 1},
    "learning_2": {"title": "Скорочтение", "kind": "learning", "cost": 900, "power": 0.20, "hours": 24, "lesson": 2},
    "learning_3": {"title": "Интенсив «Фокус»", "kind": "learning", "cost": 2400, "power": 0.35, "hours": 48, "lesson": 3},
    "learning_4": {"title": "Интенсив «Гений»", "kind": "learning", "cost": 5000, "power": 0.50, "hours": 72,
                   "lesson": 4},
    "recovery_1": {"title": "Основы здорового сна", "kind": "recovery", "cost": 300, "power": 0.15, "hours": 12, "lesson": 1},
    "recovery_2": {"title": "Курс реабилитации", "kind": "recovery", "cost": 900, "power": 0.30, "hours": 24, "lesson": 2},
    "recovery_3": {"title": "Санаторная программа", "kind": "recovery", "cost": 2400, "power": 0.50, "hours": 48, "lesson": 3},
    "recovery_4": {"title": "Курорт", "kind": "recovery", "cost": 5000, "power": 0.70, "hours": 72, "lesson": 4},
}
COURSE_ENERGY_RATE = -2.0         # энергия в час во время занятия
COURSE_MIN_ENERGY = 25.0          # меньше энергии - на курс не пойти


# ---------- Продукты, холодильник, готовка ----------
FRIDGE_SLOTS = 10                 # слотов в холодильнике: один слот = один вид продукта
# unit: g / ml / pcs (хранится целым числом); pack: сколько в одной упаковке; price: цена упаковки;
# slot: сколько этого продукта помещается в один слот
INGREDIENTS = {
    "potato": {"title": "Картошка", "cat": "ingr", "unit": "g", "pack": 1000, "price": 18, "slot": 5000},
    "onion": {"title": "Лук", "cat": "ingr", "unit": "g", "pack": 1000, "price": 14, "slot": 3000},
    "carrot": {"title": "Морковь", "cat": "ingr", "unit": "g", "pack": 1000, "price": 16, "slot": 3000},
    "cabbage": {"title": "Капуста", "cat": "ingr", "unit": "g", "pack": 1000, "price": 12, "slot": 3000},
    "tomato": {"title": "Помидоры", "cat": "ingr", "unit": "g", "pack": 1000, "price": 35, "slot": 2000},
    "oil": {"title": "Масло", "cat": "ingr", "unit": "ml", "pack": 1000, "price": 95, "slot": 2000},
    "flour": {"title": "Мука", "cat": "ingr", "unit": "g", "pack": 1000, "price": 28, "slot": 3000},
    "eggs": {"title": "Яйца", "cat": "ingr", "unit": "pcs", "pack": 6, "price": 48, "slot": 12},
    "milk": {"title": "Молоко", "cat": "ingr", "unit": "ml", "pack": 1000, "price": 40, "slot": 2000},
    "rice": {"title": "Рис", "cat": "ingr", "unit": "g", "pack": 1000, "price": 45, "slot": 3000},
    "chicken": {"title": "Курица", "cat": "ingr", "unit": "g", "pack": 1000, "price": 160, "slot": 2000},
    "sugar": {"title": "Сахар", "cat": "ingr", "unit": "g", "pack": 1000, "price": 40, "slot": 2000},
    "pasta": {"title": "Макароны", "cat": "ingr", "unit": "g", "pack": 1000, "price": 30, "slot": 3000},
    "buckwheat": {"title": "Гречка", "cat": "ingr", "unit": "g", "pack": 1000, "price": 38, "slot": 3000},
    "butter": {"title": "Сливочное масло", "cat": "ingr", "unit": "g", "pack": 200, "price": 70, "slot": 1000},
    "cheese": {"title": "Сыр", "cat": "ingr", "unit": "g", "pack": 500, "price": 110, "slot": 1500},
    "beef": {"title": "Говядина", "cat": "ingr", "unit": "g", "pack": 1000, "price": 260, "slot": 2000},
    "fish": {"title": "Рыба", "cat": "ingr", "unit": "g", "pack": 1000, "price": 190, "slot": 2000},
    "apple": {"title": "Яблоки", "cat": "ingr", "unit": "g", "pack": 1000, "price": 40, "slot": 3000},
    "beet": {"title": "Свёкла", "cat": "ingr", "unit": "g", "pack": 1000, "price": 15, "slot": 3000},
}
# minutes: время на 0 уровне повара; items: сколько чего нужно; energy/happy: эффект на 0 уровне; xp: опыт Повара
RECIPES = {
    "fried_eggs": {"title": "Яичница с помидорами", "minutes": 5, "energy": 4, "happy": 2, "xp": 2,
                   "items": {"eggs": 2, "tomato": 100, "oil": 10}},
    "omelet": {"title": "Омлет", "minutes": 10, "energy": 6, "happy": 3, "xp": 3,
               "items": {"eggs": 3, "milk": 100, "oil": 10}},
    "fried_potato": {"title": "Жареная картошка с луком", "minutes": 25, "energy": 10, "happy": 4, "xp": 4,
                     "items": {"potato": 400, "onion": 100, "oil": 30}},
    "pancakes": {"title": "Блины", "minutes": 35, "energy": 9, "happy": 9, "xp": 5,
                 "items": {"flour": 250, "milk": 400, "eggs": 2, "sugar": 30, "oil": 20}},
    "shchi": {"title": "Щи с курицей", "minutes": 50, "energy": 15, "happy": 6, "xp": 6,
              "items": {"chicken": 200, "cabbage": 300, "potato": 300, "carrot": 100, "onion": 100, "oil": 10}},
    "plov": {"title": "Плов", "minutes": 75, "energy": 25, "happy": 12, "xp": 8,
             "items": {"rice": 300, "chicken": 400, "carrot": 200, "onion": 150, "oil": 60}},
    "porridge": {"title": "Гречневая каша", "minutes": 20, "energy": 9, "happy": 3, "xp": 3, "lvl": 0,
                 "items": {"buckwheat": 200, "butter": 20}},
    "mashed": {"title": "Картофельное пюре", "minutes": 30, "energy": 12, "happy": 4, "xp": 4, "lvl": 1,
               "items": {"potato": 400, "butter": 30, "milk": 100}},
    "pasta_chicken": {"title": "Макароны с курицей", "minutes": 35, "energy": 16, "happy": 6, "xp": 5, "lvl": 2,
                      "items": {"pasta": 250, "chicken": 200, "oil": 15, "onion": 50}},
    "cutlets": {"title": "Котлеты с гречкой", "minutes": 55, "energy": 19, "happy": 7, "xp": 6, "lvl": 3,
                "items": {"beef": 300, "onion": 100, "eggs": 1, "flour": 30, "buckwheat": 200, "oil": 30}},
    "fish_rice": {"title": "Рыба с рисом", "minutes": 45, "energy": 18, "happy": 8, "xp": 6, "lvl": 4,
                  "items": {"fish": 300, "rice": 200, "onion": 50, "oil": 20}},
    "borscht": {"title": "Борщ", "minutes": 70, "energy": 20, "happy": 9, "xp": 7, "lvl": 5,
                "items": {"beef": 250, "beet": 200, "cabbage": 200, "potato": 250, "carrot": 100,
                          "onion": 100, "oil": 20}},
    "charlotte": {"title": "Шарлотка", "minutes": 60, "energy": 10, "happy": 14, "xp": 7, "lvl": 6,
                  "items": {"apple": 400, "eggs": 3, "flour": 150, "sugar": 120}},
}
COOK_MIN_ENERGY = 20.0            # меньше энергии - готовить нельзя
COOK_ENERGY_RATE = -1.5           # энергия в час во время готовки
# Опыт Повара: уровень N требует BASE + STEP * N опыта
COOK_MAX_LEVEL = 20
COOK_XP_BASE = 8.0
COOK_XP_STEP = 4.0
# Каждый уровень: быстрее готовка и сильнее эффект еды
COOK_TIME_STEP = 0.015            # -1.5% времени за уровень (минимум один квант)
COOK_BOOST_STEP = 0.02            # +2% к энергии и счастью за уровень
# Убывающая отдача за игровые сутки по числу уже приготовленных блюд: (до какого номера блюда, множитель)
COOK_EFFECT_TIERS = ((2, 1.0), (3, 0.5), (None, 0.2))    # на эффект: 1-2 блюда полностью, 3-е вдвое меньше
COOK_XP_TIERS = ((3, 1.0), (5, 0.4), (None, 0.1))        # на опыт: 1-3 полностью, 4-5 по 40%, дальше 10%