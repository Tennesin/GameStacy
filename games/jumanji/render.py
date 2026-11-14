"""Табло Джуманджи: картинка карты с фишками игроков (Pillow).
Про Discord и БД ничего не знает: на входе карта и список фишек, на выходе PNG в байтах.
Рисуется в двойном размере и уменьшается: так края гладкие."""
import asyncio
import io
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .models import GameMap, parse_cell_id, trace_path
from .rules import BOT_IDS

RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS

# ---------- Размеры (логические пиксели; реально рисуется в SS раз крупнее) ----------
SS = 2
CELL = 112                 # сторона клетки сетки
RADIUS = 44                # радиус круга-ячейки
PAD = 10                   # поля вокруг карты

# ---------- Цвета ----------
BG = (43, 45, 49, 255)
NORMAL = (214, 217, 222)
EFFECT_COLORS = {
    "jump": (59, 165, 93), "back": (237, 66, 69), "skip": (240, 147, 43),
    "charge": (51, 160, 240), "mini": (247, 209, 84), "reset": (24, 25, 28),
}
FINISH_FILL = (240, 240, 240)
START_RING = (80, 200, 220)
FINISH_RING = (255, 196, 40)
LINK_ON = (140, 146, 156, 255)       # стрелки маршрута
LINK_OFF = (85, 89, 97, 255)         # стрелки у ячеек вне маршрута
ORPHAN_ALPHA = 90
GOLD = (255, 214, 64, 255)

BOT_COLORS = [(155, 89, 182), (233, 30, 140), (26, 188, 156), (141, 110, 99)]
BOT_COLOR_NAMES = ["фиолетовый", "розовый", "бирюзовый", "коричневый"]
HUMAN_COLORS = [(88, 101, 242), (87, 242, 135), (254, 231, 92), (235, 69, 158), (237, 66, 69), (52, 152, 219)]

DIM_RGB, DIM_ALPHA = 0.45, 0.5       # затемнение фишек, которые делят ячейку с ходящим

# ---------- Шрифт (в картинке только цифры и латиница; кириллица нужна лишь для букв-заглушек) ----------
_ROOT = Path(__file__).resolve().parents[2]
_FONT_PATHS = [
    _ROOT / "assets" / "fonts" / "DejaVuSans-Bold.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
]

@lru_cache(maxsize=8)
def _font(size: int):
    """(шрифт, умеет_ли_кириллицу)."""
    for p in _FONT_PATHS:
        try:
            return ImageFont.truetype(str(p), size), True
        except OSError:
            continue
    try:
        return ImageFont.load_default(size), False
    except TypeError:                                         # старая Pillow: размер не поддерживается
        return ImageFont.load_default(), False

# ---------- Фишки игроков ----------

@dataclass
class Token:
    pid: int
    pos: int                          # индекс на маршруте (0 — старт)
    is_bot: bool = False
    color: int = 0                    # номер цвета: у ботов — номер бота, у людей без аватарки — любой
    avatar: Optional[bytes] = None    # PNG/JPEG аватарки
    current: bool = False             # у него сейчас ход
    letter: str = ""                  # буква на заглушке, если аватарки нет

def make_tokens(match, avatars: dict) -> list:
    """Фишки по состоянию партии. avatars: {pid: байты}. На карте остаются только играющие."""
    cur = None if match.over else match.current_player.pid
    out = []
    for p in match.players:
        if not p.on_map:
            continue
        if p.is_bot:
            color = BOT_IDS.index(p.pid) if p.pid in BOT_IDS else 0
        else:
            color = abs(p.pid) % len(HUMAN_COLORS)
        letter = (p.name.strip()[:1] or "?").upper()
        out.append(Token(p.pid, p.pos, p.is_bot, color, avatars.get(p.pid), p.pid == cur, letter))
    return out

def _slots(n: int) -> list:
    """Места фишек внутри ячейки: (смещение x, смещение y, диаметр)."""
    if n == 1:
        return [(0, 0, 58)]
    if n == 2:
        return [(-18, 0, 40), (18, 0, 40)]
    ring, dia = {3: (20, 34), 4: (24, 32)}.get(n, (25, 28))
    return [(ring * math.cos(-math.pi / 2 + 2 * math.pi * i / n),
             ring * math.sin(-math.pi / 2 + 2 * math.pi * i / n), dia) for i in range(n)]

@lru_cache(maxsize=32)
def _circle_mask(d: int):
    big = Image.new("L", (d * 3, d * 3), 0)
    ImageDraw.Draw(big).ellipse((0, 0, d * 3 - 1, d * 3 - 1), fill=255)
    return big.resize((d, d), RESAMPLE)

def _tile(tok: Token, dia: int, dim: bool):
    """Готовая круглая фишка с обводкой (RGBA)."""
    d = int(dia * SS)
    face, plain = None, False
    if tok.avatar and not tok.is_bot:
        try:
            face = Image.open(io.BytesIO(tok.avatar)).convert("RGBA").resize((d, d), RESAMPLE)
        except Exception:
            face = None
    if face is None:
        palette = BOT_COLORS if tok.is_bot else HUMAN_COLORS
        face = Image.new("RGBA", (d, d), palette[tok.color % len(palette)] + (255,))
        plain = True
    face.putalpha(_circle_mask(d))
    if plain and not tok.is_bot and tok.letter:               # у ботов буквы нет: «обычные кружки»
        font, cyr = _font(int(d * 0.5))
        if cyr or tok.letter.isascii():
            try:
                ImageDraw.Draw(face).text((d / 2, d / 2), tok.letter, font=font,
                                          fill=(255, 255, 255, 255), anchor="mm")
            except Exception:
                pass
    width = (4 if tok.current else 2) * SS
    ImageDraw.Draw(face).ellipse((0, 0, d - 1, d - 1),
                                 outline=GOLD if tok.current else (255, 255, 255, 255), width=width)
    if dim:
        r, g, b, a = face.split()
        rgb = [int(i * DIM_RGB) for i in range(256)]
        alpha = [int(i * DIM_ALPHA) for i in range(256)]
        face = Image.merge("RGBA", (r.point(rgb), g.point(rgb), b.point(rgb), a.point(alpha)))
    return face

# ---------- Рисование ----------

def _dashed(d, a, b, color, width, dash, gap):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return
    ux, uy = dx / length, dy / length
    t = 0.0
    while t < length:
        e = min(t + dash, length)
        d.line([(a[0] + ux * t, a[1] + uy * t), (a[0] + ux * e, a[1] + uy * e)], fill=color, width=width)
        t += dash + gap

def _arrow(d, a, b, color, width, head, start_off, end_off, dashed=False):
    """Стрелка от a к b, укороченная с обоих концов (чтобы не залезать на круги)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= start_off + end_off + head:
        return
    ux, uy = dx / length, dy / length
    p1 = (a[0] + ux * start_off, a[1] + uy * start_off)
    tip = (b[0] - ux * end_off, b[1] - uy * end_off)
    base = (tip[0] - ux * head, tip[1] - uy * head)
    if dashed:
        _dashed(d, p1, base, color, width, 10 * SS, 7 * SS)
    else:
        d.line([p1, base], fill=color, width=width)
    nx, ny = -uy, ux
    d.polygon([tip, (base[0] + nx * head * 0.6, base[1] + ny * head * 0.6),
               (base[0] - nx * head * 0.6, base[1] - ny * head * 0.6)], fill=color)

def _icon(d, kind: str, cx: float, cy: float, fg):
    k = SS

    def P(pts):
        return [(cx + x * k, cy + y * k) for x, y in pts]

    if kind == "jump":
        d.polygon(P([(-16, -7), (1, -7), (1, -16), (18, 0), (1, 16), (1, 7), (-16, 7)]), fill=fg)
    elif kind == "back":
        d.polygon(P([(16, -7), (-1, -7), (-1, -16), (-18, 0), (-1, 16), (-1, 7), (16, 7)]), fill=fg)
    elif kind == "skip":
        d.rectangle(P([(-13, -15), (-4, 15)]), fill=fg)
        d.rectangle(P([(4, -15), (13, 15)]), fill=fg)
    elif kind == "charge":
        d.polygon(P([(4, -18), (-10, 2), (-1, 2), (-5, 18), (10, -4), (1, -4)]), fill=fg)
    elif kind == "mini":
        d.rounded_rectangle(P([(-16, -16), (16, 16)]), radius=5 * k, outline=fg, width=3 * k)
        for x, y in ((-7, -7), (7, -7), (0, 0), (-7, 7), (7, 7)):
            d.ellipse(P([(x - 2.5, y - 2.5), (x + 2.5, y + 2.5)]), fill=fg)
    elif kind == "reset":
        d.line(P([(-12, -12), (12, 12)]), fill=fg, width=6 * k)
        d.line(P([(-12, 12), (12, -12)]), fill=fg, width=6 * k)
    elif kind == "start":
        d.line(P([(-8, -17), (-8, 17)]), fill=fg, width=3 * k)
        d.polygon(P([(-8, -17), (16, -8), (-8, 2)]), fill=fg)
    elif kind == "finish":
        for i in range(4):
            for j in range(4):
                x0, y0 = -14 + 7 * i, -14 + 7 * j
                d.rectangle(P([(x0, y0), (x0 + 7, y0 + 7)]),
                            fill=fg if (i + j) % 2 == 0 else (255, 255, 255, fg[3] if len(fg) > 3 else 255))
        d.rectangle(P([(-14, -14), (14, 14)]), outline=fg, width=2 * k)

def _bounds(m: GameMap, show_grid: bool) -> tuple:
    pts = [p for p in (parse_cell_id(c) for c in m.cells) if p]
    if show_grid or not pts:
        return 0, 0, max(m.width, 1) - 1, max(m.height, 1) - 1
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)

def render_board(m: GameMap, tokens=(), selected: Optional[str] = None, show_grid: bool = False) -> bytes:
    """PNG-табло. tokens — фишки (make_tokens); selected — подсветить ячейку (для редактора);
    show_grid — показать пустые места сетки (для редактора). Карта может быть недоделанной."""
    path, _problem = trace_path(m)
    index = {cid: i for i, cid in enumerate(path)}
    x0, y0, x1, y1 = _bounds(m, show_grid)
    W = ((x1 - x0 + 1) * CELL + 2 * PAD) * SS
    H = ((y1 - y0 + 1) * CELL + 2 * PAD) * SS
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img, "RGBA")

    def center(cid: str):
        x, y = parse_cell_id(cid)
        return (((x - x0) * CELL + CELL / 2 + PAD) * SS, ((y - y0) * CELL + CELL / 2 + PAD) * SS)

    def drawable(cid) -> bool:
        return bool(cid) and cid in m.cells and parse_cell_id(cid) is not None

    R = RADIUS * SS

    # пустые места сетки
    if show_grid:
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                cx = ((gx - x0) * CELL + CELL / 2 + PAD) * SS
                cy = ((gy - y0) * CELL + CELL / 2 + PAD) * SS
                if f"{chr(65 + gx)}{gy + 1}" not in m.cells:
                    d.ellipse((cx - 6 * SS, cy - 6 * SS, cx + 6 * SS, cy + 6 * SS), fill=(80, 84, 92, 255))

    # пунктир прыжков и откатов
    for cid, c in m.cells.items():
        if c.effect in ("jump", "back") and drawable(cid) and drawable(c.target):
            col = EFFECT_COLORS[c.effect] + (170,)
            _arrow(d, center(cid), center(c.target), col, 4 * SS, 12 * SS, R + 3 * SS, R + 3 * SS, dashed=True)

    # стрелки маршрута
    for cid, c in m.cells.items():
        if cid == m.finish or cid == c.next or not drawable(cid) or not drawable(c.next):
            continue
        _arrow(d, center(cid), center(c.next), LINK_ON if cid in index else LINK_OFF,
               5 * SS, 11 * SS, R + 2 * SS, R + 2 * SS)

    # ячейки
    small, _ = _font(15 * SS)
    for cid, c in m.cells.items():
        if not drawable(cid):
            continue
        cx, cy = center(cid)
        on = cid in index
        a = 255 if on else ORPHAN_ALPHA
        if cid == m.finish:
            kind, fill, ring, fg = "finish", FINISH_FILL, FINISH_RING, (30, 30, 30)
        elif cid == m.start:
            kind, fill, ring, fg = "start", NORMAL, START_RING, (200, 40, 40)
        elif c.effect in EFFECT_COLORS:
            fill = EFFECT_COLORS[c.effect]
            kind, ring = c.effect, ((150, 150, 160) if c.effect == "reset" else tuple(int(v * 0.7) for v in fill))
            fg = (45, 45, 45) if c.effect == "mini" else (255, 255, 255)
        else:
            kind, fill, ring, fg = None, NORMAL, (150, 153, 160), None
        width = (6 if kind in ("start", "finish") else 4) * SS
        d.ellipse((cx - R, cy - R, cx + R, cy + R), fill=fill + (a,), outline=ring + (a,), width=width)
        if kind:
            _icon(d, kind, cx, cy, fg + (a,))
        if on and kind is None or (on and kind in EFFECT_COLORS):
            d.text((cx - CELL / 2 * SS + 6 * SS, cy - CELL / 2 * SS + 3 * SS), str(index[cid]),
                   font=small, fill=(190, 194, 202, 255))
        if cid == selected:
            rr = R + 8 * SS
            d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(255, 255, 255, 255), width=5 * SS)

    # фишки: на одной ячейке несколько — все, кроме ходящего, затемнены
    groups: dict = {}
    for t in tokens:
        if 0 <= t.pos < len(path):
            groups.setdefault(t.pos, []).append(t)
    for pos, group in groups.items():
        cx, cy = center(path[pos])
        shared = len(group) > 1
        items = sorted(zip(group, _slots(len(group))), key=lambda it: it[0].current)   # ходящий — сверху
        for tok, (dx, dy, dia) in items:
            tile = _tile(tok, dia, dim=shared and not tok.current)
            px, py = cx + dx * SS, cy + dy * SS
            if tok.current:
                halo = tile.width / 2 + 6 * SS
                ImageDraw.Draw(img, "RGBA").ellipse((px - halo, py - halo, px + halo, py + halo),
                                                    fill=(255, 214, 64, 70))
            img.alpha_composite(tile, (int(px - tile.width / 2), int(py - tile.height / 2)))

    out = img.resize((W // SS, H // SS), RESAMPLE).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()

_lock: Optional[asyncio.Semaphore] = None

async def render_board_async(m: GameMap, tokens=(), selected: Optional[str] = None,
                             show_grid: bool = False) -> bytes:
    """Рисует в отдельном потоке и не больше одной картинки за раз (на сервере всего 1 ГБ памяти)."""
    global _lock
    if _lock is None:
        _lock = asyncio.Semaphore(1)
    async with _lock:
        return await asyncio.to_thread(render_board, m, list(tokens), selected, show_grid)