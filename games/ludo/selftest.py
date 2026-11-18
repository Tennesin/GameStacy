"""Самопроверка движка Ludo. Запуск из корня проекта:  python -m games.ludo.selftest"""
import json
import random

from core.players import BOT_IDS
from .board import HOME_COLUMN, RING, STARS, START, is_safe, ring_index
from .logic import Match, Player, effective_bots

class FixedRng(random.Random):
    """Кубик с заранее заданными значениями (остальное - обычный Random)."""
    def __init__(self, *vals):
        super().__init__()
        self.vals = list(vals)

    def randint(self, a, b):
        return self.vals.pop(0)

def duo(rng=None, first=0):
    a, b = Player(1, "A"), Player(2, "B")
    m = Match([a, b], rng, first=first)
    return m, a, b

def kinds(events):
    return [e.kind for e in events]

# ---------- Точечные проверки ----------

def test_board():
    assert len(RING) == 52 and len(set(RING)) == 52
    rot = lambda p: (14 - p[1], p[0])
    for i in range(52):
        assert rot(RING[i]) == RING[(i + 13) % 52], i
    for k in range(4):
        assert START[k] == 13 * k and STARS[k] == START[k] + 8
        assert rot(HOME_COLUMN[k][0]) == HOME_COLUMN[(k + 1) % 4][0]
    print("геометрия доски: OK")

def test_bots_setting():
    assert effective_bots(1, 1) == 1 and effective_bots(1, 3) == 3
    assert effective_bots(2, 1) == 0 and effective_bots(2, 3) == 2
    assert effective_bots(3, 3) == 1 and effective_bots(4, 3) == 0
    print("число ботов: OK")

def test_basic():
    m, a, b = duo(FixedRng(3, 6)); m.start()
    ev = m.roll()                                              # у A все в базе, выпало 3
    assert kinds(ev) == ["roll", "no_moves", "turn"] and m.current_player is b
    ev = m.roll()                                              # B: шестёрка, 4 одинаковых хода -> авто
    assert kinds(ev) == ["roll", "auto", "exit", "bonus_roll"]
    assert b.pieces == [1, 0, 0, 0] and m.current_player is b and m.phase == "roll"
    print("бросок, автоход, бонус за шестёрку: OK")

def test_three_sixes():
    m, a, b = duo(FixedRng(6, 6, 6)); m.start()
    m.roll()                                                   # выход, авто
    ev = m.roll()                                              # шестёрка: либо 1->7, либо выход второй -> выбор
    assert m.phase == "pick" and len(m.options()) == 2
    m.choose(0)
    assert a.pieces == [7, 0, 0, 0] and m.current_player is a and m.sixes == 2
    ev = m.roll()                                              # третья шестёрка
    assert "three_sixes" in kinds(ev) and a.pieces == [7, 0, 0, 0]
    assert m.current_player is b and m.sixes == 0
    print("три шестёрки подряд: OK")

def test_capture_and_safety():
    # взятие на обычной клетке
    m, a, b = duo(FixedRng(3)); a.pieces = [10, 0, 0, 0]; b.pieces = [39, 0, 0, 0]
    ev = m.roll()
    assert "capture" in kinds(ev) and b.pieces[0] == 0 and a.pieces[0] == 13 and m.current_player is b
    # звезда безопасна
    m, a, b = duo(FixedRng(4)); a.pieces = [5, 0, 0, 0]; b.pieces = [35, 0, 0, 0]
    ev = m.roll()
    assert "capture" not in kinds(ev) and b.pieces[0] == 35 and a.pieces[0] == 9
    # собственная стартовая клетка безопасна для владельца
    m, a, b = duo(FixedRng(3)); a.pieces = [24, 0, 0, 0]; b.pieces = [1, 0, 0, 0]
    ev = m.roll()
    assert "capture" not in kinds(ev) and b.pieces[0] == 1 and a.pieces[0] == 27
    # а на чужой стартовой клетке фигура уязвима: выход A (шестёрка) берёт фигуру B на стартовой клетке A
    m, a, b = duo(FixedRng(6)); b.pieces = [27, 0, 0, 0]
    assert ring_index(2, 27) == 0 and not is_safe(2, 0)
    ev = m.roll()
    assert "capture" in kinds(ev) and b.pieces[0] == 0 and a.pieces[0] == 1
    print("взятие и безопасные клетки: OK")

def test_blocks():
    m, a, b = duo(); a.pieces = [5, 5, 0, 0]; b.pieces = [29, 0, 0, 0]       # блок A на клетке 4, B стоит на клетке 2
    assert [x.piece for x in m.legal_moves(b, 1)] == [0]                      # на клетку 3 можно
    assert m.legal_moves(b, 2) == []                                          # встать на блок нельзя
    assert m.legal_moves(b, 3) == []                                          # пройти через блок нельзя
    m, a, b = duo(); a.pieces = [5, 5, 3, 0]                                  # свой блок мешает и своим
    assert [x.piece for x in m.legal_moves(a, 2)] == [0, 1]
    m, a, b = duo(); a.pieces = [1, 1, 0, 0]                                  # блок на собственной стартовой клетке
    assert [x.piece for x in m.legal_moves(a, 6)] == [0, 1]                   # выйти из базы нельзя
    print("блоки: OK")

def test_exact_finish():
    m, a, b = duo(); a.pieces = [55, 0, 0, 0]
    assert m.legal_moves(a, 3) == []
    assert [x.to for x in m.legal_moves(a, 2)] == [57]
    assert [x.piece for x in m.legal_moves(a, 6)] == [1, 2, 3]
    print("точный бросок в центр: OK")

def test_pick_and_auto():
    m, a, b = duo(FixedRng(2)); a.pieces = [10, 20, 0, 0]
    ev = m.roll()
    assert m.phase == "pick" and kinds(ev)[-1] == "pick" and [o.piece for o in m.options()] == [0, 1]
    try:
        m.choose(2); assert False
    except ValueError:
        pass
    m.choose(1)
    assert a.pieces == [10, 22, 0, 0] and m.current_player is b
    m, a, b = duo(FixedRng(2)); a.pieces = [10, 10, 0, 0]                     # две фигуры в блоке: выбор не нужен
    ev = m.roll()
    assert "auto" in kinds(ev) and a.pieces == [12, 10, 0, 0]
    print("выбор фигуры и автоход: OK")

def test_timeouts():
    m, a, b = duo(FixedRng(2)); a.pieces = [10, 20, 0, 0]
    m.roll()                                                                  # ждём выбора
    ev = m.on_timeout()
    assert kinds(ev)[:2] == ["timeout", "auto"] and a.timeouts == 1 and m.phase == "roll"
    m, a, b = duo()
    for _ in range(3):
        m.current, m.phase = 0, "roll"
        ev = m.on_timeout()
    assert a.left and m.over
    print("таймауты: OK")

def test_finish_game():
    m, a, b = duo(FixedRng(1)); a.pieces = [57, 57, 57, 56]
    ev = m.roll()
    assert m.over and m.standings == [a, b] and a.place == 1 and b.place == 2
    assert ev[-1].kind == "game_over" and ev[-1].data["reason"] == "end"
    print("финиш и места: OK")

def test_leave():
    a, b, c = Player(1, "A"), Player(2, "B"), Player(3, "C")
    m = Match([a, b, c], first=0); b.pieces = [10, 0, 0, 0]
    m.leave(1)
    assert a.left and a.pieces == [0] * 4 and m.current_player is b and not m.over
    m.leave(2)
    assert m.over and m.standings[0] is c
    h, bot = Player(1, "H"), Player(BOT_IDS[0], "Бот", True)
    m = Match([h, bot], first=0); m.leave(1)
    assert m.over
    print("выход игрока: OK")

def test_deadlock_guard():
    m, a, b = duo(); m.start()
    m.legal_moves = lambda p, d, occ=None: []                                 # имитация тупика
    ev = m._pass_turn()
    assert m.over and ev[-1].data["reason"] == "deadlock"
    print("страховка от тупика: OK")

def test_snapshot():
    m, a, b = duo(FixedRng(2)); a.pieces = [10, 20, 0, 0]; m.roll()
    snap = json.loads(json.dumps(m.snapshot()))
    again = Match.restore(snap)
    assert again.snapshot() == m.snapshot() and again.phase == "pick"
    again.choose(0)
    assert again.current_player.pid == 2
    print("snapshot/restore: OK")

# ---------- Случайные партии ----------

def check_invariants(m):
    cells = {}
    for p in m.players:
        assert len(p.pieces) == 4
        for i, prog in enumerate(p.pieces):
            assert 0 <= prog <= 57
            idx = ring_index(p.seat, prog)
            if idx is not None:
                cells.setdefault(idx, {}).setdefault(p.seat, []).append(i)
        if p.left:
            assert p.pieces == [0] * 4
        if p.finished:
            assert all(x == 57 for x in p.pieces)
    for idx, by in cells.items():
        assert all(len(v) <= 2 for v in by.values()), (idx, by)
        assert len([s for s in by if not is_safe(s, idx)]) <= 1, (idx, by)    # две уязвимые стороны на клетке невозможны

def brute_legal(m, p, d):
    """Независимый расчёт допустимых ходов без ring_index."""
    blocks = set()
    for q in m.players:
        cnt = {}
        for prog in q.pieces:
            if 1 <= prog <= 51:
                c = (13 * q.seat + prog - 1) % 52
                cnt[c] = cnt.get(c, 0) + 1
        blocks |= {c for c, k in cnt.items() if k >= 2}
    res = []
    for i, prog in enumerate(p.pieces):
        if prog == 57 or (prog == 0 and d != 6):
            continue
        to = 1 if prog == 0 else prog + d
        if to > 57:
            continue
        if any((13 * p.seat + q - 1) % 52 in blocks for q in range(prog + 1, min(to, 51) + 1)):
            continue
        res.append(i)
    return res

def play_random(seed):
    rng = random.Random(seed)
    n = 2 + seed % 3
    humans = 1 + (seed // 3) % 2
    players = [Player(i + 1, f"H{i}") for i in range(humans)]
    players += [Player(BOT_IDS[i], f"B{i}", True) for i in range(n - humans)]
    m = Match(players, rng)
    m.start()
    for _ in range(40000):
        if m.over:
            break
        check_invariants(m)
        p = m.current_player
        for d in range(1, 7):
            assert [x.piece for x in m.legal_moves(p, d)] == brute_legal(m, p, d)
        lazy = not p.is_bot and rng.random() < 0.03
        if m.phase == "roll":
            m.on_timeout() if lazy else m.roll()
        else:
            opts = m.options()
            assert opts
            if lazy:
                m.on_timeout()
            elif p.is_bot:
                m.bot_pick()
            else:
                m.choose(rng.choice(opts).piece)
    assert m.over, f"партия {seed} не закончилась"
    assert sorted(p.place for p in m.players) == list(range(1, n + 1))
    return m

def test_random_games():
    for seed in range(150):
        play_random(seed)
    print("150 случайных партий: OK")

def main():
    test_board()
    test_bots_setting()
    test_basic()
    test_three_sixes()
    test_capture_and_safety()
    test_blocks()
    test_exact_finish()
    test_pick_and_auto()
    test_timeouts()
    test_finish_game()
    test_leave()
    test_deadlock_guard()
    test_snapshot()
    test_random_games()
    print("Все проверки пройдены.")

if __name__ == "__main__":
    main()