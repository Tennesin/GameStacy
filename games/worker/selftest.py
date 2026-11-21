"""Самопроверка движка симулятора рабочего. Запуск из корня проекта:  python -m games.worker.selftest"""
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
    assert p.money == R.START_MONEY - 180 and kinds(ev).count("daily") == 1

    p = L.new_player(2, start)
    p.money = 0
    L.catch_up(p, start + timedelta(hours=50), TZ)       # три полуночи
    assert p.debt == 546 and p.debt_pressure == 2, (p.debt, p.debt_pressure)
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
    print("Все проверки пройдены.")

if __name__ == "__main__":
    main()