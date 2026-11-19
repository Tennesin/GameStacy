"""Правила и константы Ludo. Всё, что хочется подкрутить, лежит здесь.
Все имена с префиксом LUDO_: в других играх те же понятия имеют другие значения."""
from core.players import BOT_IDS

# ---------- Партия ----------
LUDO_MIN_PLAYERS = 2
LUDO_MAX_PLAYERS = 4
LUDO_MAX_BOTS = 3                      # при одном человеке максимум 4 участника
LUDO_DEFAULT_BOTS = 1
LUDO_PIECES = 4                        # фигур у каждого игрока
LUDO_DICE_SIDES = 6
LUDO_EXIT_ROLL = 6                     # число, которым фигура выходит из базы
LUDO_MAX_SIXES_IN_ROW = 3              # на третьей подряд шестёрке ход не выполняется
LUDO_BONUS_ON_CAPTURE = True
LUDO_BONUS_ON_FINISH = True
LUDO_PASS_OWN_BLOCKS = True            # свои фигуры могут перешагнуть свой блок (встать на него нельзя)

# ---------- Время ----------
LUDO_ROLL_SECONDS = 30                 # на бросок
LUDO_PICK_SECONDS = 15                 # на выбор фигуры
LUDO_TIMEOUTS_TO_LEAVE = 3             # пропущенных подряд до исключения
LUDO_RESET_TIMEOUTS_ON_MOVE = True
LUDO_BOT_DELAY_SECONDS = 2.5           # пауза перед действием бота и перед автоходом
LUDO_LOBBY_SECONDS = 300
LUDO_LOG_LINES = 8

# ---------- Доска (детали геометрии: board.py) ----------
LUDO_RING_LEN = 52                     # клеток на общем кольце
LUDO_SEAT_GAP = 13                     # расстояние между стартовыми клетками соседних мест
LUDO_STAR_OFFSET = 8                   # звезда стоит на 8 клеток дальше своей стартовой клетки
LUDO_RING_STEPS = 51                   # прогресс 1..51: кольцо (1 = стартовая клетка)
LUDO_HOME_COLUMN = 5                   # прогресс 52..56: домашняя колонка
LUDO_FINISH = 57                       # прогресс 57: центр; прогресс 0: база

# Какие углы занимают игроки при разном их числе (углы идут по часовой стрелке)
LUDO_LAYOUT = {2: (0, 2), 3: (0, 1, 2), 4: (0, 1, 2, 3)}
LUDO_SEAT_NAMES = ("Красный", "Зелёный", "Жёлтый", "Синий")
LUDO_SEAT_EMOJI = ("🔴", "🟢", "🟡", "🔵")