"""Самопроверка движка симулятора рабочего. Запуск из корня проекта:  python -m games.worker.selftest"""
import math
import json
import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import logic as L
from . import market as M
from . import rules as R
from .storage import StorageRegistry

TZ = ZoneInfo("Asia/Tashkent")
T0 = datetime(2030, 1, 1, 9, 0, tzinfo=TZ).astimezone(timezone.utc)      # 09:00 по Ташкенту
TS0 = int(T0.timestamp())

def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)

def kinds(events) -> list:
    return [e.kind for e in events]

def fake_shift() -> dict:
    """Искусственная смена: 10 часов, тяжёлая; после неё «дорога домой»."""
    return {"kind": "work", "career": True, "wage": 40.0, "quanta": 120, "done_q": 0, "earned": 0.0,
            "rates": {"energy": -9.0, "health": -1.0, "happiness": -0.5}}

def with_shift(p: L.Player) -> L.Player:
    p.act = fake_shift()
    p.queue = [{"kind": "commute", "keep": True, "quanta": 3, "rates": {"energy": -4.0}}]
    return p

def make_vac(prof: str, shift_h: float, commute: int = 20, kind: str = "once") -> M.Vacancy:
    pr = R.PROFESSIONS[prof]
    return M.Vacancy(1, kind, prof, int(shift_h * 60), commute, 0.0, pr["wage"] * R.ONCE_WAGE_MULT,
                     pr["energy"], pr["health"], pr["happy"], pr["fare"], pr["min_rep"], (),
                     TS0 - 100, TS0 + 5 * 3600)

# ---------- Проверки ----------

def test_tax():
    assert L.income_tax(1500) == 150
    assert L.income_tax(3500) == 450
    assert L.income_tax(5000) == 750
    for a, b in ((100, 900), (1400, 300), (3000, 4000), (8000, 5000)):
        assert L.income_tax(a) + (L.income_tax(a + b) - L.income_tax(a)) == L.income_tax(a + b)
    print("налог: OK")

def test_sleep():
    p = L.new_player(1, T0)
    p.energy = 20.0
    ok, _ = L.start_sleep(p)
    assert ok
    ev = L.catch_up(p, at(10), TZ)
    assert p.energy == 100.0 and p.act is None and "finished" in kinds(ev)
    p2 = L.new_player(2, T0)
    assert not L.start_sleep(p2)[0]                      # энергия 100%: спать нельзя
    p3 = L.new_player(3, T0)
    p3.energy = 10.0
    L.start_sleep(p3)
    assert p3.act["forced"] and not L.wake(p3, TZ)[0]    # принудительный сон
    L.catch_up(p3, at(5), TZ)
    assert p3.act is None and p3.energy >= R.ENERGY_FORCED_WAKE
    print("сон: OK")

def test_slow_health():
    p = L.new_player(1, T0)
    p.health, p.energy = 60.0, 20.0
    L.start_sleep(p)
    L.catch_up(p, at(1), TZ)
    assert abs(p.energy - (20 + 12 * 0.5)) < 0.5         # восстановление вдвое медленнее
    print("штраф здоровья: OK")

def test_daily_and_debt():
    start = datetime(2030, 1, 1, 23, 0, tzinfo=TZ).astimezone(timezone.utc)
    p = L.new_player(1, start)
    assert L.catch_up(p, start + timedelta(minutes=50), TZ) == []
    ev = L.catch_up(p, start + timedelta(hours=2), TZ)
    assert p.money == R.START_MONEY - L.daily_cost(p) and kinds(ev).count("daily") == 1

    p = L.new_player(2, start)
    p.money = 0
    cost = L.daily_cost(p)
    L.catch_up(p, start + timedelta(hours=50), TZ)       # три полуночи
    expected = cost                                      # 1-я полночь: долга ещё нет, процентов нет
    for _ in range(2):                                   # 2-я и 3-я: проценты на старый долг, затем платёж
        expected += math.ceil(expected * R.DEBT_RATE) + cost
    assert p.debt == expected and p.debt_pressure == 2, (p.debt, expected, p.debt_pressure)
    p.money = 500
    assert L.pay_debt(p, 100) == 100
    assert p.debt_paid_today and p.debt_pressure == 0
    before = p.debt
    L.catch_up(p, start + timedelta(hours=75), TZ)       # ещё одна полночь: процентов нет
    assert p.debt >= before
    p.money = 10 ** 6
    L.pay_debt(p, 10 ** 6)
    assert p.debt == 0 and p.debt_pressure == 0
    print("сутки и долг: OK")

def test_benefit():
    start = datetime(2030, 1, 1, 23, 0, tzinfo=TZ).astimezone(timezone.utc)
    p = L.new_player(1, start)
    p.money, p.health = 0, 20.0
    ev = L.catch_up(p, start + timedelta(hours=2), TZ)
    assert "benefit" in kinds(ev) and p.money == R.BENEFIT_AMOUNT
    print("пособие: OK")

def test_hospital():
    for money, cost, health, debt in ((900, 800, 100.0, 0), (600, 500, 75.0, 0), (100, 100, 75.0, 400)):
        p = L.new_player(1, T0)
        p.health, p.money = 4.0, money
        ev = L.catch_up(p, at(0.2), TZ)
        assert p.act and p.act["kind"] == "hospital" and "hospital" in kinds(ev)
        assert p.money == money - cost and p.debt == debt
        L.catch_up(p, at(11), TZ)
        # после выписки игрок свободен и немного восстанавливается: здоровье чуть выше выписного
        assert p.act is None and health <= p.health < health + 1 and p.energy == 100.0, p.health
    print("больница: OK")

def test_career_interrupt():
    p = with_shift(L.new_player(1, T0))
    ev = L.catch_up(p, at(14), TZ)
    ks = kinds(ev)
    assert "interrupted" in ks and "paid" in ks and "forced_sleep" in ks
    paid = next(e for e in ev if e.kind == "paid")
    assert paid.data["aborted"] and paid.data["net"] < paid.data["gross"]
    assert p.reputation == R.START_REPUTATION - R.REP_ABORT_FORCED
    assert p.money > R.START_MONEY and p.act is None and p.energy >= R.ENERGY_FORCED_WAKE
    # добровольный уход со смены
    p = with_shift(L.new_player(2, T0))
    L.catch_up(p, at(2), TZ)
    ok, _, evs = L.abort_shift(p, TZ)
    assert ok and "paid" in kinds(evs) and p.act["kind"] == "commute"
    # штатное завершение короткой смены: полная оплата и репутация
    p = L.new_player(3, T0)
    p.act = fake_shift()
    p.act["quanta"], p.act["rates"] = 24, {"energy": -2.0}
    ev = L.catch_up(p, at(3), TZ)
    paid = next(e for e in ev if e.kind == "paid")
    assert not paid.data["aborted"] and p.reputation > R.START_REPUTATION
    print("смена и срыв: OK")

def test_freeze():
    p = L.new_player(1, T0)
    assert L.freeze(p, T0, TZ)[0]
    assert not L.unfreeze(p, at(1), TZ)[0]               # слишком рано
    assert L.catch_up(p, at(240), TZ) == []
    before = (p.money, p.energy, p.health, p.happiness, p.debt)
    assert L.unfreeze(p, at(240), TZ)[0]
    assert (p.money, p.energy, p.health, p.happiness, p.debt) == before   # заморозка ничего не изменила
    ev = L.catch_up(p, at(242), TZ)                      # без полуночей: платежей нет
    assert "daily" not in kinds(ev) and p.money == before[0] and p.debt == before[4]
    assert not L.freeze(p, at(243), TZ)[0]               # откат после разморозки
    q = with_shift(L.new_player(2, T0))
    assert not L.freeze(q, T0, TZ)[0]                    # во время смены нельзя
    print("заморозка: OK")

def build_a() -> L.Player:
    p = L.new_player(1, T0)
    p.money, p.energy = 100, 30.0
    L.start_sleep(p)
    return p

def build_b() -> L.Player:
    return with_shift(L.new_player(2, T0))

def test_equivalence():
    for build, hours in ((build_a, 120), (build_b, 40)):
        whole = build()
        ev_whole = L.catch_up(whole, at(hours), TZ)
        for seed in range(25):
            rng = random.Random(seed)
            p, t, evs = build(), T0, []
            while t < at(hours):
                t = min(at(hours), t + timedelta(minutes=rng.randint(1, 700)))
                evs += L.catch_up(p, t, TZ)
            assert p.snapshot() == whole.snapshot(), (build.__name__, seed)
            assert [(e.kind, e.data) for e in evs] == [(e.kind, e.data) for e in ev_whole]
    print("один большой расчёт равен серии маленьких: OK")

def test_snapshot_and_storage():
    p = with_shift(L.new_player(7, T0))
    p.exp, p.education, p.view_vac = {"loader": 1.5}, ["school"], 3
    L.catch_up(p, at(3), TZ)
    snap = json.loads(json.dumps(p.snapshot()))
    assert L.Player.restore(snap).snapshot() == p.snapshot()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        reg = StorageRegistry(Path(tmp), limit=2)
        try:
            a, b = reg.get(111), reg.get(222)
            assert a is not b and (Path(tmp) / "111.db").exists() and (Path(tmp) / "222.db").exists()
            a.save_player(p)
            assert b.get_player(7) is None                # серверы изолированы
            assert a.get_player(7).snapshot() == p.snapshot()
            reg.get(333)                                  # 111 вытеснено из кэша, файл остался
            assert reg.get(111).get_player(7).snapshot() == p.snapshot()
        finally:
            reg.close_all()
    print("снимок и хранилище: OK")

# ---------- Этап 3 ----------

def test_exp():
    assert L.exp_level(0) == 0 and L.exp_level(3.49) == 0 and L.exp_level(3.5) == 1
    assert L.exp_level(57.5) == 10 and L.exp_level(322.5) == 30 and L.exp_level(10 ** 4) == 30
    pk = L.perks(30)
    assert abs(pk["bonus"] - 0.25) < 1e-9 and abs(pk["res_neg"] - 0.15) < 1e-9
    assert abs(pk["res_energy"] - 0.20) < 1e-9
    assert L.perks(1) == {"bonus": 0.025, "res_neg": 0.0, "res_energy": 0.0}
    assert abs(L.perks(2)["res_neg"] - 0.015) < 1e-9 and abs(L.perks(3)["res_energy"] - 0.02) < 1e-9
    print("опыт: OK")

def test_windows_and_weights():
    now = datetime(2030, 1, 1, 4, 10, tzinfo=TZ).astimezone(timezone.utc)
    ws = M.window_start(now, TZ).astimezone(TZ)
    assert (ws.hour, ws.minute) == (3, 0)
    assert M.next_window(now, TZ).astimezone(TZ).hour == 6
    w = {c: M.prof_weight(c) for c in R.PROFESSIONS}
    assert w["loader"] > w["courier"] > w["clerk"] > w["engineer"] > w["doctor"]
    assert w["accountant"] == w["clerk"] * R.COURSE_WEIGHT_MULT
    rng = random.Random(1)
    for kind in ("once", "perm"):
        for _ in range(400):
            row = M.generate(kind, TS0, TS0, rng)
            pr = R.PROFESSIONS[row["prof"]]
            assert kind in pr["kinds"]
            assert row["shift_min"] % R.SHIFT_STEP_MIN == 0
            assert pr["shift"][0] * 60 <= row["shift_min"] <= min(pr["shift"][1], R.MAX_SHIFT_HOURS) * 60
            assert row["commute_min"] % R.QUANTUM_MINUTES == 0
            assert pr["commute"][0] <= row["commute_min"] <= pr["commute"][1]
            assert -1 <= row["heavy"] <= 1
    print("окна и генератор: OK")

def test_market_and_race():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        reg = StorageRegistry(Path(tmp))
        try:
            st = reg.get(1)
            rng = random.Random(7)
            t, refreshes = T0, 0
            for i in range(300):
                t += timedelta(minutes=rng.randint(30, 400))
                ts = int(t.timestamp())
                fresh = M.ensure_market(st, t, TZ, rng)
                alive = st.list_vacancies(ts)
                assert len(alive) <= R.MARKET_MAX, (i, len(alive))
                if fresh:
                    refreshes += 1
                    assert R.MARKET_MIN <= len(alive), (i, len(alive))
                assert [v.appears_ts for v in alive] == sorted((v.appears_ts for v in alive), reverse=True)
                assert not M.ensure_market(st, t, TZ, rng)       # в том же окне повторно не обновляется
                for v in rng.sample(alive, min(len(alive), rng.randint(0, 5))):
                    st.claim_vacancy(v.id, 5, ts)
                st.con.commit()
            assert refreshes > 50

            # гонка: из двух попыток победит одна
            t += timedelta(hours=4)
            M.ensure_market(st, t, TZ, rng)
            ts = int(t.timestamp())
            v = st.list_vacancies(ts)[0]
            assert st.claim_vacancy(v.id, 1, ts) and not st.claim_vacancy(v.id, 2, ts)
            st.con.commit()
            assert st.get_vacancy(v.id).taken_by == 1
            assert v.id not in [x.id for x in st.list_vacancies(ts)]
            w = st.list_vacancies(ts)[0]
            assert not st.claim_vacancy(w.id, 3, w.expires_ts)   # истёкшую взять нельзя
            st.con.rollback()
        finally:
            reg.close_all()
    print("рынок и гонка за вакансию: OK")

def test_can_take():
    p = L.new_player(1, T0)
    assert L.can_take(p, make_vac("loader", 4), TS0)[0]
    assert not L.can_take(p, make_vac("clerk", 4), TS0)[0]           # нет образования
    p.education, p.reputation = ["school", "college"], 30.0
    assert L.can_take(p, make_vac("clerk", 4), TS0)[0]
    assert not L.can_take(p, make_vac("clerk", 4, kind="perm"), TS0)[0] or R.PERMANENT_ENABLED
    p.energy = 20.0
    assert not L.can_take(p, make_vac("loader", 4), TS0)[0]          # мало сил
    p.energy, p.money = 100.0, 5
    assert not L.can_take(p, make_vac("miner", 6), TS0)[0]           # не хватает на проезд (30 м.)
    p.money = 400
    v = make_vac("loader", 4)
    v.taken_by = 9
    assert not L.can_take(p, v, TS0)[0]
    v = make_vac("loader", 4)
    assert not L.can_take(p, v, v.expires_ts)[0]
    assert not L.can_take(p, None, TS0)[0]
    print("условия взятия вакансии: OK")

def test_job_flow():
    p = L.new_player(1, T0)
    v = make_vac("loader", 4)
    assert L.can_take(p, v, TS0)[0]
    L.start_job(p, v)
    assert p.money == R.START_MONEY - 2 * v.fare and p.act["kind"] == "commute" and len(p.queue) == 2
    ev = L.catch_up(p, at(6), TZ)
    ks = kinds(ev)
    paid = next(e for e in ev if e.kind == "paid")
    assert not paid.data["aborted"] and paid.data["prof"] == "loader"
    assert "level_up" in ks and p.exp["loader"] == 4.0
    assert p.act is None and p.queue == [] and p.money > R.START_MONEY - 2 * v.fare

    # слишком тяжёлая смена: срыв, дорога домой остаётся, опыт вдвое меньше
    p = L.new_player(2, T0)
    L.start_job(p, make_vac("miner", 10, commute=40))
    ev = L.catch_up(p, at(20), TZ)
    ks = kinds(ev)
    assert "interrupted" in ks and "paid" in ks and p.act is None and p.queue == []
    paid = next(e for e in ev if e.kind == "paid")
    assert paid.data["aborted"] and 4.0 < p.exp["miner"] < 5.5, p.exp
    print("разовая смена: OK")

# ---------- Этап 2 ----------

def test_housing():
    p = L.new_player(1, T0)
    p.money = 5000
    assert L.move_housing(p, "loft", T0, TZ)[0]
    assert p.housing == "loft" and p.money == 5000 - 2 * 520
    assert not L.move_housing(p, "flat", at(1), TZ)[0]             # откат переезда
    L.catch_up(p, at(10), TZ)
    assert 62 < p.happiness < 68, p.happiness                      # счастье тянется к планке лофта (68)
    assert L.move_housing(p, "flat", at(25), TZ)[0]                # через сутки можно
    q = L.new_player(2, T0)
    q.money = 10
    assert not L.move_housing(q, "penthouse", T0, TZ)[0]           # не хватает денег
    assert not L.move_housing(q, "room", T0, TZ)[0]                # уже живёт здесь
    print("жильё и планка счастья: OK")

def test_shop():
    p = L.new_player(1, T0)
    p.energy, p.health, p.money = 40.0, 50.0, 1000
    assert L.start_errand(p, "food")[0]
    assert not L.start_errand(p, "pharm")[0]                       # уже в пути
    L.catch_up(p, at(0.2), TZ)
    assert p.venue == "food" and p.act is None
    assert not L.buy(p, "medicine")[0]                             # в «Продуктах» лекарств нет
    e0 = p.energy
    assert L.buy(p, "lunch")[0] and p.money == 960 and p.energy > e0 + 7
    assert L.buy(p, "energy_drink")[0] and L.buy(p, "energy_drink")[0]
    assert not L.buy(p, "energy_drink")[0]                         # лимит 2 в сутки
    assert p.day_buys == {"lunch": 1, "energy_drink": 2}
    assert L.good_price("food", "lunch") == 40 and L.good_price("super", "lunch") == 44
    assert L.good_price("super", "medicine") > L.good_price("pharm", "medicine")
    assert L.leave_venue(p)[0] and p.venue is None and p.act["kind"] == "errand"
    L.catch_up(p, at(0.4), TZ)
    assert p.act is None
    L.catch_up(p, at(16), TZ)                                      # прошла полночь
    assert p.day_buys == {}
    q = L.new_player(2, T0)
    q.venue = "food"
    assert not L.buy(q, "snack")[0]                                # показатели на максимуме
    r = L.new_player(3, T0)
    r.venue, r.health = "pharm", 20.0
    assert L.buy(r, "medicine")[0] and r.health == 30.0
    assert not L.buy(r, "lunch")[0]                                # в аптеке еды нет
    print("рынок и магазины: OK")

def test_buffs():
    p = L.new_player(1, T0)
    p.money = 2000
    assert L.start_course(p, "stamina_2")[0] and p.money == 2000 - 900
    L.catch_up(p, at(3), TZ)
    b = p.buffs["stamina"]
    assert b["power"] == 0.15 and 0 < b["left"] < 288, b
    p.buffs["stamina"]["power"] = 0.25
    assert not L.start_course(p, "stamina_1")[0]                   # слабее действующего нельзя
    p.buffs["stamina"]["power"] = 0.15
    p.money = 5000
    assert L.start_course(p, "stamina_3")[0]
    L.catch_up(p, at(7), TZ)
    assert p.buffs["stamina"]["code"] == "stamina_3" and p.buffs["stamina"]["left"] < 576

    a, b = with_shift(L.new_player(2, T0)), with_shift(L.new_player(3, T0))
    b.buffs = {"stamina": {"code": "stamina_3", "power": 0.25, "left": 10 ** 6}}
    L.catch_up(a, at(3), TZ)
    L.catch_up(b, at(3), TZ)
    assert b.energy > a.energy + 5, (a.energy, b.energy)           # выносливость экономит энергию

    a, b = L.new_player(4, T0), L.new_player(5, T0)
    for x in (a, b):
        x.energy = 20.0
        L.start_sleep(x)
    b.buffs = {"recovery": {"code": "recovery_3", "power": 0.5, "left": 10 ** 6}}
    L.catch_up(a, at(1), TZ)
    L.catch_up(b, at(1), TZ)
    assert b.energy > a.energy + 4, (a.energy, b.energy)           # восстановление ускоряет сон

    c = L.new_player(6, T0)
    c.buffs = {"recovery": {"code": "recovery_1", "power": 0.15, "left": 3}}
    ev = L.catch_up(c, at(1), TZ)
    assert "buff_end" in kinds(ev) and c.buffs == {}               # срок баффа истекает

    d = L.new_player(7, T0)
    d.buffs = {"learning": {"code": "learning_3", "power": 0.35, "left": 10 ** 6}}
    L.start_job(d, make_vac("loader", 4))
    L.catch_up(d, at(6), TZ)
    assert abs(d.exp["loader"] - 5.4) < 1e-6, d.exp               # 4 часа × 1.35
    print("курсы и баффы: OK")

def test_leisure():
    assert L.dim_mult(0, 24) == 1.0
    assert abs(L.dim_mult(24, 12) - 0.5) < 1e-9 and abs(L.dim_mult(36, 12) - 0.2) < 1e-9
    assert abs(L.dim_mult(0, 36) - 30 / 36) < 1e-9
    p = L.new_player(1, T0)
    p.energy, p.money = 80.0, 500
    assert L.start_leisure(p, "cinema", 2)[0] and p.money == 430
    L.catch_up(p, at(3), TZ)
    assert p.act is None and p.happiness > 70, p.happiness
    assert L.start_leisure(p, "cinema", 2)[0]                      # вторая за день: отдача ниже
    assert abs(p.act["rates"]["happiness"] - 7.0 * L.dim_mult(24, 24)) < 1e-2
    L.catch_up(p, at(16), TZ)                                      # полночь сбрасывает счётчик
    assert p.day_ent == {}

    q = L.new_player(2, T0)
    q.energy = 30.0
    q.act = {"kind": "leisure", "guard": True, "quanta": 24, "done_q": 0, "rates": {"energy": -20.0}}
    ks = kinds(L.catch_up(q, at(2), TZ))
    assert "interrupted" in ks and "forced_sleep" in ks            # мягкое прерывание без штрафов
    assert q.reputation == R.START_REPUTATION

    r = L.new_player(3, T0)
    r.energy = 20.0
    assert not L.start_leisure(r, "park", 1)[0]                    # мало сил

    s = L.new_player(4, T0)
    assert L.start_leisure(s, "park", 3)[0]
    L.catch_up(s, at(1), TZ)
    ok, _, _ev = L.stop_leisure(s, TZ)
    assert ok and s.act is None
    print("развлечения: OK")

def test_snapshot_stage2():
    p = L.new_player(9, T0)
    p.venue, p.last_move = "food", T0
    p.buffs = {"stamina": {"code": "stamina_1", "power": 0.08, "left": 30}}
    p.day_buys, p.day_ent = {"lunch": 2}, {"park": 12}
    snap = json.loads(json.dumps(p.snapshot()))
    assert L.Player.restore(snap).snapshot() == p.snapshot()
    print("снимок этапа 2: OK")

# ---------- Этап 4 ----------

def make_perm(off=(5, 6)) -> M.Vacancy:
    v = make_vac("loader", 4, kind="perm")
    v.days_off = tuple(off)
    v.expires_ts = TS0 + 10 ** 7
    return v

def test_permanent():
    # найм и день найма без прогула
    p = L.new_player(1, T0)                                  # T0: вторник 09:00
    assert L.can_take(p, make_perm(), TS0)[0]
    L.sign_contract(p, make_perm(), T0, TZ)
    assert p.job and p.job["days_off"] == [5, 6]
    assert not L.can_take(p, make_perm(), TS0)[0]            # вторая работа сразу нельзя
    assert "absence" not in kinds(L.catch_up(p, at(16), TZ))

    # среда без смены: прогул
    ev = L.catch_up(p, at(40), TZ)
    assert kinds(ev).count("absence") == 1 and p.job["absences"] == 1
    assert p.reputation < R.START_REPUTATION

    # четверг: смена, прогула нет, копится недельная сумма
    assert L.start_shift(p, at(40), TZ)[0]
    assert not L.start_shift(p, at(40), TZ)[0]               # уже занят / уже работал
    ev = L.catch_up(p, at(64), TZ)
    assert "paid" in kinds(ev) and "absence" not in kinds(ev) and p.job["week_gross"] > 0

    # три прогула: увольнение и кулдаун
    q = L.new_player(2, T0)
    L.sign_contract(q, make_perm(), T0, TZ)
    ks = kinds(L.catch_up(q, at(90), TZ))
    assert ks.count("absence") == 3 and "fired" in ks and q.job is None and q.job_cooldown
    assert not L.can_take(q, make_perm(), int(at(90).timestamp()))[0]
    assert L.can_take(q, make_perm(), int(at(103).timestamp()))[0]

    # уважительная причина: больница
    r = L.new_player(3, T0)
    L.sign_contract(r, make_perm(), T0, TZ)
    L.catch_up(r, at(16), TZ)
    r.health = 4.0
    ks = kinds(L.catch_up(r, at(40), TZ))
    assert "hospital" in ks and "absence" not in ks and r.job["absences"] == 0

    # заморозка: полуночи не обрабатываются
    f = L.new_player(4, T0)
    L.sign_contract(f, make_perm(), T0, TZ)
    assert L.freeze(f, T0, TZ)[0]
    assert L.catch_up(f, at(200), TZ) == [] and f.job["absences"] == 0

    # недельная премия (все дни выходные, чтобы не было прогулов)
    w = L.new_player(5, T0)
    L.sign_contract(w, make_perm((0, 1, 2, 3, 4, 5, 6)), T0, TZ)
    w.job["week_gross"] = 1000
    ev = L.catch_up(w, at(136), TZ)                          # воскресенье закончилось в at(135)
    b = next(e for e in ev if e.kind == "week_bonus")
    assert b.data["gross"] == 100 and b.data["tax"] == 10 and w.job["week_gross"] == 0

    # увольнение по желанию
    assert L.quit_job(w, at(140))[0] and w.job is None and w.job_cooldown

    # снимок
    assert L.Player.restore(json.loads(json.dumps(p.snapshot()))).snapshot() == p.snapshot()
    print("постоянная работа: OK")

# ---------- Этап 5 ----------

def test_education():
    # поминутная оплата и прогресс
    p = L.new_player(1, T0)
    p.money = 1000
    assert not L.start_study(p, "college", 2)[0]               # нет школы
    assert L.start_study(p, "school", 2)[0] and p.money == 1000 # заранее ничего не списано
    L.catch_up(p, at(1), TZ)
    assert p.act and p.act["kind"] == "study" and p.edu_progress["school"] == 60
    assert abs((1000 - p.money) - R.EDUCATION["school"]["fee"]) <= 1   # за час - стоимость часа
    L.catch_up(p, at(3), TZ)
    assert p.act is None and p.edu_progress["school"] == 120 and "school" not in p.education

    # выпуск ровно в тот квант, когда набраны часы; занятие не длиннее остатка
    q = L.new_player(2, T0)
    q.money = 5000
    q.edu_progress["school"] = R.EDUCATION["school"]["hours"] * 60 - 30
    assert L.start_study(q, "school", 4)[0] and q.act["quanta"] == 6
    ev = L.catch_up(q, at(2), TZ)
    assert "edu_done" in kinds(ev) and "school" in q.education and q.act is None
    assert not L.start_study(q, "school", 1)[0]                # уже получено
    assert not L.start_study(q, "course_med", 1)[0]            # нужен колледж
    assert L.start_study(q, "course_acc", 1)[0]                # нужна только школа

    # деньги кончились: мягкое прерывание без штрафов, прогресс сохранён
    r = L.new_player(3, T0)
    r.money = 25
    assert L.start_study(r, "school", 4)[0]
    ev = L.catch_up(r, at(4), TZ)
    stop = next(e for e in ev if e.kind == "interrupted")
    assert stop.data["why"] == "money" and stop.data["reason"] == "soft"
    assert r.act is None and 0 < r.edu_progress["school"] < 240
    assert r.reputation == R.START_REPUTATION and r.money <= 1 and r.debt == 0

    # мало сил, заморозка, досрочное окончание
    t = L.new_player(4, T0)
    t.energy = 20.0
    assert not L.start_study(t, "school", 1)[0]
    u = L.new_player(5, T0)
    assert L.start_study(u, "school", 4)[0]
    assert not L.freeze(u, T0, TZ)[0]                          # во время учёбы нельзя
    L.catch_up(u, at(1), TZ)
    ok, _, evs = L.stop_study(u, TZ)
    assert ok and u.act is None and u.edu_progress["school"] == 60 and u.reputation == R.START_REPUTATION

    # бухгалтер бывает и разовым
    assert "once" in R.PROFESSIONS["accountant"]["kinds"] and "perm" in R.PROFESSIONS["accountant"]["kinds"]
    rng = random.Random(3)
    assert "accountant" in {M.generate("once", TS0, TS0, rng)["prof"] for _ in range(3000)}
    a = L.new_player(6, T0)
    a.education, a.reputation = ["school", "college", "course_acc"], 50.0
    v = make_vac("accountant", 4)
    assert L.can_take(a, v, TS0)[0]
    L.start_job(a, v)
    ev = L.catch_up(a, at(6), TZ)
    paid = next(e for e in ev if e.kind == "paid")
    assert paid.data["prof"] == "accountant" and not paid.data["aborted"]

    # снимок
    p.edu_progress = {"school": 120}
    assert L.Player.restore(json.loads(json.dumps(p.snapshot()))).snapshot() == p.snapshot()

    # бафф «Концентрация» ускоряет учёбу, а выпуск обрывает занятие
    b = L.new_player(7, T0)
    b.money = 5000
    b.buffs = {"learning": {"code": "learning_3", "power": 0.35, "left": 10 ** 6}}
    assert L.start_study(b, "school", 2)[0]
    L.catch_up(b, at(1), TZ)
    assert 80 <= b.edu_progress["school"] <= 81, b.edu_progress              # 60 мин × 1.35
    c = L.new_player(8, T0)
    c.money = 5000
    c.buffs = {"learning": {"code": "learning_3", "power": 0.35, "left": 10 ** 6}}
    c.edu_progress["school"] = R.EDUCATION["school"]["hours"] * 60 - 30
    assert L.start_study(c, "school", 4)[0]
    ev = L.catch_up(c, at(3), TZ)
    assert "edu_done" in kinds(ev) and "school" in c.education and c.act is None
    assert c.money > 5000 - 30                                               # лишнего не списано
    print("образование: OK")

def test_kitchen():
    assert L.cook_level(0) == 0 and L.cook_level(11.9) == 0 and L.cook_level(12) == 1
    assert L.cook_level(10 ** 6) == R.COOK_MAX_LEVEL

    # покупка: сумка, слоты, лимиты слота
    p = L.new_player(1, T0)
    p.money, p.venue = 1000, "food"
    assert L.buy(p, "eggs")[0] and p.bag == {"eggs": 6} and p.money == 1000 - 48
    assert L.buy(p, "potato")[0] and p.bag["potato"] == 1000           # слот занят частично
    p.buy_qty = "fill"
    assert L.buy(p, "potato")[0] and p.bag["potato"] == 5000           # дополнили ровно до полного слота
    assert p.money == 1000 - 48 - 18 - 72                              # 18 м. за упаковку + 4 кг по 18 м./кг
    assert not L.buy(p, "potato")[0]                                   # слот полон
    assert L.buy_mode(p, "onion") == "1"                               # слот лука пуст: «дополнить» недоступно
    p.buy_qty = "1"
    for code in ("onion", "carrot", "cabbage", "tomato", "oil", "flour", "milk", "rice"):
        assert L.buy(p, code)[0], code
    assert not L.buy(p, "chicken")[0]                                 # нет свободных слотов
    assert L.buy(p, "eggs")[0] and p.bag["eggs"] == 12                # в существующий слот можно
    assert not L.buy(p, "eggs")[0]
    assert p.fridge == {}                                             # дома ещё нет
    assert L.leave_venue(p)[0]
    ev = L.catch_up(p, at(0.3), TZ)
    assert "bag_home" in kinds(ev) and p.bag == {} and p.fridge["potato"] == 5000 and p.fridge["eggs"] == 12

    # готовка: списание, освобождение слотов, результат
    q = L.new_player(2, T0)
    q.fridge = {"eggs": 2, "tomato": 100, "oil": 10}
    q.energy, q.happiness = 50.0, 40.0
    assert L.start_cook(q, "fried_eggs")[0] and q.fridge == {}        # слоты освободились
    assert not L.start_cook(q, "omelet")[0]                           # занят
    ev = L.catch_up(q, at(0.5), TZ)
    assert "cooked" in kinds(ev) and q.cook_xp == 2.0 and q.day_meals == 1
    assert q.energy > 50 and q.happiness > 40
    assert not L.start_cook(q, "plov")[0]                             # нет продуктов

    # убывающая отдача: 4-е блюдо за сутки
    r = L.new_player(3, T0)
    r.fridge, r.day_meals = {"eggs": 2, "tomato": 100, "oil": 10}, 3
    assert L.start_cook(r, "fried_eggs")[0]
    assert r.act["cook"]["energy"] == 0.8 and r.act["cook"]["xp"] == 0.8

    # больница обрывает готовку: продукты пропали
    s = L.new_player(4, T0)
    s.fridge = {"rice": 300, "chicken": 400, "carrot": 200, "onion": 150, "oil": 60}
    assert L.start_cook(s, "plov")[0]
    s.health = 4.0
    ks = kinds(L.catch_up(s, at(0.2), TZ))
    assert "cook_lost" in ks and "hospital" in ks and s.cook_xp == 0 and s.fridge == {}

    # полночь сбрасывает счётчик блюд; снимок
    q.day_meals = 5
    L.catch_up(q, at(16), TZ)
    assert q.day_meals == 0
    assert L.Player.restore(json.loads(json.dumps(q.snapshot()))).snapshot() == q.snapshot()

    # «Дополнить слот» после готовки: остаток меньше упаковки
    g = L.new_player(5, T0)
    g.money, g.venue, g.buy_qty = 100, "food", "fill"
    g.fridge = {"potato": 4600}                                       # не хватает 400 г, это меньше упаковки
    assert L.ingredient_offer(g, "potato") == (400, 8, 400)           # ceil(18 * 400 / 1000) = 8
    assert L.buy(g, "potato")[0] and g.bag["potato"] == 400 and g.money == 92
    g.buy_qty = "1"
    assert not L.buy(g, "potato")[0]                                  # слот теперь полон
    print("продукты и готовка: OK")

def test_info():
    from .info import PAGE_LIMIT, build_pages
    for key, _title, text in build_pages():
        assert len(text) <= PAGE_LIMIT, (key, len(text))
    print("справка /workinfo: OK")

# ---------- Контент, окно подтверждения, страницы ----------

def maxed_player() -> L.Player:
    """Игрок с максимумом доступного: всё образование, большой холодильник, опыт во всех профессиях."""
    p = L.new_player(1, T0)
    p.money, p.reputation, p.cook_xp, p.housing = 10 ** 6, 100.0, 10 ** 4, "penthouse"
    p.education = list(R.EDUCATION)
    p.exp = {c: 100.0 for c in R.PROFESSIONS}
    p.fridge = {c: R.INGREDIENTS[c]["slot"] for c in list(R.INGREDIENTS)[:L.fridge_slots(p)]}
    return p

def check_specs(specs: list, where: str):
    """Лимиты Discord: пунктов в списке до 25, подписи до 100, в ряду не больше 5 «клеток»."""
    rows = {}
    for s in specs:
        if s.kind == "select":
            opts = s.opts or []
            assert 1 <= len(opts) <= 25, (where, s.action, len(opts))
            assert len(s.label) <= 150, (where, s.label)
            for label, value, desc in opts:
                assert 1 <= len(label) <= 100 and len(value) <= 100, (where, label)
                assert desc is None or len(desc) <= 100, (where, label, len(desc))
        else:
            assert len(s.label) <= 80, (where, s.label)
        rows.setdefault(s.row, []).append(s)
    for r, items in rows.items():
        width = sum(5 if i.kind == "select" else 1 for i in items)
        assert width <= 5, (where, r, [i.action for i in items])

def test_content_limits():
    from . import texts as T
    from .views import build_specs
    fresh = L.new_player(2, T0)
    fresh.money, fresh.education = 5000, ["school", "college"]
    for who, p in (("maxed", maxed_player()), ("fresh", fresh)):
        # постраничные экраны: каждая страница, каждого магазина
        for scr, body in (("kitchen", T.kitchen_text), ("ent", T.ent_text), ("edu", T.edu_text)):
            pages = L.page_slice(L.screen_items(p, scr), scr, 0)[2]
            for pg in range(pages):
                p.page, p.list_screen = pg, scr
                assert len(body(p)) <= 1900, (who, scr, pg, len(body(p)))
                check_specs(build_specs(scr, p), f"{who}:{scr}:{pg}")
        for venue in R.VENUES:
            p.venue = venue
            pages = L.page_slice(L.screen_items(p, "venue"), "venue", 0)[2]
            for pg in range(pages):
                p.page, p.list_screen = pg, "venue"
                assert len(T.venue_text(p)) <= 1900, (who, venue, pg, len(T.venue_text(p)))
                check_specs(build_specs("venue", p), f"{who}:{venue}:{pg}")
            p.buy_item = next(iter(L.screen_items(p, "venue")))[0]
            check_specs(build_specs("buy_confirm", p), f"{who}:{venue}:buy")
            assert len(T.buy_confirm_text(p)) <= 1900
            p.buy_item = None
        p.venue, p.page = None, 0
        # остальные экраны
        for name, text in (("hospital", T.hospital_text(p)), ("housing", T.housing_text(p, T0, TZ)),
                           ("status", T.status_text(p, T0, TZ))):
            assert len(text) <= 1900, (who, name, len(text))
        for scr in ("menu", "career", "leisure", "market", "hospital", "housing", "sleep", "status"):
            check_specs(build_specs(scr, p), f"{who}:{scr}")
        # окно подтверждения: каждый вариант каждого вида
        for kind, table in L.PICK_TABLES.items():
            for code in table:
                assert L.set_pick(p, kind, code)[0]
                assert len(T.pick_text(p, T0, TZ)) <= 1900, (who, kind, code)
                check_specs(build_specs("pick", p), f"{who}:pick:{kind}:{code}")
        L.clear_pick(p)
    print("лимиты списков и текстов: OK")

def test_pick_flow():
    p = L.new_player(1, T0)
    p.money, p.energy = 1000, 80.0
    assert L.set_pick(p, "ent", "cinema")[0] and p.pick_hours == 2          # у кино один вариант
    assert L.set_pick(p, "ent", "park")[0] and p.pick_hours == 1
    L.set_pick_hours(p, 3)
    assert p.pick_hours == 3
    L.set_pick_hours(p, 9)
    assert p.pick_hours == 3                                                # недопустимое игнорируется
    assert not L.set_pick(p, "ent", "нет_такого")[0]
    assert L.pick_problem(p, T0, TZ) == "" and p.act is None and p.money == 1000
    p.money = 0
    L.set_pick(p, "ent", "cinema")
    assert L.pick_problem(p, T0, TZ) != "" and p.money == 0                 # причина найдена, игрок цел
    p.money = 1000
    ok, _ = L.pick_start(p, T0, TZ)
    assert ok and p.act["kind"] == "leisure" and p.money == 1000 - 70
    L.clear_pick(p)
    assert L.Player.restore(json.loads(json.dumps(p.snapshot()))).snapshot() == p.snapshot()

    q = L.new_player(2, T0)                                                 # переезд через окно подтверждения
    q.money = 5000
    L.set_pick(q, "house", "loft")
    assert L.pick_start(q, T0, TZ)[0] and q.housing == "loft"

    r = L.new_player(3, T0)                                                 # учёба: длительность выбирается
    r.money = 1000
    L.set_pick(r, "edu", "school", 4)
    assert r.pick_hours == 4 and L.pick_start(r, T0, TZ)[0] and r.act["kind"] == "study"

    s = L.Player.restore({**L.new_player(4, T0).snapshot(), "pick_kind": "ent", "pick_code": "удалённый"})
    assert s.pick_kind is None                                              # выведенный из игры код не ломает снимок
    print("выбор с подтверждением: OK")

def test_paging_and_levels():
    size = R.PAGE_SIZES["venue"]
    chunk, page, pages = L.page_slice(list(range(20)), "venue", 99)
    assert page == pages - 1 and len(chunk) == 20 - (pages - 1) * size
    assert L.page_slice(list(range(20)), "venue", -5)[1] == 0
    assert L.page_slice([], "venue", 3) == ([], 0, 1)

    # уровень Повара открывает рецепты
    p = L.new_player(1, T0)
    p.fridge = dict(R.RECIPES["borscht"]["items"])
    ok, why = L.can_cook(p, "borscht")
    assert not ok and "уровне Повара" in why
    p.cook_xp = 10 ** 4
    assert L.can_cook(p, "borscht")[0]
    assert [c for c, r in L.recipe_list()][0] in R.RECIPES
    assert all(a[1].get("lvl", 0) <= b[1].get("lvl", 0) for a, b in zip(L.recipe_list(), L.recipe_list()[1:]))

    # холодильник зависит от жилья
    q = L.new_player(2, T0)
    q.money = 5000
    small = R.HOUSING["bunk"]["fridge"]
    mid = R.HOUSING["dorm"]["fridge"]
    assert small < mid <= L.fridge_slots(q)                                 # проверка самих констант
    q.fridge = {c: 1 for c in list(R.INGREDIENTS)[:mid]}
    assert not L.can_move(q, "bunk", T0, TZ)[0]                             # занято больше, чем влезет в bunk
    assert L.can_move(q, "dorm", T0, TZ)[0]                                 # в dorm помещается ровно
    q.housing = "bunk"
    q.fridge = {c: 1 for c in list(R.INGREDIENTS)[:small]}                  # bunk заполнен целиком
    q.venue = "food"
    assert "Холодильник полон" in L.buy_problem(q, "pasta")                 # новый вид не влезает
    print("страницы, уровни рецептов, холодильник: OK")

def main():
    test_tax()
    test_sleep()
    test_slow_health()
    test_daily_and_debt()
    test_benefit()
    test_hospital()
    test_career_interrupt()
    test_freeze()
    test_equivalence()
    test_snapshot_and_storage()
    test_exp()
    test_windows_and_weights()
    test_market_and_race()
    test_can_take()
    test_job_flow()
    test_housing()
    test_shop()
    test_buffs()
    test_leisure()
    test_snapshot_stage2()
    test_permanent()
    test_education()
    test_kitchen()
    test_info()
    test_content_limits()
    test_pick_flow()
    test_paging_and_levels()
    print("Все проверки пройдены.")

if __name__ == "__main__":
    main()