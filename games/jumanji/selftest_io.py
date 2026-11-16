"""Проверка хранилища, сохранения партии и отрисовки. Запуск из корня проекта:
    python -m games.jumanji.selftest_io
Примеры картинок складываются в папку jumanji_preview/ — откройте и посмотрите."""
import io
import json
import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageDraw

from .logic import Match, Player, Track, roll_outcome
from .models import Cell, GameMap, cell_id
from .render import Token, make_tokens, render_board
from .rules import BOT_IDS
from .storage import Storage

class FakeClock:
    def now(self):
        return datetime.now(timezone.utc)

def snake(n: int, effects: dict = None, name: str = "Test") -> GameMap:
    """Трасса змейкой по сетке 6 в ширину. effects: индекс -> 'skip' или ('jump', индекс_цели)."""
    effects, w = effects or {}, 6
    ids = []
    for i in range(n):
        y, k = divmod(i, w)
        ids.append(cell_id(k if y % 2 == 0 else w - 1 - k, y))
    m = GameMap(name=name, width=w, height=(n + w - 1) // w, start=ids[0], finish=ids[-1])
    for i, cid in enumerate(ids):
        c = Cell(cid, next=ids[i + 1] if i < n - 1 else None)
        e = effects.get(i)
        if isinstance(e, tuple):
            c.effect, c.target = e[0], ids[e[1]]
        elif e:
            c.effect = e
        m.cells[cid] = c
    return m

GOOD_EFFECTS = {3: ("jump", 9), 5: "skip", 8: "charge", 10: "mini", 13: ("back", 7), 15: "reset"}

def fake_avatar(color) -> bytes:
    im = Image.new("RGB", (128, 128), color)
    ImageDraw.Draw(im).ellipse((34, 20, 94, 80), fill=(255, 255, 255))
    ImageDraw.Draw(im).ellipse((14, 78, 114, 160), fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()

def test_finish_rule():
    track = Track(snake(10))
    assert roll_outcome(track, 7, 3).blocked and roll_outcome(track, 7, 6).blocked   # осталось 2: 3+ не ходит
    assert roll_outcome(track, 7, 2).finished                                         # ровно 2: финиш
    s = roll_outcome(track, 7, 1)
    assert not s.blocked and not s.finished and s.end == 8
    print("правило финиша: OK")

def test_snapshot():
    track = Track(snake(20, GOOD_EFFECTS))
    rng = random.Random(5)
    match = Match(track, [Player(1, "A"), Player(2, "B"), Player(BOT_IDS[0], "Бот", True)], rng)
    match.start()
    for _ in range(25):
        if match.over:
            break
        if match.awaiting_minigame:
            match.finish_minigame([1])
            continue
        match.roll()
    snap = json.loads(json.dumps(match.snapshot()))
    again = Match.restore(track, snap)
    assert again.snapshot() == match.snapshot()
    if not again.over and not again.awaiting_minigame:
        again.roll()                                                                   # восстановленная партия играбельна
    print("snapshot/restore: OK")

def test_storage():
    good = snake(20, GOOD_EFFECTS)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        st = Storage(FakeClock(), Path(tmp) / "j.db")
        try:
            mid = st.export_map("Test", good.to_json())
            assert st.export_map("TEST", good.to_json()) == mid
            assert [r["name"] for r in st.list_maps()] == ["Test"]

            gid = st.create_game(1, 10, host_id=111, home_id=None, solo=False, bots=1, map_id=mid)
            st.set_message(gid, 555)
            assert st.add_player(gid, 111, "Host", "https://cdn.discordapp.com/x.png")
            assert not st.add_player(gid, 111, "Host")
            st.update_settings(gid, bots=9)
            assert st.get(gid)["bots"] == 4                                            # зажато в пределы 0..4
            assert [r["game_id"] for r in st.active()] == [gid]

            track = Track(GameMap.from_json(st.get_map(mid)["json"]))
            match = Match(track, [Player(111, "Host"), Player(BOT_IDS[0], "Бот 1", True)])
            match.start()
            st.start(gid, st.get_map(mid), [(BOT_IDS[0], "Бот 1")], match.snapshot(),
                     "2030-01-01T00:00:00+00:00")
            row = st.get(gid)
            assert row["status"] == "playing" and len(st.players(gid)) == 2
            assert st.map_of(row).to_dict() == good.to_dict()

            match.roll()
            st.save_state(gid, match.snapshot(), None)
            restored = Match.restore(track, st.state_of(st.get(gid)))
            assert restored.snapshot() == match.snapshot()

            st.finish(gid, match.snapshot())
            assert st.active() == []
            assert st.purge(0) == 1
            assert st.get(gid) is None and st.players(gid) == []
        finally:
            st.con.close()
    print("хранилище: OK")

def test_render():
    out = Path("jumanji_preview")
    out.mkdir(exist_ok=True)
    good = snake(24, GOOD_EFFECTS)
    (out / "sample_map.json").write_text(good.to_json(), encoding="utf-8")
    path = [c for c in good.cells]                                                     # порядок для текста ниже
    av = [fake_avatar(c) for c in ((200, 80, 80), (80, 160, 220), (90, 190, 120), (230, 190, 60))]

    def save(name, data):
        assert data[:4] == b"\x89PNG"
        w, h = Image.open(io.BytesIO(data)).size
        (out / name).write_bytes(data)
        print(f"  {name}: {w}x{h}, {len(data) // 1024} КБ")

    save("1_empty_grid.png", render_board(GameMap(name="x", width=10, height=8), show_grid=True))
    save("2_map_only.png", render_board(good))
    save("3_game.png", render_board(good, [
        Token(1, 0, False, 0, av[0], True, "И"),
        Token(2, 0, False, 1, None, False, "П"),
        Token(BOT_IDS[0], 0, True, 0),
        Token(3, 7, False, 2, av[1], False, "А"),
        Token(BOT_IDS[1], 12, True, 1),
    ]))
    save("4_crowd.png", render_board(good, [
        Token(1, 5, False, 0, av[0], False, "А"), Token(2, 5, False, 1, av[1], True, "Б"),
        Token(3, 5, False, 2, av[2], False, "В"), Token(4, 5, False, 3, av[3], False, "Г"),
        Token(BOT_IDS[0], 5, True, 0),
        Token(5, 10, False, 4, av[1], True, "Д"), Token(6, 10, False, 5, av[2], False, "Е"),
        Token(7, 10, False, 0, None, False, "Ж"),
    ]))
    save("5_selected.png", render_board(good, selected=path[4]))

    track = Track(good)
    match = Match(track, [Player(1, "Иван"), Player(2, "Пётр"), Player(BOT_IDS[0], "Бот 1", True)])
    match.start()
    toks = make_tokens(match, {1: av[0]})
    assert len(toks) == 3 and toks[0].current and not toks[1].current and toks[2].is_bot
    print("отрисовка: OK")

def main():
    test_finish_rule()
    test_snapshot()
    test_storage()
    test_render()
    print("Все проверки пройдены. Картинки: jumanji_preview/")

if __name__ == "__main__":
    main()