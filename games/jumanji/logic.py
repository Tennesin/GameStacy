"""Чистая логика Джуманджи: маршрут, правила хода, партия. Про Discord и БД ничего не знает.

Как этим пользоваться из Discord (позже):
  match = Match(Track(game_map), players);  events = match.start()
  ход игрока:   events = match.roll()              (по кнопке «🎲»)
  таймер хода:  events = match.roll(timed_out=True)
  мини-игра:    если match.awaiting_minigame — провести её и вызвать match.finish_minigame(winners)
  каждый вызов возвращает список Event; табло и тексты строит слой Discord."""
import random
from dataclasses import asdict, dataclass, field
from typing import Optional

from .models import GameMap, MapFormatError, trace_path
from .rules import (DICE_SIDES, EFFECTS, MAX_EXTRA_ROLLS, MAX_PLAYERS, MIN_PLAYERS, REWARDS,
                    RESET_TIMEOUTS_ON_ROLL, TARGET_EFFECTS, TIMEOUTS_TO_LEAVE)

# ---------- Маршрут ----------

class Track:
    """Карта, превращённая в прямую трассу: ячейка = индекс 0..finish."""

    def __init__(self, m: GameMap):
        path, problem = trace_path(m)
        if problem:
            raise MapFormatError(problem)
        if len(path) < 2:
            raise MapFormatError("Нужно минимум две ячейки: старт и финиш.")
        index = {cid: i for i, cid in enumerate(path)}
        self.name = m.name
        self.cells = path                          # индекс -> id ячейки
        self.finish = len(path) - 1
        self.effects = [None] * len(path)          # индекс -> эффект
        self.targets = [None] * len(path)          # индекс -> индекс цели (jump / back)
        for i in range(1, self.finish):            # у старта и финиша эффектов нет
            c = m.cells[path[i]]
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

def roll_outcome(track: Track, pos: int, d: int) -> Step:
    remaining = track.finish - pos
    if d > remaining:                                        # «точечное» выбивание
        return Step(d, pos, pos, pos, True, None, False)
    landed = pos + d
    if landed == track.finish:
        return Step(d, pos, landed, landed, False, None, True)
    effect = track.effects[landed]
    end = landed
    if effect in ("jump", "back"):
        end = track.targets[landed]
    elif effect == "reset":
        end = 0
    return Step(d, pos, landed, end, False, effect, end == track.finish)

def effective_bots(humans: int, setting: int) -> int:
    """Сколько ботов реально сядет за стол.
    Каждый живой игрок сверх хоста вытесняет одного бота; если человек один — нужен хотя бы один бот."""
    bots = setting - (humans - 1)
    bots = max(bots, 1 if humans < 2 else 0)
    return max(0, min(bots, MAX_PLAYERS - humans))

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

    @property
    def on_map(self) -> bool:
        return not self.finished and not self.left

@dataclass
class Event:
    """kind: turn, roll, blocked, move, jump, back, reset, skip_set, skip_used, charge, charge_denied,
    minigame, reward, reward_none, reward_move, finished, timeout, left, joined, game_over."""
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
        self.rng = rng or random.Random()
        self.current = 0
        self.extra_in_row = 0
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
        m.rng = rng or random.Random()
        m.current = int(data["current"])
        m.extra_in_row = int(data["extra_in_row"])
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
        self.players.append(Player(pid, name))                # встаёт на старт, ходит в конце очереди
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
        step = roll_outcome(self.track, p.pos, d)
        ev = [Event("roll", p.pid, {"d": d})]

        if step.blocked:
            ev.append(Event("blocked", p.pid, {"d": d, "left": self.track.finish - p.pos}))
            return ev + self._pass_turn()

        p.pos = step.landed
        ev.append(Event("move", p.pid, {"from": step.start, "to": step.landed}))
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
        return ev + self._pass_turn()

    def finish_minigame(self, winners: list) -> list:
        """Завершает мини-игру на клетке: случайная награда победителям, затем ход переходит дальше."""
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
        return ev + self._pass_turn()

    # ---------- Внутреннее ----------

    def _apply_reward(self, reward: dict, winners: list) -> list:
        kind, ev, last = reward["kind"], [], self.track.finish - 1
        others = [p for p in self.players if p.on_map and p.pid not in winners]
        if kind == "forward":
            for pid in winners:
                p = self.get(pid)
                new = min(p.pos + reward["n"], max(p.pos, last))   # финиширует только бросок
                if new != p.pos:
                    ev.append(Event("reward_move", pid, {"from": p.pos, "to": new}))
                    p.pos = new
        elif kind == "back_others":
            for p in others:
                new = max(0, p.pos - reward["n"])
                if new != p.pos:
                    ev.append(Event("reward_move", p.pid, {"from": p.pos, "to": new}))
                    p.pos = new
        elif kind == "skip_others":
            for p in others:
                p.skip += 1
                ev.append(Event("skip_set", p.pid))
        else:
            raise ValueError(f"Неизвестный тип награды: {kind}")
        return ev

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
        """Места: дошедшие по порядку, затем оставшиеся по позиции, затем вышедшие (поздний выше)."""
        if self.over:
            return []
        self.over = True
        done = sorted((p for p in self.players if p.finished), key=lambda p: p.place)
        rest = sorted((p for p in self.players if p.on_map), key=lambda p: -p.pos)   # при равенстве — по порядку хода
        gone = sorted((p for p in self.players if p.left), key=lambda p: -p.leave_order)
        self.standings = done + rest + gone
        for i, p in enumerate(self.standings, start=1):
            p.place = i
        return [Event("game_over", 0, {"order": [p.pid for p in self.standings]})]