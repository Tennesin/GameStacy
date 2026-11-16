"""Табло Джуманджи: картинка карты с фишками игроков (Pillow)."""
import random
import zlib
from PIL import ImageOps
import asyncio
import io
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional
from PIL import Image, ImageChops, ImageDraw, ImageFont

from .models import GameMap, parse_cell_id, side_chains, trace_path
from .rules import BOT_IDS

RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
BICUBIC = getattr(Image, "Resampling", Image).BICUBIC

# ---------- Размеры (логические пиксели; реально рисуется в SS раз крупнее) ----------
SS = 2
CELL = 112                 # сторона клетки сетки
RADIUS = 44                # радиус круга-ячейки
PAD = 36                   # поля вокруг карты
MAX_SIDE = 1600            # итоговая картинка не больше этого по длинной стороне (сетка 16x16 иначе слишком тяжёлая)

# ---------- Цвета ----------
BG = (43, 45, 49, 255)
NORMAL = (214, 217, 222)
EFFECT_COLORS = {
    "jump": (59, 165, 93), "back": (237, 66, 69), "skip": (240, 147, 43),
    "charge": (51, 160, 240), "mini": (170, 60, 255), "reset": (24, 25, 28),
    "branch": (250, 204, 21),
}
STRIPE = (250, 204, 21)              # диагональные полоски на клетках особого пути
SIDE_LINK = (150, 110, 20, 255)
FINISH_FILL = (240, 240, 240)
START_RING = (80, 200, 220)
FINISH_RING = (255, 196, 40)
LINK_ON = (70, 52, 34, 255)
LINK_OFF = (85, 89, 97, 255)         # стрелки у ячеек вне маршрута
ORPHAN_ALPHA = 90
GOLD = (255, 214, 64, 255)
GRASS_DARK, GRASS_LIGHT = (46, 78, 52), (82, 118, 72)
TRAIL_EDGE, TRAIL_SAND = (88, 66, 44), (198, 172, 122)
SIDE_EDGE, SIDE_SAND = (122, 92, 28), (232, 200, 112)
TRAIL_EXTRA = 28          # на сколько тропа шире круга ячейки (логические px)

BOT_COLORS = [(155, 89, 182), (233, 30, 140), (26, 188, 156), (141, 110, 99)]
BOT_COLOR_NAMES = ["фиолетовый", "розовый", "бирюзовый", "коричневый"]
BOT_NAMES = ["Фиолетовый бот", "Розовый бот", "Бирюзовый бот", "Коричневый бот"]
HUMAN_COLORS = [(66, 133, 244), (59, 200, 100), (250, 210, 50), (235, 60, 60), (245, 140, 40)]

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
    for seat, p in enumerate(match.players):
        if not p.on_map:
            continue
        if p.is_bot:
            color = BOT_IDS.index(p.pid) if p.pid in BOT_IDS else 0
        else:
            color = seat % len(HUMAN_COLORS)
        letter = (p.name.strip()[:1] or "?").upper()
        out.append(Token(p.pid, p.pos, p.is_bot, color, avatars.get(p.pid), p.pid == cur, letter))
    return out

def _slots(n: int) -> list:
    """Места фишек внутри ячейки: (смещение x, смещение y, диаметр)."""
    if n == 1:
        return [(0, 0, 62)]
    if n == 2:
        return [(-21, 0, 42), (21, 0, 42)]
    ring, dia = {3: (22, 38), 4: (25, 34)}.get(n, (26, 30))
    return [(ring * math.cos(-math.pi / 2 + 2 * math.pi * i / n),
             ring * math.sin(-math.pi / 2 + 2 * math.pi * i / n), dia) for i in range(n)]

@lru_cache(maxsize=32)
def _circle_mask(d: int):
    big = Image.new("L", (d * 3, d * 3), 0)
    ImageDraw.Draw(big).ellipse((0, 0, d * 3 - 1, d * 3 - 1), fill=255)
    return big.resize((d, d), RESAMPLE)

def _tile(tok: Token, dia: int, dim: bool):
    """Готовая круглая фишка с цветной обводкой (RGBA)."""
    d = int(dia * SS)
    palette = BOT_COLORS if tok.is_bot else HUMAN_COLORS
    ring_rgb = palette[tok.color % len(palette)]

    face, plain = None, False
    if tok.avatar and not tok.is_bot:
        try:
            face = Image.open(io.BytesIO(tok.avatar)).convert("RGBA").resize((d, d), RESAMPLE)
        except Exception:
            face = None
    if face is None:
        face = Image.new("RGBA", (d, d), ring_rgb + (255,))
        plain = True

    face.putalpha(_circle_mask(d))          # изменяет face на месте и возвращает None: присваивать результат нельзя

    if plain and not tok.is_bot and tok.letter:               # у ботов буквы нет: «обычные кружки»
        font, cyr = _font(int(d * 0.5))
        if cyr or tok.letter.isascii():
            try:
                ImageDraw.Draw(face).text((d / 2, d / 2), tok.letter, font=font,
                                          fill=(255, 255, 255, 255), anchor="mm")
            except Exception:
                pass

    width = (7 if tok.current else 5) * SS
    ImageDraw.Draw(face).ellipse((0, 0, d - 1, d - 1), outline=ring_rgb + (255,), width=width)

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

def _cubic(a, c1, c2, b, steps):
    """Кубическая кривая Безье от a к b с контрольными точками c1 и c2."""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        pts.append((u * u * u * a[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * b[0],
                    u * u * u * a[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * b[1]))
    return pts

def _intrusion(pts, a, b, trail, cells, trail_need, exempt, cell_need) -> float:
    """Насколько путь заходит на тропу и на чужие ячейки (0 — путь чистый)."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    reach = max(trail_need, cell_need)
    x0, x1, y0, y1 = min(xs) - reach, max(xs) + reach, min(ys) - reach, max(ys) + reach
    near_t = [o for o in trail if x0 <= o[0] <= x1 and y0 <= o[1] <= y1]
    near_c = [o for o in cells if x0 <= o[0] <= x1 and y0 <= o[1] <= y1]
    if not near_t and not near_c:
        return 0.0
    total = 0.0
    for px, py in pts:
        if near_t and math.hypot(px - a[0], py - a[1]) >= exempt and math.hypot(px - b[0], py - b[1]) >= exempt:
            best = min((px - ox) ** 2 + (py - oy) ** 2 for ox, oy in near_t)
            if best < trail_need * trail_need:
                total += trail_need - math.sqrt(best)
        for ox, oy in near_c:
            dist = math.hypot(px - ox, py - oy)
            if dist < cell_need:
                total += cell_need - dist
    return total

def _route(a, b, na, nb, trail, cells, box, half, R):
    """Путь от a к b снаружи тропы."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 1:
        return [a, b]
    chord = (-(b[1] - a[1]) / L, (b[0] - a[0]) / L)
    na, nb = na or chord, nb or chord
    trail_need = half + 4 * SS
    exempt = half + 10 * SS
    cell_need = R + 6 * SS
    ks = sorted({half * m for m in (1.0, 1.4, 1.9, 2.5, 3.2, 4.2)} | {0.4 * L, 0.7 * L})

    best, best_v = None, 0.0
    for k in ks:                                              # от малого обхода к большому
        steps = max(24, int((L + 3 * k) / (18 * SS)))
        for sa, sb in ((1, 1), (-1, -1), (1, -1), (-1, 1)):   # с какой стороны тропы выйти и войти
            c1 = (a[0] + na[0] * k * sa, a[1] + na[1] * k * sa)
            c2 = (b[0] + nb[0] * k * sb, b[1] + nb[1] * k * sb)
            pts = _cubic(a, c1, c2, b, steps)
            if not all(box[0] <= x <= box[2] and box[1] <= y <= box[3] for x, y in pts):
                continue
            v = _intrusion(pts, a, b, trail, cells, trail_need, exempt, cell_need)
            if v == 0:
                return pts
            if best is None or v < best_v:
                best, best_v = pts, v
    return best if best is not None else [a, b]

def _trim(pts, a, b, s_off, e_off):
    """Обрезает ломаную у кругов ячеек a и b (точно, с интерполяцией)."""
    def cut(inside, outside, c, off):
        d_in, d_out = math.dist(inside, c), math.dist(outside, c)
        t = 0.0 if d_out == d_in else max(0.0, min(1.0, (off - d_in) / (d_out - d_in)))
        return (inside[0] + (outside[0] - inside[0]) * t, inside[1] + (outside[1] - inside[1]) * t)

    i = 0
    while i < len(pts) and math.dist(pts[i], a) < s_off:
        i += 1
    j = len(pts) - 1
    while j >= 0 and math.dist(pts[j], b) < e_off:
        j -= 1
    if i >= j or i == 0 or j == len(pts) - 1:
        return pts[i:j + 1] if i < j else []
    return [cut(pts[i - 1], pts[i], a, s_off)] + pts[i:j + 1] + [cut(pts[j + 1], pts[j], b, e_off)]

def _dashed_poly(d, pts, color, width, dash, gap):
    on, left = True, dash
    for p, q in zip(pts, pts[1:]):
        seg = math.hypot(q[0] - p[0], q[1] - p[1])
        if seg == 0:
            continue
        ux, uy = (q[0] - p[0]) / seg, (q[1] - p[1]) / seg
        pos = 0.0
        while pos < seg:
            step = min(left, seg - pos)
            if on:
                d.line([(p[0] + ux * pos, p[1] + uy * pos),
                        (p[0] + ux * (pos + step), p[1] + uy * (pos + step))], fill=color, width=width)
            pos += step
            left -= step
            if left <= 1e-9:
                on = not on
                left = dash if on else gap

def _poly_arrow(d, pts, color, width, head):
    """Сплошная стрелка вдоль ломаной; наконечник направлен по касательной к концу."""
    if len(pts) < 2:
        return
    tip, back = pts[-1], pts[0]
    for p in reversed(pts[:-1]):
        if math.dist(p, tip) >= head * 0.8:
            back = p
            break
    ux, uy = tip[0] - back[0], tip[1] - back[1]
    ln = math.hypot(ux, uy)
    if ln == 0:
        return
    ux, uy = ux / ln, uy / ln
    base = (tip[0] - ux * head, tip[1] - uy * head)

    body = [p for p in pts if math.dist(p, tip) > head]     # тело стрелки без участка под наконечником
    body.append(base)
    if len(body) >= 2:
        d.line(body, fill=color, width=width, joint="curve")

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

def _stripes(img, cx: float, cy: float, R: int, alpha: int):
    """Диагональные жёлтые полоски внутри круга ячейки."""
    size = int(2 * R)
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    for k in range(-size, 2 * size, 20 * SS):
        td.line([(k, size), (k + size, 0)], fill=STRIPE + (alpha,), width=8 * SS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    tile.putalpha(ImageChops.multiply(tile.getchannel("A"), mask))
    img.alpha_composite(tile, (int(cx - R), int(cy - R)))

def _bounds(m: GameMap, show_grid: bool) -> tuple:
    pts = [p for p in (parse_cell_id(c) for c in m.cells) if p]
    if show_grid or not pts:
        return 0, 0, max(m.width, 1) - 1, max(m.height, 1) - 1
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)

# ---------- Ландшафт и тропа ----------

def _noise(w: int, h: int, cell: int, rng: random.Random):
    """Гладкий шум: маленькая случайная картинка, растянутая BICUBIC."""
    sw, sh = max(2, w // cell + 2), max(2, h // cell + 2)
    small = Image.new("L", (sw, sh))
    small.putdata([rng.randrange(256) for _ in range(sw * sh)])
    return small.resize((w, h), BICUBIC)

def _terrain(W: int, H: int, rng: random.Random):
    """Травяной фон. Считается в логическом размере, потом растягивается до SS."""
    w, h = max(2, W // SS), max(2, H // SS)
    mix = ImageChops.blend(_noise(w, h, 110, rng), _noise(w, h, 30, rng), 0.35)
    tex = ImageOps.colorize(ImageOps.autocontrast(mix), GRASS_DARK, GRASS_LIGHT)
    return tex.resize((W, H), RESAMPLE).convert("RGBA")

def _spline(pts, per: int = 8):
    """Плавная кривая Catmull-Rom строго через заданные точки."""
    if len(pts) < 3:
        return list(pts)
    p = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for k in range(per):
            t = k / per
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t
                       + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                       + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3)
                for j in (0, 1)))
    out.append(tuple(pts[-1]))
    return out

def _draw_trail(img, pts, edge, sand, R: int):
    """Тропа: тёмная кайма и светлая середина. Возвращает точки кривой."""
    d = ImageDraw.Draw(img, "RGBA")
    curve = _spline(pts)
    for color, width in ((edge, 2 * R + (TRAIL_EXTRA + 12) * SS), (sand, 2 * R + TRAIL_EXTRA * SS)):
        d.line(curve, fill=color + (255,), width=int(width), joint="curve")
        r = width / 2
        for x, y in (curve[0], curve[-1]):                    # круглые концы
            d.ellipse((x - r, y - r, x + r, y + r), fill=color + (255,))
    return curve

def _decorate(img, rng: random.Random, samples: list, W: int, H: int, R: int):
    """Деревья, кусты, камни и пруды. Не ближе безопасного расстояния к тропе."""
    d = ImageDraw.Draw(img, "RGBA")
    clear = R + (TRAIL_EXTRA // 2 + 26) * SS
    step = 64 * SS
    pts = samples[::2]
    ponds, items = [], []
    for gy in range(0, H, step):
        for gx in range(0, W, step):
            x = gx + rng.uniform(0.1, 0.9) * step             # все случайные числа берём ДО проверок:
            y = gy + rng.uniform(0.1, 0.9) * step             # тогда декор не «прыгает» при правке карты
            kind, size = rng.random(), rng.random()
            need = clear + (70 * SS if kind > 0.97 else 0)
            need2 = need * need
            if any((x - px) ** 2 + (y - py) ** 2 < need2 for px, py in pts):
                continue
            (ponds if kind > 0.97 else items).append((y, x, kind, size))

    for y, x, _k, s in ponds[:3]:
        rw, rh = (40 + 30 * s) * SS, (26 + 16 * s) * SS
        d.ellipse((x - rw - 7 * SS, y - rh - 7 * SS, x + rw + 7 * SS, y + rh + 7 * SS), fill=(176, 160, 110, 255))
        d.ellipse((x - rw, y - rh, x + rw, y + rh), fill=(62, 120, 168, 255))
        d.ellipse((x - rw * 0.6, y - rh * 0.65, x + rw * 0.25, y - rh * 0.05), fill=(94, 152, 198, 255))

    for y, x, kind, s in sorted(items):                       # дальние выше, ближние рисуются поверх
        if kind < 0.55:                                       # дерево
            r = (18 + 12 * s) * SS
            d.ellipse((x - r * 0.9, y + r * 0.35, x + r * 1.1, y + r * 0.95), fill=(0, 0, 0, 55))
            d.ellipse((x - r, y - r, x + r, y + r), fill=(30, 66, 38, 255))
            d.ellipse((x - r * 0.7, y - r * 0.85, x + r * 0.4, y - r * 0.1), fill=(56, 102, 56, 255))
        elif kind < 0.8:                                      # куст
            r = (10 + 7 * s) * SS
            d.ellipse((x - r, y - r * 0.8, x + r, y + r * 0.8), fill=(48, 94, 52, 255))
            d.ellipse((x - r * 0.6, y - r * 0.7, x + r * 0.2, y), fill=(76, 124, 70, 255))
        else:                                                 # камень
            r = (8 + 6 * s) * SS
            d.ellipse((x - r, y - r * 0.7, x + r, y + r * 0.7), fill=(120, 124, 128, 255))
            d.ellipse((x - r * 0.6, y - r * 0.55, x + r * 0.1, y - r * 0.05), fill=(160, 164, 168, 255))

def render_board(m: GameMap, tokens=(), selected: Optional[str] = None, show_grid: bool = False) -> bytes:
    """PNG-табло. tokens — фишки (make_tokens)."""
    path, _problem = trace_path(m)
    index = {cid: i for i, cid in enumerate(path)}
    chains, _bad = side_chains(m, path)
    full = list(path)                                         # клетки в том же порядке, что индексы позиций у Track
    side_label = {}                                           # клетка дороги -> подпись вида «12.3»
    for bid, chain, _exit in chains:
        for k, cid in enumerate(chain, start=1):
            full.append(cid)
            side_label[cid] = f"{index[bid]}.{k}"

    x0, y0, x1, y1 = _bounds(m, show_grid)
    shift = 0.0 if show_grid else max((max(abs(c.dx), abs(c.dy)) for c in m.cells.values()), default=0.0)
    pad = PAD + math.ceil(shift * CELL)                       # поля растут, чтобы сдвинутые ячейки не обрезались
    W = ((x1 - x0 + 1) * CELL + 2 * pad) * SS
    H = ((y1 - y0 + 1) * CELL + 2 * pad) * SS
    rng = random.Random(zlib.crc32(m.name.encode("utf-8")))
    img = Image.new("RGBA", (W, H), BG) if show_grid else _terrain(W, H, rng)
    d = ImageDraw.Draw(img, "RGBA")
    R = RADIUS * SS

    def center(cid: str):
        x, y = parse_cell_id(cid)
        c = m.cells.get(cid)
        ox, oy = (0.0, 0.0) if (show_grid or c is None) else (c.dx, c.dy)
        return (((x - x0 + 0.5 + ox) * CELL + pad) * SS, ((y - y0 + 0.5 + oy) * CELL + pad) * SS)

    def drawable(cid) -> bool:
        return bool(cid) and cid in m.cells and parse_cell_id(cid) is not None

    centers = {cid: center(cid) for cid in m.cells if drawable(cid)}

    # тропа и ландшафт (в режиме редактора с сеткой не рисуем)
    samples = []
    if not show_grid:
        main_pts = [centers[c] for c in path if c in centers]
        for chain_info in chains:
            bid, chain, exit_id = chain_info
            ids = [bid, *chain] + ([exit_id] if exit_id else [])
            if all(c in centers for c in ids) and len(ids) >= 2:
                samples += _draw_trail(img, [centers[c] for c in ids], SIDE_EDGE, SIDE_SAND, R)
        if len(main_pts) >= 2:
            samples += _draw_trail(img, main_pts, TRAIL_EDGE, TRAIL_SAND, R)
        _decorate(img, rng, samples, W, H, R)

    # данные для обхода тропы стрелками прыжков
    half = R + (TRAIL_EXTRA + 12) * SS / 2                    # полуширина тропы вместе с каймой
    trail_pts = samples[::2]
    off_route = [p for cid, p in centers.items() if cid not in index and cid not in side_label]
    normals = {}                                              # нормаль к тропе в ячейках основного маршрута
    for i, cid in enumerate(path):
        p, q = centers.get(path[max(i - 1, 0)]), centers.get(path[min(i + 1, len(path) - 1)])
        if p is None or q is None:
            continue
        tx, ty = q[0] - p[0], q[1] - p[1]
        ln = math.hypot(tx, ty)
        if ln:
            normals[cid] = (-ty / ln, tx / ln)

    # подписи координат и пустые места сетки (только в редакторе)
    if show_grid:
        label_font, _ = _font(13 * SS)
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                cx = ((gx - x0) * CELL + CELL / 2 + pad) * SS
                cy = ((gy - y0) * CELL + CELL / 2 + pad) * SS
                cid = f"{chr(65 + gx)}{gy + 1}"
                if cid not in m.cells:
                    d.ellipse((cx - 6 * SS, cy - 6 * SS, cx + 6 * SS, cy + 6 * SS), fill=(80, 84, 92, 255))
                d.text((cx - CELL / 2 * SS + 6 * SS, cy + CELL / 2 * SS - 20 * SS), cid,
                       font=label_font, fill=(125, 130, 140, 255))

    # стрелки прыжков и откатов: идут снаружи тропы, у каждой тёмная обводка
    box = (3 * SS, 3 * SS, W - 3 * SS, H - 3 * SS)
    for cid, c in m.cells.items():
        if c.effect in ("jump", "back") and drawable(cid) and drawable(c.target):
            a, b = centers[cid], centers[c.target]
            pts = _route(a, b, normals.get(cid), normals.get(c.target), trail_pts, off_route, box, half, R)
            pts = _trim(pts, a, b, R + 3 * SS, R + 3 * SS)
            _poly_arrow(d, pts, (20, 22, 26, 230), 8 * SS, 15 * SS)
            _poly_arrow(d, pts, EFFECT_COLORS[c.effect] + (255,), 4 * SS, 12 * SS)

    # стрелки маршрута (внутри дороги — жёлтые)
    for cid, c in m.cells.items():
        if cid == m.finish or cid == c.next or not drawable(cid) or not drawable(c.next):
            continue
        color = SIDE_LINK if cid in side_label else (LINK_ON if cid in index else LINK_OFF)
        _arrow(d, centers[cid], centers[c.next], color, 5 * SS, 11 * SS, R + 2 * SS, R + 2 * SS)

    # связь «начало особого пути -> первая клетка дороги»: жёлтый пунктир
    for cid, c in m.cells.items():
        if c.effect == "branch" and drawable(cid) and drawable(c.target):
            _arrow(d, centers[cid], centers[c.target], EFFECT_COLORS["branch"] + (255,),
                   5 * SS, 11 * SS, R + 2 * SS, R + 2 * SS, dashed=True)

    # ячейки
    for cid, c in m.cells.items():
        if not drawable(cid):
            continue
        cx, cy = centers[cid]
        on = cid in index or cid in side_label
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
        circle = (cx - R, cy - R, cx + R, cy + R)
        d.ellipse(circle, fill=fill + (a,), outline=ring + (a,), width=width)
        if c.side:
            _stripes(img, cx, cy, R, a)
            d.ellipse(circle, outline=ring + (a,), width=width)
        if kind in ("start", "finish"):
            _icon(d, kind, cx, cy, fg + (a,))
        if cid == selected:
            rr = R + 8 * SS
            d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(255, 255, 255, 255), width=5 * SS)

    # фишки: на одной ячейке несколько — все, кроме ходящего, затемнены
    groups: dict = {}
    for t in tokens:
        if 0 <= t.pos < len(full):
            groups.setdefault(t.pos, []).append(t)
    for pos, group in groups.items():
        cx, cy = centers[full[pos]]
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
            if tok.current:                                   # булавка над ходящим
                top = py - tile.height / 2 - 4 * SS
                d.polygon([(px, top), (px - 15 * SS, top - 24 * SS), (px + 15 * SS, top - 24 * SS)],
                          fill=GOLD, outline=(30, 30, 30, 255))

    tw, th = W // SS, H // SS
    big = max(tw, th)
    if big > MAX_SIDE:                                        # большая сетка: сразу уменьшаем до разумного размера
        k = MAX_SIDE / big
        tw, th = max(1, int(tw * k)), max(1, int(th * k))
    out = img.resize((tw, th), RESAMPLE).convert("RGB")
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