"""Контроллер симулятора рабочего: личная панель (скрытые сообщения), действия игрока,
рынок вакансий, магазины, досуг, больница, жильё."""
import logging
import math
import random
from typing import Optional

import discord
from discord import app_commands

from config import TZ
from core.locks import LockMap
from core.errors import report_error
from . import logic as L
from . import market as M
from . import rules as R
from .console import console_work
from .storage import StorageRegistry
from .texts import money, render
from .views import ConfirmView, PanelView, build_specs, persistent_views
from .info import open_info

log = logging.getLogger("stacy.worker")

# Экраны-подтверждения показываются скрытым сообщением; «Да» запускает указанное действие
CONFIRM_YES = {"quit_confirm": "quit_yes", "abort_confirm": "abort_yes", "freeze_confirm": "freeze_yes"}

def panel_owner_id(message) -> Optional[int]:
    """Кто открыл панель командой /work. None - узнать не удалось (тогда клик пропускается)."""
    if message is None:
        return None
    meta = getattr(message, "interaction_metadata", None)
    user = getattr(meta, "user", None)
    if user is None:
        user = getattr(getattr(message, "interaction", None), "user", None)   # discord.py до 2.4
    return user.id if user is not None else None

class WorkerController:
    def __init__(self, bot, registry: StorageRegistry):
        self.bot = bot
        self.registry = registry
        self.rng = random.Random()
        self.locks = LockMap()                      # по блокировке на (сервер, игрок)

    async def open_panel(self, interaction: discord.Interaction):
        await self.on_action(interaction, "menu", None, first=True)

    async def on_action(self, interaction: discord.Interaction, action: str, value=None, first: bool = False):
        """Точка входа всех нажатий. Техническая ошибка показывается скрытым сообщением."""
        try:
            await self._handle(interaction, action, value, first)
        except Exception as e:
            await report_error(interaction, e, ephemeral=True)

    async def _handle(self, interaction: discord.Interaction, action: str, value, first: bool):
        if interaction.guild_id is None:
            await interaction.response.send_message("Игра работает только на сервере.", ephemeral=True)
            return
        if not first:                                   # панель публичная: чужие клики не принимаем
            owner = panel_owner_id(interaction.message)
            if owner is not None and owner != interaction.user.id:
                await interaction.response.send_message(
                    "Это панель другого игрока. Откройте свою командой /work.", ephemeral=True)
                return

        screen, text, specs, problem = await self._process(interaction, action, value)

        if screen in CONFIRM_YES:                       # подтверждение видит только сам игрок
            view = ConfirmView(self, CONFIRM_YES[screen], interaction.message)
            await interaction.response.send_message(text, view=view, ephemeral=True)
            view.origin = interaction
            return
        view = PanelView(self, specs)
        if first:
            await interaction.response.send_message(text, view=view)
        else:
            await interaction.response.edit_message(content=text, view=view)
        if problem:                                     # причина провала: только нажавшему
            await self._hint(interaction, problem)

    @staticmethod
    async def _hint(interaction: discord.Interaction, text: str):
        """Скрытое сообщение с причиной провала (видит только нажавший)."""
        try:
            await interaction.followup.send(f"⚠️ {text}", ephemeral=True)
        except discord.HTTPException:
            log.warning("Не удалось показать скрытую подсказку", exc_info=True)

    async def on_confirm(self, interaction: discord.Interaction, yes_action: str, panel):
        """Нажата «Да» в скрытом окне: действие выполняется, публичная панель обновляется.
        При провале скрытое окно превращается в сообщение с причиной."""
        screen, text, specs, problem = await self._process(interaction, yes_action, None)
        view = PanelView(self, specs)
        await interaction.response.edit_message(
            content=f"⚠️ {problem}" if problem else "✅ Готово.", view=None)
        try:
            await panel.edit(content=text, view=view)
        except (discord.HTTPException, AttributeError):
            await interaction.followup.send(text, view=view, ephemeral=True)   # панель недоступна: покажем скрыто

    async def _process(self, interaction: discord.Interaction, action: str, value):
        """Выполняет действие под блокировкой."""
        uid, gid = interaction.user.id, interaction.guild_id
        async with self.locks[(gid, uid)]:
            # Внутри блока нет await: подключение не вытеснится из кэша, а клики не перемешаются
            store = self.registry.get(gid)
            now = self.bot.clock.now()
            p = store.get_player(uid) or L.new_player(uid, now)
            events = L.catch_up(p, now, TZ)
            try:
                screen, note, ok = self._apply(store, p, action, value, now, events)
                self._track_screen(p, screen)
                ctx = self._context(store, screen, p, now)
                if screen in CONFIRM_YES:
                    store.con.rollback()    # окно подтверждения ничего не меняет: события не «съедаем»
                    events = []
                else:
                    store.save_player(p)    # коммит: игрок и (при взятии) вакансия вместе
            except Exception:
                store.con.rollback()
                raise
        # Успех: пояснение остаётся в панели. Провал: пояснение уходит скрытым сообщением.
        problem = None if ok else (note or "Не получилось выполнить действие.")
        text = render(screen, p, now, TZ, events, note if ok else "", **ctx)
        return screen, text, build_specs(screen, p, **ctx), problem

    @staticmethod
    def _track_screen(p: L.Player, screen: str):
        """Запоминает, какой постраничный список открыт."""
        if screen in L.PAGED_SCREENS:
            if p.list_screen != screen:
                p.page = 0
            p.list_screen = screen
        elif screen not in ("pick", "buy_confirm"):
            p.list_screen = None

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
        """Выполняет действие над игроком."""
        ts = int(now.timestamp())
        if p.frozen_at is not None:
            if action == "unfreeze":
                ok, msg = L.unfreeze(p, now, TZ)
                return "status", msg, ok
            if action in ("status", "refresh", "menu", "menu_refresh"):
                return "status", "", True
            return "status", "Игра заморожена: сначала разморозьте её.", False

        # В магазине можно только покупать, листать товары и уходить
        if p.venue and action not in ("status", "refresh", "buy", "buy_qty", "buy_do", "buy_back",
                                      "venue_leave", "page_prev", "page_next"):
            if action in ("market", "menu", "leisure", "menu_refresh"):
                return "venue", "", True
            return "venue", "Сначала выйдите из магазина.", False

        if action in ("menu", "career", "leisure", "market", "ent", "housing", "edu", "kitchen"):
            L.clear_pick(p)
            return action, "", True
        if action == "nav_hosp":
            L.clear_pick(p)
            return "hospital", "", True
        if action == "menu_refresh":
            return "menu", "", True
        if action in ("status", "refresh"):
            return "status", "", True

        # ----- листание страниц -----
        if action in ("page_prev", "page_next"):
            scr = p.list_screen
            if scr not in L.PAGED_SCREENS:
                return "status", "", True
            _chunk, page, pages = L.page_slice(L.screen_items(p, scr), scr, p.page)
            p.page = max(0, min(pages - 1, page + (1 if action == "page_next" else -1)))
            return scr, "", True

        # ----- рынок (магазины) -----
        if action in ("go_food", "go_pharm", "go_super"):
            ok, msg = L.start_errand(p, action[3:])
            return ("status" if ok else "market"), msg, ok
        if action == "buy":                                 # шаг 1: выбор товара, деньги не трогаем
            ok, msg = L.pick_good(p, value or "")
            if ok:
                return "buy_confirm", msg, True
            return ("venue" if p.venue else "status"), msg, False
        if action == "buy_qty":                             # количество меняется в окне подтверждения
            p.buy_qty = value if value in ("1", "2", "fill") else "1"
            if p.venue and p.buy_item:
                return "buy_confirm", "", True
            return ("venue" if p.venue else "status"), "", True
        if action == "buy_do":                              # шаг 2: «Купить»
            ok, msg = L.buy_selected(p)
            if ok:
                return "venue", msg, True
            if p.venue and p.buy_item:
                return "buy_confirm", msg, False            # причина уйдёт скрытым сообщением, окно останется
            return ("venue" if p.venue else "status"), msg, False
        if action == "buy_back":                            # отмена: ничего не куплено, деньги целы
            p.buy_item = None
            return ("venue" if p.venue else "status"), "", True
        if action == "venue_leave":
            ok, msg = L.leave_venue(p)
            return "status", msg, ok

        # ----- выбор с подтверждением: досуг, учёба, курсы, готовка, переезд -----
        if action in ("ent_go", "edu_go"):                  # значение: «код» (или старое «код:часы»)
            kind = "ent" if action == "ent_go" else "edu"
            code, _, h = (value or "").partition(":")
            ok, msg = L.set_pick(p, kind, code, int(h) if h.isdigit() else 0)
            return ("pick" if ok else L.PICK_BACK[kind]), msg, ok
        if action in ("course_go", "cook_go", "house_go"):
            kind = {"course_go": "course", "cook_go": "cook", "house_go": "house"}[action]
            ok, msg = L.set_pick(p, kind, value or "")
            return ("pick" if ok else L.PICK_BACK[kind]), msg, ok
        if action == "dur_set":
            if not p.pick_kind:
                return "leisure", "Сначала выберите вариант.", False
            if value and value.isdigit():
                L.set_pick_hours(p, int(value))
            return "pick", "", True
        if action == "pick_do":
            if not p.pick_kind:
                return "leisure", "Сначала выберите вариант.", False
            ok, msg = L.pick_start(p, now, TZ)
            if ok:
                L.clear_pick(p)
                return "status", msg, True
            return "pick", msg, False                       # окно остаётся, причина - скрытым сообщением
        if action == "pick_back":
            kind = p.pick_kind
            L.clear_pick(p)
            return L.PICK_BACK.get(kind, "leisure"), "", True

        # ----- досуг и учёба: завершение -----
        if action == "stop_leisure":
            ok, msg, evs = L.stop_leisure(p, TZ)
            events.extend(evs)
            return "status", msg, ok
        if action == "stop_study":
            ok, msg, evs = L.stop_study(p, TZ)
            events.extend(evs)
            return "status", msg, ok

        # ----- рынок вакансий -----
        if action == "jobs":
            M.ensure_market(store, now, TZ, self.rng)
            p.view_vac = None
            return "jobs", "", True
        if action == "job_pick":
            M.ensure_market(store, now, TZ, self.rng)
            v = store.get_vacancy(int(value)) if value and value.isdigit() else None
            if v is None or not v.alive(ts):
                p.view_vac = None
                return "jobs", "Эту вакансию уже забрали или она исчезла.", False
            p.view_vac = v.id
            return "job", "", True
        if action == "take":
            M.ensure_market(store, now, TZ, self.rng)
            v = store.get_vacancy(p.view_vac) if p.view_vac else None
            ok, msg = L.can_take(p, v, ts)
            if not ok:
                gone = v is None or not v.alive(ts)
                if gone:
                    p.view_vac = None
                return ("jobs" if gone else "job"), msg, False
            if not store.claim_vacancy(v.id, p.uid, ts):
                p.view_vac = None
                return "jobs", "Эту вакансию только что забрали.", False
            if v.kind == "perm":
                return "myjob", L.sign_contract(p, v, now, TZ), True
            return "status", L.start_job(p, v), True

        # ----- постоянная работа -----
        if action == "myjob":
            return "myjob", "", True
        if action == "shift_go":
            ok, msg = L.start_shift(p, now, TZ)
            return ("status" if ok else "myjob"), msg, ok
        if action == "quit":
            if p.job is None:
                return "myjob", "У вас нет постоянной работы.", False
            if p.act and p.act.get("career"):
                return "myjob", "Сначала закончите смену.", False
            return "quit_confirm", "", True
        if action == "quit_yes":
            ok, msg = L.quit_job(p, now)
            return "myjob", msg, ok

        if action == "abort":
            if p.act and p.act.get("career"):
                return "abort_confirm", "", True
            return "status", "Сейчас нет смены.", False
        if action == "abort_yes":
            ok, msg, evs = L.abort_shift(p, TZ)
            events.extend(evs)
            return "status", msg, ok

        # ----- сон, долг, заморозка -----
        if action == "nav_sleep":
            ok, msg = L.can_sleep(p)
            return ("sleep", "", True) if ok else ("status", msg, False)
        if action == "sleep":
            if value == "full":
                quanta = None
            elif value and value.isdigit() and int(value) in R.SLEEP_CHOICES_HOURS:
                quanta = int(value) * 60 // R.QUANTUM_MINUTES
            else:
                return "status", "Не удалось понять, сколько спать.", False
            ok, msg = L.start_sleep(p, quanta)
            return "status", msg, ok
        if action == "wake":
            ok, msg, evs = L.wake(p, TZ)
            events.extend(evs)
            return "status", msg, ok

        if action in ("pay25", "pay50", "payall"):
            if p.debt <= 0:
                return "status", "У вас нет долга.", False
            if p.money <= 0:
                return "status", "У вас нет денег.", False
            share = {"pay25": 0.25, "pay50": 0.5, "payall": 1.0}[action]
            paid = L.pay_debt(p, math.ceil(p.debt * share))
            return "status", f"Погашено {money(paid)}.", True

        if action == "freeze":
            ok, msg = L.can_freeze(p, now, TZ)
            return ("freeze_confirm", "", True) if ok else ("status", msg, False)
        if action == "freeze_yes":
            ok, msg = L.freeze(p, now, TZ)
            return "status", msg, ok

        return "status", "", True

# ==================== Точка входа ====================

def setup(bot):
    """Точка входа игры: вызывается из main.py."""
    ctrl = WorkerController(bot, StorageRegistry())
    for view in persistent_views(ctrl):             # кнопки работают и после перезапуска бота
        bot.add_view(view)
    bot.console_commands["-work"] = console_work(ctrl)

    @app_commands.command(name="work", description="Симулятор рабочего: открыть свою панель")
    @app_commands.guild_only()
    async def work(interaction: discord.Interaction):
        await ctrl.open_panel(interaction)

    @app_commands.command(name="workinfo", description="Симулятор рабочего: подробные правила и подсказки")
    async def workinfo(interaction: discord.Interaction):
        await open_info(interaction)

    bot.tree.add_command(work)
    bot.tree.add_command(workinfo)