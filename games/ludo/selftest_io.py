"""Проверка отрисовки, хранилища и текстов Ludo. Запуск из корня проекта:
    python -m games.ludo.selftest_io
Примеры картинок складываются в папку ludo_preview/ - откройте и посмотрите."""
import io
import json
import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from core.players import BOT_IDS
from . import render as R
from .logic import Match, Player
from .render import SIZE, make_scene, render_board
from .storage import Storage
from .texts import describe

NAMES = ["Иван", "Пётр", "Анна", "Мария"]

class FakeClock:
    def now(self):
        return datetime.now(timezone.utc)

def fake_avatar(color) -> bytes:
    im = Image.new("RGB", (128, 128), color)
    ImageDraw.Draw(im).ellipse((34, 20, 94, 80), fill=(255, 255, 255))
    ImageDraw.Draw(im).ellipse((14, 78, 114, 160), fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()

def match_of(humans: int, bots: int = 0, seed: int = 1) -> Match:
    players = [Player(i + 1, NAMES[i]) for i in range(humans)]
    players += [Player(BOT_IDS[i], f"Бот {i + 1}", True) for i in range(bots)]
    m = Match(players, random.Random(seed), first=0)
    m.start()
    return m

def avatars_of(m: Match) -> dict:
    colors = [(200, 80, 80), (80, 160, 220), (90, 190, 120), (230, 190, 60)]
    return {p.pid: fake_avatar(colors[i % 4]) for i, p in enumerate(m.players) if not p.is_bot and i != 1}

# ---------- Картинки ----------

def scenes() -> dict:
    out = {}

    m = match_of(1, 1)                                             # старт, угол 0 и 2
    out["1_start_2p"] = m

    m = match_of(3, 1)                                             # середина игры, все четыре угла
    a, b, c, d = m.players
    a.pieces, b.pieces, c.pieces, d.pieces = [0, 14, 52, 57], [3, 30, 55, 0], [20, 45, 0, 0], [10, 51, 56, 57]
    out["2_midgame_4p"] = m

    m = match_of(3, 1)                                             # одна звезда: фигуры всех цветов + два блока
    a, b, c, d = m.players
    a.pieces, b.pieces, c.pieces, d.pieces = [9, 9, 0, 0], [48, 0, 0, 0], [35, 0, 0, 0], [22, 22, 0, 0]
    out["3_crowd_star"] = m

    m = match_of(2, 0)                                             # фаза выбора: золотые кольца
    a, b = m.players
    a.pieces, b.pieces = [10, 20, 0, 0], [30, 0, 0, 0]
    m.phase, m.dice = "pick", 2
    out["4_pick"] = m

    m = match_of(2, 0)                                             # один игрок уже дома
    a, b = m.players
    a.pieces, a.finished, b.pieces = [57] * 4, True, [40, 12, 0, 0]
    out["5_finished"] = m

    m = match_of(2, 1)                                             # партия окончена: метки хода нет
    m.players[0].pieces = [57, 57, 30, 0]
    m.over = True
    out["6_over"] = m
    return out

def test_render():
    out = Path("ludo_preview")
    out.mkdir(exist_ok=True)
    for name, m in scenes().items():
        pieces, seats = make_scene(m, avatars_of(m))
        data = render_board(pieces, seats)
        assert data[:4] == b"\x89PNG"
        assert Image.open(io.BytesIO(data)).size == (SIZE, SIZE)
        (out / f"{name}.png").write_bytes(data)
        print(f"  {name}.png: {len(data) // 1024} КБ")

    m = scenes()["4_pick"]                                         # подсвечены фигуры из options()
    pieces, seats = make_scene(m, {})
    assert sum(p.movable for p in pieces) == len(m.options()) == 2
    plain = [R.PieceToken(p.seat, p.piece, p.progress, False) for p in pieces]
    assert render_board(pieces, seats) != render_board(plain, seats)

    m = scenes()["6_over"]                                         # после конца партии метки хода нет
    _p, seats = make_scene(m, {})
    assert not any(s.current for s in seats)

    R._BASE_CACHE.clear()                                          # кэш основы: ключ - занятые места
    m = match_of(1, 1)
    pieces, seats = make_scene(m, {})
    one, two = render_board(pieces, seats), render_board(pieces, seats)
    assert one == two and len(R._BASE_CACHE) == 1
    render_board(*make_scene(match_of(3, 0), {}))
    assert len(R._BASE_CACHE) == 2
    print("отрисовка: OK")

# ---------- Хранилище ----------

def test_storage():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        st = Storage(FakeClock(), Path(tmp) / "l.db")
        try:
            gid = st.create_game(1, 10, host_id=111)
            st.set_message(gid, 555)
            assert st.add_player(gid, 111, "Host", "https://cdn.discordapp.com/x.png")
            assert not st.add_player(gid, 111, "Host")
            assert st.add_player(gid, 222, "Guest")
            assert st.remove_player(gid, 222) and not st.remove_player(gid, 222)
            st.update_settings(gid, bots=9)
            assert st.get(gid)["bots"] == 3                        # зажато в пределы 0..3
            st.set_host(gid, 111)
            st.set_ping(gid, 777)
            assert st.get(gid)["ping_id"] == 777
            st.update_player(gid, 111, "Host2", "")
            assert st.players(gid)[0]["name"] == "Host2"
            assert [r["game_id"] for r in st.active()] == [gid]

            match = Match([Player(111, "Host"), Player(BOT_IDS[0], "Бот 1", True)])
            match.start()
            started = st.start(gid, [(BOT_IDS[0], "Бот 1")], match.snapshot(), "2030-01-01T00:00:00+00:00")
            row = st.get(gid)
            assert row["status"] == "playing" and row["started_utc"] == started and len(st.players(gid)) == 2
            assert Match.restore(Storage.state_of(row)).snapshot() == match.snapshot()

            match.roll()
            st.save_state(gid, match.snapshot(), None)
            restored = Match.restore(Storage.state_of(st.get(gid)))
            assert restored.snapshot() == match.snapshot()
            json.dumps(restored.snapshot())

            st.finish(gid, match.snapshot())
            assert st.active() == []
            assert st.purge(0) == 1
            assert st.get(gid) is None and st.players(gid) == []
        finally:
            st.con.close()
    print("хранилище: OK")

# ---------- Тексты ----------

def test_texts():
    seen = set()
    for seed in range(60):
        rng = random.Random(seed)
        m = Match([Player(1, "A"), Player(2, "B"), Player(BOT_IDS[0], "Бот", True)], rng)
        m.start()
        for _ in range(3000):
            if m.over:
                break
            p = m.current_player
            if not p.is_bot and rng.random() < 0.04:
                events = m.on_timeout()
            elif not p.is_bot and rng.random() < 0.003:
                events = m.leave(p.pid)
            elif m.phase == "roll":
                events = m.roll()
            elif p.is_bot:
                events = m.bot_pick()
            else:
                events = m.choose(rng.choice(m.options()).piece)
            for e in events:
                text = describe(e, "Игрок", label=lambda pid: f"#{pid}", bold=lambda v: f"**{v}**")
                assert text is None or (isinstance(text, str) and text)
                seen.add(e.kind)
    for kind in ("roll", "exit", "move", "capture", "home_column", "piece_home", "finished", "no_moves"):
        assert kind in seen, kind
    print("тексты событий: OK")

def main():
    test_render()
    test_storage()
    test_texts()
    print("Все проверки пройдены. Картинки: ludo_preview/")

if __name__ == "__main__":
    main()