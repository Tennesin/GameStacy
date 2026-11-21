"""Кнопки симулятора рабочего."""
from typing import NamedTuple, Optional

import discord

from core.errors import is_expired, report_error
from . import logic as L
from . import rules as R
from . import texts as T

PRIMARY, SECONDARY = discord.ButtonStyle.primary, discord.ButtonStyle.secondary
SUCCESS, DANGER = discord.ButtonStyle.success, discord.ButtonStyle.danger

class Spec(NamedTuple):
    action: str
    label: str
    style: object
    row: int
    kind: str = "button"          # button / select
    opts: Optional[list] = None   # для select: [(подпись, значение, описание или None)]

# Все возможные элементы по одному разу (для зарегистрированных View)
ALL_SPECS = [
    Spec("career", "💼 Карьера", PRIMARY, 0), Spec("leisure", "🎭 Досуг", PRIMARY, 0),
    Spec("status", "📋 Моё состояние", SECONDARY, 0), Spec("menu", "◀ Назад", SECONDARY, 0),
    Spec("refresh", "🔄 Обновить", SECONDARY, 0),
    Spec("unfreeze", "🔥 Разморозить", SUCCESS, 1), Spec("wake", "⏰ Проснуться", PRIMARY, 1),
    Spec("nav_sleep", "😴 Спать", PRIMARY, 1), Spec("freeze", "🧊 Заморозить игру", SECONDARY, 1),
    Spec("pay25", "Погасить 25%", SECONDARY, 1),
    Spec("pay50", "Погасить 50%", SECONDARY, 2), Spec("payall", "Погасить всё", SECONDARY, 2),
    Spec("freeze_yes", "✅ Да, заморозить", DANGER, 2),
    Spec("jobs", "🔨 Работа", PRIMARY, 2), Spec("take", "✅ Взять", SUCCESS, 2),
    Spec("abort", "🚪 Уйти со смены", DANGER, 3), Spec("abort_yes", "✅ Да, уйти", DANGER, 3),
    Spec("market", "🛒 Рынок", PRIMARY, 3), Spec("ent", "🎭 Развлечения", PRIMARY, 3),
    Spec("nav_hosp", "🏥 Больница", PRIMARY, 3), Spec("housing", "🏠 Жильё", PRIMARY, 3),
    Spec("go_food", "🥖 Продукты", PRIMARY, 3), Spec("go_pharm", "💊 Аптека", PRIMARY, 3),
    Spec("go_super", "🛍 Супермаркет", PRIMARY, 3), Spec("venue_leave", "🚪 Уйти из магазина", SECONDARY, 3),
    Spec("stop_leisure", "🏁 Закончить досуг", PRIMARY, 3),
    Spec("myjob", "📑 Моя работа", PRIMARY, 0), Spec("shift_go", "▶ Выйти на смену", SUCCESS, 0),
    Spec("quit", "📤 Уволиться", DANGER, 0), Spec("quit_yes", "✅ Да, уволиться", DANGER, 0),
    Spec("edu", "🎓 Образование", PRIMARY, 0), Spec("stop_study", "🏁 Закончить учёбу", PRIMARY, 3),
    Spec("sleep", "Сколько спать?", None, 3, "select"),
    Spec("job_pick", "Выберите вакансию", None, 4, "select"),
    Spec("buy", "Что купить?", None, 4, "select"),
    Spec("ent_go", "Куда пойти?", None, 4, "select"),
    Spec("course_go", "Выберите курс", None, 4, "select"),
    Spec("house_go", "Куда переехать?", None, 4, "select"),
    Spec("edu_go", "Чему учиться?", None, 4, "select"),
]

def sleep_opts() -> list:
    return [("До полного восстановления", "full", None)] + [(f"{h} ч", str(h), None) for h in R.SLEEP_CHOICES_HOURS]

def venue_opts(p) -> list:
    opts = []
    for code, item in L.goods_of(p.venue):
        left = max(0, L.good_limit(code) - p.day_buys.get(code, 0))
        opts.append((f"{item['title']} · {L.good_price(p.venue, code)} м."[:100],
                     code, f"{T.effect_text(item)} · осталось {left}"[:100]))
    return opts

def ent_opts(p) -> list:
    opts = []
    for code, e in R.ENTERTAINMENT.items():
        for h in e["hours"]:
            q = h * 60 // R.QUANTUM_MINUTES
            mult = L.dim_mult(p.day_ent.get(code, 0), q)
            cost = "бесплатно" if not e["cost"] else f"{e['cost'] * h} м."
            opts.append((f"{e['title']} · {h} ч · {cost}"[:100], f"{code}:{h}",
                         f"счастье {e['happy'] * mult:+.1f}/ч (отдача ×{mult:.2f})"[:100]))
    return opts

def course_opts() -> list:
    return [(f"{c['title']} · {c['cost']} м."[:100], code,
             f"{T.buff_desc(c['kind'], c['power'])} · {c['hours']} ч · занятие {c['lesson']} ч"[:100])
            for code, c in R.COURSES.items()]

def house_opts(p) -> list:
    return [(f"{h['title']} · {h['rent']} м./сут."[:100], code,
             f"счастье → {h['base']:.0f}% · переезд {L.move_cost(code)} м."[:100])
            for code, h in R.HOUSING.items() if code != p.housing]

def edu_opts(p) -> list:
    opts = []
    for code, e in R.EDUCATION.items():
        if code in p.education or L.edu_missing(p, code):
            continue
        done = p.edu_progress.get(code, 0)
        pct = min(100, done * 100 // (e["hours"] * 60))
        for h in R.EDU_SESSION_HOURS:
            opts.append((f"{e['title']} · до {h} ч · ≈{e['fee'] * h} м."[:100], f"{code}:{h}",
                         f"пройдено {pct}% · {e['fee']} м./ч"[:100]))
    return opts

def build_specs(screen: str, p=None, jobs=None, vac=None, can: bool = False) -> list:
    """Какие элементы показывать на экране."""
    if screen == "menu":
        return [Spec("career", "💼 Карьера", PRIMARY, 0), Spec("leisure", "🎭 Досуг", PRIMARY, 0),
                Spec("status", "📋 Моё состояние", SECONDARY, 0)]
    if screen == "career":
        return [Spec("jobs", "🔨 Работа", PRIMARY, 0), Spec("myjob", "📑 Моя работа", PRIMARY, 0),
                Spec("edu", "🎓 Образование", PRIMARY, 0), Spec("menu", "◀ Назад", SECONDARY, 0)]
    if screen == "edu":
        opts = edu_opts(p)
        specs = [Spec("edu_go", "Чему учиться?", None, 0, "select", opts)] if opts else []
        return specs + [Spec("career", "◀ Назад", SECONDARY, 1)]
    if screen == "myjob":
        specs = []
        if p is not None and p.job is not None:
            specs += [Spec("shift_go", "▶ Выйти на смену", SUCCESS, 0), Spec("quit", "📤 Уволиться", DANGER, 0)]
        return specs + [Spec("career", "◀ Назад", SECONDARY, 1)]
    if screen == "quit_confirm":
        return [Spec("quit_yes", "✅ Да, уволиться", DANGER, 0), Spec("myjob", "Отмена", SECONDARY, 0)]
    if screen == "leisure":
        return [Spec("market", "🛒 Рынок", PRIMARY, 0), Spec("ent", "🎭 Развлечения", PRIMARY, 0),
                Spec("nav_hosp", "🏥 Больница", PRIMARY, 0), Spec("housing", "🏠 Жильё", PRIMARY, 0),
                Spec("menu", "◀ Назад", SECONDARY, 1)]
    if screen == "market":
        return [Spec("go_food", "🥖 Продукты", PRIMARY, 0), Spec("go_pharm", "💊 Аптека", PRIMARY, 0),
                Spec("go_super", "🛍 Супермаркет", PRIMARY, 0), Spec("leisure", "◀ Назад", SECONDARY, 1)]
    if screen == "venue" and p is not None and p.venue:
        return [Spec("buy", "Что купить?", None, 0, "select", venue_opts(p)),
                Spec("venue_leave", "🚪 Уйти из магазина", SECONDARY, 1)]
    if screen == "ent":
        return [Spec("ent_go", "Куда пойти?", None, 0, "select", ent_opts(p)),
                Spec("leisure", "◀ Назад", SECONDARY, 1)]
    if screen == "hospital":
        return [Spec("course_go", "Выберите курс", None, 0, "select", course_opts()),
                Spec("leisure", "◀ Назад", SECONDARY, 1)]
    if screen == "housing":
        return [Spec("house_go", "Куда переехать?", None, 0, "select", house_opts(p)),
                Spec("leisure", "◀ Назад", SECONDARY, 1)]
    if screen == "jobs":
        specs = []
        if jobs:
            opts = []
            for v in jobs[:25]:
                lock = "🔒 " if p is not None and T.L.lock_reason(p, v) else ""
                label = f"{lock}№{v.id} {R.PROFESSIONS[v.prof]['title']} · {T.dur(v.shift_min)}"[:100]
                desc = f"{v.wage:.0f} м./ч · дорога {v.commute_min} мин"
                opts.append((label, str(v.id), desc))
            specs.append(Spec("job_pick", "Выберите вакансию, чтобы открыть подробности", None, 0, "select", opts))
        specs += [Spec("jobs", "🔄 Обновить", SECONDARY, 1), Spec("career", "◀ Назад", SECONDARY, 1)]
        return specs
    if screen == "job":
        specs = [Spec("take", "✅ Взять", SUCCESS, 0)] if can else []
        return specs + [Spec("jobs", "◀ К списку", SECONDARY, 0)]
    if screen == "abort_confirm":
        return [Spec("abort_yes", "✅ Да, уйти", DANGER, 0), Spec("status", "Отмена", SECONDARY, 0)]
    if screen == "sleep":
        return [Spec("sleep", "Сколько спать?", None, 0, "select", sleep_opts()),
                Spec("status", "◀ Назад", SECONDARY, 1)]
    if screen == "freeze_confirm":
        return [Spec("freeze_yes", "✅ Да, заморозить", DANGER, 0), Spec("status", "Отмена", SECONDARY, 0)]
    # status
    specs = [Spec("refresh", "🔄 Обновить", SECONDARY, 0)]
    if p is None:
        return specs
    if p.frozen_at is not None:
        return specs + [Spec("unfreeze", "🔥 Разморозить", SUCCESS, 0)]
    if p.venue:
        return specs + [Spec("market", "🛒 В магазин", PRIMARY, 0)]
    if p.act and p.act["kind"] == "sleep" and not p.act.get("forced"):
        specs.append(Spec("wake", "⏰ Проснуться", PRIMARY, 0))
    elif p.act is None and p.energy < R.SLEEP_AVAILABLE_BELOW:
        specs.append(Spec("nav_sleep", "😴 Спать", PRIMARY, 0))
    if p.act and p.act.get("career"):
        specs.append(Spec("abort", "🚪 Уйти со смены", DANGER, 0))
    if p.act and p.act["kind"] == "leisure":
        specs.append(Spec("stop_leisure", "🏁 Закончить досуг", PRIMARY, 0))
    if p.act and p.act["kind"] == "study":
        specs.append(Spec("stop_study", "🏁 Закончить учёбу", PRIMARY, 0))
    specs.append(Spec("freeze", "🧊 Заморозить игру", SECONDARY, 0))
    if p.debt > 0 and p.money > 0:
        specs += [Spec("pay25", "Погасить 25%", SECONDARY, 1), Spec("pay50", "Погасить 50%", SECONDARY, 1),
                  Spec("payall", "Погасить всё", SECONDARY, 1)]
    specs.append(Spec("menu", "◀ Назад", SECONDARY, 2))
    return specs

class PanelView(discord.ui.View):
    def __init__(self, ctrl, specs: list, auto_rows: bool = False):
        """auto_rows=True - для регистрации при старте: ряды Discord расставит сам."""
        super().__init__(timeout=None)
        self.ctrl = ctrl
        for s in specs:
            row = None if auto_rows else s.row
            if s.kind == "select":
                opts = s.opts or [("—", "0", None)]
                options = [discord.SelectOption(label=l, value=v, description=d) for l, v, d in opts]
                sel = discord.ui.Select(custom_id=f"wrk:{s.action}", placeholder=s.label,
                                        options=options, row=row)
                sel.callback = self._make_select(s.action, sel)
                self.add_item(sel)
            else:
                btn = discord.ui.Button(label=s.label, style=s.style, custom_id=f"wrk:{s.action}", row=row)
                btn.callback = self._make_button(s.action)
                self.add_item(btn)

    def _make_button(self, action: str):
        async def callback(interaction: discord.Interaction):
            await self.ctrl.on_action(interaction, action)
        return callback

    def _make_select(self, action: str, sel: discord.ui.Select):
        async def callback(interaction: discord.Interaction):
            await self.ctrl.on_action(interaction, action, sel.values[0])
        return callback

    async def on_error(self, interaction, error, item):
        if is_expired(error):
            return
        await report_error(interaction, error)

def _chunks(items: list, n: int) -> list:
    return [items[i:i + n] for i in range(0, len(items), n)]

def persistent_views(ctrl) -> list:
    """Views для bot.add_view при старте: кнопки по 20 штук, списки по 4 (лимиты Discord на один View)."""
    buttons = [s for s in ALL_SPECS if s.kind == "button"]
    selects = [s for s in ALL_SPECS if s.kind == "select"]
    views = [PanelView(ctrl, part, auto_rows=True) for part in _chunks(buttons, 20)]
    views += [PanelView(ctrl, part, auto_rows=True) for part in _chunks(selects, 4)]
    return views