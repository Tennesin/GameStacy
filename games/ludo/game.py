"""Контроллер Ludo: лобби, бросок, выбор фигуры, таймеры, боты, восстановление после перезапуска."""
import asyncio
import io
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands

from config import DEV_ID
from core.avatars import avatar_url, load_avatars
from core.bump import should_bump
from core.clock import fmt_duration
from core.errors import TECH_ERROR_TEXT, report_error
from core.full_games import FullGame
from core.locks import LockMap
from core.players import BOT_IDS, esc
from core.scope import ignore, in_scope
from .logic import Match, Player, effective_bots
from .render import make_scene, render_board_async
from .rules import (
    LUDO_BONUS_ON_CAPTURE, LUDO_BONUS_ON_FINISH, LUDO_BOT_DELAY_SECONDS, LUDO_EXIT_ROLL, LUDO_FINISH,
    LUDO_LOBBY_SECONDS, LUDO_LOG_LINES, LUDO_MAX_BOTS, LUDO_MAX_PLAYERS, LUDO_MAX_SIXES_IN_ROW,
    LUDO_MIN_PLAYERS, LUDO_PICK_SECONDS, LUDO_PIECES, LUDO_ROLL_SECONDS, LUDO_SEAT_EMOJI,
    LUDO_TIMEOUTS_TO_LEAVE,
)
from .storage import Storage
from .texts import describe
from .views import ConfirmLeaveView, LobbyView, PlayView

log = logging.getLogger("stacy.ludo")

NO_PINGS = discord.AllowedMentions.none()
ROLL_DEBOUNCE_SECONDS = 1.5     # повторный бросок от того же человека раньше этого срока считается двойным кликом
MEDALS = ["🥇", "🥈", "🥉", "4️⃣"]
BOT_NAMES = tuple(f"Бот {i + 1}" for i in range(LUDO_MAX_BOTS))

_BONUS = (["шестёрка"]
          + (["взятие чужой фигуры"] if LUDO_BONUS_ON_CAPTURE else [])
          + (["приход фигуры в центр"] if LUDO_BONUS_ON_FINISH else []))

RULES_TEXT = (
    f"**Правила.** Бросайте кубик по очереди (на бросок {LUDO_ROLL_SECONDS} с, на выбор фигуры "
    f"{LUDO_PICK_SECONDS} с). Фигура выходит из базы только на {LUDO_EXIT_ROLL}. "
    f"Ещё один бросок дают: {', '.join(_BONUS)}; на {LUDO_MAX_SIXES_IN_ROW}-й шестёрке подряд ход сгорает.\n"
    "Чужую фигуру берёте, остановившись на её клетке: она возвращается в базу. "
    "Безопасны клетки со звездой и собственная стартовая клетка. "
    "Две свои фигуры в одной клетке — блок: его нельзя ни пройти, ни занять. "
    "В центр можно попасть только точным броском.\n"
    f"Пропущенные ходы подряд ({LUDO_TIMEOUTS_TO_LEAVE}) — вы выбываете. "
    f"Кто привёл все {LUDO_PIECES} фигуры в центр, занимает очередное место. Партия заканчивается, "
    "когда на поле остаётся один игрок или не остаётся ни одного человека: "
    "остальные места определяются по продвижению фигур.")

@dataclass
class Runtime:
    """То, что нужно идущей партии в памяти (всё остальное лежит в БД)."""
    game_id: int
    match: Match
    names: dict                         # pid -> имя
    avatars: dict                       # pid -> ссылка на аватарку
    channel_id: int
    message_id: int
    seq: int = 0                        # номер «хода»: старые таймеры по нему узнают, что устарели
    deadline: Optional[datetime] = None
    task: Optional[asyncio.Task] = None
    log: list = field(default_factory=list)
    ping_id: Optional[int] = None       # сообщение «ваш ход» с настоящим тегом
    ping_pid: Optional[int] = None      # кому оно адресовано
    started: Optional[datetime] = None  # когда партия началась
    last_action: float = 0.0            # когда человек в последний раз бросил кубик (защита от двойного клика)

class LudoController:
    def __init__(self, bot, storage: Storage):
        self.bot = bot
        self.db = storage
        self.locks = LockMap()
        self.rt: dict = {}
        self.lobby_timers: dict = {}
        self.bg: set = set()

    # ==================== Запуск ====================

    async def new_lobby(self, interaction: discord.Interaction):
        """Слеш-команда /ludo: хост — тот, кто её вызвал."""
        user = interaction.user
        game_id = self.db.create_game(interaction.guild_id, interaction.channel_id, user.id)
        self.db.add_player(game_id, user.id, user.display_name, avatar_url(user))
        g = self.db.get(game_id)
        try:
            await interaction.response.send_message(self._lobby_text(g), view=self._lobby_view(g),
                                                    allowed_mentions=NO_PINGS)
            msg = await interaction.original_response()
        except discord.HTTPException:
            self.db.abort(game_id, "error")
            raise
        self.db.set_message(game_id, msg.id)
        self._schedule_expiry(game_id, LUDO_LOBBY_SECONDS)

    async def launch(self, channel, home_id=None) -> int:
        """Запуск из консоли (-game run ludo ...). Хоста нет: им станет первый, кто нажмёт «Присоединиться»."""
        game_id = self.db.create_game(channel.guild.id, channel.id, 0, home_id)
        g = self.db.get(game_id)
        try:
            msg = await channel.send(self._lobby_text(g), view=self._lobby_view(g), allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            self.db.abort(game_id, "error")
            raise
        self.db.set_message(game_id, msg.id)
        self._schedule_expiry(game_id, LUDO_LOBBY_SECONDS)
        return game_id

    # ==================== Восстановление после перезапуска ====================

    def restore_views(self):
        for g in self.db.active():
            gid = g["game_id"]
            if g["status"] == "lobby":
                self.bot.add_view(self._lobby_view(g), message_id=g["message_id"])
                idle = (self.bot.clock.now() - datetime.fromisoformat(g["updated_utc"])).total_seconds()
                self._schedule_expiry(gid, max(0.0, LUDO_LOBBY_SECONDS - idle))
                continue
            rt = self._load_runtime(g)
            if rt is None:
                self.db.abort(gid, "error")
                continue
            self.rt[gid] = rt
            self.bot.add_view(self._play_view(rt), message_id=g["message_id"])
            self._spawn(self._resume(rt))

    def _load_runtime(self, g) -> Optional[Runtime]:
        try:
            state = self.db.state_of(g)
            if state is None:
                return None
            match = Match.restore(state)                        # фаза pick и число на кубике восстанавливаются
            rows = self.db.players(g["game_id"])
        except Exception:
            log.exception("Не удалось восстановить партию #%s", g["game_id"])
            return None
        rt = Runtime(g["game_id"], match,
                     {r["pid"]: r["name"] for r in rows}, {r["pid"]: r["avatar_url"] for r in rows},
                     g["channel_id"], g["message_id"])
        rt.ping_id = g["ping_id"]
        rt.started = datetime.fromisoformat(g["started_utc"]) if g["started_utc"] else None
        return rt

    async def _resume(self, rt: Runtime):
        """После перезапуска: таймер ходящего (в том числе таймер выбора фигуры) запускается заново."""
        await self.bot.wait_until_ready()
        try:
            async with self.locks[rt.game_id]:
                await self._drop_ping(rt)
                rt.log.append("🔄 Бот перезапускался: игра продолжается.")
                await self._after(rt, [])
        except Exception:
            log.exception("Сбой восстановления партии #%s", rt.game_id)
            await self._fail_silent(rt)

    # ==================== Лобби ====================

    def _lobby_view(self, g) -> LobbyView:
        return LobbyView(self, g["game_id"], g)

    def _lobby_row(self, game_id: int):
        g = self.db.get(game_id)
        return g if g is not None and g["status"] == "lobby" else None

    def _lobby_text(self, g) -> str:
        rows = self.db.players(g["game_id"])
        n_bots = effective_bots(len(rows), g["bots"]) if rows else g["bots"]
        host = f"<@{g['host_id']}>" if g["host_id"] else "станет первый, кто нажмёт «Присоединиться»"
        people = ", ".join(esc(r["name"]) for r in rows) or "пока никого"
        bots = ", ".join(f"🤖 {BOT_NAMES[i]}" for i in range(n_bots)) or "нет"
        ends = int(self.bot.clock.now().timestamp()) + LUDO_LOBBY_SECONDS
        lines = [
            f"🎲 **Ludo** · партия #{g['game_id']}",
            f"Хост: {host}",
            f"Игроки: {people}",
            f"Боты: {bots} (мест занято {len(rows) + n_bots} из {LUDO_MAX_PLAYERS})",
            "ℹ️ Число ботов выбирается для игры в одиночку: каждый присоединившийся человек вытесняет одного бота. "
            "Цвета и очерёдность определятся при старте.",
        ]
        if g["home_channel_id"]:
            lines.append(f"📍 Играть можно только в <#{g['home_channel_id']}> и его ветках.")
        lines += ["", RULES_TEXT, "", f"Настройки меняет хост. Набор закроется <t:{ends}:R>."]
        return "\n".join(lines)[:1990]

    async def _lobby_refresh(self, interaction: discord.Interaction, game_id: int):
        g = self.db.get(game_id)
        content, view = self._lobby_text(g), self._lobby_view(g)
        await interaction.response.defer()                   # подтверждаем сразу: should_bump ходит в Discord

        sent_new = False
        if g["message_id"] and await should_bump(self.bot, g["channel_id"], g["message_id"]):
            chan = self.bot.get_partial_messageable(g["channel_id"])
            try:
                new = await chan.send(content, view=view, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                log.exception("Не удалось поднять лобби #%s, правлю старое", game_id)
            else:
                old = g["message_id"]
                self.db.set_message(game_id, new.id)
                try:
                    await chan.get_partial_message(old).delete()
                except discord.HTTPException:
                    pass
                sent_new = True
        if not sent_new:
            await interaction.edit_original_response(content=content, view=view,
                                                     allowed_mentions=NO_PINGS)
        self._schedule_expiry(game_id, LUDO_LOBBY_SECONDS)

    async def _guard(self, interaction: discord.Interaction, g) -> bool:
        """False — взаимодействие пришло не из канала игры (и не из его ветки): оно уже погашено."""
        home = g["home_channel_id"]
        if in_scope(interaction, home):
            return True
        log.info("Партия #%s: взаимодействие от %s вне зоны игры (канал %s) проигнорировано",
                 g["game_id"], interaction.user.id, interaction.channel_id)
        await ignore(interaction, home)
        return False

    async def _host_only(self, interaction: discord.Interaction, g) -> bool:
        if not g["host_id"]:
            await interaction.response.send_message(
                "Хоста ещё нет: им станет первый, кто нажмёт «Присоединиться».", ephemeral=True)
            return False
        if interaction.user.id != g["host_id"]:
            await interaction.response.send_message(
                f"Настройки меняет только хост: <@{g['host_id']}>.", ephemeral=True)
            return False
        return True

    async def lobby_bots(self, interaction: discord.Interaction, game_id: int, value):
        async with self.locks[game_id]:
            g = self._lobby_row(game_id)
            if g is None:
                await interaction.response.send_message("Этот набор уже закрыт.", ephemeral=True)
                return
            if not await self._guard(interaction, g) or not await self._host_only(interaction, g):
                return
            self.db.update_settings(game_id, bots=int(value))
            await self._lobby_refresh(interaction, game_id)

    async def lobby_join(self, interaction: discord.Interaction, game_id: int):
        async with self.locks[game_id]:
            g = self._lobby_row(game_id)
            if g is None:
                await interaction.response.send_message("Этот набор уже закрыт.", ephemeral=True)
                return
            if not await self._guard(interaction, g):
                return
            user, rows = interaction.user, self.db.players(game_id)
            if any(r["pid"] == user.id for r in rows):
                await interaction.response.send_message("Вы уже за столом.", ephemeral=True)
                return
            if len(rows) >= LUDO_MAX_PLAYERS:
                await interaction.response.send_message("Все места заняты.", ephemeral=True)
                return
            self.db.add_player(game_id, user.id, user.display_name, avatar_url(user))
            if not g["host_id"]:
                self.db.set_host(game_id, user.id)
            await self._lobby_refresh(interaction, game_id)

    async def lobby_leave(self, interaction: discord.Interaction, game_id: int):
        """Нажата «Выйти» в лобби: сначала спрашиваем подтверждение (скрытым сообщением)."""
        g = self._lobby_row(game_id)
        if g is None:
            await interaction.response.send_message("Этот набор уже закрыт.", ephemeral=True)
            return
        if not await self._guard(interaction, g):
            return
        if not any(r["pid"] == interaction.user.id for r in self.db.players(game_id)):
            await interaction.response.send_message("Вас нет в списке игроков.", ephemeral=True)
            return
        view = ConfirmLeaveView(self, game_id, lobby=True)
        await interaction.response.send_message(
            "Вы точно хотите выйти из набора на партию?", view=view, ephemeral=True)
        view.origin = interaction

    async def lobby_leave_confirm(self, interaction: discord.Interaction, game_id: int):
        """Нажата «Да» в окне подтверждения. Interaction относится к скрытому окну,
        поэтому сообщение лобби правим через канал."""
        async with self.locks[game_id]:
            g = self._lobby_row(game_id)
            if g is None:
                await interaction.response.edit_message(content="Этот набор уже закрыт.", view=None)
                return
            uid = interaction.user.id
            if not self.db.remove_player(game_id, uid):
                await interaction.response.edit_message(content="Вас уже нет в списке игроков.", view=None)
                return
            await interaction.response.edit_message(content="Вы вышли из набора.", view=None)

            rows = self.db.players(game_id)
            lobby_msg = self.bot.get_partial_messageable(g["channel_id"]).get_partial_message(g["message_id"])
            try:
                if not rows:
                    self.db.abort(game_id, "closed")
                    self._cancel_lobby_timer(game_id)
                    await lobby_msg.edit(
                        content="Набор закрыт: все игроки вышли. Для новой партии используйте /ludo.",
                        view=None)
                else:
                    if g["host_id"] == uid:
                        self.db.set_host(game_id, rows[0]["pid"])
                    fresh = self.db.get(game_id)
                    await lobby_msg.edit(content=self._lobby_text(fresh), view=self._lobby_view(fresh),
                                         allowed_mentions=NO_PINGS)
                    self._schedule_expiry(game_id, LUDO_LOBBY_SECONDS)
            except discord.HTTPException:
                log.exception("Не удалось обновить лобби #%s после выхода игрока", game_id)

    async def lobby_play(self, interaction: discord.Interaction, game_id: int):
        async with self.locks[game_id]:
            g = self._lobby_row(game_id)
            if g is None:
                await interaction.response.send_message("Этот набор уже закрыт.", ephemeral=True)
                return
            if not await self._guard(interaction, g) or not await self._host_only(interaction, g):
                return
            humans = self.db.players(game_id)
            n_bots = effective_bots(len(humans), g["bots"])
            if len(humans) + n_bots < LUDO_MIN_PLAYERS:
                await interaction.response.send_message("Для игры нужно минимум два участника.", ephemeral=True)
                return

            await interaction.response.defer()
            self._cancel_lobby_timer(game_id)
            bots = [(BOT_IDS[i], BOT_NAMES[i]) for i in range(n_bots)]
            players = ([Player(r["pid"], r["name"]) for r in humans]
                       + [Player(pid, name, True) for pid, name in bots])
            random.shuffle(players)                                # цвета (углы) достаются случайно
            match = Match(players)                                 # места и первый ход назначает движок
            events = match.start()
            started = self.db.start(game_id, bots, match.snapshot(), None)

            rt = Runtime(game_id, match,
                         {r["pid"]: r["name"] for r in humans} | dict(bots),
                         {r["pid"]: r["avatar_url"] for r in humans},
                         g["channel_id"], g["message_id"])
            rt.started = datetime.fromisoformat(started)
            rt.log.append("🎬 Игра началась!")
            rt.log.append(f"🎲 Первым ходит {self._label(rt, match.current_player.pid)}")
            self.rt[game_id] = rt
            await self._after(rt, events)

    # ---- таймер набора ----

    def _cancel_lobby_timer(self, game_id: int):
        t = self.lobby_timers.pop(game_id, None)
        if t is not None and t is not asyncio.current_task():
            t.cancel()

    def _schedule_expiry(self, game_id: int, delay: float):
        self._cancel_lobby_timer(game_id)
        self.lobby_timers[game_id] = asyncio.create_task(self._expire(game_id, delay))

    async def _expire(self, game_id: int, delay: float):
        try:
            await asyncio.sleep(delay)
            async with self.locks[game_id]:
                g = self.db.get(game_id)
                if g is None or g["status"] != "lobby":
                    return
                self.db.abort(game_id, "timeout")
                chan = self.bot.get_partial_messageable(g["channel_id"])
                try:
                    await chan.get_partial_message(g["message_id"]).edit(
                        content="⌛ Время ожидания вышло: набор закрыт. Для новой партии используйте /ludo.",
                        view=None)
                except discord.HTTPException:
                    log.exception("Не удалось обновить сообщение закрытого набора #%s", game_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Сбой таймера набора #%s", game_id)
        finally:
            if self.lobby_timers.get(game_id) is asyncio.current_task():
                self.lobby_timers.pop(game_id, None)

    # ==================== Идущая партия: действия игроков ====================

    @staticmethod
    async def _reply(interaction: discord.Interaction, text: str):
        """Скрытый ответ игроку: работает и до, и после defer()."""
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def _playing(self, interaction: discord.Interaction, game_id: int):
        """(Runtime, строка БД) или None, если партия закрыта / взаимодействие вне зоны игры."""
        rt = self.rt.get(game_id)
        g = self.db.get(game_id)
        if rt is None or g is None or g["status"] != "playing":
            await self._reply(interaction, "Партия уже завершена.")
            return None
        if not await self._guard(interaction, g):
            return None
        return rt, g

    async def roll(self, interaction: discord.Interaction, game_id: int):
        await interaction.response.defer()
        async with self.locks[game_id]:
            got = await self._playing(interaction, game_id)
            if got is None:
                return
            rt = got[0]
            m, uid = rt.match, interaction.user.id
            p = m.get(uid)
            if p is None:
                await self._reply(interaction, "Вы не участвуете в этой партии.")
            elif p.left:
                await self._reply(interaction, "Вы вышли из этой партии.")
            elif p.finished:
                await self._reply(interaction, "Все ваши фигуры уже в центре.")
            elif m.current_player.pid != uid:
                await self._reply(
                    interaction, f"Сейчас не ваш ход: ходит {self._label(rt, m.current_player.pid)}.")
            elif m.phase != "roll":
                await self._reply(interaction, "Сначала выберите фигуру.")
            elif time.monotonic() - rt.last_action < ROLL_DEBOUNCE_SECONDS:
                await self._reply(interaction, "Кубик только что был брошен. Подождите секунду.")
            else:
                await self._after(rt, m.roll())
                rt.last_action = time.monotonic()

    async def pick(self, interaction: discord.Interaction, game_id: int, piece: int):
        await interaction.response.defer()
        async with self.locks[game_id]:
            got = await self._playing(interaction, game_id)
            if got is None:
                return
            rt = got[0]
            m, uid = rt.match, interaction.user.id
            p = m.get(uid)
            if p is None:
                await self._reply(interaction, "Вы не участвуете в этой партии.")
            elif p.left:
                await self._reply(interaction, "Вы вышли из этой партии.")
            elif m.current_player.pid != uid:
                await self._reply(
                    interaction, f"Сейчас не ваш ход: ходит {self._label(rt, m.current_player.pid)}.")
            elif m.phase != "pick":
                await self._reply(interaction, "Сейчас нужно бросить кубик.")
            elif piece not in {o.piece for o in m.options()}:
                await self._reply(interaction, "Этой фигурой сейчас ходить нельзя: выберите другую.")
            else:
                await self._after(rt, m.choose(piece))

    async def play_leave(self, interaction: discord.Interaction, game_id: int):
        """Нажата «Выйти» под табло: сначала спрашиваем подтверждение (скрытым сообщением)."""
        got = await self._playing(interaction, game_id)
        if got is None:
            return
        rt = got[0]
        p = rt.match.get(interaction.user.id)
        if p is None or not p.on_map:
            await interaction.response.send_message("Вы не участвуете в игре.", ephemeral=True)
            return
        view = ConfirmLeaveView(self, game_id, lobby=False)
        await interaction.response.send_message(
            "Вы точно хотите выйти из партии? Вернуться в неё потом не получится.",
            view=view, ephemeral=True)
        view.origin = interaction

    async def play_leave_confirm(self, interaction: discord.Interaction, game_id: int):
        """Нажата «Да» в окне подтверждения: реальный выход из партии."""
        await interaction.response.defer()
        async with self.locks[game_id]:
            rt, g = self.rt.get(game_id), self.db.get(game_id)
            if rt is None or g is None or g["status"] != "playing":
                await interaction.edit_original_response(content="Партия уже завершена.", view=None)
                return
            p = rt.match.get(interaction.user.id)
            if p is None or not p.on_map:
                await interaction.edit_original_response(content="Вы уже не участвуете в игре.", view=None)
                return
            await interaction.edit_original_response(content="🚪 Вы вышли из партии.", view=None)
            events = rt.match.leave(p.pid)
            await self._after(rt, events, rearm=any(e.kind == "turn" for e in events))

    # ==================== Идущая партия: ядро ====================

    async def _after(self, rt: Runtime, events: list, rearm: bool = True, bump: bool = False):
        """После любого изменения партии (вызывать под блокировкой): лог, таймер, сохранение, табло, пинг хода."""
        self._log(rt, events)
        m = rt.match
        if not bump:
            bump = await should_bump(self.bot, rt.channel_id, rt.message_id)
        if m.over:
            try:
                self._cancel(rt)
                rt.deadline = None
                self.db.finish(rt.game_id, m.snapshot())
                await self._drop_ping(rt)
                await self._publish(rt, bump=bump)          # табло: итоговая картинка и расстановка
                await self._send_results(rt)                # итоги: отдельным сообщением
            finally:
                self.rt.pop(rt.game_id, None)
                self.locks.pop(rt.game_id, None)
            return
        if rearm:
            self._arm(rt)
        self._save(rt)
        await self._publish(rt, bump=bump)
        await self._sync_ping(rt, reset=bump)

    def _save(self, rt: Runtime):
        self.db.save_state(rt.game_id, rt.match.snapshot(),
                           rt.deadline.isoformat() if rt.deadline else None)

    def _cancel(self, rt: Runtime):
        t, rt.task = rt.task, None
        if t is not None and t is not asyncio.current_task():
            t.cancel()

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self.bg.add(t)
        t.add_done_callback(self.bg.discard)
        return t

    async def purge_loop(self):
        """Раз в сутки удаляет завершённые и закрытые партии старше 30 дней."""
        await self.bot.wait_until_ready()
        while True:
            try:
                removed = self.db.purge(30)
                if removed:
                    log.info("Ludo: удалено старых партий: %d", removed)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Сбой уборки старых партий")
            await asyncio.sleep(86400)

    def _arm(self, rt: Runtime):
        """Таймер хода. Человек: на бросок LUDO_ROLL_SECONDS, на выбор фигуры LUDO_PICK_SECONDS.
        Бот: короткая пауза и автоматическое действие (бросок или выбор)."""
        self._cancel(rt)
        rt.seq += 1
        m = rt.match
        if m.over:
            rt.deadline = None
            return
        p = m.current_player
        if p.is_bot:
            delay, rt.deadline = LUDO_BOT_DELAY_SECONDS, None
        else:
            delay = LUDO_ROLL_SECONDS if m.phase == "roll" else LUDO_PICK_SECONDS
            rt.deadline = self.bot.clock.now() + timedelta(seconds=delay)
        rt.task = asyncio.create_task(self._timer(rt, rt.seq, delay))

    async def _timer(self, rt: Runtime, seq: int, delay: float):
        try:
            await asyncio.sleep(delay)
            async with self.locks[rt.game_id]:
                m = rt.match
                if seq != rt.seq or m.over:
                    return                                         # за это время ход уже сделан
                p = m.current_player
                if p.is_bot:
                    events = m.roll() if m.phase == "roll" else m.bot_pick()
                else:
                    events = m.on_timeout()                        # пропуск броска или автовыбор фигуры
                await self._after(rt, events)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Сбой таймера партии #%s", rt.game_id)
            await self._fail_silent(rt)

    # ==================== Табло ====================

    def _play_view(self, rt: Runtime) -> Optional[PlayView]:
        """Кнопки под табло: в ход человека - кубик или выбор фигуры; в ход бота кубик выключен."""
        m = rt.match
        if m.over:
            return None
        cur = m.current_player
        if cur.is_bot:
            return PlayView(self, rt.game_id, locked=True)
        if m.phase == "pick":
            return PlayView(self, rt.game_id, options=m.options())
        return PlayView(self, rt.game_id, turn_name=str(rt.names.get(cur.pid, "")))

    async def _drop_ping(self, rt: Runtime):
        mid, rt.ping_id, rt.ping_pid = rt.ping_id, None, None
        if not mid:
            return
        self.db.set_ping(rt.game_id, None)
        try:
            await self.bot.get_partial_messageable(rt.channel_id).get_partial_message(mid).delete()
        except discord.HTTPException:
            pass

    async def _sync_ping(self, rt: Runtime, reset: bool = False):
        """Отдельное сообщение с настоящим тегом, когда ход переходит к человеку.
        Удаляется, когда ход ушёл дальше. reset=True: прислать заново (табло только что поднялось вниз канала)."""
        m = rt.match
        want = None
        if not m.over and not m.current_player.is_bot:
            want = m.current_player.pid
        if reset or want != rt.ping_pid:
            await self._drop_ping(rt)
        if want is None or rt.ping_pid == want:
            return
        try:
            msg = await self.bot.get_partial_messageable(rt.channel_id).send(
                f"🎲 <@{want}>, ваш ход! Бросьте кубик (на бросок {LUDO_ROLL_SECONDS} с) "
                f"или выберите фигуру (на выбор {LUDO_PICK_SECONDS} с).",
                allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=want)]))
            rt.ping_id, rt.ping_pid = msg.id, want
            self.db.set_ping(rt.game_id, msg.id)
        except discord.HTTPException:
            log.exception("Не удалось прислать сообщение о ходе (партия #%s)", rt.game_id)

    def _label(self, rt: Runtime, pid: int) -> str:
        """Цветной значок (цвет равен месту) и имя."""
        p = rt.match.get(pid)
        emoji = LUDO_SEAT_EMOJI[p.seat % len(LUDO_SEAT_EMOJI)] if p is not None and p.seat >= 0 else "⚪"
        return f"{emoji} {esc(rt.names.get(pid, 'Бот' if pid in BOT_IDS else pid))}"

    def _describe(self, rt: Runtime, ev) -> Optional[str]:
        who = self._label(rt, ev.pid) if ev.pid else ""
        return describe(ev, who, label=lambda pid: self._label(rt, pid), bold=lambda v: f"**{v}**")

    def _log(self, rt: Runtime, events: list):
        rt.log += [t for t in (self._describe(rt, e) for e in events) if t]
        rt.log = rt.log[-LUDO_LOG_LINES:]

    def _player_line(self, rt: Runtime, p: Player) -> str:
        m = rt.match
        mark = "▶️" if (not m.over and p is m.current_player) else "▫️"
        if p.finished:
            status = f"🏁 все фигуры дома, место {p.place}"
        elif p.left:
            status = "🚪 вышел"
        else:
            base = sum(1 for x in p.pieces if x == 0)
            home = sum(1 for x in p.pieces if x == LUDO_FINISH)
            status = f"в базе: {base} · на поле: {LUDO_PIECES - base - home} · дома: {home}"
        return f"{mark} {self._label(rt, p.pid)} — {status}"

    def _board_text(self, rt: Runtime) -> str:
        m = rt.match
        lines = [f"🎲 **Ludo** · партия #{rt.game_id}"]
        lines += [self._player_line(rt, p) for p in m.players]
        if m.over:
            lines.append("🏁 **Партия окончена.** Итоговые места — в сообщении ниже.")
        else:
            cur = m.current_player
            if cur.is_bot:
                lines.append(f"Ходит {self._label(rt, cur.pid)}…")
            else:
                ts = int(rt.deadline.timestamp()) if rt.deadline else 0
                if m.phase == "pick":
                    lines.append(f"Ход: <@{cur.pid}> — выпало **{m.dice}**, выберите фигуру кнопкой <t:{ts}:R>")
                else:
                    lines.append(f"Ход: <@{cur.pid}> — бросьте кубик <t:{ts}:R>")
        if rt.log:
            lines += [""] + rt.log
        return "\n".join(lines)[:1990]

    @staticmethod
    def _file(png: bytes) -> discord.File:
        return discord.File(io.BytesIO(png), filename=f"board_{time.time_ns()}.png")

    async def _publish(self, rt: Runtime, bump: bool = False):
        """Рисует табло и правит сообщение партии. bump=True: прислать новое внизу, старое удалить."""
        m = rt.match
        png = None
        try:
            urls = {p.pid: rt.avatars.get(p.pid, "") for p in m.players if not p.left and not p.is_bot}
            avatars = await load_avatars(urls)
            pieces, seats = make_scene(m, avatars)
            png = await render_board_async(pieces, seats)
        except Exception:
            log.exception("Не удалось нарисовать табло партии #%s", rt.game_id)
        content = self._board_text(rt)
        view = self._play_view(rt)
        chan = self.bot.get_partial_messageable(rt.channel_id)

        async def send_new():
            kw = {"file": self._file(png)} if png else {}
            new = await chan.send(content=content, view=view, allowed_mentions=NO_PINGS, **kw)
            old, rt.message_id = rt.message_id, new.id
            self.db.set_message(rt.game_id, new.id)
            try:
                await chan.get_partial_message(old).delete()
            except discord.HTTPException:
                pass

        if bump:
            try:
                await send_new()
                return
            except discord.HTTPException:
                log.exception("Не удалось прислать табло заново (партия #%s), правлю старое", rt.game_id)
        try:
            kw = {"attachments": [self._file(png)]} if png else {}
            await chan.get_partial_message(rt.message_id).edit(
                content=content, view=view, allowed_mentions=NO_PINGS, **kw)
        except discord.NotFound:                                    # сообщение удалили: присылаем новое
            try:
                await send_new()
            except discord.HTTPException:
                log.exception("Не удалось отправить табло партии #%s", rt.game_id)
        except discord.HTTPException:
            log.exception("Не удалось обновить табло партии #%s", rt.game_id)

    def _results_text(self, rt: Runtime) -> str:
        m = rt.match
        lines = [f"🏁 **Ludo** · партия #{rt.game_id} окончена", "", "**Итоговые места:**"]
        for i, p in enumerate(m.standings):
            place = MEDALS[i] if i < len(MEDALS) else f"{i + 1}."
            lines.append(f"{place} {self._label(rt, p.pid)}" + (" (вышел)" if p.left else ""))
        if rt.started is not None:
            took = (self.bot.clock.now() - rt.started).total_seconds()
            lines += ["", f"⏱ Длительность партии: {fmt_duration(max(0, took))}"]
        lines += ["", "Для новой партии используйте /ludo."]
        return "\n".join(lines)[:1990]

    async def _send_results(self, rt: Runtime):
        try:
            await self.bot.get_partial_messageable(rt.channel_id).send(
                self._results_text(rt), allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            log.exception("Не удалось отправить итоги партии #%s", rt.game_id)

    # ==================== Ошибки ====================

    async def fail(self, interaction: discord.Interaction, game_id: int, error: BaseException):
        """Нештатный сбой в кнопке: партия закрывается, в консоли Traceback, в Discord извинение.
        Работает и для партии в лобби (рантайма ещё нет)."""
        rt = self.rt.pop(game_id, None)
        if rt is not None:
            self._cancel(rt)
            await self._drop_ping(rt)
        self._cancel_lobby_timer(game_id)
        self.db.abort(game_id, "error")
        await report_error(interaction, error, closing_text="⚠️ Партия закрыта из-за технической ошибки.")

    async def _fail_silent(self, rt: Runtime):
        """То же для сбоя без взаимодействия (таймер, восстановление)."""
        self._cancel(rt)
        self.rt.pop(rt.game_id, None)
        self.db.abort(rt.game_id, "error")
        await self._drop_ping(rt)
        chan = self.bot.get_partial_messageable(rt.channel_id)
        try:
            await chan.get_partial_message(rt.message_id).edit(
                content="⚠️ Партия закрыта из-за технической ошибки.", view=None, attachments=[])
            await chan.send(TECH_ERROR_TEXT.format(dev=DEV_ID))
        except discord.HTTPException:
            log.exception("Не удалось сообщить о сбое партии #%s", rt.game_id)

# ==================== Точка входа ====================

def setup(bot):
    """Точка входа игры: вызывается из main.py."""
    ctrl = LudoController(bot, Storage(bot.clock))
    ctrl.restore_views()
    ctrl.purge_task = asyncio.create_task(ctrl.purge_loop())
    bot.full_games["ludo"] = FullGame("ludo", "Ludo", ctrl.launch)

    @app_commands.command(name="ludo", description="Ludo: открыть набор на партию")
    @app_commands.guild_only()
    async def ludo(interaction: discord.Interaction):
        await ctrl.new_lobby(interaction)

    bot.tree.add_command(ludo)