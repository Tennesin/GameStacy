"""Самопроверка движка и валидатора. Запуск из корня проекта:  python -m games.jumanji.selftest"""
import random

from .logic import Match, Player, Track, effective_bots
from .models import Cell, GameMap, cell_id
from .rules import BOT_IDS
from .validator import validate_map

def make_line(n: int, effects: dict = None, name: str = "Тест") -> GameMap:
    """Прямая трасса из n ячеек змейкой по сетке. effects: индекс -> 'skip' или ('jump', индекс_цели)."""
    effects = effects or {}
    w = 10
    ids = [cell_id(i % w, i // w) for i in range(n)]
    m = GameMap(name=name, width=w, height=8, start=ids[0], finish=ids[-1])
    for i, cid in enumerate(ids):
        c = Cell(cid, next=ids[i + 1] if i < n - 1 else None)
        e = effects.get(i)
        if isinstance(e, tuple):
            c.effect, c.target = e[0], ids[e[1]]
        elif e:
            c.effect = e
        m.cells[cid] = c
    return m

def check(title: str, m: GameMap, expect_ok: bool):
    rep = validate_map(m)
    print(f"--- {title}: {'OK' if rep.ok else 'ОШИБКИ'}")
    print(rep.text())
    print()
    assert rep.ok == expect_ok, f"«{title}»: ожидалось ok={expect_ok}"

def play_random(track: Track, seed: int):
    rng = random.Random(seed)
    players = [Player(1, "A"), Player(2, "B"), Player(BOT_IDS[0], "Бот", True)]
    match = Match(track, players, rng)
    match.start()
    for _ in range(5000):
        if match.over:
            break
        if match.awaiting_minigame:
            match.finish_minigame([rng.choice(match.players).pid])
            continue
        p = match.current_player
        match.roll(timed_out=(not p.is_bot and rng.random() < 0.03))
    assert match.over, "партия не закончилась"
    assert sorted(p.place for p in match.players) == list(range(1, len(players) + 1))

class FixedRng(random.Random):
    """Кубик с заранее заданными значениями."""
    def __init__(self, *vals):
        super().__init__()
        self.vals = list(vals)

    def randint(self, a, b):
        return self.vals.pop(0)

def test_bonuses():
    track = Track(make_line(20))

    m = Match(track, [Player(1, "A"), Player(2, "B")], FixedRng(3))
    m.start()
    m.players[0].bonus = "x2"
    m.roll()
    assert m.players[0].pos == 6 and m.players[0].bonus is None and m.current_player.pid == 2

    m = Match(track, [Player(1, "A"), Player(2, "B")], FixedRng(2, 3))
    m.start()
    m.players[0].bonus = "double_roll"
    ev = m.roll()
    assert m.players[0].pos == 2 and m.current_player.pid == 1 and any(e.kind == "bonus_roll" for e in ev)
    m.roll()
    assert m.players[0].pos == 5 and m.current_player.pid == 2

    m = Match(track, [Player(1, "A"), Player(2, "B")])
    m.players[1].pos = 1
    m._apply_reward({"kind": "back_others", "n": 2}, [1])
    assert m.players[1].pos == 0
    m._apply_reward({"kind": "x2"}, [1])
    assert m.players[0].bonus == "x2"

    snap = m.snapshot()
    assert Match.restore(track, snap).snapshot() == snap
    print("бонусы наград: OK")

def test_editor_helpers():
    from .editor import initial_tail, parse_cells, place_cells, remove_cell
    m = GameMap(name="T", width=10, height=8)

    ids, err = parse_cells("a1-c1 C2, а3", m)            # последняя «а» русская
    assert err is None and ids == ["A1", "B1", "C1", "C2", "A3"], ids
    assert parse_cells("K1", m)[1] and parse_cells("A1-B2", m)[1] and parse_cells("zz", m)[1]
    assert parse_cells("C1-A1", m)[0] == ["C1", "B1", "A1"]

    added, skipped, tail = place_cells(m, ["A1", "B1", "C1"], None)
    assert m.start == "A1" and m.cells["A1"].next == "B1" and m.cells["B1"].next == "C1" and tail == "C1"
    added, skipped, tail = place_cells(m, ["C1", "D1"], tail)
    assert added == ["D1"] and skipped == ["C1"] and m.cells["C1"].next == "D1" and tail == "D1"
    assert initial_tail(m) == "D1"

    m.cells["D1"].effect, m.cells["D1"].target = "jump", "B1"
    remove_cell(m, "B1")
    assert m.cells["A1"].next is None and m.cells["D1"].target is None
    remove_cell(m, "A1")
    assert m.start is None
    print("помощники редактора: OK")

def main():
    good = make_line(20, {3: ("jump", 9), 5: "skip", 8: "charge", 10: "mini", 12: ("back", 7), 15: "reset"})
    check("хорошая карта", good, True)

    check("стена из сбросов", make_line(30, {i: "reset" for i in range(5, 11)}), False)

    m = make_line(10)
    m.cells[cell_id(6, 0)].next = cell_id(2, 0)
    check("цикл", m, False)

    m = make_line(10)
    m.cells[cell_id(4, 0)].next = None
    check("обрыв маршрута", m, False)

    check("эффект на старте", make_line(10, {0: "skip"}), False)
    check("прыжок назад", make_line(12, {6: ("jump", 2)}), False)
    check("прыжок на ячейку с эффектом", make_line(15, {3: ("jump", 8), 8: "skip"}), True)

    again = GameMap.from_json(good.to_json())
    assert again.to_dict() == good.to_dict(), "JSON не совпал после круга"

    assert effective_bots(1, 1) == 1 and effective_bots(1, 4) == 4
    assert effective_bots(2, 1) == 0 and effective_bots(3, 2) == 0
    assert effective_bots(2, 3) == 2 and effective_bots(4, 4) == 1 and effective_bots(5, 4) == 0

    track = Track(good)
    for seed in range(300):
        play_random(track, seed)
    test_bonuses()
    test_editor_helpers()
    print("Все проверки пройдены.")

if __name__ == "__main__":
    main()