"""Проверка карты перед экспортом: ошибки блокируют экспорт, предупреждения только показываются."""
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .logic import Track, roll_outcome
from .models import GameMap, MapFormatError, parse_cell_id, side_chains, trace_path
from .rules import (
    BRANCH, DICE_SIDES, EFFECTS, EFFECT_SHARE_WARN, EFFECT_TITLES, GRID_MAX_H, GRID_MAX_W,
    MAP_NAME_MAX, MAX_EXTRA_ROLLS, MIN_PATH_WARN, SIM_FAIL_SHARE, SIM_LONG_TURNS,
    SIM_MAX_TURNS, SIM_RUNS, SIM_SEED, SIM_SHORT_TURNS, TARGET_EFFECTS
)

@dataclass
class Report:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def text(self, limit: int = 1800) -> str:
        """Читаемый вариант для Discord (лимит сообщения 2000 символов)."""
        if self.ok:
            lines = ["✅ Карта проверена: ошибок нет."]
        else:
            lines = [f"❌ Ошибок: {len(self.errors)}. Экспорт невозможен."]
            lines += ["", "Ошибки:"] + [f"• {e}" for e in self.errors]
        if self.warnings:
            lines += ["", "Предупреждения:"] + [f"• {w}" for w in self.warnings]
        s = self.stats
        if "avg_turns" in s:
            lines += ["", f"Маршрут: {s['path']} ячеек. Одиночная партия: в среднем {s['avg_turns']:.0f} ходов "
                          f"(медиана {s['median_turns']}, 95% партий не длиннее {s['p95_turns']}). "
                          "Мини-игры в оценке не учитываются."]
        out = "\n".join(lines)
        return out if len(out) <= limit else out[:limit - 1] + "…"

def _many(items, limit: int = 8) -> str:
    items = list(items)
    text = ", ".join(items[:limit])
    return text + (f" … и ещё {len(items) - limit}" if len(items) > limit else "")

def validate_map(m: GameMap, runs: int = SIM_RUNS) -> Report:
    r = Report()
    err, warn = r.errors.append, r.warnings.append

    # 1. Название и размер
    name = m.name.strip()
    if not name:
        err("У карты нет названия.")
    elif len(name) > MAP_NAME_MAX:
        err(f"Название длиннее {MAP_NAME_MAX} символов.")
    size_ok = 1 <= m.width <= GRID_MAX_W and 1 <= m.height <= GRID_MAX_H
    if not size_ok:
        err(f"Размер сетки {m.width}x{m.height} недопустим (максимум {GRID_MAX_W}x{GRID_MAX_H}).")

    # 2. Ячейки
    bad_pos, bad_eff = [], []
    for cid, c in m.cells.items():
        pos = parse_cell_id(cid)
        if pos is None or cid != c.id or (size_ok and not (pos[0] < m.width and pos[1] < m.height)):
            bad_pos.append(cid)
        if c.effect is not None and c.effect not in EFFECTS:
            bad_eff.append(f"{cid} («{c.effect}»)")
    if bad_pos:
        err(f"Ячейки вне сетки или с неверными координатами: {_many(bad_pos)}.")
    if bad_eff:
        err(f"Неизвестные эффекты: {_many(bad_eff)}.")
    side_fx = [cid for cid, c in m.cells.items() if c.side and c.effect]
    if side_fx:
        err(f"На клетках особого пути эффектов быть не может: {_many(side_fx)}.")

    # 3. Старт и финиш
    ends_ok = True
    for label, cid in (("Старт", m.start), ("Финиш", m.finish)):
        if not cid:
            err(f"{label} не задан.")
            ends_ok = False
        elif cid not in m.cells:
            err(f"{label} ({cid}) не поставлен на карту.")
            ends_ok = False
        elif m.cells[cid].side:
            err(f"{label} ({cid}) не может быть клеткой особого пути.")
            ends_ok = False
    if ends_ok and m.start == m.finish:
        err("Старт и финиш — одна и та же ячейка.")
        ends_ok = False

    # 4. Связи «следующая ячейка» (у каждой, кроме финиша; у последней клетки особого пути — выход на маршрут)
    no_next, self_next, bad_next = [], [], []
    for cid, c in m.cells.items():
        if cid == m.finish:
            continue
        if not c.next:
            no_next.append(cid)
        elif c.next == cid:
            self_next.append(cid)
        elif c.next not in m.cells:
            bad_next.append(f"{cid}→{c.next}")
    if no_next:
        err(f"Не указана следующая ячейка: {_many(no_next)}.")
    if self_next:
        err(f"Ячейка ведёт сама на себя: {_many(self_next)}.")
    if bad_next:
        err(f"Следующая ячейка не существует: {_many(bad_next)}.")
    if m.finish in m.cells and m.cells[m.finish].next:
        warn(f"У финиша указана следующая ячейка ({m.cells[m.finish].next}): она будет проигнорирована.")
    links_ok = not (no_next or self_next or bad_next)

    # 5. Эффекты: на старте и финише нельзя; цели jump / back (у «Особого пути» цель проверяет side_chains)
    on_ends, no_target, bad_target, extra_target = [], [], [], []
    for cid, c in m.cells.items():
        if c.effect not in EFFECTS or c.side:
            continue
        if cid in (m.start, m.finish):
            on_ends.append(cid)
        if c.effect in TARGET_EFFECTS:
            if not c.target:
                no_target.append(cid)
            elif c.target not in m.cells:
                bad_target.append(f"{cid}→{c.target}")
        elif c.effect != BRANCH and c.target:
            extra_target.append(cid)
    if on_ends:
        err(f"На старте и финише эффектов быть не может: {_many(on_ends)}.")
    if no_target:
        err(f"Не указана цель у «Перепрыгнуть/Откатить»: {_many(no_target)}.")
    if bad_target:
        err(f"Цель эффекта не существует: {_many(bad_target)}.")
    if extra_target:
        warn(f"У этих эффектов цель не нужна и будет проигнорирована: {_many(extra_target)}.")

    # 6. Маршрут
    if not (ends_ok and links_ok):
        if ends_ok:
            warn("Маршрут не проверен: сначала исправьте связи между ячейками.")
        return r
    path, problem = trace_path(m)
    if problem:
        err(problem)
        return r

    index = {cid: i for i, cid in enumerate(path)}
    in_side = [cid for cid in path if m.cells[cid].side]
    if in_side:
        err(f"Основной маршрут заходит на клетки особого пути: {_many(in_side)}. Особый путь должен "
            "начинаться с эффекта «Особый путь» на клетке маршрута, а не с обычной связи «далее».")
        return r

    chains, problems = side_chains(m, path)
    for text in problems:
        err(text)
    owned = {cid for _b, chain, _e in chains for cid in chain}
    r.stats["path"] = len(path)
    r.stats["branches"] = len(chains)
    if len(path) < MIN_PATH_WARN:
        warn(f"Маршрут очень короткий: {len(path)} ячеек (рекомендуется не меньше {MIN_PATH_WARN}).")
    orphans = [cid for cid in m.cells if cid not in index and cid not in owned]
    if orphans:
        warn(f"Не входят в маршрут от старта и будут проигнорированы: {_many(orphans)}.")

    # 7. Цели прыжков и откатов
    for i, cid in enumerate(path):
        c = m.cells[cid]
        if c.effect not in TARGET_EFFECTS or not c.target or c.target not in m.cells:
            continue
        t = index.get(c.target)
        if t is None:
            err(f"Цель эффекта ячейки {cid} ({c.target}) не лежит на основном маршруте.")
        elif c.effect == "jump" and t <= i:
            err(f"«Перепрыгнуть» в {cid} ведёт назад или на месте ({c.target}): прыжок должен идти вперёд.")
        elif c.effect == "back" and t >= i:
            err(f"«Откатить» в {cid} ведёт вперёд или на месте ({c.target}): откат должен идти назад.")
        elif c.effect == "jump" and t == len(path) - 1:
            warn(f"«Перепрыгнуть» в {cid} ведёт прямо на финиш: игрок выиграет без точного броска.")
        elif m.cells[c.target].effect:
            warn(f"Цель эффекта ячейки {cid} ({c.target}) сама с эффектом: он не сработает "
                 "(эффект не срабатывает после перемещения; на начале особого пути заход тоже не начнётся).")

    inner = path[1:-1]
    counts = Counter(m.cells[cid].effect for cid in inner if m.cells[cid].effect in EFFECTS)
    r.stats["effects"] = dict(counts)
    if len(inner) >= 3 and sum(counts.values()) / len(inner) > EFFECT_SHARE_WARN:
        warn(f"Эффектами занято больше {int(EFFECT_SHARE_WARN * 100)}% клеток: игра превратится в лотерею.")

    if r.errors:
        return r
    try:
        track = Track(m)
    except MapFormatError as e:
        err(str(e))
        return r
    _analyze(track, r, runs)
    return r

def _simulate(track: Track, runs: int) -> tuple:
    """Одиночные партии без мини-игр."""
    rng = random.Random(SIM_SEED)
    turns, capped = [], 0
    for _ in range(runs):
        pos, com, n, done = 0, False, 0, False
        while n < SIM_MAX_TURNS and not done:
            n += 1
            extra = 0
            while True:
                s = roll_outcome(track, pos, rng.randint(1, DICE_SIDES), com)
                if not s.blocked:
                    pos, com = s.end, s.entered
                if s.finished:
                    done = True
                    break
                if s.effect == "skip":
                    n += 1                                   # пропущенный ход
                if s.effect == "charge" and extra < MAX_EXTRA_ROLLS:
                    extra += 1
                    continue
                break
        if done:
            turns.append(n)
        else:
            capped += 1
    return turns, capped

def _analyze(track: Track, r: Report, runs: int):
    """Граф состояний (клетка, заход на особый путь) и моделирование одиночных партий."""
    err, warn = r.errors.append, r.warnings.append
    f = track.finish
    start, goal = (0, False), (f, False)

    states = [(i, False) for i in range(len(track.cells)) if i != f]
    states += [(i, True) for i in track.entry]               # «стоит на начале и пойдёт по дороге»
    edges, lands = {}, {}
    for s in states:
        ends, lnd = set(), set()
        for d in range(1, DICE_SIDES + 1):
            st = roll_outcome(track, s[0], d, s[1])
            if st.blocked:
                continue
            ends.add((st.end, st.entered))
            lnd.add(st.landed)
        edges[s], lands[s] = ends, lnd

    seen, stack = {start}, [start]                           # достижимые от старта
    while stack:
        for t in edges.get(stack.pop(), ()):
            if t not in seen:
                seen.add(t)
                stack.append(t)

    back = defaultdict(set)
    for s, ts in edges.items():
        for t in ts:
            back[t].add(s)
    good, stack = {goal}, [goal]                             # откуда финиш достижим
    while stack:
        for s in back.get(stack.pop(), ()):
            if s not in good:
                good.add(s)
                stack.append(s)

    if goal not in seen:
        err("До финиша невозможно добраться броском кубика: на пути есть «стена» из эффектов, "
            "через которую нельзя перебраться (например шесть подряд клеток отката или сброса).")
        return
    traps = sorted({s[0] for s in seen if s not in good})
    if traps:
        err("Есть ловушки: попав в эти ячейки, игрок уже не сможет дойти до финиша: "
            + _many(track.cells[i] for i in traps) + ".")
        return

    # Основной маршрут не должен зависеть от особых путей: без захода на них до финиша можно дойти из любой
    # клетки основного маршрута, куда игрок вообще может попасть.
    free_back = defaultdict(set)
    for s, ts in edges.items():
        for t in ts:
            if not t[1]:                                     # ребро не ведёт на заход
                free_back[t].add(s)
    free, stack = {goal}, [goal]
    while stack:
        for s in free_back.get(stack.pop(), ()):
            if s not in free:
                free.add(s)
                stack.append(s)
    forced = sorted({s[0] for s in seen if not s[1] and not track.is_side(s[0]) and s not in free})
    if forced:
        err("Игрок обязан пойти по особому пути: из этих ячеек до финиша нельзя дойти, не встав точно на начало "
            "особого пути: " + _many(track.cells[i] for i in forced) + ". Основной маршрут не должен зависеть "
            "от особых путей: разведите начала особых путей так, чтобы их можно было перешагнуть.")
        return

    landed = set()
    for s in seen:
        landed |= lands.get(s, set())
    dead = [track.cells[j] for j in range(1, f) if track.effects[j] and j not in landed]
    if dead:
        warn(f"На эти ячейки с эффектами нельзя попасть броском кубика: {_many(dead)}.")

    turns, capped = _simulate(track, runs)
    share = capped / runs
    if share >= SIM_FAIL_SHARE:
        err(f"Карта практически непроходима: {share * 100:.0f}% тестовых партий не дошли до финиша "
            f"за {SIM_MAX_TURNS} ходов.")
        return
    turns.sort()
    avg = sum(turns) / len(turns)
    r.stats.update(avg_turns=avg, median_turns=turns[len(turns) // 2],
                   p95_turns=turns[int(0.95 * (len(turns) - 1))], unfinished_share=share)
    if avg > SIM_LONG_TURNS:
        warn(f"Карта очень длинная: в среднем {avg:.0f} ходов в одиночной партии (порог {SIM_LONG_TURNS}).")
    if avg < SIM_SHORT_TURNS:
        warn(f"Карта очень короткая: в среднем {avg:.0f} ходов (порог {SIM_SHORT_TURNS}).")
    if capped:
        warn(f"{capped} из {runs} тестовых партий не завершились за {SIM_MAX_TURNS} ходов.")