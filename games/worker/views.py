"""Кнопки симулятора рабочего. Все постоянные: timeout=None и фиксированные custom_id вида wrk:действие.
Игра личная, партии нет: игрока определяет interaction.user.id, сервер - interaction.guild_id.
На старте регистрируется один «общий» View со всеми кнопками (ALL_SPECS), поэтому панели,
отправленные до перезапуска бота, продолжают работать."""
from typing import NamedTuple

import discord

from core.errors import is_expired, report_error
from . import rules as R

PRIMARY, SECONDARY = discord.ButtonStyle.primary, discord.ButtonStyle.secondary
SUCCESS, DANGER = discord.ButtonStyle.success, discord.ButtonStyle.danger

class Spec(NamedTuple):
    action: str
    label: str
    style: object
    row: int
    kind: str = "button"          # button / select

# Все возможные элементы по одному разу (для общего зарегистрированного View)
ALL_SPECS = [
    Spec("career", "💼 Карьера", PRIMARY, 0), Spec("leisure", "🎭 Досуг", PRIMARY, 0),
    Spec("status", "📋 Моё состояние", SECONDARY, 0), Spec("menu", "◀ Назад", SECONDARY, 0),
    Spec("refresh", "🔄 Обновить", SECONDARY, 0),
    Spec("unfreeze", "🔥 Разморозить", SUCCESS, 1), Spec("wake", "⏰ Проснуться", PRIMARY, 1),
    Spec("nav_sleep", "😴 Спать", PRIMARY, 1), Spec("freeze", "🧊 Заморозить игру", SECONDARY, 1),
    Spec("pay25", "Погасить 25%", SECONDARY, 1),
    Spec("pay50", "Погасить 50%", SECONDARY, 2), Spec("payall", "Погасить всё", SECONDARY, 2),
    Spec("freeze_yes", "✅ Да, заморозить", DANGER, 2),
    Spec("sleep", "Сколько спать?", None, 3, "select"),
]

def build_specs(screen: str, p=None) -> list:
    """Какие элементы показывать на экране."""
    if screen == "menu":
        return [Spec("career", "💼 Карьера", PRIMARY, 0), Spec("leisure", "🎭 Досуг", PRIMARY, 0),
                Spec("status", "📋 Моё состояние", SECONDARY, 0)]
    if screen in ("career", "leisure"):
        return [Spec("menu", "◀ Назад", SECONDARY, 0)]
    if screen == "sleep":
        return [Spec("sleep", "Сколько спать?", None, 0, "select"), Spec("status", "◀ Назад", SECONDARY, 1)]
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
    specs.append(Spec("freeze", "🧊 Заморозить игру", SECONDARY, 0))
    if p.debt > 0 and p.money > 0:
        specs += [Spec("pay25", "Погасить 25%", SECONDARY, 1), Spec("pay50", "Погасить 50%", SECONDARY, 1),
                  Spec("payall", "Погасить всё", SECONDARY, 1)]
    specs.append(Spec("menu", "◀ Назад", SECONDARY, 2))
    return specs

def sleep_options() -> list:
    opts = [discord.SelectOption(label="До полного восстановления", value="full")]
    opts += [discord.SelectOption(label=f"{h} ч", value=str(h)) for h in R.SLEEP_CHOICES_HOURS]
    return opts

class PanelView(discord.ui.View):
    def __init__(self, ctrl, specs: list):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        for s in specs:
            if s.kind == "select":
                sel = discord.ui.Select(custom_id=f"wrk:{s.action}", placeholder=s.label,
                                        options=sleep_options(), row=s.row)
                sel.callback = self._make_select(s.action, sel)
                self.add_item(sel)
            else:
                btn = discord.ui.Button(label=s.label, style=s.style, custom_id=f"wrk:{s.action}", row=s.row)
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