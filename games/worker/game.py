"""Контроллер симулятора рабочего: личная панель (скрытые сообщения), действия игрока,
рынок вакансий, магазины, досуг, больница, жильё."""
import logging
import math
import random

import discord
from discord import app_commands

from config import TZ
from core.locks import LockMap
from . import logic as L
from . import market as M
from . import rules as R
from .storage import StorageRegistry
from .texts import money, render
from .views import PanelView, build_specs, persistent_views

log = logging.getLogger("stacy.worker")

class WorkerController:
    def __init__(self, bot, registry: StorageRegistry):
        self.bot = bot
        self.registry = registry
        self.rng = random.Random()
        self.locks = LockMap()                      # по блокировке на (сервер, игрок)

    async def open_panel(self, interaction: discord.Interaction):
        await self.on_action(interaction, "menu", None, first=True)

    async def on_action(self, interaction: discord.Interaction, action: str, value=None, first: bool = False):
        if interaction.guild_id is None:
            await interaction.response.send_message("Игра работает только на сервере.", ephemeral=True)
            return
        uid, gid = interaction.user.id, interaction.guild_id
        async with self.locks[(gid, uid)]:
            # Внутри блока нет await: подключение не вытеснится из кэша, а клики не перемешаются
            store = self.registry.get(gid)
            now = self.bot.clock.now()
            p = store.get_player(uid) or L.new_player(uid, now)
            events = L.catch_up(p, now, TZ)
            try:
                screen, note = self._apply(store, p, action, value, now, events)
                ctx = self._context(store, screen, p, now)
                store.save_player(p)                # коммит: игрок и (при взятии) вакансия вместе
            except Exception:
                store.con.rollback()
                raise
        text = render(screen, p, now, TZ, events, note, **ctx)
        view = PanelView(self, build_specs(screen, p, **ctx))
        if first:
            await interaction.response.send_message(text, view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(content=text, view=view)

    def _context(self, store, screen: str, p: L.Player, now) -> dict:
        """Данные рынка вакансий для отрисовки экрана."""
        ts = int(now.timestamp())
        if screen == "jobs":
            return {"jobs": store.list_vacancies(ts)}
        if screen == "job":
            v = store.get_vacancy(p.view_vac) if p.view_vac else None
            return {"vac": v, "can": L.can_take(p, v, ts)[0]}
        return {}

    def _apply(self, store, p: L.Player, action: str, value, now, events: list) -> tuple:
        """Выполняет действие над игроком. Возвращает (экран, пояснение)."""
        ts = int(now.timestamp())
        if p.frozen_at is not None:
            if action == "unfreeze":
                ok, msg = L.unfreeze(p, now, TZ)
                return "status", msg
            return "status", ""

        # В магазине можно только покупать и уходить
        if p.venue and action not in ("status", "refresh", "buy", "venue_leave"):
            note = "" if action in ("market", "menu", "leisure") else "Сначала выйдите из магазина."
            return "venue", note

        if action in ("menu", "career", "leisure", "market", "ent", "housing"):
            return action, ""
        if action == "nav_hosp":
            return "hospital", ""
        if action in ("status", "refresh"):
            return "status", ""

        # ----- рынок (магазины) -----
        if action in ("go_food", "go_pharm", "go_super"):
            ok, msg = L.start_errand(p, action[3:])
            return ("status" if ok else "market"), msg
        if action == "buy":
            ok, msg = L.buy(p, value or "")
            return ("venue" if p.venue else "status"), msg
        if action == "venue_leave":
            ok, msg = L.leave_venue(p)
            return "status", msg

        # ----- досуг, больница, жильё -----
        if action == "ent_go":
            code, _, h = (value or "").partition(":")
            if not h.isdigit():
                return "ent", "Не удалось понять выбор."
            ok, msg = L.start_leisure(p, code, int(h))
            return ("status" if ok else "ent"), msg
        if action == "stop_leisure":
            ok, msg, evs = L.stop_leisure(p, TZ)
            events.extend(evs)
            return "status", msg
        if action == "course_go":
            ok, msg = L.start_course(p, value or "")
            return ("status" if ok else "hospital"), msg
        if action == "house_go":
            ok, msg = L.move_housing(p, value or "", now, TZ)
            return ("status" if ok else "housing"), msg

        # ----- рынок вакансий -----
        if action == "jobs":
            M.ensure_market(store, now, TZ, self.rng)
            p.view_vac = None
            return "jobs", ""
        if action == "job_pick":
            M.ensure_market(store, now, TZ, self.rng)
            v = store.get_vacancy(int(value)) if value and value.isdigit() else None
            if v is None or not v.alive(ts):
                p.view_vac = None
                return "jobs", "Эту вакансию уже забрали или она исчезла."
            p.view_vac = v.id
            return "job", ""
        if action == "take":
            M.ensure_market(store, now, TZ, self.rng)
            v = store.get_vacancy(p.view_vac) if p.view_vac else None
            ok, msg = L.can_take(p, v, ts)
            if not ok:
                gone = v is None or not v.alive(ts)
                if gone:
                    p.view_vac = None
                return ("jobs" if gone else "job"), msg
            if not store.claim_vacancy(v.id, p.uid, ts):
                p.view_vac = None
                return "jobs", "Эту вакансию только что забрали."
            if v.kind == "perm":
                return "myjob", L.sign_contract(p, v, now, TZ)
            return "status", L.start_job(p, v)

        # ----- постоянная работа -----
        if action == "myjob":
            return "myjob", ""
        if action == "shift_go":
            ok, msg = L.start_shift(p, now, TZ)
            return ("status" if ok else "myjob"), msg
        if action == "quit":
            if p.job is None:
                return "myjob", "У вас нет постоянной работы."
            if p.act and p.act.get("career"):
                return "myjob", "Сначала закончите смену."
            return "quit_confirm", ""
        if action == "quit_yes":
            ok, msg = L.quit_job(p, now)
            return "myjob", msg

        if action == "abort":
            if p.act and p.act.get("career"):
                return "abort_confirm", ""
            return "status", "Сейчас нет смены."
        if action == "abort_yes":
            ok, msg, evs = L.abort_shift(p, TZ)
            events.extend(evs)
            return "status", msg

        # ----- сон, долг, заморозка -----
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
    for view in persistent_views(ctrl):             # кнопки работают и после перезапуска бота
        bot.add_view(view)

    @app_commands.command(name="work", description="Симулятор рабочего: открыть личную панель")
    @app_commands.guild_only()
    async def work(interaction: discord.Interaction):
        await ctrl.open_panel(interaction)

    bot.tree.add_command(work)