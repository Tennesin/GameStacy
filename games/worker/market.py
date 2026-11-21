"""Рынок вакансий: чистая логика (без Discord и без SQL). Хранение - storage.py.

Рынок общий для сервера. Раз в окно (3 часа по Ташкенту) он «обновляется»: выбирается случайная цель
от MARKET_MIN до MARKET_MAX, и рынок добивается до неё новыми вакансиями. Живых вакансий после обновления
не меньше MARKET_MIN и не больше MARKET_MAX: если их уже больше цели, новые не добавляются."""
import random
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone, tzinfo
from typing import Optional

from . import rules as R

UTC = timezone.utc

@dataclass
class Vacancy:
    id: int
    kind: str                 # once / perm
    prof: str
    shift_min: int            # длина смены, минуты
    commute_min: int          # дорога в одну сторону, минуты
    heavy: float              # «тяжесть» от -1 до +1
    wage: float               # ставка в час, брутто (уже с учётом тяжести и типа)
    rate_energy: float        # влияние в час (уже с учётом тяжести)
    rate_health: float
    rate_happy: float
    fare: int
    min_rep: int
    days_off: tuple           # выходные (0 - пн ... 6 - вс), только у постоянных
    appears_ts: int
    expires_ts: int
    taken_by: Optional[int] = None

    def alive(self, now_ts: int) -> bool:
        return self.taken_by is None and self.appears_ts <= now_ts < self.expires_ts

def window_start(now: datetime, tz: tzinfo) -> datetime:
    """Начало текущего окна обновления (UTC)."""
    local = now.astimezone(tz)
    midnight = datetime.combine(local.date(), dtime.min, tzinfo=tz)
    step = timedelta(hours=R.MARKET_WINDOW_HOURS)
    idx = (local - midnight) // step
    return (midnight + idx * step).astimezone(UTC)

def next_window(now: datetime, tz: tzinfo) -> datetime:
    return window_start(now, tz) + timedelta(hours=R.MARKET_WINDOW_HOURS)

def prof_weight(code: str) -> float:
    """Чем ниже требования к образованию, тем чаще профессия попадает на рынок."""
    levels = [R.EDUCATION[e]["level"] for e in R.PROFESSIONS[code]["edu"]]
    courses = sum(1 for lv in levels if lv == 0)
    return R.EDU_WEIGHTS[max(levels + [0])] * (R.COURSE_WEIGHT_MULT ** courses)

def pick_profession(kind: str, rng: random.Random) -> str:
    codes = [c for c, p in R.PROFESSIONS.items() if kind in p["kinds"]]
    return rng.choices(codes, weights=[prof_weight(c) for c in codes])[0]

def _scaled(value: float, load: float) -> float:
    """Нагрузка: отрицательное влияние растёт с тяжестью, положительное (польза) уменьшается."""
    return value * load if value < 0 else value / load

def generate(kind: str, window_ts: int, now_ts: int, rng: random.Random) -> dict:
    """Одна новая вакансия (значения колонок таблицы, без id)."""
    code = pick_profession(kind, rng)
    pr = R.PROFESSIONS[code]
    d = round(rng.uniform(-1.0, 1.0), 2)
    pay, load = 1 + R.HEAVY_PAY * d, 1 + R.HEAVY_LOAD * d

    lo, hi = pr["shift"]
    hi = min(hi, R.MAX_SHIFT_HOURS)
    step = R.SHIFT_STEP_MIN
    shift_min = rng.randint(lo * 60 // step, hi * 60 // step) * step
    c_lo, c_hi = pr["commute"]
    q = R.QUANTUM_MINUTES
    commute = rng.randint(c_lo // q, c_hi // q) * q

    wage = pr["wage"] * pay * (R.ONCE_WAGE_MULT if kind == "once" else 1.0)
    life_lo, life_hi = R.ONCE_LIFE_HOURS if kind == "once" else R.PERM_LIFE_HOURS
    life = int(rng.uniform(life_lo, life_hi) * 3600)

    if kind != "perm":
        days_off = ()
    elif pr.get("weekend"):
        days_off = (5, 6)
    else:
        days_off = tuple(sorted(rng.sample(range(7), rng.randint(*pr["off"]))))

    return {
        "kind": kind, "prof": code, "shift_min": shift_min, "commute_min": commute, "heavy": d,
        "wage": round(wage, 1),
        "rate_energy": round(_scaled(pr["energy"], load), 2),
        "rate_health": round(_scaled(pr["health"], load), 2),
        "rate_happy": round(_scaled(pr["happy"], load), 2),
        "fare": pr["fare"], "min_rep": pr["min_rep"],
        "days_off": ",".join(map(str, days_off)),
        "appears_ts": window_ts, "expires_ts": now_ts + life,
    }

def plan_refresh(alive_once: int, alive_perm: int, rng: random.Random) -> tuple:
    """Сколько разовых и постоянных вакансий добавить. Итог: живых не меньше MARKET_MIN и не больше MARKET_MAX."""
    target = rng.randint(R.MARKET_MIN, R.MARKET_MAX)
    add = max(0, target - alive_once - alive_perm)
    if not R.PERMANENT_ENABLED:
        return add, 0
    perm_goal = round(target * R.PERM_SHARE)
    n_perm = min(add, max(0, perm_goal - alive_perm))
    return add - n_perm, n_perm

def ensure_market(store, now: datetime, tz: tzinfo, rng: random.Random) -> bool:
    """Обновляет рынок, если наступило новое окно. True - обновление было.
    Пропущенные окна не досоздаются: создаётся одно обновление."""
    wts = int(window_start(now, tz).timestamp())
    if store.market_window() == wts:
        return False
    nts = int(now.timestamp())
    a_once, a_perm = store.count_alive(nts)
    n_once, n_perm = plan_refresh(a_once, a_perm, rng)
    rows = ([generate("once", wts, nts, rng) for _ in range(n_once)]
            + [generate("perm", wts, nts, rng) for _ in range(n_perm)])
    store.refresh_market(wts, nts, rows)
    return True