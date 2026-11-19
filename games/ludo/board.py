"""Геометрия доски Ludo: сетка 15x15, 52 клетки кольца, колонки, базы. Чистые данные, без Discord и БД."""
from .rules import (LUDO_RING_LEN, LUDO_SEAT_GAP, LUDO_STAR_OFFSET, LUDO_RING_STEPS)

# Кольцо по часовой стрелке. Индекс 0 - стартовая клетка красного (1, 6). Координаты (x, y), y вниз.
RING = (
    [(x, 6) for x in range(1, 6)] + [(6, y) for y in range(5, -1, -1)] + [(7, 0), (8, 0)]
    + [(8, y) for y in range(1, 6)] + [(x, 6) for x in range(9, 15)] + [(14, 7), (14, 8)]
    + [(x, 8) for x in range(13, 8, -1)] + [(8, y) for y in range(9, 15)] + [(7, 14), (6, 14)]
    + [(6, y) for y in range(13, 8, -1)] + [(x, 8) for x in range(5, -1, -1)] + [(0, 7), (0, 6)]
)

START = tuple(LUDO_SEAT_GAP * k for k in range(4))                # 0, 13, 26, 39
STARS = tuple(s + LUDO_STAR_OFFSET for s in START)                # 8, 21, 34, 47
SAFE_CELLS = frozenset(STARS) | frozenset(START)       # звёзды и стартовые клетки всех четырёх мест

# Домашние колонки (5 клеток от кольца к центру) и центр
HOME_COLUMN = (
    [(x, 7) for x in range(1, 6)],
    [(7, y) for y in range(1, 6)],
    [(x, 7) for x in range(13, 8, -1)],
    [(7, y) for y in range(13, 8, -1)],
)
CENTER = (7, 7)

# Базы: левый верхний угол квадрата 6x6 каждого места и места четырёх фигур внутри него
BASE_ORIGIN = ((0, 0), (9, 0), (9, 9), (0, 9))
BASE_SLOTS = ((2.1, 2.1), (3.9, 2.1), (2.1, 3.9), (3.9, 3.9))

# Аватарка живого игрока: круг в самом дальнем от центра углу его квадрата (центр круга, радиус 0.7 клетки)
AVATAR_XY = ((0.85, 0.85), (14.15, 0.85), (14.15, 14.15), (0.85, 14.15))
AVATAR_R = 0.7
# Отметка «сейчас его ход» (рисуется X): маленькое поле в ближнем к центру углу квадрата
TURN_MARK_XY = ((5.35, 5.35), (9.65, 5.35), (9.65, 9.65), (5.35, 9.65))
TURN_MARK_SIZE = 0.8                   # сторона маленького квадрата, в клетках

def ring_index(seat: int, progress: int):
    """Индекс клетки кольца для фигуры места seat с прогрессом 1..51; иначе None."""
    if 1 <= progress <= LUDO_RING_STEPS:
        return (START[seat] + progress - 1) % LUDO_RING_LEN
    return None

def is_safe(seat: int, ring_idx: int) -> bool:
    """Безопасные клетки: звёзды и ВСЕ стартовые клетки."""
    return ring_idx in SAFE_CELLS

def cell_xy(seat: int, progress: int, piece: int):
    """Координаты центра фигуры на сетке (в клетках, с учётом +0.5 для клеток)."""
    if progress == 0:
        ox, oy = BASE_ORIGIN[seat]
        sx, sy = BASE_SLOTS[piece]
        return ox + sx, oy + sy
    idx = ring_index(seat, progress)
    if idx is not None:
        x, y = RING[idx]
    elif progress <= 56:
        x, y = HOME_COLUMN[seat][progress - 52]
    else:
        x, y = CENTER
    return x + 0.5, y + 0.5