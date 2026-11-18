"""Табло Ludo: картинка доски с фигурами (Pillow).

Основа (поле, базы, кольцо, колонки, центр, звёзды, стрелки старта) рисуется в двойном разрешении ОДИН раз,
уменьшается до итогового размера и хранится в кэше. Ключ кэша - множество занятых мест: от него зависит,
какие углы рисуются яркими, а какие бледными. На каждый ход дорисовываются только фигуры, аватарки и метка хода.
Про Discord и БД модуль ничего не знает: входные данные - PieceToken и SeatToken (их строит make_scene)."""
import io
import math
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw

from core.imaging import font as _font, render_in_thread
from .board import (AVATAR_R, AVATAR_XY, BASE_ORIGIN, BASE_SLOTS, HOME_COLUMN, RING, START, STARS,
                    TURN_MARK_SIZE, TURN_MARK_XY, cell_xy)
from .rules import LUDO_FINISH, LUDO_RING_STEPS

RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS

# ---------- Размеры (логические пиксели; основа рисуется в SS раз крупнее) ----------
SS = 2
CELL = 52                          # сторона клетки сетки 15x15
MARGIN = 10                        # поля вокруг доски
GRID = 15
SIZE = GRID * CELL + 2 * MARGIN    # итоговая картинка: 800x800
CU = CELL * SS                     # клетка в пикселях «большого» рисунка

# ---------- Цвета (порядок мест: красный, зелёный, жёлтый, синий) ----------
SEAT_COLORS = ((226, 59, 59), (46, 170, 90), (240, 190, 40), (52, 120, 220))
BG = (244, 238, 226)
RING_FILL = (252, 252, 250)
LINE = (120, 122, 130)
WHITE = (255, 255, 255)
DARK = (28, 30, 34)
GOLD = (255, 214, 64)
STAR_FILL = (140, 146, 160)
IDLE_GRAY = (205, 205, 205)        # с этим цветом смешиваются углы без игрока

UVEC = ((1, 0), (0, 1), (-1, 0), (0, -1))   # от стороны центра к его середине, по местам
BASE_DIA = 1.2                              # диаметр фигуры в базе (в клетках)
SINGLE_DIA = 0.8                            # диаметр одинокой фигуры на кольце

_BASE_CACHE: OrderedDict = OrderedDict()    # frozenset занятых мест -> основа (RGB, итоговый размер)
_BASE_CACHE_LIMIT = 6

# ---------- Входные данные ----------

@dataclass
class PieceToken:
    seat: int
    piece: int                    # номер фигуры 0..3 (на круге рисуется piece + 1)
    progress: int                 # 0 база, 1..51 кольцо, 52..56 колонка, 57 центр
    movable: bool = False         # фаза выбора: этой фигурой можно ходить

@dataclass
class SeatToken:
    seat: int
    name: str = ""
    is_bot: bool = False
    avatar: Optional[bytes] = None
    letter: str = ""              # буква на круге, если аватарки нет
    current: bool = False         # сейчас его ход

def make_scene(match, avatars: dict) -> tuple:
    """(фигуры, места) по состоянию партии. avatars: {pid: байты картинки}.
    Вышедшие игроки на доске не показываются."""
    cur = None if match.over else match.current_player
    movable = {m.piece for m in match.options()} if cur is not None else set()
    pieces, seats = [], []
    for p in match.players:
        if p.left:
            continue
        letter = (p.name.strip()[:1] or "?").upper()
        seats.append(SeatToken(p.seat, p.name, p.is_bot, avatars.get(p.pid), letter, p is cur))
        for i, prog in enumerate(p.pieces):
            pieces.append(PieceToken(p.seat, i, prog, p is cur and i in movable))
    return pieces, seats

# ---------- Мелочи ----------

def _P(x: float, y: float) -> tuple:
    """Клетки сетки (с дробной частью) -> пиксели «большого» рисунка."""
    return (x * CELL + MARGIN) * SS, (y * CELL + MARGIN) * SS

def _mix(c, other, k: float) -> tuple:
    return tuple(int(c[i] * (1 - k) + other[i] * k) for i in range(3))

def _seat_color(seat: int, active: bool) -> tuple:
    c = SEAT_COLORS[seat]
    return c if active else _mix(c, IDLE_GRAY, 0.72)

def _star(cx, cy, ro, ri) -> list:
    pts = []
    for i in range(10):
        r = ro if i % 2 == 0 else ri
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts

@lru_cache(maxsize=16)
def _circle_mask(d: int):
    big = Image.new("L", (d * 3, d * 3), 0)
    ImageDraw.Draw(big).ellipse((0, 0, d * 3 - 1, d * 3 - 1), fill=255)
    return big.resize((d, d), RESAMPLE)

def _paste(img, sprite, gx: float, gy: float):
    """Уменьшает спрайт (нарисован в SS раз крупнее) и ставит его центром в точку сетки (gx, gy)."""
    w, h = sprite.width // SS, sprite.height // SS
    small = sprite.resize((w, h), RESAMPLE)
    img.paste(small, (round(gx * CELL + MARGIN - w / 2), round(gy * CELL + MARGIN - h / 2)), small)

# ---------- Основа доски ----------

def _build_base(active: frozenset):
    big = Image.new("RGB", (SIZE * SS, SIZE * SS), BG)
    d = ImageDraw.Draw(big)
    col = {s: _seat_color(s, s in active) for s in range(4)}

    def rect(x0, y0, x1, y1):
        return (*_P(x0, y0), *_P(x1, y1))

    def cell(x, y, fill):
        d.rectangle(rect(x, y, x + 1, y + 1), fill=fill, outline=LINE, width=SS)

    # Базы: цветной квадрат, светлая площадка и четыре «гнезда» под фигуры
    for s in range(4):
        ox, oy = BASE_ORIGIN[s]
        d.rounded_rectangle(rect(ox + 0.04, oy + 0.04, ox + 5.96, oy + 5.96), radius=int(0.45 * CU),
                            fill=col[s], outline=_mix(col[s], DARK, 0.35), width=2 * SS)
        d.rounded_rectangle(rect(ox + 1.45, oy + 1.45, ox + 4.55, oy + 4.55), radius=int(0.3 * CU),
                            fill=_mix(col[s], WHITE, 0.8))
        for sx, sy in BASE_SLOTS:
            cx, cy = _P(ox + sx, oy + sy)
            r = 0.66 * CU
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_mix(col[s], WHITE, 0.45),
                      outline=_mix(col[s], DARK, 0.25), width=SS)

    # Кольцо, домашние колонки, стартовые клетки
    for x, y in RING:
        cell(x, y, RING_FILL)
    for s in range(4):
        for x, y in HOME_COLUMN[s]:
            cell(x, y, col[s])
    for s, idx in enumerate(START):
        x, y = RING[idx]
        cell(x, y, col[s])

    # Центр: четыре треугольника
    tri = {0: [(6, 6), (6, 9), (7.5, 7.5)], 1: [(6, 6), (9, 6), (7.5, 7.5)],
           2: [(9, 6), (9, 9), (7.5, 7.5)], 3: [(6, 9), (9, 9), (7.5, 7.5)]}
    for s, pts in tri.items():
        d.polygon([_P(*p) for p in pts], fill=col[s], outline=LINE)

    # Звёзды
    for idx in STARS:
        x, y = RING[idx]
        cx, cy = _P(x + 0.5, y + 0.5)
        d.polygon(_star(cx, cy, 0.36 * CU, 0.15 * CU), fill=STAR_FILL, outline=DARK)

    # Стрелка на стартовой клетке: куда пойдёт фигура
    for idx in START:
        x, y = RING[idx]
        nx, ny = RING[(idx + 1) % len(RING)]
        ln = math.hypot(nx - x, ny - y) or 1.0
        ux, uy = (nx - x) / ln, (ny - y) / ln
        px, py = -uy, ux
        cx, cy = x + 0.5, y + 0.5
        pts = [(cx + ux * 0.30, cy + uy * 0.30),
               (cx - ux * 0.22 + px * 0.26, cy - uy * 0.22 + py * 0.26),
               (cx - ux * 0.22 - px * 0.26, cy - uy * 0.22 - py * 0.26)]
        d.polygon([_P(*p) for p in pts], fill=WHITE)

    return big.resize((SIZE, SIZE), RESAMPLE)

def _get_base(active: frozenset):
    base = _BASE_CACHE.get(active)
    if base is None:
        base = _build_base(active)
        _BASE_CACHE[active] = base
        while len(_BASE_CACHE) > _BASE_CACHE_LIMIT:
            _BASE_CACHE.popitem(last=False)
    else:
        _BASE_CACHE.move_to_end(active)
    return base

# ---------- Фигуры ----------

def _draw_piece(d, x: float, y: float, dia: float, color, number: int, movable: bool):
    r = dia / 2
    if movable:                                                  # золотое кольцо: этой фигурой можно ходить
        g = r + 4 * SS
        d.ellipse((x - g, y - g, x + g, y + g), fill=GOLD, outline=DARK, width=SS)
    d.ellipse((x - r, y - r, x + r, y + r), fill=_mix(color, DARK, 0.5))
    ri = r - 2.5 * SS
    d.ellipse((x - ri, y - ri, x + ri, y + ri), fill=color)
    if number and dia >= 0.36 * CU:                              # на мелких фигурах цифра не читается
        f, _cyr = _font(int(dia * 0.55))
        try:
            d.text((x, y), str(number), font=f, fill=WHITE, anchor="mm",
                   stroke_width=SS, stroke_fill=DARK)
        except Exception:
            pass

def _slots(n: int, single: float) -> list:
    """Места фигур внутри клетки: (смещение x, смещение y, диаметр) в клетках."""
    if n == 1:
        return [(0.0, 0.0, single)]
    cols = 2 if n <= 4 else 3
    rows = math.ceil(n / cols)
    step, dia = (0.46, 0.44) if cols == 2 else (0.31, 0.30)
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        in_row = min(cols, n - r * cols)
        out.append(((c - (in_row - 1) / 2) * step, (r - (rows - 1) / 2) * step, dia))
    return out

def _capsule(d, pts: list, dia: float):
    """Отметка блока: капсула за двумя своими фигурами (тёмная кайма и белая заливка)."""
    for extra, color in ((8 * SS, DARK), (4 * SS, WHITE)):
        d.line(pts, fill=color, width=int(dia + extra))
        r = (dia + extra) / 2
        for x, y in pts:
            d.ellipse((x - r, y - r, x + r, y + r), fill=color)

def _cell_sprite(group: list, single: float = SINGLE_DIA):
    """Спрайт одной клетки (2x2 клетки, чтобы вместились кольцо подсветки и капсула блока)."""
    group = sorted(group, key=lambda p: (p.seat, p.piece))      # свои фигуры идут подряд
    sp = Image.new("RGBA", (2 * CU, 2 * CU), (0, 0, 0, 0))
    d = ImageDraw.Draw(sp)
    slots = _slots(len(group), single)
    pos = [(CU + dx * CU, CU + dy * CU) for dx, dy, _dia in slots]
    dia = slots[0][2] * CU

    by_seat: dict = {}
    for i, p in enumerate(group):
        if p.progress <= LUDO_RING_STEPS:
            by_seat.setdefault(p.seat, []).append(i)
    for idxs in by_seat.values():
        if len(idxs) >= 2:
            _capsule(d, [pos[i] for i in idxs], dia)
    for p, (x, y) in zip(group, pos):
        _draw_piece(d, x, y, dia, SEAT_COLORS[p.seat], p.piece + 1, p.movable)
    return sp

def _center_sprite(finished: dict):
    """Финишировавшие фигуры: маленькие кружки в «своём» треугольнике центра (до 4 на цвет)."""
    sp = Image.new("RGBA", (3 * CU, 3 * CU), (0, 0, 0, 0))
    d = ImageDraw.Draw(sp)
    r = 0.23 * CU
    for seat, n in finished.items():
        ux, uy = UVEC[seat]
        vx, vy = -uy, ux
        for k in range(min(n, 4)):
            row, c = divmod(k, 2)
            t, lat = 0.35 + 0.5 * row, (c - 0.5) * 0.56
            gx = -ux * 1.5 + ux * t + vx * lat                   # от середины центра
            gy = -uy * 1.5 + uy * t + vy * lat
            x, y = 1.5 * CU + gx * CU, 1.5 * CU + gy * CU
            d.ellipse((x - r, y - r, x + r, y + r), fill=WHITE)
            ri = r - 2.5 * SS
            d.ellipse((x - ri, y - ri, x + ri, y + ri), fill=SEAT_COLORS[seat])
    return sp

# ---------- Аватарки и метка хода ----------

def _bot_icon(d, size: int):
    """Значок робота: голова, глаза, антенна."""
    s = size
    d.line((s * 0.5, s * 0.36, s * 0.5, s * 0.25), fill=WHITE, width=max(1, int(s * 0.04)))
    r = s * 0.045
    d.ellipse((s * 0.5 - r, s * 0.23 - r, s * 0.5 + r, s * 0.23 + r), fill=WHITE)
    d.rounded_rectangle((s * 0.27, s * 0.36, s * 0.73, s * 0.70), radius=int(s * 0.08), fill=WHITE)
    for ex in (0.41, 0.59):
        e = s * 0.055
        d.ellipse((s * ex - e, s * 0.53 - e, s * ex + e, s * 0.53 + e), fill=DARK)

def _avatar_tile(s: SeatToken):
    d = round(2 * AVATAR_R * CELL) * SS
    color = SEAT_COLORS[s.seat]
    face = None
    if s.avatar and not s.is_bot:
        try:
            face = Image.open(io.BytesIO(s.avatar)).convert("RGBA").resize((d, d), RESAMPLE)
        except Exception:
            face = None
    if face is None:
        face = Image.new("RGBA", (d, d), color + (255,))
        fd = ImageDraw.Draw(face)
        if s.is_bot:
            _bot_icon(fd, d)
        elif s.letter:
            f, cyr = _font(int(d * 0.5))
            if cyr or s.letter.isascii():
                try:
                    fd.text((d / 2, d / 2), s.letter, font=f, fill=WHITE, anchor="mm")
                except Exception:
                    pass
    face.putalpha(_circle_mask(d))                               # на месте; результат не присваивать
    width = (7 if s.current else 5) * SS
    ImageDraw.Draw(face).ellipse((0, 0, d - 1, d - 1), outline=color + (255,), width=width)
    return face

def _turn_mark_tile(seat: int):
    """Маленький квадрат с крестом: «сейчас его ход»."""
    size = round((TURN_MARK_SIZE + 0.3) * CELL) * SS
    sp = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(sp)
    color = SEAT_COLORS[seat]
    side = round(TURN_MARK_SIZE * CELL) * SS
    a, b = (size - side) / 2, (size + side) / 2
    d.rounded_rectangle((a, a, b, b), radius=4 * SS, fill=WHITE, outline=color, width=4 * SS)
    m = side * 0.25
    for p, q in (((a + m, a + m), (b - m, b - m)), ((a + m, b - m), (b - m, a + m))):
        d.line([p, q], fill=color, width=4 * SS)
    return sp

# ---------- Сборка картинки ----------

def render_board(pieces=(), seats=()) -> bytes:
    """PNG-табло. pieces/seats - из make_scene. Основа берётся из кэша, рисуются только фигуры и метки."""
    active = frozenset(s.seat for s in seats)
    img = _get_base(active).copy()                               # основу в кэше портить нельзя

    in_base, finished, cells = [], defaultdict(int), {}
    for p in pieces:
        if p.progress <= 0:
            in_base.append(p)
        elif p.progress >= LUDO_FINISH:
            finished[p.seat] += 1
        else:
            cells.setdefault(cell_xy(p.seat, p.progress, p.piece), []).append(p)

    if finished:
        _paste(img, _center_sprite(finished), 7.5, 7.5)
    for p in in_base:
        gx, gy = cell_xy(p.seat, 0, p.piece)
        _paste(img, _cell_sprite([p], BASE_DIA), gx, gy)
    for (gx, gy), group in sorted(cells.items(), key=lambda kv: any(p.movable for p in kv[1])):
        _paste(img, _cell_sprite(group), gx, gy)                 # клетки с подсвеченными фигурами - сверху

    for s in seats:
        ax, ay = AVATAR_XY[s.seat]
        _paste(img, _avatar_tile(s), ax, ay)
        if s.current:
            tx, ty = TURN_MARK_XY[s.seat]
            _paste(img, _turn_mark_tile(s.seat), tx, ty)

    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=3)
    return buf.getvalue()

async def render_board_async(pieces=(), seats=()) -> bytes:
    """Рисует в отдельном потоке (по одной картинке за раз на весь бот)."""
    return await render_in_thread(render_board, list(pieces), list(seats))