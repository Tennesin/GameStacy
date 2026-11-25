"""Кнопки симулятора рабочего."""
from contextlib import suppress
from typing import NamedTuple, Optional
import discord

from config import TZ
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
    Spec("menu_refresh", "🔄 Обновить", SECONDARY, 1),
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
    Spec("kitchen", "🍳 Кухня", PRIMARY, 0),
    Spec("buy_do", "🛒 Купить", SUCCESS, 3), Spec("buy_back", "◀ Назад", SECONDARY, 3),
    Spec("buy_qty", "Сколько продуктов?", None, 4, "select"),
    Spec("cook_go", "Что приготовить?", None, 4, "select"),
    Spec("dur_set", "Сколько часов?", None, 3, "select"),
    Spec("pick_do", "▶ Начать", SUCCESS, 3), Spec("pick_back", "◀ Назад", SECONDARY, 3),
    Spec("page_prev", "⬅ Пред. стр.", SECONDARY, 3), Spec("page_next", "След. стр. ➡", SECONDARY, 3),
]

def sleep_opts(p) -> list:
    variants = [("До полного восстановления", "full", None)]
    variants += [(f"{h} ч", str(h), h * 60 // R.QUANTUM_MINUTES) for h in R.SLEEP_CHOICES_HOURS]
    opts = []
    for label, value, quanta in variants:
        e0, e1, h0, h1, minutes = L.sleep_forecast(p, quanta, TZ)
        desc = f"⚡ {e0:.0f}% → {e1:.0f}% · ❤️ {h0:.0f}% → {h1:.0f}%"
        if quanta is None or minutes < quanta * R.QUANTUM_MINUTES:
            desc += f" · {T.dur(minutes)}"        # выспитесь раньше срока или режим «до полного»
        opts.append((label, value, desc[:100]))
    return opts

PICK_DO_LABEL = {"ent": "🎭 Пойти", "edu": "📚 Начать учёбу", "course": "🎓 Записаться",
                 "cook": "🍳 Готовить", "house": "🏠 Переехать"}

def pager_specs(page: int, pages: int, row: int) -> list:
    """Кнопки листания (только те, что имеют смысл на этой странице)."""
    specs = []
    if page > 0:
        specs.append(Spec("page_prev", "⬅ Пред. стр.", SECONDARY, row))
    if page < pages - 1:
        specs.append(Spec("page_next", "След. стр. ➡", SECONDARY, row))
    return specs

def venue_opts(p, chunk: list) -> list:
    opts = []
    for code, item in chunk:
        price = L.good_price(p.venue, code)
        if code in R.INGREDIENTS:
            have = L.held(p, code)
            desc = (f"уп. {L.amount_text(code, item['pack'])} · "
                    f"у вас: {L.amount_text(code, have) if have else 'нет'}")
            opts.append((f"{item['title']} · {price} м."[:100], code, desc[:100]))
            continue
        left = max(0, L.good_limit(code) - p.day_buys.get(code, 0))
        opts.append((f"{item['title']} · {price} м."[:100],
                     code, f"{T.effect_text(item)} · осталось {left}"[:100]))
    return opts

def ent_opts(p, chunk: list) -> list:
    opts = []
    for code, e in chunk:
        hours = e["hours"]
        span = f"{hours[0]} ч" if len(hours) == 1 else f"{hours[0]}–{hours[-1]} ч"
        cost = "бесплатно" if not e["cost"] else f"{e['cost']} м./ч"
        mult = L.dim_mult(p.day_ent.get(code, 0), 60 // R.QUANTUM_MINUTES)
        opts.append((f"{e['title']} · {cost}"[:100], code,
                     f"счастье {e['happy'] * mult:+.1f}/ч (отдача ×{mult:.2f}) · {span}"[:100]))
    return opts

def edu_opts(p, chunk: list) -> list:
    """Только то, что можно начать (не получено и выполнены условия)."""
    opts = []
    for code, e in chunk:
        if code in p.education or L.edu_missing(p, code):
            continue
        done = p.edu_progress.get(code, 0)
        pct = min(100, done * 100 // (e["hours"] * 60))
        opts.append((f"{e['title']} · {e['fee']} м./ч"[:100], code,
                     f"пройдено {pct}% · всего {e['hours']} ч"[:100]))
    return opts

def cook_opts(p, chunk: list) -> list:
    opts = []
    have_lvl = L.cook_level(p.cook_xp)
    for code, r in chunk:
        need = r.get("lvl", 0)
        if have_lvl < need:
            opts.append((f"🔒 {r['title']}"[:100], code, f"откроется на уровне Повара {need}"))
            continue
        plan = L.cook_plan(p, code)
        miss = L.missing_ingredients(p, code)
        if miss:
            desc = "не хватает: " + ", ".join(miss)
        else:
            desc = f"⚡+{plan['energy']:.1f} 😊+{plan['happy']:.1f} · опыт +{plan['xp']:.1f}"
        opts.append((f"{'❌ ' if miss else ''}{r['title']} · {plan['quanta'] * R.QUANTUM_MINUTES} мин"[:100],
                     code, desc[:100]))
    return opts

def house_opts(p) -> list:
    return [(f"{h['title']} · {h['rent']} м./сут."[:100], code,
             f"счастье → {h['base']:.0f}% · холодильник {h['fridge']} · переезд {L.move_cost(code)} м."[:100])
            for code, h in R.HOUSING.items() if code != p.housing]

def pick_hours_opts(p) -> list:
    """Варианты длительности в окне подтверждения (досуг и учёба)."""
    kind, code = p.pick_kind, p.pick_code
    opts = []
    for h in L.pick_hours_options(kind, code):
        if kind == "ent":
            e = R.ENTERTAINMENT[code]
            mult = L.dim_mult(p.day_ent.get(code, 0), h * 60 // R.QUANTUM_MINUTES)
            cost = "бесплатно" if not e["cost"] else f"{e['cost'] * h} м."
            opts.append((f"{h} ч · {cost}", str(h),
                         f"счастье {e['happy'] * mult:+.1f}/ч (отдача ×{mult:.2f})"[:100]))
        else:
            e = R.EDUCATION[code]
            real = min(h, L.edu_left_min(p, code) / 60)
            opts.append((f"до {h} ч · ≈{int(e['fee'] * real)} м.", str(h),
                         f"{e['fee']} м./ч, списывается по минутам"))
    return opts

def qty_opts() -> list:
    return [("1 упаковка (яйца: 6 шт.)", "1", "Для продуктов: одна упаковка"),
            ("2 упаковки (яйца: 12 шт.)", "2", "Для продуктов: две упаковки"),
            ("Максимум в слот", "max", "Докупить столько, сколько влезет в слот")]

def course_opts() -> list:
    return [(f"{c['title']} · {c['cost']} м."[:100], code,
             f"{T.buff_desc(c['kind'], c['power'])} · {c['hours']} ч · занятие {c['lesson']} ч"[:100])
            for code, c in R.COURSES.items()]

def build_specs(screen: str, p=None, jobs=None, vac=None, can: bool = False) -> list:
    """Какие элементы показывать на экране."""
    if screen == "menu":
        return [Spec("career", "💼 Карьера", PRIMARY, 0), Spec("leisure", "🎭 Досуг", PRIMARY, 0),
                Spec("status", "📋 Моё состояние", SECONDARY, 0),
                Spec("menu_refresh", "🔄 Обновить", SECONDARY, 1)]
    if screen == "career":
        return [Spec("jobs", "🔨 Работа", PRIMARY, 0), Spec("myjob", "📑 Моя работа", PRIMARY, 0),
                Spec("edu", "🎓 Образование", PRIMARY, 0), Spec("menu", "◀ Назад", SECONDARY, 0)]
    if screen == "edu":
        chunk, page, pages = L.page_slice(L.screen_items(p, "edu"), "edu", p.page)
        opts = edu_opts(p, chunk)
        specs = [Spec("edu_go", "Чему учиться?", None, 0, "select", opts)] if opts else []
        return specs + pager_specs(page, pages, 1) + [Spec("career", "◀ Назад", SECONDARY, 1)]
    if screen == "myjob":
        specs = []
        if p is not None and p.job is not None:
            specs += [Spec("shift_go", "▶ Выйти на смену", SUCCESS, 0), Spec("quit", "📤 Уволиться", DANGER, 0)]
        return specs + [Spec("career", "◀ Назад", SECONDARY, 1)]
    if screen == "quit_confirm":
        return [Spec("quit_yes", "✅ Да, уволиться", DANGER, 0), Spec("myjob", "Отмена", SECONDARY, 0)]
    if screen == "leisure":
        return [Spec("market", "🛒 Рынок", PRIMARY, 0), Spec("ent", "🎭 Развлечения", PRIMARY, 0),
                Spec("kitchen", "🍳 Кухня", PRIMARY, 0), Spec("nav_hosp", "🏥 Больница", PRIMARY, 0),
                Spec("housing", "🏠 Жильё", PRIMARY, 0), Spec("menu", "◀ Назад", SECONDARY, 1)]
    if screen == "market":
        return [Spec("go_food", "🥖 Продукты", PRIMARY, 0), Spec("go_pharm", "💊 Аптека", PRIMARY, 0),
                Spec("go_super", "🛍 Супермаркет", PRIMARY, 0), Spec("leisure", "◀ Назад", SECONDARY, 1)]
    if screen == "venue" and p is not None and p.venue:
        chunk, page, pages = L.page_slice(L.screen_items(p, "venue"), "venue", p.page)
        return ([Spec("buy", "Что купить?", None, 0, "select", venue_opts(p, chunk))]
                + pager_specs(page, pages, 1)
                + [Spec("venue_leave", "🚪 Уйти из магазина", SECONDARY, 1)])
    if screen == "buy_confirm" and p is not None and p.venue and p.buy_item:
        specs, row = [], 0
        if p.buy_item in R.INGREDIENTS:
            specs.append(Spec("buy_qty", "Сколько продуктов?", None, 0, "select", qty_opts()))
            row = 1
        return specs + [Spec("buy_do", "🛒 Купить", SUCCESS, row),
                        Spec("buy_back", "◀ Назад", SECONDARY, row)]
    if screen == "ent":
        chunk, page, pages = L.page_slice(L.screen_items(p, "ent"), "ent", p.page)
        return ([Spec("ent_go", "Куда пойти?", None, 0, "select", ent_opts(p, chunk))]
                + pager_specs(page, pages, 1) + [Spec("leisure", "◀ Назад", SECONDARY, 1)])
    if screen == "kitchen":
        chunk, page, pages = L.page_slice(L.screen_items(p, "kitchen"), "kitchen", p.page)
        return ([Spec("cook_go", "Что приготовить?", None, 0, "select", cook_opts(p, chunk))]
                + pager_specs(page, pages, 1) + [Spec("leisure", "◀ Назад", SECONDARY, 1)])
    if screen == "pick" and p is not None and p.pick_kind:
        specs = []
        opts = pick_hours_opts(p)
        if len(opts) > 1:
            specs.append(Spec("dur_set", f"Длительность: {p.pick_hours} ч", None, 0, "select", opts))
        row = 1 if specs else 0
        return specs + [Spec("pick_do", PICK_DO_LABEL.get(p.pick_kind, "▶ Начать"), SUCCESS, row),
                        Spec("pick_back", "◀ Назад", SECONDARY, row)]
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
                lock = "🔒 " if p is not None and L.lock_reason(p, v) else ""
                label = f"{lock}№{v.slot} {R.PROFESSIONS[v.prof]['title']} · {T.dur(v.shift_min)}"[:100]
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
        return [Spec("sleep", "Сколько спать?", None, 0, "select", sleep_opts(p)),
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
        await report_error(interaction, error, ephemeral=True)

class ConfirmView(discord.ui.View):
    """Скрытое окно подтверждения. Не постоянное: живёт 2 минуты.
    «Да» выполняет действие и обновляет публичную панель (panel - её сообщение)."""

    def __init__(self, ctrl, yes_action: str, panel):
        super().__init__(timeout=120)
        self.ctrl, self.yes_action, self.panel = ctrl, yes_action, panel
        self.origin = None          # нажатие, открывшее это окно (для правки по тайм-ауту)

    @discord.ui.button(label="✅ Да", style=DANGER)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.ctrl.on_confirm(interaction, self.yes_action, self.panel)

    @discord.ui.button(label="Отмена", style=SECONDARY)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Отменено.", view=None)

    async def on_timeout(self):
        if self.origin is not None:
            with suppress(discord.HTTPException):
                await self.origin.edit_original_response(content="Время вышло: ничего не изменено.", view=None)

    async def on_error(self, interaction, error, item):
        if is_expired(error):
            return
        await report_error(interaction, error, ephemeral=True)
        self.stop()

def _chunks(items: list, n: int) -> list:
    return [items[i:i + n] for i in range(0, len(items), n)]

def persistent_views(ctrl) -> list:
    """Views для bot.add_view при старте: кнопки по 20 штук, списки по 4 (лимиты Discord на один View)."""
    buttons = [s for s in ALL_SPECS if s.kind == "button"]
    selects = [s for s in ALL_SPECS if s.kind == "select"]
    views = [PanelView(ctrl, part, auto_rows=True) for part in _chunks(buttons, 20)]
    views += [PanelView(ctrl, part, auto_rows=True) for part in _chunks(selects, 4)]
    return views