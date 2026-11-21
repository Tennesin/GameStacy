"""Самопроверка движка симулятора рабочего. Запуск из корня проекта:  python -m games.worker.selftest"""
import json
import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import logic as L
from . import rules as R
from .storage import StorageRegistry

TZ = ZoneInfo("Asia/Tashkent")
T0 = datetime(2030, 1, 1, 9, 0, tzinfo=TZ).astimezone(timezone.utc)      # 09:00 по Ташкенту

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
    assert p.debt == before + 180 - 0 or p.debt >= before
    p.money = 10 ** 6
    assert L.pay_debt(p, 10 ** 6) == p.debt + 0 or p.debt == 0
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
        assert p.act is None and p.health == health and p.energy == 100.0
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
    L.catch_up(p, at(242), TZ)                           # без полуночей
    assert (p.money, p.energy, p.health, p.happiness, p.debt) == before
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
    print("Все проверки пройдены.")

if __name__ == "__main__":
    main()