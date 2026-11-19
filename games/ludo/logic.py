"""Чистая логика Ludo: правила хода, взятие, блоки, партия. Про Discord и БД ничего не знает.

Как этим пользоваться из Discord:
  match = Match(players);  events = match.start()
  бросок:          events = match.roll()            (кнопка «🎲»; у бота - после паузы)
  выбор фигуры:    если match.phase == "pick": match.options() -> кнопки; events = match.choose(piece)
  бот выбирает:    events = match.bot_pick()
  таймер:          events = match.on_timeout()      (и на бросок, и на выбор)
  выход игрока:    events = match.leave(pid)
Каждый вызов возвращает список Event; табло и тексты строит слой Discord.

Прогресс фигуры: 0 база, 1..51 кольцо (1 = стартовая клетка), 52..56 домашняя колонка, 57 центр."""
import random
from dataclasses import asdict, dataclass, field
from typing import Optional

from .board import is_safe, ring_index
from .rules import (
    LUDO_BONUS_ON_CAPTURE, LUDO_BONUS_ON_FINISH, LUDO_DICE_SIDES, LUDO_EXIT_ROLL, LUDO_FINISH,
    LUDO_LAYOUT, LUDO_MAX_PLAYERS, LUDO_MAX_SIXES_IN_ROW, LUDO_MIN_PLAYERS, LUDO_PIECES,
    LUDO_RESET_TIMEOUTS_ON_MOVE, LUDO_RING_LEN, LUDO_RING_STEPS, LUDO_TIMEOUTS_TO_LEAVE,
)

def effective_bots(humans: int, setting: int) -> int:
    """Сколько ботов реально сядет за стол: каждый человек сверх первого вытесняет одного бота;
    если человек один - нужен хотя бы один бот."""
    bots = setting - (humans - 1)
    bots = max(bots, 1 if humans < 2 else 0)
    return max(0, min(bots, LUDO_MAX_PLAYERS - humans))

# ---------- Участники, ходы, события ----------

@dataclass
class Player:
    pid: int                     # ID в Discord; у ботов отрицательный
    name: str
    is_bot: bool = False
    seat: int = -1               # угол доски 0..3 (он же цвет); задаётся при создании партии
    pieces: list = field(default_factory=lambda: [0] * LUDO_PIECES)   # прогресс каждой фигуры
    timeouts: int = 0
    finished: bool = False
    left: bool = False
    leave_order: int = 0
    place: Optional[int] = None

    @property
    def on_map(self) -> bool:
        return not self.finished and not self.left

    @property
    def advance(self) -> int:
        return sum(self.pieces)

@dataclass(frozen=True)
class Move:
    piece: int                   # номер фигуры 0..3
    frm: int                     # прогресс до хода
    to: int                      # прогресс после хода
    d: int                       # выпавшее число
    captures: tuple = ()         # ((место жертвы, номер фигуры жертвы), ...)

    @property
    def kind(self) -> str:
        if self.to == LUDO_FINISH:
            return "finish"
        if self.frm == 0:
            return "exit"
        if self.frm <= LUDO_RING_STEPS < self.to:
            return "home"
        return "ring"

@dataclass
class Event:
    """kind: turn, roll, three_sixes, no_moves, pick (ждём выбора фигуры), auto (ход выполнен системой;
    data.why: single / same / timeout), exit, move, capture, home_column, piece_home, block, finished,
    bonus_roll, timeout, left, game_over (data.reason: end / deadlock)."""
    kind: str
    pid: int = 0
    data: dict = field(default_factory=dict)

# ---------- Партия ----------

class Match:
    def __init__(self, players: list, rng: Optional[random.Random] = None, first: Optional[int] = None):
        n = len(players)
        if not (LUDO_MIN_PLAYERS <= n <= LUDO_MAX_PLAYERS):
            raise ValueError(f"Участников должно быть от {LUDO_MIN_PLAYERS} до {LUDO_MAX_PLAYERS}.")
        if all(p.is_bot for p in players):
            raise ValueError("В партии должен быть хотя бы один живой игрок.")
        if len({p.pid for p in players}) != n:
            raise ValueError("ID участников повторяются.")
        self.rng = rng or random.Random()
        self.players = list(players)
        for p, seat in zip(self.players, LUDO_LAYOUT[n]):
            p.seat = seat
        self.players.sort(key=lambda p: p.seat)                # порядок ходов - по часовой стрелке
        self.current = self.rng.randrange(n) if first is None else first
        self.phase = "roll"                                    # roll - ждём броска; pick - ждём выбора фигуры
        self.dice: Optional[int] = None                        # выпавшее число, пока идёт выбор
        self.sixes = 0                                         # шестёрок подряд в этой цепочке бросков
        self.over = False
        self.standings: list = []
        self._finish_count = 0
        self._leave_count = 0

    # ---------- Сохранение и восстановление ----------

    def snapshot(self) -> dict:
        return {
            "players": [asdict(p) for p in self.players],
            "current": self.current,
            "phase": self.phase,
            "dice": self.dice,
            "sixes": self.sixes,
            "over": self.over,
            "standings": [p.pid for p in self.standings],
            "finish_count": self._finish_count,
            "leave_count": self._leave_count,
        }

    @classmethod
    def restore(cls, data: dict, rng: Optional[random.Random] = None) -> "Match":
        m = cls.__new__(cls)
        m.rng = rng or random.Random()
        m.players = [Player(**d) for d in data["players"]]
        m.current = int(data["current"])
        m.phase = data.get("phase", "roll")
        m.dice = data.get("dice")
        m.sixes = int(data.get("sixes", 0))
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

    def by_seat(self, seat: int) -> Optional[Player]:
        for p in self.players:
            if p.seat == seat:
                return p
        return None

    @property
    def current_player(self) -> Player:
        return self.players[self.current]

    def start(self) -> list:
        return [Event("turn", self.current_player.pid)]

    # ---------- Поле: кто где стоит ----------

    def _ring_map(self) -> dict:
        """клетка кольца -> {место: [номера фигур]} (только фигуры, стоящие на кольце)."""
        occ: dict = {}
        for p in self.players:
            if not p.on_map:
                continue
            for i, prog in enumerate(p.pieces):
                idx = ring_index(p.seat, prog)
                if idx is not None:
                    occ.setdefault(idx, {}).setdefault(p.seat, []).append(i)
        return occ

    @staticmethod
    def _blocked_cells(occ: dict) -> set:
        """Клетки с блоком: две фигуры одного цвета. Ни пройти, ни встать."""
        return {idx for idx, by in occ.items() if any(len(v) >= 2 for v in by.values())}

    @staticmethod
    def _path_blocked(seat: int, frm: int, to: int, blocked: set) -> bool:
        for q in range(frm + 1, min(to, LUDO_RING_STEPS) + 1):     # колонка и центр блоков не знают
            if ring_index(seat, q) in blocked:
                return True
        return False

    @staticmethod
    def _captures(p: Player, to: int, occ: dict) -> tuple:
        """Кого возьмёт фигура, остановившись на прогрессе to: чужие фигуры, для которых клетка небезопасна."""
        idx = ring_index(p.seat, to)
        if idx is None:
            return ()
        victims = []
        for seat, pcs in occ.get(idx, {}).items():
            if seat == p.seat or is_safe(seat, idx):
                continue
            victims += [(seat, i) for i in pcs]
        return tuple(victims)

    def legal_moves(self, p: Player, d: int, occ: Optional[dict] = None) -> list:
        """Все допустимые ходы игрока p на числе d (по возрастанию номера фигуры)."""
        occ = occ if occ is not None else self._ring_map()
        blocked = self._blocked_cells(occ)
        out = []
        for i, prog in enumerate(p.pieces):
            if prog >= LUDO_FINISH:
                continue
            if prog == 0:
                if d != LUDO_EXIT_ROLL:
                    continue
                to = 1
            else:
                to = prog + d
                if to > LUDO_FINISH:                           # центр только точным броском
                    continue
            if self._path_blocked(p.seat, prog, to, blocked):
                continue
            out.append(Move(i, prog, to, d, self._captures(p, to, occ)))
        return out

    def options(self) -> list:
        """Варианты выбора для кнопок: ходы, различающиеся результатом (фигуры-близнецы схлопнуты)."""
        if self.phase != "pick" or self.over:
            return []
        seen, out = set(), []
        for m in self.legal_moves(self.current_player, self.dice):
            key = (m.frm, m.to)
            if key not in seen:
                seen.add(key)
                out.append(m)
        return out

    # ---------- Ход ----------

    def _require(self, phase: str):
        if self.over:
            raise RuntimeError("Партия окончена.")
        if self.phase != phase:
            raise RuntimeError(f"Сейчас не фаза «{phase}».")

    def _reset_timeouts(self, p: Player):
        if LUDO_RESET_TIMEOUTS_ON_MOVE:
            p.timeouts = 0

    def roll(self) -> list:
        self._require("roll")
        p = self.current_player
        d = self.rng.randint(1, LUDO_DICE_SIDES)
        ev = [Event("roll", p.pid, {"d": d})]

        if d == 6:
            self.sixes += 1
            if self.sixes >= LUDO_MAX_SIXES_IN_ROW:            # третья подряд: ход не выполняется
                self._reset_timeouts(p)
                return ev + [Event("three_sixes", p.pid)] + self._pass_turn()
        else:
            self.sixes = 0                                     # цепочка шестёрок прервалась

        moves = self.legal_moves(p, d)
        if not moves:
            self._reset_timeouts(p)
            ev.append(Event("no_moves", p.pid, {"d": d}))
            return ev + self._next(p, d == 6)

        if len({(m.frm, m.to) for m in moves}) == 1:           # выбора нет или он ничего не меняет
            m = moves[0]
            self._reset_timeouts(p)
            ev.append(Event("auto", p.pid, {"piece": m.piece, "why": "single" if len(moves) == 1 else "same"}))
            return ev + self._apply(p, m)

        self.phase, self.dice = "pick", d
        ev.append(Event("pick", p.pid, {"d": d, "pieces": [m.piece for m in moves]}))
        return ev

    def choose(self, piece: int) -> list:
        """Игрок (или бот) выбрал фигуру."""
        self._require("pick")
        p = self.current_player
        m = next((x for x in self.legal_moves(p, self.dice) if x.piece == piece), None)
        if m is None:
            raise ValueError("Этой фигурой сейчас ходить нельзя.")
        self.phase, self.dice = "roll", None
        self._reset_timeouts(p)
        return self._apply(p, m)

    def bot_pick(self) -> list:
        self._require("pick")
        p = self.current_player
        return self.choose(self.bot_choose(p, self.legal_moves(p, self.dice)).piece)

    def on_timeout(self) -> list:
        """Время на бросок или на выбор вышло."""
        if self.over:
            raise RuntimeError("Партия окончена.")
        p = self.current_player
        p.timeouts += 1
        ev = [Event("timeout", p.pid, {"count": p.timeouts})]
        if p.timeouts >= LUDO_TIMEOUTS_TO_LEAVE:
            return ev + self._leave(p, "timeout")
        if self.phase == "roll":
            return ev + self._pass_turn()
        m = self.bot_choose(p, self.legal_moves(p, self.dice))    # бросок уже сделан: ходит система
        self.phase, self.dice = "roll", None
        ev.append(Event("auto", p.pid, {"piece": m.piece, "why": "timeout"}))
        return ev + self._apply(p, m)

    def leave(self, pid: int, reason: str = "left") -> list:
        p = self.get(pid)
        if self.over or p is None or not p.on_map:
            return []
        return self._leave(p, reason)

    # ---------- Внутреннее ----------

    def _apply(self, p: Player, m: Move) -> list:
        p.pieces[m.piece] = m.to
        ev = [Event("exit" if m.frm == 0 else "move", p.pid, {"piece": m.piece, "from": m.frm, "to": m.to})]

        captured = False
        for seat, i in m.captures:
            victim = self.by_seat(seat)
            lost = victim.pieces[i]
            victim.pieces[i] = 0
            captured = True
            ev.append(Event("capture", p.pid, {"victim": victim.pid, "piece": i, "lost": lost,
                                               "by_piece": m.piece}))

        if m.to == LUDO_FINISH:
            ev.append(Event("piece_home", p.pid, {"piece": m.piece}))
        elif m.kind == "home":
            ev.append(Event("home_column", p.pid, {"piece": m.piece}))
        elif m.to <= LUDO_RING_STEPS and sum(1 for x in p.pieces if x == m.to) >= 2:
            ev.append(Event("block", p.pid, {"piece": m.piece, "to": m.to}))

        if all(x == LUDO_FINISH for x in p.pieces):
            return ev + self._finish(p)
        bonus = (m.d == 6 or (captured and LUDO_BONUS_ON_CAPTURE)
                 or (m.to == LUDO_FINISH and LUDO_BONUS_ON_FINISH))
        return ev + self._next(p, bonus)

    def _next(self, p: Player, bonus: bool) -> list:
        """После хода: либо тот же игрок бросает ещё раз, либо ход переходит дальше."""
        if bonus and p.on_map and not self.over:
            if self._stuck():
                return self._finalize("deadlock")
            return [Event("bonus_roll", p.pid)]
        return self._pass_turn()

    def _finish(self, p: Player) -> list:
        p.finished = True
        self._finish_count += 1
        p.place = self._finish_count
        return [Event("finished", p.pid, {"place": p.place})] + self._pass_turn()

    def _leave(self, p: Player, reason: str) -> list:
        was_current = p is self.current_player
        p.left = True
        self._leave_count += 1
        p.leave_order = self._leave_count
        p.pieces = [0] * LUDO_PIECES                           # фигуры снимаются с поля, блоки исчезают
        ev = [Event("left", p.pid, {"reason": reason})]
        if self._check_end():
            return ev + self._finalize("end")
        if was_current:
            return ev + self._pass_turn()
        if self._stuck():
            return ev + self._finalize("deadlock")
        return ev

    def _check_end(self) -> bool:
        """Конец: на поле меньше двух игроков или не осталось ни одного живого."""
        active = [p for p in self.players if p.on_map]
        return len(active) < 2 or all(p.is_bot for p in active)

    def _stuck(self) -> bool:
        """Страховка: ни у кого нет хода ни на каком числе 1..6 - дальше партия не сдвинется никогда."""
        occ = self._ring_map()
        for p in self.players:
            if not p.on_map:
                continue
            for d in range(1, LUDO_DICE_SIDES + 1):
                if self.legal_moves(p, d, occ):
                    return False
        return True

    def _pass_turn(self) -> list:
        self.sixes, self.dice, self.phase = 0, None, "roll"
        if self._check_end():
            return self._finalize("end")
        if self._stuck():
            return self._finalize("deadlock")
        n, i = len(self.players), self.current
        for _ in range(n):
            i = (i + 1) % n
            if self.players[i].on_map:
                self.current = i
                return [Event("turn", self.players[i].pid)]
        return self._finalize("end")

    def _finalize(self, reason: str = "end") -> list:
        """Места: финишировавшие по порядку, затем оставшиеся по сумме продвижения фигур, затем вышедшие
        (кто вышел позже, тот выше)."""
        if self.over:
            return []
        self.over, self.phase, self.dice = True, "roll", None
        done = sorted((p for p in self.players if p.finished), key=lambda p: p.place)
        rest = sorted((p for p in self.players if p.on_map), key=lambda p: -p.advance)
        gone = sorted((p for p in self.players if p.left), key=lambda p: -p.leave_order)
        self.standings = done + rest + gone
        for i, p in enumerate(self.standings, start=1):
            p.place = i
        return [Event("game_over", 0, {"order": [p.pid for p in self.standings], "reason": reason})]

    # ---------- Бот ----------

    def _danger(self, seat: int, idx: Optional[int]) -> int:
        """Сколько чужих фигур могут взять фигуру места seat на клетке кольца idx одним броском."""
        if idx is None or is_safe(seat, idx):
            return 0
        threats = 0
        for q in self.players:
            if not q.on_map or q.seat == seat:
                continue
            for prog in q.pieces:
                e = ring_index(q.seat, prog)
                if e is None:
                    continue
                dist = (idx - e) % LUDO_RING_LEN
                if 1 <= dist <= LUDO_DICE_SIDES and prog + dist <= LUDO_RING_STEPS:
                    threats += 1
        return threats

    def _hunt(self, seat: int, idx: int) -> int:
        """Сколько уязвимых чужих фигур стоит на 1-6 клеток впереди клетки idx."""
        count = 0
        for q in self.players:
            if not q.on_map or q.seat == seat:
                continue
            for prog in q.pieces:
                e = ring_index(q.seat, prog)
                if e is not None and 1 <= (e - idx) % LUDO_RING_LEN <= LUDO_DICE_SIDES \
                        and not is_safe(q.seat, e):
                    count += 1
        return count

    def _score(self, p: Player, m: Move) -> float:
        s = m.to * 0.6 + m.frm * 0.3                           # мелкое предпочтение продвинутым фигурам
        if m.to == LUDO_FINISH:
            s += 1000
        if m.captures:
            s += 600 + 3 * sum(self.by_seat(seat).pieces[i] for seat, i in m.captures)
        if m.frm == 0:
            on_board = any(0 < x < LUDO_FINISH for x in p.pieces)
            s += 250 if on_board else 350
        if m.kind == "home":
            s += 150                                           # в колонке фигуру уже не взять
        before, after = ring_index(p.seat, m.frm), ring_index(p.seat, m.to)
        if after is not None and is_safe(p.seat, after):
            s += 90
        s += self._danger(p.seat, before) * (200 + 6 * m.frm) / 6      # уходим из-под угрозы
        s -= self._danger(p.seat, after) * (200 + 6 * m.to) / 6        # не встаём под удар
        if after is not None:
            s += 15 * self._hunt(p.seat, after)
            if any(i != m.piece and x == m.to for i, x in enumerate(p.pieces)):
                s += 40                                        # строим блок
        return s

    def bot_choose(self, p: Player, moves: list) -> Move:
        best, best_score = None, None
        for m in moves:
            s = self._score(p, m) + self.rng.random()          # случайность разбирает равные варианты
            if best is None or s > best_score:
                best, best_score = m, s
        return best