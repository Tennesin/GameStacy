"""Чистая логика симулятора рабочего: время, показатели, активности, суточные события, налог, заморозка.
Про Discord и БД ничего не знает.

Принцип: «ленивая симуляция». Состояние игрока хранится вместе с моментом, до которого оно рассчитано
(Player.updated, всегда на сетке по 5 минут). Перед любым действием вызывается catch_up(p, now, tz):
она проходит квантами от Player.updated до now и применяет эффекты. Результат не зависит от того,
сколько раз и в какие моменты её вызывали.

Активность (Player.act) - словарь, длительность в КВАНТАХ (поэтому заморозка не требует пересчёта дат):
  kind      "sleep" / "hospital" / любой другой (работа, учёба, дорога...)
  quanta    сколько квантов осталось (None - пока не сработает условие окончания, как у сна)
  rates     {"energy", "health", "happiness"} в час; отрицательные значения смягчаются res_neg / res_energy
  career    True: прерывается при энергии ниже ENERGY_FORCED и здоровье ниже HEALTH_CAREER_BLOCK
  keep      True: при прерывании остаётся в очереди (например, «дорога домой»)
  wage      ставка в час (брутто); earned копится сама, оплата и налог - при завершении
  bonus     добавка к зарплате (опыт), res_neg, res_energy - сопротивления (опыт)
Очередь Player.queue - активности, которые начнутся одна за другой."""
import copy
import math
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone, tzinfo
from typing import Optional

from . import rules as R

UTC = timezone.utc
QUANTUM = timedelta(minutes=R.QUANTUM_MINUTES)
QH = R.QUANTUM_MINUTES / 60.0          # доля часа в одном кванте

# ---------- Мелочи ----------

def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))

def floor_q(dt: datetime) -> datetime:
    """Округление вниз до сетки квантов (общей для всех игроков)."""
    step = R.QUANTUM_MINUTES * 60
    ts = int(dt.timestamp())
    return datetime.fromtimestamp(ts - ts % step, tz=UTC)

def next_midnight(dt: datetime, tz: tzinfo) -> datetime:
    """Ближайшая полночь по местному времени строго после dt (UTC)."""
    local = dt.astimezone(tz)
    nxt = datetime.combine(local.date() + timedelta(days=1), dtime.min, tzinfo=tz)
    return nxt.astimezone(UTC)

def happy_mult(happiness: float) -> float:
    """Множитель продуктивности: 0% -> 0.7, 50% -> 1.0, 100% -> 1.1."""
    if happiness <= 50:
        return R.HAPPY_MULT_MIN + (1.0 - R.HAPPY_MULT_MIN) * happiness / 50
    return 1.0 + (R.HAPPY_MULT_MAX - 1.0) * (happiness - 50) / 50

def income_tax(income: int) -> int:
    """Налог со всего дохода за неделю по ступенчатой шкале (ставка действует только на свою часть)."""
    tax, low = 0.0, 0
    for limit, rate in R.TAX_BRACKETS:
        top = income if limit is None else min(income, limit)
        if top > low:
            tax += (top - low) * rate
        if limit is None or income <= limit:
            break
        low = limit
    return int(round(tax))

# ---------- Данные ----------

@dataclass
class Event:
    kind: str
    at: Optional[datetime] = None
    data: dict = field(default_factory=dict)

@dataclass
class Player:
    uid: int
    updated: datetime                       # состояние рассчитано до этого момента (UTC, сетка квантов)
    energy: float = R.START_ENERGY
    health: float = R.START_HEALTH
    happiness: float = R.START_HAPPINESS
    reputation: float = R.START_REPUTATION
    money: int = R.START_MONEY
    debt: int = 0
    debt_principal: int = 0                 # сколько из долга - «тело» (проценты рассчитываются от лимита по нему)
    debt_pressure: int = 0                  # «звонки», 0..10
    debt_paid_today: bool = False
    housing: str = R.START_HOUSING
    food: str = R.START_FOOD
    act: Optional[dict] = None              # текущая активность; None - свободен
    queue: list = field(default_factory=list)
    frozen_at: Optional[datetime] = None
    last_unfreeze: Optional[datetime] = None
    week_start: Optional[str] = None        # понедельник текущей налоговой недели (ISO)
    week_income: int = 0                    # доход (брутто) за эту неделю
    education: list = field(default_factory=list)
    edu_progress: dict = field(default_factory=dict)   # программа -> минуты учёбы (прогресс сохраняется)
    exp: dict = field(default_factory=dict)         # профессия -> отработанные часы (опыт)
    view_vac: Optional[int] = None          # вакансия, открытая в панели (для кнопки «Взять»)
    venue: Optional[str] = None             # магазин, в котором игрок сейчас находится
    buffs: dict = field(default_factory=dict)       # вид -> {"code", "power", "left" (кванты)}
    last_move: Optional[datetime] = None    # последний переезд
    day_buys: dict = field(default_factory=dict)    # товар -> покупок за игровые сутки
    day_ent: dict = field(default_factory=dict)     # занятие -> квантов за игровые сутки
    job: Optional[dict] = None              # договор постоянной работы (параметры заморожены на момент найма)
    job_cooldown: Optional[datetime] = None # до какого момента нельзя нанять новую постоянную

    def snapshot(self) -> dict:
        iso = lambda v: v.isoformat() if v else None
        return {
            "uid": self.uid, "updated": iso(self.updated),
            "energy": self.energy, "health": self.health, "happiness": self.happiness,
            "reputation": self.reputation, "money": self.money, "debt": self.debt,
            "debt_principal": self.debt_principal, "debt_pressure": self.debt_pressure,
            "debt_paid_today": self.debt_paid_today, "housing": self.housing, "food": self.food,
            "act": copy.deepcopy(self.act), "queue": copy.deepcopy(self.queue),
            "frozen_at": iso(self.frozen_at), "last_unfreeze": iso(self.last_unfreeze),
            "week_start": self.week_start, "week_income": self.week_income,
            "education": list(self.education), "edu_progress": dict(self.edu_progress),
            "exp": dict(self.exp), "view_vac": self.view_vac,
            "venue": self.venue, "buffs": copy.deepcopy(self.buffs), "last_move": iso(self.last_move),
            "day_buys": dict(self.day_buys), "day_ent": dict(self.day_ent),
            "job": copy.deepcopy(self.job), "job_cooldown": iso(self.job_cooldown),
        }

    @classmethod
    def restore(cls, d: dict) -> "Player":
        """Читает и старые снимки: новых полей нет - берутся значения по умолчанию."""
        dt = lambda v: datetime.fromisoformat(v) if v else None
        p = cls(uid=int(d["uid"]), updated=dt(d["updated"]))
        for k in ("energy", "health", "happiness", "reputation"):
            setattr(p, k, float(d.get(k, getattr(p, k))))
        for k in ("money", "debt", "debt_principal", "debt_pressure", "week_income"):
            setattr(p, k, int(d.get(k, getattr(p, k))))
        p.debt_paid_today = bool(d.get("debt_paid_today", False))
        p.housing = d.get("housing", p.housing)
        p.food = d.get("food", p.food)
        p.act = copy.deepcopy(d.get("act"))
        p.queue = copy.deepcopy(d.get("queue") or [])
        p.frozen_at = dt(d.get("frozen_at"))
        p.last_unfreeze = dt(d.get("last_unfreeze"))
        p.week_start = d.get("week_start")
        p.education = list(d.get("education") or [])
        p.edu_progress = {str(k): int(v) for k, v in (d.get("edu_progress") or {}).items()}
        p.exp = {str(k): float(v) for k, v in (d.get("exp") or {}).items()}
        p.view_vac = d.get("view_vac")
        p.venue = d.get("venue")
        p.buffs = {str(k): dict(v) for k, v in (d.get("buffs") or {}).items()}
        p.last_move = dt(d.get("last_move"))
        p.day_buys = {str(k): int(v) for k, v in (d.get("day_buys") or {}).items()}
        p.day_ent = {str(k): int(v) for k, v in (d.get("day_ent") or {}).items()}
        p.job = copy.deepcopy(d.get("job"))
        p.job_cooldown = dt(d.get("job_cooldown"))
        return p

def new_player(uid: int, now: datetime) -> Player:
    return Player(uid=uid, updated=floor_q(now))

def daily_cost(p: Player) -> int:
    return R.HOUSING[p.housing]["rent"] + R.FOOD[p.food]["cost"]

def career_blocked(p: Player) -> bool:
    """Карьера (и казино) недоступны по здоровью."""
    return p.health < R.HEALTH_CAREER_BLOCK

def act_end(p: Player) -> Optional[datetime]:
    """Когда закончится текущая активность (None - свободен или срок заранее неизвестен)."""
    if p.act and p.act.get("quanta") is not None:
        return p.updated + p.act["quanta"] * QUANTUM
    return None

# ---------- Симуляция ----------

class Sim:
    """Один прогон расчёта над игроком; копит события."""

    def __init__(self, p: Player, tz: tzinfo):
        self.p, self.tz, self.events = p, tz, []

    def emit(self, kind: str, **data):
        self.events.append(Event(kind, self.p.updated, data))

    # ----- основной цикл -----

    def run(self, until: datetime):
        p = self.p
        if p.frozen_at is not None:
            return                                         # заморожен: время для него не идёт
        midnight = next_midnight(p.updated, self.tz)
        while p.updated < until:
            p.updated += QUANTUM
            self._step()
            if p.updated >= midnight:                      # квант закончился в полночь
                self._daily()
                midnight = next_midnight(p.updated, self.tz)

    def _step(self):
        p, act = self.p, self.p.act
        kind = act["kind"] if act else "free"
        home = R.HOUSING[p.housing]
        if kind == "sleep":
            rates = {"energy": R.SLEEP_ENERGY_RATE * home["sleep"],
                     "health": R.SLEEP_HEALTH_RATE * home["heal"]}
        elif kind == "hospital":
            rates = {}
        elif act:
            rates = act.get("rates", {})
        else:
            rates = {"energy": R.FREE_ENERGY_RATE, "health": R.FREE_HEALTH_RATE}
        res_neg = act.get("res_neg", 0.0) if act else 0.0
        res_energy = act.get("res_energy", 0.0) if act else 0.0
        res_energy = min(R.RES_ENERGY_CAP, res_energy + buff_power(p, "stamina"))
        rec = 1.0 + buff_power(p, "recovery")
        low = p.health < R.HEALTH_SLOW
        mult = happy_mult(p.happiness)

        e = rates.get("energy", 0.0)
        if e < 0:
            e *= 1 - res_energy
            if low:
                e *= R.LOW_HEALTH_DRAIN_MULT
        elif e > 0:
            if low:
                e *= R.LOW_HEALTH_REGEN_MULT
            e *= rec
        hl = rates.get("health", 0.0)
        if hl < 0:
            hl *= 1 - res_neg
        elif hl > 0:
            hl *= rec
        if p.energy <= 0 and kind != "hospital":
            hl -= R.ZERO_ENERGY_HEALTH_LOSS
        hp = rates.get("happiness", 0.0)
        if hp < 0:
            hp *= 1 - res_neg
        hp += (home["base"] - p.happiness) * R.HAPPY_SPRING - R.DEBT_PRESSURE_HAPPY * p.debt_pressure

        p.energy = clamp(p.energy + e * QH)
        p.health = clamp(p.health + hl * QH)
        p.happiness = clamp(p.happiness + hp * QH)

        if act:
            act["done_q"] = act.get("done_q", 0) + 1
            if act.get("quanta") is not None:
                act["quanta"] -= 1
            if act.get("wage"):
                act["earned"] = act.get("earned", 0.0) + act["wage"] * QH * mult * (1 + act.get("bonus", 0.0))
            if act.get("prof"):
                act["xp"] = act.get("xp", 0.0) + 1.0 + buff_power(p, "learning")
            if act.get("study"):
                self._study_tick(act)

        for bk in list(p.buffs):
            b = p.buffs[bk]
            b["left"] -= 1
            if b["left"] <= 0:
                del p.buffs[bk]
                self.emit("buff_end", buff=bk, code=b["code"])

        if p.job is not None and p.health < R.HEALTH_CAREER_BLOCK:
            self._excuse()
        # Проверка порогов (порядок важен)
        if p.health < R.HEALTH_HOSPITAL and kind != "hospital":
            self._hospital()
            return
        if act is None:
            return
        if self._done(act):
            self._finish()
            return
        if act.get("guard") and p.energy < R.ENERGY_FORCED:    # досуг и курсы: мягкое прерывание без штрафов
            self.emit("interrupted", activity=act["kind"], why="energy", reason="soft")
            p.queue = [a for a in p.queue if a.get("keep")]
            p.act = None
            self._next()
            return
        if act.get("study"):                               # этап 5: учёба прерывается мягко (здоровье, деньги)
            why = ("health" if p.health < R.HEALTH_CAREER_BLOCK
                   else "money" if p.money < math.ceil(act["tuition"] * QH) else None)
            if why:
                self.emit("interrupted", activity="study", why=why, reason="soft")
                p.queue = [a for a in p.queue if a.get("keep")]
                p.act = None
                self._next()
                return
        if act.get("career") and (p.energy < R.ENERGY_FORCED or p.health < R.HEALTH_CAREER_BLOCK):
            self._interrupt("forced", "energy" if p.energy < R.ENERGY_FORCED else "health")

    def _done(self, act: dict) -> bool:
        p = self.p
        if act["kind"] == "sleep":
            if act.get("forced"):
                return p.energy >= R.ENERGY_FORCED_WAKE
            if p.energy >= 100.0:
                return True
        return act.get("quanta") is not None and act["quanta"] <= 0

    def _study_tick(self, act: dict):
        """Один квант учёбы: поминутная оплата и прогресс."""
        p = self.p
        act["fee_f"] = act.get("fee_f", 0.0) + act["tuition"] * QH
        whole = int(act["fee_f"])
        if whole:
            act["fee_f"] -= whole
            p.money -= min(whole, p.money)
        code = act["study"]
        p.edu_progress[code] = p.edu_progress.get(code, 0) + R.QUANTUM_MINUTES
        if p.edu_progress[code] >= R.EDUCATION[code]["hours"] * 60 and code not in p.education:
            p.education.append(code)
            self.emit("edu_done", edu=code)

    # ----- смена активностей -----

    def _next(self):
        """Берёт следующую активность из очереди."""
        p = self.p
        p.act = p.queue.pop(0) if p.queue else None
        if p.act is None and p.energy < R.ENERGY_FORCED:
            p.venue = None
            p.act = {"kind": "sleep", "forced": True, "quanta": None, "done_q": 0}
            self._excuse()                                 # принудительный сон - уважительная причина
            self.emit("forced_sleep")

    def _finish(self):
        p, act = self.p, self.p.act
        self._settle(act, None)
        data = {"activity": act["kind"]}
        if act["kind"] == "hospital":
            p.health = act["discharge_health"]
            p.energy = 100.0
            data["health"] = p.health
        if act.get("arrive"):                              # дошли до магазина
            p.venue = act["arrive"]
            data["venue"] = p.venue
        if act.get("course"):                              # курс закончен: включаем бафф
            apply_buff(p, act["course"])
            data["course"] = act["course"]
        self.emit("finished", **data)
        p.act = None
        self._next()

    def _interrupt(self, reason: str, why: str):
        """Досрочное завершение (срыв). reason: forced / voluntary."""
        p, act = self.p, self.p.act
        self._settle(act, reason)
        self.emit("interrupted", activity=act["kind"], why=why, reason=reason)
        p.queue = [a for a in p.queue if a.get("keep")]
        p.act = None
        self._next()

    # ----- деньги -----

    def _add_debt(self, amount: int):
        self.p.debt += amount
        self.p.debt_principal += amount

    def _tax(self, gross: int) -> int:
        """Налог с этой выплаты: разность налога недельного дохода после и до неё."""
        p = self.p
        local = p.updated.astimezone(self.tz).date()
        monday = (local - timedelta(days=local.weekday())).isoformat()
        if p.week_start != monday:
            p.week_start, p.week_income = monday, 0
        tax = income_tax(p.week_income + gross) - income_tax(p.week_income)
        p.week_income += gross
        return tax

    def _settle(self, act: dict, reason: Optional[str]):
        """Оплата активности с зарплатой. reason: None (штатно), forced, voluntary."""
        if not act.get("wage"):
            return
        p = self.p
        minutes = act.get("done_q", 0) * R.QUANTUM_MINUTES
        if minutes < R.MIN_PAY_MINUTES:
            self.emit("shift_void", activity=act["kind"])
            return
        if reason is None:
            k = 1.0
        else:
            k = R.ABORT_PAY_FORCED if reason == "forced" else R.ABORT_PAY_VOLUNTARY
        gross = int(round(act.get("earned", 0.0) * k))
        tax = self._tax(gross)
        net = gross - tax
        p.money += net
        if reason is None:
            rep = min(R.REP_PER_HOUR * minutes / 60, R.REP_SHIFT_MAX)
        else:
            rep = -(R.REP_ABORT_FORCED if reason == "forced" else R.REP_ABORT_VOLUNTARY)
        p.reputation = clamp(p.reputation + rep)
        self.emit("paid", activity=act["kind"], gross=gross, tax=tax, net=net,
                  reputation=rep, aborted=reason is not None, prof=act.get("prof"))
        if act.get("perm") and p.job is not None:
            p.job["week_gross"] = int(p.job.get("week_gross", 0)) + gross
            if reason == "voluntary":
                p.job["week_ok"] = False
        self._add_exp(act, reason)

    def _add_exp(self, act: dict, reason: Optional[str]):
        """Опыт по профессии: час работы - час опыта."""
        prof = act.get("prof")
        if not prof:
            return
        p = self.p
        hours = act.get("xp", act.get("done_q", 0)) * R.QUANTUM_MINUTES / 60
        if reason is not None:
            hours *= R.EXP_ABORT_MULT
        before = exp_level(p.exp.get(prof, 0.0))
        p.exp[prof] = p.exp.get(prof, 0.0) + hours
        after = exp_level(p.exp[prof])
        if after > before:
            self.emit("level_up", prof=prof, level=after)

    # ----- больница -----

    def _hospital(self):
        p = self.p
        if p.act:
            self._settle(p.act, "forced")
        p.queue = []
        p.venue = None
        debt = 0
        if p.money >= R.HOSPITAL_FULL_COST:
            cost, hours, health = R.HOSPITAL_FULL_COST, R.HOSPITAL_FULL_HOURS, R.HOSPITAL_FULL_HEALTH
        elif p.money >= R.HOSPITAL_LIGHT_COST:
            cost, hours, health = R.HOSPITAL_LIGHT_COST, R.HOSPITAL_LIGHT_HOURS, R.HOSPITAL_LIGHT_HEALTH
        else:                                              # забираются все деньги, остаток - в долг
            cost, hours, health = p.money, R.HOSPITAL_LIGHT_HOURS, R.HOSPITAL_LIGHT_HEALTH
            debt = R.HOSPITAL_LIGHT_COST - p.money
        p.money -= cost
        if debt:
            self._add_debt(debt)
        p.act = {"kind": "hospital", "quanta": hours * 60 // R.QUANTUM_MINUTES, "done_q": 0,
                 "discharge_health": health, "rates": {}}
        self.emit("hospital", cost=cost, debt=debt, health=health, hours=hours)

    # ----- полночь -----

    def _daily(self):
        """Обработка конца игровых суток: долг, платежи, пособие. Вызывается строго по хронологии."""
        p = self.p
        # 1. Долг: проценты и «звонки»
        if p.debt > 0:
            if not p.debt_paid_today:
                room = max(0, int(R.DEBT_CAP_MULT * p.debt_principal) - p.debt)
                p.debt += min(math.ceil(p.debt * R.DEBT_RATE), room)
                p.debt_pressure = min(R.DEBT_PRESSURE_MAX, p.debt_pressure + 1)
            p.reputation -= R.DEBT_PRESSURE_REP * p.debt_pressure
        else:
            p.debt_pressure = 0
        p.debt_paid_today = False
        p.day_buys, p.day_ent = {}, {}
        # 2. Жильё и питание
        cost = daily_cost(p)
        paid = min(p.money, cost)
        short = cost - paid
        p.money -= paid
        if short:
            self._add_debt(short)
            p.happiness = clamp(p.happiness - R.UNPAID_HAPPINESS_LOSS)
            p.health = clamp(p.health - R.UNPAID_HEALTH_LOSS)
        self.emit("daily", cost=cost, paid=paid, short=short)
        # 2б. Постоянная работа: прогулы, увольнение, недельная премия
        self._job_day()
        # 3. Пособие: страховка от «спирали смерти»
        if p.health < R.HEALTH_CAREER_BLOCK and p.money < R.BENEFIT_BELOW_MONEY:
            p.money += R.BENEFIT_AMOUNT
            self.emit("benefit", amount=R.BENEFIT_AMOUNT)
        p.reputation = clamp(p.reputation)

    def _excuse(self):
        """Отмечает уважительную причину для текущих суток (прогула за них не будет)."""
        job = self.p.job
        if job is not None:
            day = (self.p.updated - timedelta(seconds=1)).astimezone(self.tz).date()
            job["excused_day"] = day.isoformat()

    def _job_day(self):
        """Конец суток для постоянной работы. Вызывается из _daily, p.updated - ровно полночь."""
        p = self.p
        job = p.job
        if job is None:
            return
        ended = (p.updated - timedelta(seconds=1)).astimezone(self.tz).date()   # сутки, которые закончились
        ended_s = ended.isoformat()
        today_s = (ended + timedelta(days=1)).isoformat()

        absent = (ended.weekday() not in job["days_off"]
                  and job.get("worked_day") != ended_s
                  and job.get("excused_day") != ended_s
                  and job.get("hired_day") != ended_s)
        if absent:
            job["absences"] = int(job.get("absences", 0)) + 1
            job["week_ok"] = False
            p.reputation -= R.JOB_ABSENCE_REP
            self.emit("absence", absences=job["absences"], limit=R.JOB_FIRE_ABSENCES)
            if job["absences"] >= R.JOB_FIRE_ABSENCES:
                p.job = None
                p.job_cooldown = p.updated + timedelta(hours=R.JOB_HIRE_COOLDOWN_HOURS)
                self.emit("fired", prof=job["prof"])
                return

        # смена, идущая в полночь, засчитывается и новым суткам (дорога домой - нет)
        if p.act and p.act.get("perm") and not p.act.get("keep"):
            job["worked_day"] = today_s

        if ended.weekday() == 6:                           # воскресенье закончилось: итог недели
            gross = int(round(int(job.get("week_gross", 0)) * R.JOB_WEEK_BONUS))
            if job.get("week_ok", True) and gross > 0:
                tax = self._tax(gross)
                p.money += gross - tax
                self.emit("week_bonus", gross=gross, tax=tax, net=gross - tax)
            if job.get("week_ok", True) and job.get("absences", 0) > 0:
                job["absences"] -= 1                       # неделя без прогулов «прощает» один прогул
            job["week_gross"], job["week_ok"] = 0, True

# ---------- Публичные функции ----------

def catch_up(p: Player, now: datetime, tz: tzinfo) -> list:
    """Догоняет состояние до now. Возвращает список Event."""
    sim = Sim(p, tz)
    sim.run(floor_q(now))
    return sim.events

def can_sleep(p: Player) -> tuple:
    if p.frozen_at is not None:
        return False, "Игра заморожена."
    if p.act is not None:
        return False, "Сейчас вы заняты."
    if p.energy >= R.SLEEP_AVAILABLE_BELOW:
        return False, f"Спать можно, когда энергия ниже {R.SLEEP_AVAILABLE_BELOW:.0f}%."
    return True, ""

def start_sleep(p: Player, quanta: Optional[int] = None) -> tuple:
    """quanta=None - до полного восстановления. При энергии ниже порога сон принудительный."""
    ok, why = can_sleep(p)
    if not ok:
        return False, why
    forced = p.energy < R.ENERGY_FORCED
    p.act = {"kind": "sleep", "forced": forced, "quanta": None if forced else quanta, "done_q": 0}
    return True, "Вы легли спать." if not forced else "Вы слишком устали: сон принудительный."

def wake(p: Player, tz: tzinfo) -> tuple:
    """Проснуться раньше срока (возвращает ok, текст, события)."""
    act = p.act
    if not act or act["kind"] != "sleep":
        return False, "Вы не спите.", []
    if act.get("forced"):
        return False, "Вы слишком устали: проснуться получится, когда энергия восстановится.", []
    sim = Sim(p, tz)
    sim._finish()
    return True, "Вы проснулись.", sim.events

def abort_shift(p: Player, tz: tzinfo) -> tuple:
    """Добровольный уход со смены или учёбы (срыв). Возвращает ok, текст, события."""
    if not p.act or not p.act.get("career"):
        return False, "Сейчас нет смены.", []
    sim = Sim(p, tz)
    sim._interrupt("voluntary", "voluntary")
    return True, "Вы ушли со смены раньше срока.", sim.events

def pay_debt(p: Player, amount: int) -> int:
    """Погашает долг на amount (не больше денег и долга). Возвращает фактически выплаченное."""
    amount = min(int(amount), p.money, p.debt)
    if amount <= 0:
        return 0
    before = p.debt
    p.money -= amount
    p.debt -= amount
    p.debt_principal = max(0, p.debt_principal - amount)
    if p.debt == 0:
        p.debt_pressure = 0
        p.debt_principal = 0
    elif amount >= before * R.DEBT_PAY_MIN_SHARE:
        p.debt_paid_today = True
        p.debt_pressure = max(0, p.debt_pressure - R.DEBT_PAY_PRESSURE_DROP)
    return amount

# ---------- Опыт ----------

def exp_level(hours: float) -> int:
    """Уровень по накопленным часам: уровень N требует BASE + STEP * N часов."""
    total, level = 0.0, 0
    while level < R.EXP_MAX_LEVEL:
        need = R.EXP_BASE_HOURS + R.EXP_STEP_HOURS * (level + 1)
        if hours + 1e-9 < total + need:
            break
        total += need
        level += 1
    return level

def perks(level: int) -> dict:
    """Бонусы уровня: улучшения идут по кругу (зарплата, негатив, энергия)."""
    return {"bonus": ((level + 2) // 3) * R.EXP_WAGE_STEP,
            "res_neg": ((level + 1) // 3) * R.EXP_NEG_STEP,
            "res_energy": (level // 3) * R.EXP_ENERGY_STEP}

# ---------- Работа ----------

def lock_reason(p: Player, vac) -> str:
    """Постоянная причина недоступности вакансии (образование, репутация, формат). Пусто - доступна."""
    pr = R.PROFESSIONS[vac.prof]
    missing = [R.EDUCATION[e]["title"] for e in pr["edu"] if e not in p.education]
    if missing:
        return ("нужно образование: " + ", ".join(missing)
                + " (получить можно в «Карьера» → «Образование»)")
    if p.reputation < vac.min_rep:
        return f"нужна репутация {vac.min_rep} (у вас {p.reputation:.0f})"
    if vac.kind == "perm" and not R.PERMANENT_ENABLED:
        return "постоянная работа появится в одном из следующих обновлений игры"
    return ""

def can_take(p: Player, vac, now_ts: int) -> tuple:
    """Можно ли взять вакансию прямо сейчас."""
    if vac is None or not vac.alive(now_ts):
        return False, "Эту вакансию уже забрали или она исчезла."
    if p.frozen_at is not None:
        return False, "Игра заморожена."
    why = lock_reason(p, vac)
    if why:
        return False, "Вакансия недоступна: " + why + "."
    if vac.kind == "perm":                             # договор подписывается без смены и дороги
        if p.job is not None:
            return False, "У вас уже есть постоянная работа: сначала увольтесь."
        if p.job_cooldown and p.job_cooldown.timestamp() > now_ts:
            return False, f"После ухода с работы нужно подождать: новую можно взять <t:{int(p.job_cooldown.timestamp())}:R>."
        return True, ""
    if p.act is not None or p.queue:
        return False, "Сейчас вы заняты."
    if career_blocked(p):
        return False, f"Здоровье ниже {R.HEALTH_CAREER_BLOCK:.0f}%: карьера заблокирована."
    if p.energy < R.MIN_START_ENERGY:
        return False, f"Мало сил: на смену можно выйти при энергии не ниже {R.MIN_START_ENERGY:.0f}%."
    if p.money < 2 * vac.fare:
        return False, f"Не хватает денег на проезд ({2 * vac.fare} м. туда и обратно)."
    return True, ""

def estimate(p: Player, vac) -> tuple:
    """Прикидка: (зарплата брутто за полную смену, энергия к концу смены, хватит ли сил доработать)."""
    pk = perks(exp_level(p.exp.get(vac.prof, 0.0)))
    shift_h, com_h = vac.shift_min / 60, vac.commute_min / 60
    k = R.LOW_HEALTH_DRAIN_MULT if p.health < R.HEALTH_SLOW else 1.0
    cost = (abs(R.COMMUTE_ENERGY_RATE) * com_h
            + abs(vac.rate_energy) * (1 - pk["res_energy"]) * shift_h) * k
    left = p.energy - cost
    gross = vac.wage * shift_h * happy_mult(p.happiness) * (1 + pk["bonus"])
    return gross, left, left >= R.ENERGY_FORCED

def start_job(p: Player, vac) -> str:
    """Старт разовой смены: дорога, работа, дорога домой."""
    pr = R.PROFESSIONS[vac.prof]
    pk = perks(exp_level(p.exp.get(vac.prof, 0.0)))
    commute_q = vac.commute_min // R.QUANTUM_MINUTES
    road = {"energy": R.COMMUTE_ENERGY_RATE}
    work = {"kind": "work", "title": pr["title"], "prof": vac.prof, "career": True,
            "wage": vac.wage, "quanta": vac.shift_min // R.QUANTUM_MINUTES, "done_q": 0, "earned": 0.0,
            "rates": {"energy": vac.rate_energy, "health": vac.rate_health, "happiness": vac.rate_happy},
            "bonus": pk["bonus"], "res_neg": pk["res_neg"], "res_energy": pk["res_energy"]}
    go = {"kind": "commute", "title": f"Дорога на работу ({pr['title']})", "career": True,
          "quanta": commute_q, "done_q": 0, "rates": dict(road)}
    home = {"kind": "commute", "title": "Дорога домой", "keep": True,
            "quanta": commute_q, "done_q": 0, "rates": dict(road)}
    p.money -= 2 * vac.fare
    p.act, p.queue, p.view_vac = go, [work, home], None
    return f"Вы взяли подработку «{pr['title']}». Вы выехали на работу."

# ---------- Постоянная работа ----------

def sign_contract(p: Player, vac, now: datetime, tz: tzinfo) -> str:
    """Подписывает договор: параметры вакансии замораживаются. Вызывать после can_take и claim_vacancy."""
    pr = R.PROFESSIONS[vac.prof]
    p.job = {
        "prof": vac.prof, "wage": vac.wage, "shift_min": vac.shift_min, "commute_min": vac.commute_min,
        "rate_energy": vac.rate_energy, "rate_health": vac.rate_health, "rate_happy": vac.rate_happy,
        "fare": vac.fare, "days_off": list(vac.days_off),
        "hired_day": now.astimezone(tz).date().isoformat(),
        "absences": 0, "week_gross": 0, "week_ok": True, "worked_day": None, "excused_day": None,
    }
    p.view_vac = None
    return (f"Вы устроились: «{pr['title']}». Договор подписан, условия больше не меняются. "
            "Сегодня прогула не будет; выходите на смену в разделе «Моя работа».")

def can_start_shift(p: Player, now: datetime, tz: tzinfo) -> tuple:
    job = p.job
    if job is None:
        return False, "У вас нет постоянной работы."
    why = free_reason(p)
    if why:
        return False, why
    if p.venue:
        return False, "Сначала выйдите из магазина."
    if career_blocked(p):
        return False, f"Здоровье ниже {R.HEALTH_CAREER_BLOCK:.0f}%: карьера заблокирована."
    local = now.astimezone(tz)
    if local.weekday() in job["days_off"]:
        return False, "Сегодня у вас выходной."
    if job.get("worked_day") == local.date().isoformat():
        return False, "Сегодня вы уже выходили на смену."
    if p.energy < R.MIN_START_ENERGY:
        return False, f"Мало сил: на смену можно выйти при энергии не ниже {R.MIN_START_ENERGY:.0f}%."
    if p.money < 2 * job["fare"]:
        return False, f"Не хватает денег на проезд ({2 * job['fare']} м. туда и обратно)."
    return True, ""

def start_shift(p: Player, now: datetime, tz: tzinfo) -> tuple:
    """Смена по договору: дорога, работа, дорога домой. Метка perm нужна для прогулов и премии."""
    ok, why = can_start_shift(p, now, tz)
    if not ok:
        return False, why
    job = p.job
    pr = R.PROFESSIONS[job["prof"]]
    pk = perks(exp_level(p.exp.get(job["prof"], 0.0)))
    commute_q = job["commute_min"] // R.QUANTUM_MINUTES
    road = {"energy": R.COMMUTE_ENERGY_RATE}
    work = {"kind": "work", "title": pr["title"], "prof": job["prof"], "career": True, "perm": True,
            "wage": job["wage"], "quanta": job["shift_min"] // R.QUANTUM_MINUTES, "done_q": 0, "earned": 0.0,
            "rates": {"energy": job["rate_energy"], "health": job["rate_health"],
                      "happiness": job["rate_happy"]},
            "bonus": pk["bonus"], "res_neg": pk["res_neg"], "res_energy": pk["res_energy"]}
    go = {"kind": "commute", "title": f"Дорога на работу ({pr['title']})", "career": True, "perm": True,
          "quanta": commute_q, "done_q": 0, "rates": dict(road)}
    home = {"kind": "commute", "title": "Дорога домой", "keep": True, "perm": True,
            "quanta": commute_q, "done_q": 0, "rates": dict(road)}
    p.money -= 2 * job["fare"]
    p.act, p.queue = go, [work, home]
    job["worked_day"] = now.astimezone(tz).date().isoformat()
    return True, f"Вы вышли на смену: «{pr['title']}»."

def quit_job(p: Player, now: datetime) -> tuple:
    """Увольнение по желанию. Во время смены нельзя. После ухода действует кулдаун найма."""
    if p.job is None:
        return False, "У вас нет постоянной работы."
    if p.frozen_at is not None:
        return False, "Игра заморожена."
    if p.act and p.act.get("career"):
        return False, "Сначала закончите смену."
    p.job = None
    p.job_cooldown = now + timedelta(hours=R.JOB_HIRE_COOLDOWN_HOURS)
    return True, f"Вы уволились. Новую постоянную работу можно взять через {R.JOB_HIRE_COOLDOWN_HOURS} ч."

# ---------- Общее для досуга ----------

def free_reason(p: Player) -> str:
    """Почему игрок не может начать новое дело. Пусто - может."""
    if p.frozen_at is not None:
        return "Игра заморожена."
    if p.act is not None or p.queue:
        return "Сейчас вы заняты."
    return ""

# ---------- Баффы (курсы больницы) ----------

def buff_power(p: Player, kind: str) -> float:
    b = p.buffs.get(kind)
    return float(b["power"]) if b else 0.0

def apply_buff(p: Player, code: str):
    """Включает бафф курса: сильнее заменяет, равный продлевает, слабее игнорируется."""
    c = R.COURSES[code]
    kind = c["kind"]
    q = c["hours"] * 60 // R.QUANTUM_MINUTES
    cap = R.BUFF_MAX_HOURS * 60 // R.QUANTUM_MINUTES
    cur = p.buffs.get(kind)
    if cur is None or c["power"] > cur["power"]:
        p.buffs[kind] = {"code": code, "power": c["power"], "left": q}
    elif c["power"] == cur["power"]:
        cur["left"] = min(cur["left"] + q, cap)

def start_course(p: Player, code: str) -> tuple:
    c = R.COURSES.get(code)
    if c is None:
        return False, "Такого курса нет."
    why = free_reason(p)
    if why:
        return False, why
    if p.venue:
        return False, "Сначала выйдите из магазина."
    if p.energy < R.COURSE_MIN_ENERGY:
        return False, f"Слишком мало сил: на курс можно прийти при энергии не ниже {R.COURSE_MIN_ENERGY:.0f}%."
    if p.money < c["cost"]:
        return False, f"Не хватает денег ({c['cost']} м.)."
    cur = p.buffs.get(c["kind"])
    if cur and cur["power"] > c["power"]:
        return False, "У вас уже действует более сильный эффект этого вида."
    p.money -= c["cost"]
    p.act = {"kind": "course", "title": f"Курс «{c['title']}»", "guard": True, "course": code,
             "quanta": c["lesson"] * 60 // R.QUANTUM_MINUTES, "done_q": 0,
             "rates": {"energy": R.COURSE_ENERGY_RATE}}
    return True, f"Вы записались на курс «{c['title']}»: эффект включится после занятия."

# ---------- Жильё ----------

def move_cost(code: str) -> int:
    return R.HOUSING_MOVE_DAYS * R.HOUSING[code]["rent"]

def can_move(p: Player, code: str, now: datetime, tz: tzinfo) -> tuple:
    if code not in R.HOUSING:
        return False, "Такого жилья нет."
    if code == p.housing:
        return False, "Вы уже здесь живёте."
    why = free_reason(p)
    if why:
        return False, why
    if p.venue:
        return False, "Сначала выйдите из магазина."
    if p.last_move:
        until = p.last_move + timedelta(hours=R.HOUSING_MOVE_COOLDOWN_HOURS)
        if now < until:
            return False, f"Переезжать так часто нельзя: следующий переезд {_fmt(until, tz)}."
    fee = move_cost(code)
    if p.money < fee:
        return False, f"Не хватает денег на переезд ({fee} м.)."
    return True, ""

def move_housing(p: Player, code: str, now: datetime, tz: tzinfo) -> tuple:
    ok, why = can_move(p, code, now, tz)
    if not ok:
        return False, why
    p.money -= move_cost(code)
    p.housing = code
    p.last_move = now
    return True, f"Вы переехали: {R.HOUSING[code]['title']}."

# ---------- Рынок ----------

def goods_of(venue: str) -> list:
    cats = R.VENUES[venue]["cats"]
    return [(code, item) for code, item in R.GOODS.items() if item["cat"] in cats]

def good_price(venue: str, code: str) -> int:
    base = R.GOODS[code]["price"]
    markup = R.VENUES[venue]["markup"]
    price = int(round(base * markup))
    return max(price, base + 1) if markup > 1 else price

def good_limit(code: str) -> int:
    return R.GOODS[code].get("limit", R.DEFAULT_DAILY_LIMIT)

def start_errand(p: Player, venue: str) -> tuple:
    """Дорога в магазин. По прибытии (см. Sim._finish) игрок оказывается «в магазине»."""
    v = R.VENUES.get(venue)
    if v is None:
        return False, "Такого магазина нет."
    why = free_reason(p)
    if why:
        return False, why
    if p.venue:
        return False, "Вы уже в магазине."
    p.act = {"kind": "errand", "title": f"Дорога: {v['title']}", "quanta": R.ERRAND_QUANTA, "done_q": 0,
             "rates": {"energy": R.ERRAND_ENERGY_RATE}, "arrive": venue}
    return True, f"Вы отправились: «{v['title']}»."

def leave_venue(p: Player) -> tuple:
    if not p.venue:
        return False, "Вы не в магазине."
    p.venue = None
    p.act = {"kind": "errand", "title": "Дорога домой", "quanta": R.ERRAND_QUANTA, "done_q": 0,
             "rates": {"energy": R.ERRAND_ENERGY_RATE}}
    return True, "Вы вышли из магазина и идёте домой."

def buy(p: Player, code: str) -> tuple:
    venue = p.venue
    if not venue:
        return False, "Вы не в магазине."
    item = R.GOODS.get(code)
    if item is None or item["cat"] not in R.VENUES[venue]["cats"]:
        return False, "Здесь такого нет."
    price = good_price(venue, code)
    if p.money < price:
        return False, f"Не хватает денег ({price} м.)."
    if p.day_buys.get(code, 0) >= good_limit(code):
        return False, f"На сегодня лимит по этому товару исчерпан ({good_limit(code)} шт.)."
    gain = {"energy": item.get("energy", 0), "health": item.get("health", 0), "happiness": item.get("happy", 0)}
    cur = {"energy": p.energy, "health": p.health, "happiness": p.happiness}
    if not any(gain[k] > 0 and cur[k] < 100 for k in gain):
        return False, "Вам это сейчас ни к чему: показатели уже на максимуме."
    if gain["health"] < 0 and p.health + gain["health"] < R.HEALTH_CAREER_BLOCK:
        return False, "Здоровье и так слишком низкое: это вам сейчас вредно."
    p.money -= price
    p.energy = clamp(p.energy + gain["energy"])
    p.health = clamp(p.health + gain["health"])
    p.happiness = clamp(p.happiness + gain["happiness"])
    p.day_buys[code] = p.day_buys.get(code, 0) + 1
    return True, f"Куплено: {item['title']} за {price} м."

# ---------- Развлечения ----------

def dim_mult(used_q: int, q: int) -> float:
    """Средний множитель отдачи для q квантов занятия, если за сутки уже пройдено used_q квантов."""
    total = 0.0
    for i in range(used_q, used_q + q):
        h = i * R.QUANTUM_MINUTES / 60
        for limit, k in R.DIM_TIERS:
            if limit is None or h < limit:
                total += k
                break
    return total / q

def start_leisure(p: Player, code: str, hours: int) -> tuple:
    ent = R.ENTERTAINMENT.get(code)
    if ent is None or hours not in ent["hours"]:
        return False, "Такого занятия нет."
    why = free_reason(p)
    if why:
        return False, why
    if p.venue:
        return False, "Сначала выйдите из магазина."
    if p.energy < R.LEISURE_MIN_ENERGY:
        return False, f"Слишком мало сил: нужно не меньше {R.LEISURE_MIN_ENERGY:.0f}% энергии."
    cost = ent["cost"] * hours
    if p.money < cost:
        return False, f"Не хватает денег ({cost} м.)."
    q = hours * 60 // R.QUANTUM_MINUTES
    used = p.day_ent.get(code, 0)
    mult = dim_mult(used, q)
    rates = {"energy": ent["energy"], "health": ent["health"], "happiness": ent["happy"]}
    for k, v in rates.items():
        if v > 0:
            rates[k] = round(v * mult, 3)
    p.money -= cost
    p.day_ent[code] = used + q
    p.act = {"kind": "leisure", "title": ent["title"], "guard": True, "quanta": q, "done_q": 0, "rates": rates}
    return True, f"{ent['title']}: {hours} ч."

def stop_leisure(p: Player, tz: tzinfo) -> tuple:
    """Закончить досуг раньше срока (деньги не возвращаются). Возвращает ok, текст, события."""
    if not p.act or p.act["kind"] != "leisure":
        return False, "Сейчас нечего заканчивать.", []
    sim = Sim(p, tz)
    sim._finish()
    return True, "Вы закончили досуг раньше срока.", sim.events

# ---------- Образование ----------

def edu_missing(p: Player, code: str) -> list:
    """Названия программ, которые нужно получить заранее."""
    return [R.EDUCATION[e]["title"] for e in R.EDUCATION[code]["need"] if e not in p.education]

def edu_left_min(p: Player, code: str) -> int:
    """Сколько минут учёбы осталось до получения."""
    return max(0, R.EDUCATION[code]["hours"] * 60 - p.edu_progress.get(code, 0))

def start_study(p: Player, code: str, hours: int) -> tuple:
    """Занятие до `hours` часов (но не дольше, чем осталось до конца программы). Деньги списываются поминутно."""
    e = R.EDUCATION.get(code)
    if e is None or hours not in R.EDU_SESSION_HOURS:
        return False, "Такой программы нет."
    if code in p.education:
        return False, "Это образование уже получено."
    missing = edu_missing(p, code)
    if missing:
        return False, "Сначала нужно получить: " + ", ".join(missing) + "."
    why = free_reason(p)
    if why:
        return False, why
    if p.venue:
        return False, "Сначала выйдите из магазина."
    if career_blocked(p):
        return False, f"Здоровье ниже {R.HEALTH_CAREER_BLOCK:.0f}%: учёба недоступна."
    if p.energy < R.EDU_MIN_ENERGY:
        return False, f"Слишком мало сил: на занятие можно прийти при энергии не ниже {R.EDU_MIN_ENERGY:.0f}%."
    if p.money < e["fee"]:
        return False, f"Не хватает денег: час учёбы стоит {e['fee']} м."
    q = min(hours * 60 // R.QUANTUM_MINUTES, math.ceil(edu_left_min(p, code) / R.QUANTUM_MINUTES))
    if q <= 0:
        return False, "Эта программа уже пройдена."
    p.act = {"kind": "study", "title": f"Учёба: {e['title']}", "guard": True, "study": code,
             "tuition": e["fee"], "quanta": q, "done_q": 0,
             "rates": {"energy": R.EDU_ENERGY_RATE, "happiness": R.EDU_HAPPY_RATE}}
    return True, f"Вы начали учёбу: {e['title']} ({q * R.QUANTUM_MINUTES / 60:g} ч)."

def stop_study(p: Player, tz: tzinfo) -> tuple:
    """Закончить занятие раньше срока: без штрафов, прогресс сохраняется. Возвращает ok, текст, события."""
    if not p.act or p.act["kind"] != "study":
        return False, "Сейчас вы не учитесь.", []
    sim = Sim(p, tz)
    sim._finish()
    return True, "Вы закончили занятие раньше срока: прогресс сохранён, штрафов нет.", sim.events

# ---------- Заморозка ----------

def _fmt(dt: datetime, tz: tzinfo) -> str:
    return dt.astimezone(tz).strftime("%d.%m %H:%M")

def can_freeze(p: Player, now: datetime, tz: tzinfo) -> tuple:
    if p.frozen_at is not None:
        return False, "Игра уже заморожена."
    if p.venue:
        return False, "Сначала выйдите из магазина."
    if p.act and (p.act.get("career") or p.act.get("study") or p.act["kind"] == "hospital"):
        return False, "Заморозка недоступна во время смены, учёбы и лечения: сначала закончите дело."
    if p.last_unfreeze:
        until = p.last_unfreeze + timedelta(days=R.FREEZE_COOLDOWN_DAYS)
        if now < until:
            return False, f"Повторная заморозка будет доступна {_fmt(until, tz)}."
    return True, ""

def freeze(p: Player, now: datetime, tz: tzinfo) -> tuple:
    """Вызывать после catch_up: состояние должно быть рассчитано до now."""
    ok, why = can_freeze(p, now, tz)
    if not ok:
        return False, why
    p.frozen_at = now
    return True, "Игра заморожена: время для вашего рабочего остановилось."

def unfreeze(p: Player, now: datetime, tz: tzinfo) -> tuple:
    """Без последствий: время расчёта переносится на «сейчас», пропущенные полночи не обрабатываются."""
    if p.frozen_at is None:
        return False, "Игра не заморожена."
    until = p.frozen_at + timedelta(hours=R.FREEZE_MIN_HOURS)
    if now < until:
        return False, f"Разморозка будет доступна {_fmt(until, tz)}."
    p.updated = floor_q(now)
    p.frozen_at = None
    p.last_unfreeze = now
    return True, "С возвращением! Время рабочего снова идёт."