"""Чистая логика Джуманджи: маршрут, правила хода, партия. Про Discord и БД ничего не знает.

Как этим пользоваться из Discord:
  match = Match(Track(game_map), players);  events = match.start()
  ход игрока:   events = match.roll()              (по кнопке «🎲»)
  таймер хода:  events = match.roll(timed_out=True)
  мини-игра:    если match.awaiting_minigame — провести её и вызвать match.finish_minigame(winners)
  каждый вызов возвращает список Event; табло и тексты строит слой Discord."""
import random
from dataclasses import asdict, dataclass, field
from typing import Optional

from .models import GameMap, MapFormatError, side_chains, trace_path
from .rules import (
    BRANCH, DICE_SIDES, EFFECTS, MAX_EXTRA_ROLLS, MAX_PLAYERS, MIN_PLAYERS,
    REWARDS, RESET_TIMEOUTS_ON_ROLL, TARGET_EFFECTS, TIMEOUTS_TO_LEAVE
)

# ---------- Маршрут ----------

class Track:
    """Карта, превращённая в трассу."""

    def __init__(self, m: GameMap):
        path, problem = trace_path(m)
        if problem:
            raise MapFormatError(problem)
        if len(path) < 2:
            raise MapFormatError("Нужно минимум две ячейки: старт и финиш.")
        index = {cid: i for i, cid in enumerate(path)}
        self.name = m.name
        self.cells = list(path)                    # индекс -> id ячейки
        self.main_len = len(path)
        self.finish = len(path) - 1
        self.effects = [None] * len(path)          # индекс -> эффект
        self.targets = [None] * len(path)          # индекс -> индекс цели (jump / back)
        self.entry: dict = {}                      # индекс начала особого пути -> индекс первой клетки дороги
        self.succ: dict = {}                       # индекс клетки дороги -> индекс следующей клетки
        self.pred: dict = {}                       # индекс клетки дороги -> индекс предыдущей (или начала)
        self.owner: dict = {}                      # индекс клетки дороги -> индекс начала её пути
        self.step_no: dict = {}                    # индекс клетки дороги -> её номер на дороге (с 1)

        for i in range(1, self.finish):            # у старта и финиша эффектов нет
            c = m.cells[path[i]]
            if c.side:
                raise MapFormatError(f"Основной маршрут проходит по клетке особого пути {c.id}.")
            if not c.effect:
                continue
            if c.effect not in EFFECTS:
                raise MapFormatError(f"У ячейки {c.id} неизвестный эффект «{c.effect}».")
            if c.effect in TARGET_EFFECTS:
                t = index.get(c.target)
                if t is None:
                    raise MapFormatError(f"Цель эффекта ячейки {c.id} не лежит на маршруте.")
                self.targets[i] = t
            self.effects[i] = c.effect

        chains, problems = side_chains(m, path)
        if problems:
            raise MapFormatError(problems[0])
        for bid, chain, exit_id in chains:
            b, exit_idx = index[bid], index[exit_id]
            self.entry[b] = len(self.cells)
            for k, cid in enumerate(chain):
                i = len(self.cells)
                self.cells.append(cid)
                self.effects.append(None)
                self.targets.append(None)
                self.pred[i] = b if k == 0 else i - 1
                self.succ[i] = i + 1 if k < len(chain) - 1 else exit_idx
                self.owner[i] = b
                self.step_no[i] = k + 1

    def is_side(self, i: int) -> bool:
        return i >= self.main_len

    def next_cell(self, i: int, committed: bool = False) -> int:
        """Куда ведёт один шаг вперёд. committed — игрок стоит на начале особого пути и идёт по нему."""
        if i in self.succ:
            return self.succ[i]
        if committed and i in self.entry:
            return self.entry[i]
        return i + 1

    def remaining(self, pos: int, committed: bool = False) -> int:
        """Сколько шагов до финиша по маршруту игрока."""
        n = 0
        while pos != self.finish:
            pos, committed = self.next_cell(pos, committed), False
            n += 1
        return n

    def forward(self, pos: int, committed: bool, n: int) -> int:
        """Вперёд на n шагов, но не дальше клетки перед финишем (финиширует только бросок)."""
        for _ in range(n):
            nxt = self.next_cell(pos, committed)
            if nxt == self.finish:
                break
            pos, committed = nxt, False
        return pos

    def back(self, pos: int, n: int) -> int:
        """Назад на n шагов: с дороги — по дороге к её началу, дальше по основному маршруту."""
        for _ in range(n):
            if self.is_side(pos):
                pos = self.pred[pos]
            elif pos > 0:
                pos -= 1
            else:
                break
        return pos

    def label(self, i: int) -> str:
        """Подпись клетки для игроков: «12» на маршруте, «12.3» — третья клетка дороги от клетки 12."""
        if i < self.main_len:
            return str(i)
        return f"{self.owner[i]}.{self.step_no[i]}"

@dataclass(frozen=True)
class Step:
    """Итог одного броска (чистая функция, ей же пользуется проверка карты)."""
    d: int
    start: int
    landed: int                  # куда привёл бросок (до эффекта)
    end: int                     # где игрок оказался после эффекта
    blocked: bool                # бросок больше остатка до финиша: игрок не ходит
    effect: Optional[str]        # сработавший эффект
    finished: bool
    entered: bool = False        # бросок кончился ровно на начале особого пути
    exited: bool = False         # по пути броска игрок сошёл с особого пути на обычный маршрут

def roll_outcome(track: Track, pos: int, d: int, committed: bool = False) -> Step:
    """committed — игрок стоит на начале особого пути и первый шаг сделает по дороге."""
    cur, first, exited = pos, committed, False
    for _ in range(d):
        if cur == track.finish:                              # шаги остались, а финиш уже под ногами
            return Step(d, pos, pos, pos, True, None, False)
        nxt = track.next_cell(cur, first)
        if track.is_side(cur) and not track.is_side(nxt):
            exited = True
        cur, first = nxt, False
    landed = cur
    if landed == track.finish:
        return Step(d, pos, landed, landed, False, None, True, False, exited)
    effect = track.effects[landed]
    end = landed
    if effect in ("jump", "back"):
        end = track.targets[landed]
    elif effect == "reset":
        end = 0
    return Step(d, pos, landed, end, False, effect, end == track.finish, effect == BRANCH, exited)

def effective_bots(humans: int, setting: int) -> int:
    """Сколько ботов реально сядет за стол.
    Каждый живой игрок сверх хоста вытесняет одного бота; если человек один — нужен хотя бы один бот."""
    bots = setting - (humans - 1)
    bots = max(bots, 1 if humans < 2 else 0)
    return max(0, min(bots, MAX_PLAYERS - humans))

class OrderRoll:
    """Определение очереди ходов перед партией."""

    def __init__(self, pids: list, rng: Optional[random.Random] = None):
        if len(set(pids)) != len(pids):
            raise ValueError("ID участников повторяются.")
        self.rng = rng or random.Random()
        self.slots: list = [list(pids)]              # упорядоченные группы; группа из одного = место определено
        self.history: dict = {pid: [] for pid in pids}   # pid -> числа по раундам
        self.rounds = 0                              # сколько раундов уже записано

    @property
    def done(self) -> bool:
        return all(len(g) == 1 for g in self.slots)

    def active(self) -> list:
        return [pid for g in self.slots if len(g) > 1 for pid in g]

    def throw(self) -> int:
        return self.rng.randint(1, DICE_SIDES)

    def last(self, pid: int) -> Optional[int]:
        h = self.history.get(pid)
        return h[-1] if h else None

    def apply(self, rolls: dict):
        self.rounds += 1
        new = []
        for group in self.slots:
            if len(group) == 1:
                new.append(group)
                continue
            by_value: dict = {}
            for pid in group:
                self.history[pid].append(rolls[pid])
                by_value.setdefault(rolls[pid], []).append(pid)
            for value in sorted(by_value, reverse=True):     # большее число - раньше
                new.append(by_value[value])
        self.slots = new

    def force(self):
        """Предохранитель: все оставшиеся ничьи решаются жребием."""
        new = []
        for group in self.slots:
            group = list(group)
            self.rng.shuffle(group)
            new.extend([pid] for pid in group)
        self.slots = new

    def order(self) -> list:
        return [pid for g in self.slots for pid in g]

# ---------- Участники и события ----------

@dataclass
class Player:
    pid: int                     # ID в Discord; у ботов отрицательный
    name: str
    is_bot: bool = False
    pos: int = 0                 # индекс на трассе
    skip: int = 0                # сколько ходов предстоит пропустить
    timeouts: int = 0
    finished: bool = False
    left: bool = False
    leave_order: int = 0
    place: Optional[int] = None
    bonus: Optional[str] = None  # награда мини-игры на следующий ход: "x2" или "double_roll"
    on_branch: bool = False      # стоит на начале особого пути: следующий ход пойдёт по дороге
    color: int = -1

    @property
    def on_map(self) -> bool:
        return not self.finished and not self.left

@dataclass
class Event:
    """kind: turn, roll, blocked, move, jump, back, reset, skip_set, skip_used, charge, charge_denied,
    minigame, reward, reward_none, reward_move, bonus_set, bonus_roll, finished, timeout, left, joined,
    branch (встал на начало особого пути), rejoin (сошёл с особого пути), game_over."""
    kind: str
    pid: int = 0
    data: dict = field(default_factory=dict)

# ---------- Партия ----------

class Match:
    def __init__(self, track: Track, players: list, rng: Optional[random.Random] = None):
        if not (MIN_PLAYERS <= len(players) <= MAX_PLAYERS):
            raise ValueError(f"Участников должно быть от {MIN_PLAYERS} до {MAX_PLAYERS}.")
        if all(p.is_bot for p in players):
            raise ValueError("В партии должен быть хотя бы один живой игрок.")
        if len({p.pid for p in players}) != len(players):
            raise ValueError("ID участников повторяются.")
        self.track = track
        self.players = list(players)
        for p in self.players:
            if not p.is_bot and p.color < 0:
                p.color = self._pick_color()
        self.rng = rng or random.Random()
        self.current = 0
        self.extra_in_row = 0
        self.bonus_rolls = 0                 # сколько бонусных бросков осталось у ходящего («два броска подряд»)
        self.awaiting_minigame = False
        self.over = False
        self.standings: list = []
        self._finish_count = 0
        self._leave_count = 0

    # ---------- Сохранение и восстановление ----------

    def snapshot(self) -> dict:
        """Всё состояние партии простыми значениями (для JSON в БД)."""
        return {
            "players": [asdict(p) for p in self.players],
            "current": self.current,
            "extra_in_row": self.extra_in_row,
            "bonus_rolls": self.bonus_rolls,
            "awaiting_minigame": self.awaiting_minigame,
            "over": self.over,
            "standings": [p.pid for p in self.standings],
            "finish_count": self._finish_count,
            "leave_count": self._leave_count,
        }

    @classmethod
    def restore(cls, track: Track, data: dict, rng: Optional[random.Random] = None) -> "Match":
        """Собирает партию из snapshot(). Проверок состава нет: данные написаны самим движком."""
        m = cls.__new__(cls)
        m.track = track
        m.players = [Player(**d) for d in data["players"]]
        for seat, p in enumerate(m.players):
            if not p.is_bot and p.color < 0:
                p.color = seat % MAX_PLAYERS
        m.rng = rng or random.Random()
        m.current = int(data["current"])
        m.extra_in_row = int(data["extra_in_row"])
        m.bonus_rolls = int(data.get("bonus_rolls", 0))
        m.awaiting_minigame = bool(data["awaiting_minigame"])
        m.over = bool(data["over"])
        m.standings = [m.get(pid) for pid in data["standings"]]
        m._finish_count = int(data["finish_count"])
        m._leave_count = int(data["leave_count"])
        return m

    # ---------- Справка ----------

    def get(self, pid: int) -> Optional[Player]:
        for p in self.players:
            if p.pid == pid:
                return p
        return None

    @property
    def current_player(self) -> Player:
        return self.players[self.current]

    def free_seats(self) -> int:
        return MAX_PLAYERS - sum(1 for p in self.players if p.on_map)

    def _pick_color(self) -> int:
        """Цвет новому человеку."""
        ever = {p.color for p in self.players if not p.is_bot and p.color >= 0}
        now = {p.color for p in self.players if not p.is_bot and p.on_map and p.color >= 0}
        for used in (ever, now):
            for c in range(MAX_PLAYERS):
                if c not in used:
                    return c
        return 0

    def start(self) -> list:
        self.current = 0
        return [Event("turn", self.current_player.pid)]

    # ---------- Вход и выход ----------

    def can_join(self, pid: int) -> tuple:
        if self.over:
            return False, "Партия уже окончена."
        if self.get(pid) is not None:
            return False, "Вы уже участвовали в этой партии."
        if self.free_seats() <= 0:
            return False, "Все места заняты."
        return True, ""

    def join(self, pid: int, name: str) -> list:
        ok, why = self.can_join(pid)
        if not ok:
            raise ValueError(why)
        p = Player(pid, name)
        p.color = self._pick_color()
        self.players.append(p)
        return [Event("joined", pid)]

    def leave(self, pid: int, reason: str = "left") -> list:
        p = self.get(pid)
        if self.over or p is None or not p.on_map:
            return []
        return self._leave(p, reason)

    # ---------- Ход ----------

    def roll(self, timed_out: bool = False) -> list:
        if self.over:
            raise RuntimeError("Партия окончена.")
        if self.awaiting_minigame:
            raise RuntimeError("Сначала нужно завершить мини-игру.")
        p = self.current_player

        if timed_out:
            p.timeouts += 1
            if p.timeouts >= TIMEOUTS_TO_LEAVE:
                return self._leave(p, "timeout")
            return [Event("timeout", p.pid, {"count": p.timeouts})] + self._pass_turn()

        if RESET_TIMEOUTS_ON_ROLL:
            p.timeouts = 0
        d = self.rng.randint(1, DICE_SIDES)
        bonus, p.bonus = p.bonus, None                        # награда мини-игры тратится на первом броске хода
        mult = 2 if bonus == "x2" else 1
        if bonus == "double_roll":
            self.bonus_rolls += 1
        step = roll_outcome(self.track, p.pos, d * mult, p.on_branch)
        ev = [Event("roll", p.pid, {"d": d, "mult": mult, "total": d * mult})]

        if step.blocked:                                      # заход на особый путь при этом сохраняется
            ev.append(Event("blocked", p.pid,
                            {"d": d * mult, "left": self.track.remaining(p.pos, p.on_branch)}))
            return ev + self._end_roll(p)

        p.on_branch = False                                   # любой реальный шаг «тратит» заход
        p.pos = step.landed
        ev.append(Event("move", p.pid, {"from": step.start, "to": step.landed}))
        if step.exited:
            ev.append(Event("rejoin", p.pid, {"to": step.landed}))
        if step.effect in ("jump", "back", "reset"):
            p.pos = step.end                                  # эффект в клетке назначения не срабатывает
            ev.append(Event(step.effect, p.pid, {"from": step.landed, "to": step.end}))
        if step.finished:
            return ev + self._finish(p)

        if step.effect == "skip":
            p.skip += 1
            ev.append(Event("skip_set", p.pid))
        elif step.effect == "charge":
            if self.extra_in_row < MAX_EXTRA_ROLLS:
                self.extra_in_row += 1
                ev.append(Event("charge", p.pid))
                return ev                                     # ход остаётся у игрока
            ev.append(Event("charge_denied", p.pid))
        elif step.effect == "mini":
            self.awaiting_minigame = True
            ev.append(Event("minigame", p.pid,
                            {"players": [q.pid for q in self.players if q.on_map]}))
            return ev
        elif step.effect == BRANCH:                           # ровно на начале: дальше пойдёт по дороге
            p.on_branch = True
            ev.append(Event("branch", p.pid))
        return ev + self._end_roll(p)

    def finish_minigame(self, winners: list) -> list:
        """Завершает мини-игру на клетке: случайная награда победителям, затем ход идёт дальше
        (или ходящий добирает бонусный бросок, если он у него остался)."""
        if not self.awaiting_minigame:
            raise RuntimeError("Мини-игра не запускалась.")
        self.awaiting_minigame = False
        if self.over:
            return []
        win = [pid for pid in winners if self.get(pid) is not None and self.get(pid).on_map]
        ev = []
        if not win or not REWARDS:
            ev.append(Event("reward_none"))
        else:
            reward = self.rng.choice(REWARDS)
            ev.append(Event("reward", 0, {"title": reward["title"], "kind": reward["kind"],
                                           "winners": win}))
            ev += self._apply_reward(reward, win)
        return ev + self._end_roll(self.current_player)

    # ---------- Внутреннее ----------

    def _apply_reward(self, reward: dict, winners: list) -> list:
        kind, ev = reward["kind"], []
        others = [p for p in self.players if p.on_map and p.pid not in winners]
        if kind == "forward":
            for pid in winners:
                p = self.get(pid)
                new = self.track.forward(p.pos, p.on_branch, reward["n"])   # финиширует только бросок
                if new != p.pos:
                    ev.append(Event("reward_move", pid, {"from": p.pos, "to": new}))
                    p.pos, p.on_branch = new, False
        elif kind == "back_others":
            for p in others:
                new = self.track.back(p.pos, reward["n"])
                if new != p.pos:
                    ev.append(Event("reward_move", p.pid, {"from": p.pos, "to": new}))
                    p.pos, p.on_branch = new, False
        elif kind in ("double_roll", "x2"):
            for pid in winners:
                self.get(pid).bonus = kind
                ev.append(Event("bonus_set", pid, {"bonus": kind}))
        else:
            raise ValueError(f"Неизвестный тип награды: {kind}")
        return ev

    def _end_roll(self, p: Player) -> list:
        """Конец броска: либо бонусный бросок тому же игроку, либо ход переходит дальше."""
        if self.bonus_rolls > 0 and p.on_map and not self.over:
            self.bonus_rolls -= 1
            return [Event("bonus_roll", p.pid)]
        return self._pass_turn()

    def _finish(self, p: Player) -> list:
        p.finished = True
        self._finish_count += 1
        p.place = self._finish_count
        return [Event("finished", p.pid, {"place": p.place})] + self._pass_turn()

    def _leave(self, p: Player, reason: str) -> list:
        p.left = True
        self._leave_count += 1
        p.leave_order = self._leave_count
        ev = [Event("left", p.pid, {"reason": reason})]
        if self._check_end():
            return ev + self._finalize()
        if p is self.current_player and not self.awaiting_minigame:
            ev += self._pass_turn()
        return ev

    def _check_end(self) -> bool:
        """Конец: на карте остался один игрок или не осталось ни одного живого."""
        active = [p for p in self.players if p.on_map]
        return len(active) <= 1 or all(p.is_bot for p in active)

    def _pass_turn(self) -> list:
        self.extra_in_row = 0
        self.bonus_rolls = 0
        if self._check_end():
            return self._finalize()
        ev, i, n = [], self.current, len(self.players)
        while True:
            i = (i + 1) % n
            q = self.players[i]
            if not q.on_map:
                continue
            if q.skip > 0:
                q.skip -= 1
                ev.append(Event("skip_used", q.pid))
                continue
            self.current = i
            ev.append(Event("turn", q.pid))
            return ev

    def _finalize(self) -> list:
        """Места: дошедшие по порядку, затем оставшиеся по близости к финишу, затем вышедшие (поздний выше)."""
        if self.over:
            return []
        self.over = True
        done = sorted((p for p in self.players if p.finished), key=lambda p: p.place)
        rest = sorted((p for p in self.players if p.on_map),
                      key=lambda p: self.track.remaining(p.pos, p.on_branch))   # при равенстве — по порядку хода
        gone = sorted((p for p in self.players if p.left), key=lambda p: -p.leave_order)
        self.standings = done + rest + gone
        for i, p in enumerate(self.standings, start=1):
            p.place = i
        return [Event("game_over", 0, {"order": [p.pid for p in self.standings]})]