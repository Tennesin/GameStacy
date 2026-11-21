"""Кнопки симулятора рабочего."""
from typing import NamedTuple, Optional

import discord

from core.errors import is_expired, report_error
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
    Spec("sleep", "Сколько спать?", None, 3, "select"),
    Spec("job_pick", "Выберите вакансию", None, 4, "select"),
]

def sleep_opts() -> list:
    return [("До полного восстановления", "full", None)] + [(f"{h} ч", str(h), None) for h in R.SLEEP_CHOICES_HOURS]

def build_specs(screen: str, p=None, jobs=None, vac=None, can: bool = False) -> list:
    """Какие элементы показывать на экране."""
    if screen == "menu":
        return [Spec("career", "💼 Карьера", PRIMARY, 0), Spec("leisure", "🎭 Досуг", PRIMARY, 0),
                Spec("status", "📋 Моё состояние", SECONDARY, 0)]
    if screen == "career":
        return [Spec("jobs", "🔨 Работа", PRIMARY, 0), Spec("menu", "◀ Назад", SECONDARY, 0)]
    if screen == "leisure":
        return [Spec("menu", "◀ Назад", SECONDARY, 0)]
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
    if p.act and p.act["kind"] == "sleep" and not p.act.get("forced"):
        specs.append(Spec("wake", "⏰ Проснуться", PRIMARY, 0))
    elif p.act is None and p.energy < R.SLEEP_AVAILABLE_BELOW:
        specs.append(Spec("nav_sleep", "😴 Спать", PRIMARY, 0))
    if p.act and p.act.get("career"):
        specs.append(Spec("abort", "🚪 Уйти со смены", DANGER, 0))
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

def persistent_views(ctrl) -> list:
    """Views для bot.add_view при старте (кнопки и списки отдельно)."""
    buttons = [s for s in ALL_SPECS if s.kind == "button"]
    selects = [s for s in ALL_SPECS if s.kind == "select"]
    return [PanelView(ctrl, buttons, auto_rows=True), PanelView(ctrl, selects, auto_rows=True)]