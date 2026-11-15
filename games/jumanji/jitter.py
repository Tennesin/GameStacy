"""Визуальный «шум» карты: ячейки слегка уходят в стороны от ровной змейки.
Меняются только dx/dy (сдвиг внутри своей клетки сетки). ID ячеек, маршрут, эффекты и правила игры не затрагиваются.
Про Discord и БД ничего не знает.

Как работает:
  1. Вдоль маршрута строится плавная «волна» (сумма трёх синусоид со случайными фазами) + немного белого шума.
     Ячейка сдвигается по нормали к направлению маршрута (в стороны) и чуть-чуть вдоль него.
  2. Релаксация: если две ячейки оказались слишком близко, они расталкиваются; соседние по маршруту
     не должны ни слипаться (иначе пропадёт стрелка), ни разъезжаться слишком далеко.
  3. Если ограничения не удалось выполнить, сдвиги уменьшаются, в пределе до нуля (ровная карта)."""
import math
import random

from .models import GameMap, parse_cell_id, trace_path

JITTER_STRENGTH = 0.8     # общая сила шума: 0 — ровно, 1 — максимум
SIDE_AMPL = 0.32          # амплитуда боковой волны (в долях клетки сетки)
ALONG_AMPL = 0.10         # сдвиг вдоль маршрута
MAX_SHIFT = 0.40          # предел сдвига по каждой оси
MIN_GAP = 0.90            # минимальное расстояние между центрами любых ячеек (круг ячейки = 0.79)
STEP_MIN = 0.99           # соседи по маршруту: не ближе (иначе стрелка между ними не поместится)
STEP_EXTRA = 0.45         # и не дальше исходного расстояния + это
RELAX_ITERS = 40

def new_seed() -> int:
    return random.randrange(1, 2 ** 31)

def _wave(n: int, rng: random.Random) -> list:
    parts = []
    for period, weight in ((rng.uniform(3.5, 6.0), 0.45), (rng.uniform(8.0, 14.0), 0.35),
                           (rng.uniform(15.0, 30.0), 0.20)):
        parts.append((2 * math.pi / period, rng.uniform(0, 2 * math.pi), weight))
    return [sum(w * math.sin(f * i + ph) for f, ph, w in parts) for i in range(n)]

def apply_jitter(m: GameMap, seed: int, strength: float = JITTER_STRENGTH) -> None:
    """Перезаписывает dx/dy у ячеек m (на месте). Один и тот же seed даёт один и тот же результат."""
    for c in m.cells.values():
        c.dx = c.dy = 0.0
    path, problem = trace_path(m)
    if problem or len(path) < 3:
        return
    rng = random.Random(seed)
    n = len(path)
    base = {cid: parse_cell_id(cid) for cid in m.cells}
    wave = _wave(n, rng)

    # 1. Начальные сдвиги: по нормали к маршруту + чуть вдоль
    off: dict = {}
    for i, cid in enumerate(path):
        ax, ay = base[path[max(i - 1, 0)]]
        bx, by = base[path[min(i + 1, n - 1)]]
        tx, ty = bx - ax, by - ay
        ln = math.hypot(tx, ty) or 1.0
        tx, ty = tx / ln, ty / ln
        side = SIDE_AMPL * strength * (0.8 * wave[i] + 0.2 * rng.uniform(-1, 1))
        along = ALONG_AMPL * strength * rng.uniform(-1, 1)
        off[cid] = [-ty * side + tx * along, tx * side + ty * along]

    # 2. Пары, за которыми следит релаксация: (a, b, мин. расстояние, макс. расстояние или None)
    pairs, seen = [], set()
    for a, b in zip(path, path[1:]):
        pairs.append((a, b, STEP_MIN, math.dist(base[a], base[b]) + STEP_EXTRA))
        seen.add((a, b))
        seen.add((b, a))
    by_pos = {pos: cid for cid, pos in base.items()}
    for cid, (x, y) in base.items():
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                other = by_pos.get((x + ddx, y + ddy))
                if other is None or other == cid or (cid, other) in seen:
                    continue
                seen.add((cid, other))
                seen.add((other, cid))
                if cid in off or other in off:           # две неподвижные ячейки не проверяем
                    pairs.append((cid, other, MIN_GAP, None))

    def pos(cid):
        x, y = base[cid]
        ox, oy = off.get(cid, (0.0, 0.0))
        return x + ox, y + oy

    def push(cid, sx, sy):
        v = off[cid]
        v[0] = max(-MAX_SHIFT, min(MAX_SHIFT, v[0] + sx))
        v[1] = max(-MAX_SHIFT, min(MAX_SHIFT, v[1] + sy))

    def settle():
        for _ in range(RELAX_ITERS):
            changed = False
            for a, b, lo, hi in pairs:
                (ax, ay), (bx, by) = pos(a), pos(b)
                dx, dy = bx - ax, by - ay
                d = math.hypot(dx, dy) or 1e-6
                if d < lo:
                    delta = lo - d
                elif hi is not None and d > hi:
                    delta = hi - d
                else:
                    continue
                changed = True
                ux, uy = dx / d, dy / d
                a_free, b_free = a in off, b in off
                share = (0.5 if (a_free and b_free) else 1.0) * 1.05
                if a_free:
                    push(a, -ux * delta * share, -uy * delta * share)
                if b_free:
                    push(b, ux * delta * share, uy * delta * share)
            if not changed:
                return

    def valid() -> bool:
        for a, b, lo, hi in pairs:
            d = math.dist(pos(a), pos(b))
            if d < lo - 0.02 or (hi is not None and d > hi + 0.02):
                return False
        return True

    settle()
    tries = 0
    while not valid() and tries < 12:                    # не вышло: ослабляем шум
        for v in off.values():
            v[0] *= 0.8
            v[1] *= 0.8
        settle()
        tries += 1
    if not valid():
        for v in off.values():
            v[0] = v[1] = 0.0

    for cid, (ox, oy) in off.items():
        m.cells[cid].dx, m.cells[cid].dy = round(ox, 3), round(oy, 3)

def jittered(m: GameMap, seed: int, strength: float = JITTER_STRENGTH) -> GameMap:
    """Копия карты со сдвигами. Исходная карта не меняется."""
    j = m.copy()
    apply_jitter(j, seed, strength)
    return j