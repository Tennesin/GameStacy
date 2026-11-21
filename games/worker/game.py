"""Контроллер симулятора рабочего: личная панель (скрытые сообщения), действия игрока, заморозка.
Фоновых задач нет: состояние рассчитывается при каждом нажатии (ленивая симуляция, см. logic.py)."""
import logging
import math

import discord
from discord import app_commands

from config import TZ
from core.locks import LockMap
from . import logic as L
from . import rules as R
from .storage import StorageRegistry
from .texts import money, render
from .views import ALL_SPECS, PanelView, build_specs

log = logging.getLogger("stacy.worker")

class WorkerController:
    def __init__(self, bot, registry: StorageRegistry):
        self.bot = bot
        self.registry = registry
        self.locks = LockMap()                      # по блокировке на (сервер, игрок)

    async def open_panel(self, interaction: discord.Interaction):
        await self.on_action(interaction, "menu", None, first=True)

    async def on_action(self, interaction: discord.Interaction, action: str, value=None, first: bool = False):
        if interaction.guild_id is None:
            await interaction.response.send_message("Игра работает только на сервере.", ephemeral=True)
            return
        uid, gid = interaction.user.id, interaction.guild_id
        async with self.locks[(gid, uid)]:
            # Между получением хранилища и записью нет await: подключение не вытеснится из кэша
            store = self.registry.get(gid)
            now = self.bot.clock.now()
            p = store.get_player(uid) or L.new_player(uid, now)
            events = L.catch_up(p, now, TZ)
            screen, note = self._apply(p, action, value, now, events)
            store.save_player(p)
        text = render(screen, p, now, TZ, events, note)
        view = PanelView(self, build_specs(screen, p))
        if first:
            await interaction.response.send_message(text, view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(content=text, view=view)

    def _apply(self, p: L.Player, action: str, value, now, events: list) -> tuple:
        """Выполняет действие над игроком. Возвращает (экран, пояснение)."""
        if p.frozen_at is not None:
            if action == "unfreeze":
                ok, msg = L.unfreeze(p, now, TZ)
                return "status", msg
            return "status", ""

        if action in ("menu", "career", "leisure"):
            return action, ""
        if action in ("status", "refresh"):
            return "status", ""

        if action == "nav_sleep":
            ok, msg = L.can_sleep(p)
            return ("sleep", "") if ok else ("status", msg)
        if action == "sleep":
            if value == "full":
                quanta = None
            elif value and value.isdigit() and int(value) in R.SLEEP_CHOICES_HOURS:
                quanta = int(value) * 60 // R.QUANTUM_MINUTES
            else:
                return "status", "Не удалось понять, сколько спать."
            return "status", L.start_sleep(p, quanta)[1]
        if action == "wake":
            ok, msg, evs = L.wake(p, TZ)
            events.extend(evs)
            return "status", msg

        if action in ("pay25", "pay50", "payall"):
            if p.debt <= 0:
                return "status", "У вас нет долга."
            if p.money <= 0:
                return "status", "У вас нет денег."
            share = {"pay25": 0.25, "pay50": 0.5, "payall": 1.0}[action]
            paid = L.pay_debt(p, math.ceil(p.debt * share))
            return "status", f"Погашено {money(paid)}."

        if action == "freeze":
            ok, msg = L.can_freeze(p, now, TZ)
            return ("freeze_confirm", "") if ok else ("status", msg)
        if action == "freeze_yes":
            ok, msg = L.freeze(p, now, TZ)
            return "status", msg

        return "status", ""

# ==================== Точка входа ====================

def setup(bot):
    """Точка входа игры: вызывается из main.py."""
    ctrl = WorkerController(bot, StorageRegistry())
    bot.add_view(PanelView(ctrl, ALL_SPECS))        # кнопки работают и после перезапуска бота

    @app_commands.command(name="work", description="Симулятор рабочего: открыть личную панель")
    @app_commands.guild_only()
    async def work(interaction: discord.Interaction):
        await ctrl.open_panel(interaction)

    bot.tree.add_command(work)