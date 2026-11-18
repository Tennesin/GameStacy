"""Контроллер Джуманджи: лобби, ход, таймеры, боты, мини-игры, восстановление после перезапуска."""
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
from core.bump import should_bump
from core.errors import TECH_ERROR_TEXT, report_error
from core.full_games import FullGame
from core.locks import LockMap
from core.clock import fmt_duration
from core.scope import ignore, in_scope
from .avatars import avatar_url, load_avatars
from .console import console_jmap
from .logic import Match, OrderRoll, Player, Track, effective_bots
from .models import GameMap, MapFormatError
from .render import BOT_NAMES, make_tokens, map_key, render_board_async
from .rules import (
    BONUS_LABELS, BOT_DELAY_SECONDS, BOT_IDS, LOBBY_SECONDS, LOG_LINES, MAX_PLAYERS,
    MIN_PLAYERS, MINI_GAME_PICK_SECONDS, ORDER_MAX_ROUNDS, ORDER_PAUSE_SECONDS,
    ORDER_ROLL_SECONDS, TIMEOUTS_TO_LEAVE, TURN_SECONDS
)
from .storage import Storage
from .texts import describe
from .views import ConfirmLeaveView, LobbyView, OrderView, PlayView

log = logging.getLogger("stacy.jumanji")

NO_PINGS = discord.AllowedMentions.none()
MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
HUMAN_EMOJI = ["🔵", "🟢", "🟡", "🔴", "🟠"]       # порядок как у HUMAN_COLORS в render.py
BOT_EMOJI = ["🟣", "🩷", "🩵", "🟤"]               # порядок как у BOT_COLORS
ORDERING = "ordering"

RULES_TEXT = (f"**Правила.** Перед началом все бросают кубик (на бросок {ORDER_ROLL_SECONDS} секунд): "
              "у кого число больше, тот ходит раньше; если числа совпали, эти игроки бросают снова.\n"
              f"Дальше бросайте кубик по очереди (на ход {TURN_SECONDS} секунд) и идите к финишу. "
              "Если до финиша осталось меньше, чем выпало, вы остаётесь на месте: финиш берётся точным броском.\n"
              "Цветные клетки: 🟢 прыжок вперёд, 🔴 откат, 🟠 пропуск хода, 🔵 ещё бросок, "
              "🟣 мини-игра для всех, ⚫ возврат на старт, "
              "🟡 особый путь: если встали на неё точным броском, дальше идёте по жёлтой дороге до её конца; "
              "если перешагнули, идёте как обычно.\n"
              f"Пропущенные ходы подряд ({TIMEOUTS_TO_LEAVE}) — вы выбываете. "
              "Партия идёт, пока на карте не останется один игрок.")

def esc(text) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(text)))

def bot_name(pid: int) -> str:
    """Имя бота по его ID: совпадает с цветом его фишки на картинке."""
    return BOT_NAMES[BOT_IDS.index(pid)] if pid in BOT_IDS else "Бот"

@dataclass
class Runtime:
    """То, что нужно идущей партии в памяти (всё остальное лежит в БД)."""
    game_id: int
    match: Match
    gmap: GameMap
    names: dict
    avatars: dict                       # pid -> ссылка на аватарку
    channel_id: int
    message_id: int
    solo: bool
    seq: int = 0                        # номер «хода»: старые таймеры по нему узнают, что устарели
    deadline: Optional[datetime] = None
    task: Optional[asyncio.Task] = None
    log: list = field(default_factory=list)
    ping_id: Optional[int] = None       # сообщение «ваш ход» с настоящим тегом
    ping_pid: Optional[int] = None      # кому оно адресовано
    minigame_running: bool = False      # идёт ли сейчас мини-игра, запущенная этой партией
    map_key: Optional[int] = None       # хэш карты для кэша картинки (считается один раз)
    started: Optional[datetime] = None  # когда партия началась (None у партий, начатых до появления этой колонки)

@dataclass
class OrderPhase:
    """Определение очереди перед партией. Живёт только в памяти: при перезапуске лобби открывается заново."""
    game_id: int
    roll: OrderRoll
    players: list                       # Player уже с цветами, в порядке лобби (порядок ходов определит бросок)
    names: dict                         # pid -> имя
    colors: dict                        # pid -> номер цвета (только люди)
    avatars: dict                       # pid -> ссылка на аватарку
    humans: set                         # ID людей
    bots: list                          # [(pid, имя)]
    gmap: GameMap
    track: Track
    map_row: object                     # строка карты из БД (копия кладётся в партию при старте)
    channel_id: int
    message_id: int
    solo: bool
    rolls: dict = field(default_factory=dict)       # броски текущего раунда
    need: set = field(default_factory=set)          # люди, которые должны бросить в этом раунде
    auto: set = field(default_factory=set)          # за кого бросила система в этом раунде
    open: bool = False                              # принимаются ли нажатия кнопки
    forced: bool = False                            # ничью решил жребий (сработал предохранитель)
    ends: int = 0                                   # unix-время конца приёма бросков
    all_rolled: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None

class JumanjiController:
    def __init__(self, bot, storage: Storage):
        self.bot = bot
        self.db = storage
        self.locks = LockMap()
        self.rt: dict = {}
        self.lobby_timers: dict = {}
        self.ordering: dict = {}
        self.bg: set = set()

    # ==================== Запуск ====================

    def _default_map_id(self) -> Optional[int]:
        maps = self.db.list_maps()
        return maps[0]["map_id"] if len(maps) == 1 else None

    async def new_lobby(self, interaction: discord.Interaction):
        """Слеш-команда /jumanji: хост — тот, кто её вызвал."""
        if not self.db.list_maps():
            await interaction.response.send_message("Готовых карт пока нет: играть не на чем.", ephemeral=True)
            return
        user = interaction.user
        game_id = self.db.create_game(interaction.guild_id, interaction.channel_id, user.id,
                                      map_id=self._default_map_id())
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
        self._schedule_expiry(game_id, LOBBY_SECONDS)

    async def launch(self, channel, home_id=None) -> int:
        """Запуск из консоли (-game run jumanji ...). Хоста нет: им станет первый, кто нажмёт «Присоединиться»."""
        game_id = self.db.create_game(channel.guild.id, channel.id, 0, home_id,
                                      map_id=self._default_map_id())
        g = self.db.get(game_id)
        try:
            msg = await channel.send(self._lobby_text(g), view=self._lobby_view(g), allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            self.db.abort(game_id, "error")
            raise
        self.db.set_message(game_id, msg.id)
        self._schedule_expiry(game_id, LOBBY_SECONDS)
        return game_id

    # ==================== Восстановление после перезапуска ====================

    def restore_views(self):
        for g in self.db.active():
            gid = g["game_id"]
            if g["status"] == "lobby":
                view = self._lobby_view(g)
                self.bot.add_view(view, message_id=g["message_id"])
                if g["result"] == ORDERING:
                    # бот перезапустился во время броска за очередь: возвращаем лобби в исходный вид
                    self.db.set_result(gid, None)
                    self._schedule_expiry(gid, LOBBY_SECONDS)
                    self._spawn(self._reopen_lobby(gid, view))
                else:
                    idle = (self.bot.clock.now() - datetime.fromisoformat(g["updated_utc"])).total_seconds()
                    self._schedule_expiry(gid, max(0.0, LOBBY_SECONDS - idle))
                continue
            rt = self._load_runtime(g)
            if rt is None:
                self.db.abort(gid, "error")
                continue
            self.rt[gid] = rt
            self.bot.add_view(self._play_view(rt), message_id=g["message_id"])
            self._spawn(self._resume(rt))

    async def _reopen_lobby(self, game_id: int, view):
        """После перезапуска во время броска за очередь: сообщение снова становится обычным лобби."""
        await self.bot.wait_until_ready()
        g = self._lobby_row(game_id)
        if g is None or not g["message_id"]:
            return
        text = (self._lobby_text(g)[:1850]
                + "\n⚠️ Бот перезапускался, очередь ходов не успела определиться: нажмите «▶ Играть» ещё раз.")
        try:
            await self.bot.get_partial_messageable(g["channel_id"]).get_partial_message(g["message_id"]).edit(
                content=text, view=view, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            log.exception("Не удалось восстановить лобби #%s после перезапуска", game_id)

    def _load_runtime(self, g) -> Optional[Runtime]:
        try:
            gmap = self.db.map_of(g)
            match = Match.restore(Track(gmap), self.db.state_of(g))
            rows = self.db.players(g["game_id"])
        except Exception:
            log.exception("Не удалось восстановить партию #%s", g["game_id"])
            return None
        rt = Runtime(g["game_id"], match, gmap,
                     {r["pid"]: r["name"] for r in rows}, {r["pid"]: r["avatar_url"] for r in rows},
                     g["channel_id"], g["message_id"], bool(g["solo"]))
        rt.ping_id = g["ping_id"]
        rt.started = datetime.fromisoformat(g["started_utc"]) if g["started_utc"] else None
        return rt

    async def _resume(self, rt: Runtime):
        await self.bot.wait_until_ready()
        try:
            async with self.locks[rt.game_id]:
                await self._drop_ping(rt)
                events = rt.match.finish_minigame([]) if rt.match.awaiting_minigame else []
                rt.log.append("🔄 Бот перезапускался: игра продолжается.")
                await self._after(rt, events)
        except Exception:
            log.exception("Сбой восстановления партии #%s", rt.game_id)
            await self._fail_silent(rt)

    # ==================== Лобби ====================

    def _lobby_view(self, g) -> LobbyView:
        return LobbyView(self, g["game_id"], g, self.db.list_maps()[:25])

    def _lobby_row(self, game_id: int):
        if game_id in self.ordering:
            return None
        g = self.db.get(game_id)
        return g if g is not None and g["status"] == "lobby" else None

    def _lobby_text(self, g) -> str:
        rows = self.db.players(g["game_id"])
        n_bots = effective_bots(len(rows), g["bots"]) if rows else g["bots"]
        mp = self.db.get_map(g["map_id"]) if g["map_id"] else None
        host = f"<@{g['host_id']}>" if g["host_id"] else "станет первый, кто нажмёт «Присоединиться»"
        people = ", ".join(esc(r["name"]) for r in rows) or "пока никого"
        bots = ", ".join(f"🤖 {BOT_NAMES[i]}" for i in range(n_bots)) or "нет"
        ends = int(self.bot.clock.now().timestamp()) + LOBBY_SECONDS
        lines = [
            f"🎲 **Джуманджи** · партия #{g['game_id']}",
            f"Режим: **{'одиночная игра' if g['solo'] else 'мультиплеер'}** · хост: {host}",
            f"Карта: **{esc(mp['name']) if mp else 'не выбрана'}**",
            f"Игроки: {people}",
            f"Боты: {bots} (мест занято {len(rows) + n_bots} из {MAX_PLAYERS})",
            "ℹ️ Число ботов выбирается для игры в одиночку: каждый присоединившийся человек вытесняет одного бота.",
        ]
        if g["home_channel_id"]:
            lines.append(f"📍 Играть можно только в <#{g['home_channel_id']}> и его ветках.")
        lines += ["", RULES_TEXT, "", f"Настройки меняет хост. Набор закроется <t:{ends}:R>."]
        return "\n".join(lines)[:1990]

    async def _lobby_refresh(self, interaction: discord.Interaction, game_id: int):
        g = self.db.get(game_id)
        content, view = self._lobby_text(g), self._lobby_view(g)

        if g["message_id"] and await should_bump(self.bot, g["channel_id"], g["message_id"]):
            await interaction.response.defer()               # подтверждаем нажатие, дальше шлём новое сообщение
            chan = self.bot.get_partial_messageable(g["channel_id"])
            try:
                new = await chan.send(content, view=view, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                log.exception("Не удалось поднять лобби #%s, правлю старое", game_id)
                await interaction.edit_original_response(content=content, view=view,
                                                         allowed_mentions=NO_PINGS)
            else:
                old = g["message_id"]
                self.db.set_message(game_id, new.id)
                try:
                    await chan.get_partial_message(old).delete()
                except discord.HTTPException:
                    pass
        else:
            await interaction.response.edit_message(content=content, view=view, allowed_mentions=NO_PINGS)

        self._schedule_expiry(game_id, LOBBY_SECONDS)

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

    async def lobby_settings(self, interaction: discord.Interaction, game_id: int, kind: str, value):
        async with self.locks[game_id]:
            g = self._lobby_row(game_id)
            if g is None:
                await interaction.response.send_message("Этот набор уже закрыт.", ephemeral=True)
                return
            if not await self._guard(interaction, g) or not await self._host_only(interaction, g):
                return
            if kind == "bots":
                self.db.update_settings(game_id, bots=int(value))
            elif kind == "map":
                self.db.update_settings(game_id, map_id=int(value))
            else:                                                  # переключатель режима
                if not g["solo"] and len(self.db.players(game_id)) > 1:
                    await interaction.response.send_message(
                        "Одиночный режим возможен, только когда за столом один человек.", ephemeral=True)
                    return
                self.db.update_settings(game_id, solo=not g["solo"])
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
            if g["solo"] and rows:
                await interaction.response.send_message(
                    "Это одиночная игра: присоединиться нельзя.", ephemeral=True)
                return
            if len(rows) >= MAX_PLAYERS:
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
        """Нажата «Да» в окне подтверждения: реальный выход из лобби.
        Interaction здесь относится к скрытому окну, поэтому сообщение лобби правим через канал."""
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
                        content="Набор закрыт: все игроки вышли. Для новой партии используйте /jumanji.",
                        view=None)
                else:
                    if g["host_id"] == uid:
                        self.db.set_host(game_id, rows[0]["pid"])
                    fresh = self.db.get(game_id)
                    await lobby_msg.edit(content=self._lobby_text(fresh), view=self._lobby_view(fresh),
                                         allowed_mentions=NO_PINGS)
                    self._schedule_expiry(game_id, LOBBY_SECONDS)
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
            map_row = self.db.get_map(g["map_id"]) if g["map_id"] else None
            if map_row is None:
                await interaction.response.send_message("Сначала выберите карту.", ephemeral=True)
                return
            try:
                gmap = GameMap.from_json(map_row["json"])
                track = Track(gmap)
            except MapFormatError:
                await interaction.response.send_message(
                    "Эта карта повреждена. Выберите другую.", ephemeral=True)
                return
            humans = self.db.players(game_id)
            n_bots = effective_bots(len(humans), g["bots"])
            if len(humans) + n_bots < MIN_PLAYERS:
                await interaction.response.send_message("Для игры нужно минимум два участника.", ephemeral=True)
                return

            await interaction.response.defer()
            self._cancel_lobby_timer(game_id)
            self.db.set_result(game_id, ORDERING)                  # на случай перезапуска во время броска
            bots = [(BOT_IDS[i], BOT_NAMES[i]) for i in range(n_bots)]
            players = ([Player(r["pid"], r["name"], color=i) for i, r in enumerate(humans)]
                       + [Player(pid, name, True) for pid, name in bots])
            ph = OrderPhase(
                game_id=game_id, roll=OrderRoll([p.pid for p in players]), players=players,
                names={r["pid"]: r["name"] for r in humans} | dict(bots),
                colors={p.pid: p.color for p in players if not p.is_bot},
                avatars={r["pid"]: r["avatar_url"] for r in humans},
                humans={r["pid"] for r in humans}, bots=bots, gmap=gmap, track=track, map_row=map_row,
                channel_id=g["channel_id"], message_id=g["message_id"], solo=bool(g["solo"]))
            self.ordering[game_id] = ph
            ph.task = self._spawn(self._order_phase(ph))

    # ==================== Определение очереди ходов ====================

    @staticmethod
    def _tag(pid: int, name, color: int) -> str:
        """Подпись игрока с цветным значком (до создания партии Runtime ещё нет)."""
        if pid in BOT_IDS:
            return f"{BOT_EMOJI[BOT_IDS.index(pid)]} {bot_name(pid)}"
        return f"{HUMAN_EMOJI[color % len(HUMAN_EMOJI)]} {esc(name)}"

    def _ph_tag(self, ph: OrderPhase, pid: int) -> str:
        return self._tag(pid, ph.names.get(pid, pid), ph.colors.get(pid, 0))

    def _order_status(self, ph: OrderPhase, stage: str, pid: int, tied: bool, active: set) -> str:
        if stage == "open" and pid in active:
            v = ph.rolls.get(pid)
            return f"🎲 **{v}**" if v is not None else "⏳ ждёт броска"
        last = ph.roll.last(pid)
        text = f"🎲 **{last if last is not None else '—'}**"
        if stage == "result" and pid in ph.auto:
            text += " (бросила система)"
        if tied:
            text += " · ничья"
        return text

    def _order_text(self, ph: OrderPhase, stage: str) -> str:
        """stage: «open» - идёт приём бросков; «result» - итоги раунда."""
        r = ph.roll
        lines = [f"🎲 **Джуманджи** · партия #{ph.game_id} · карта «{esc(ph.gmap.name)}»",
                 "**Кто ходит первым?** У кого число больше, тот ходит раньше. "
                 "Если числа совпали, эти игроки бросают снова.", ""]
        active = set(r.active()) if stage == "open" else set()
        place = 1
        for group in r.slots:
            n = len(group)
            label = f"{place}." if n == 1 else f"{place}–{place + n - 1}."
            place += n
            for pid in group:
                lines.append(f"{label} {self._ph_tag(ph, pid)} — "
                             + self._order_status(ph, stage, pid, n > 1, active))
        lines.append("")
        if stage == "open":
            got = sum(1 for pid in ph.need if pid in ph.rolls)
            lines.append(f"Раунд {r.rounds + 1} · нажмите «Бросить кубик» <t:{ph.ends}:R> "
                         f"(на бросок {ORDER_ROLL_SECONDS} с). Бросили: {got}/{len(ph.need)}")
            if r.rounds:
                lines.append("🔁 Перебрасывают: " + ", ".join(self._ph_tag(ph, p) for p in r.active()))
        elif r.done:
            lines.append("✅ Очередь определена"
                         + (" (ничья решена жребием)" if ph.forced else "") + ". Игра начинается…")
        else:
            lines.append(f"Раунд {r.rounds} завершён. 🔁 Ничья: перебрасывают "
                         + ", ".join(self._ph_tag(ph, p) for p in r.active()))
        return "\n".join(lines)[:1990]

    async def _order_show(self, ph: OrderPhase, text: str, locked: bool):
        """Правит сообщение с табло очереди; если его удалили, отправляет новое."""
        chan = self.bot.get_partial_messageable(ph.channel_id)
        view = OrderView(self, ph.game_id, locked=locked)
        try:
            await chan.get_partial_message(ph.message_id).edit(content=text, view=view,
                                                               allowed_mentions=NO_PINGS)
        except discord.NotFound:
            try:
                new = await chan.send(text, view=view, allowed_mentions=NO_PINGS)
                ph.message_id = new.id
                self.db.set_message(ph.game_id, new.id)
            except discord.HTTPException:
                log.exception("Не удалось отправить табло очереди партии #%s", ph.game_id)
        except discord.HTTPException:
            log.exception("Не удалось обновить табло очереди партии #%s", ph.game_id)

    async def order_roll(self, interaction: discord.Interaction, game_id: int):
        """Нажата кнопка «Бросить кубик» при определении очереди."""
        ph = self.ordering.get(game_id)
        if ph is None:
            await interaction.response.send_message("Очередь ходов уже определена.", ephemeral=True)
            return
        g = self.db.get(game_id)
        if g is None or not await self._guard(interaction, g):
            return
        uid = interaction.user.id
        if uid not in ph.humans:
            problem = "Вы не участвуете в этой партии."
        elif not ph.open:
            problem = "Сейчас приём бросков закрыт."
        elif uid not in ph.need:
            problem = "Ваше место уже определено: сейчас перебрасывают только те, у кого выпали одинаковые числа."
        elif uid in ph.rolls:
            problem = "Вы уже бросили кубик в этом раунде."
        else:
            problem = None
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return

        ph.rolls[uid] = ph.roll.throw()                            # записываем до любых await
        await interaction.response.edit_message(content=self._order_text(ph, "open"),
                                                allowed_mentions=NO_PINGS)
        if ph.need <= ph.rolls.keys():                             # сигнал только после ответа на нажатие
            ph.all_rolled.set()

    async def _order_phase(self, ph: OrderPhase):
        """Раунды броска: боты бросают сами, люди кнопкой (не успели - бросает система), ничьи перебрасываются."""
        try:
            while not ph.roll.done:
                if ph.roll.rounds >= ORDER_MAX_ROUNDS:             # предохранитель от бесконечных ничьих
                    ph.roll.force()
                    ph.forced = True
                    await self._order_show(ph, self._order_text(ph, "result"), locked=True)
                    await asyncio.sleep(ORDER_PAUSE_SECONDS)
                    break
                active = ph.roll.active()
                ph.rolls = {pid: ph.roll.throw() for pid in active if pid not in ph.humans}   # боты
                ph.need = {pid for pid in active if pid in ph.humans}
                ph.auto = set()
                ph.all_rolled.clear()
                if ph.need:
                    ph.ends = int(self.bot.clock.now().timestamp()) + ORDER_ROLL_SECONDS
                    ph.open = True
                    await self._order_show(ph, self._order_text(ph, "open"), locked=False)
                    try:
                        await asyncio.wait_for(ph.all_rolled.wait(), timeout=ORDER_ROLL_SECONDS)
                    except asyncio.TimeoutError:
                        pass
                    ph.open = False
                    for pid in sorted(ph.need - ph.rolls.keys()):  # не успели: бросает система
                        ph.rolls[pid] = ph.roll.throw()
                        ph.auto.add(pid)
                ph.roll.apply(ph.rolls)
                await self._order_show(ph, self._order_text(ph, "result"), locked=True)
                await asyncio.sleep(ORDER_PAUSE_SECONDS)
            await self._order_finish(ph)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Сбой определения очереди в партии #%s", ph.game_id)
            rt = self.rt.get(ph.game_id)
            if rt is not None:
                await self._fail_silent(rt)
            else:
                await self._order_fail(ph)

    async def _order_finish(self, ph: OrderPhase):
        """Очередь готова: создаём партию в этом порядке и показываем обычное табло."""
        async with self.locks[ph.game_id]:
            g = self.db.get(ph.game_id)
            if self.ordering.get(ph.game_id) is not ph or g is None or g["status"] != "lobby":
                return
            self.ordering.pop(ph.game_id, None)
            by_pid = {p.pid: p for p in ph.players}
            players = [by_pid[pid] for pid in ph.roll.order()]
            match = Match(ph.track, players)
            events = match.start()
            self.db.set_result(ph.game_id, None)
            started = self.db.start(ph.game_id, ph.map_row, ph.bots, match.snapshot(), None)

            rt = Runtime(ph.game_id, match, ph.gmap, ph.names, ph.avatars,
                         ph.channel_id, ph.message_id, ph.solo)
            rt.started = datetime.fromisoformat(started)
            rt.log.append("🎬 Игра началась!")
            rt.log.append("🎲 Очередь ходов: " + " → ".join(self._ph_tag(ph, p.pid) for p in players))
            self.rt[ph.game_id] = rt
            await self._after(rt, events)

    async def _order_fail(self, ph: OrderPhase):
        """Сбой до создания партии: закрываем набор и извиняемся."""
        self.ordering.pop(ph.game_id, None)
        self.db.abort(ph.game_id, "error")
        chan = self.bot.get_partial_messageable(ph.channel_id)
        try:
            await chan.get_partial_message(ph.message_id).edit(
                content="⚠️ Партия закрыта из-за технической ошибки.", view=None, attachments=[])
            await chan.send(TECH_ERROR_TEXT.format(dev=DEV_ID))
        except discord.HTTPException:
            log.exception("Не удалось сообщить о сбое определения очереди #%s", ph.game_id)

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
                        content="⌛ Время ожидания вышло: набор закрыт. Для новой партии используйте /jumanji.",
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
            if m.awaiting_minigame:
                await self._reply(interaction, "Сейчас идёт мини-игра.")
            elif p is None:
                await self._reply(interaction, "Вы не участвуете в этой партии.")
            elif p.left:
                await self._reply(interaction, "Вы вышли из этой партии.")
            elif p.finished:
                await self._reply(interaction, "Вы уже дошли до финиша.")
            elif m.current_player.pid != uid:
                await self._reply(
                    interaction, f"Сейчас не ваш ход: ходит {self._label(rt, m.current_player.pid)}.")
            else:
                await self._after(rt, m.roll())

    async def play_join(self, interaction: discord.Interaction, game_id: int):
        await interaction.response.defer()
        async with self.locks[game_id]:
            got = await self._playing(interaction, game_id)
            if got is None:
                return
            rt = got[0]
            m, user = rt.match, interaction.user
            if rt.solo:
                await self._reply(interaction, "Это одиночная партия: присоединиться нельзя.")
                return
            if m.awaiting_minigame:
                await self._reply(interaction, "Дождитесь конца мини-игры.")
                return
            ok, why = m.can_join(user.id)
            if not ok:
                await self._reply(interaction, why)
                return
            url = avatar_url(user)
            events = m.join(user.id, user.display_name)
            rt.names[user.id], rt.avatars[user.id] = user.display_name, url
            self.db.add_player(game_id, user.id, user.display_name, url)
            await self._after(rt, events, rearm=False)             # таймер ходящего не сбрасываем

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
        if not bump:                                    # после мини-игры bump уже True; иначе смотрим на канал
            bump = await should_bump(self.bot, rt.channel_id, rt.message_id)
        if m.over:
            self._stop_minigame(rt)
            self._cancel(rt)
            rt.deadline = None
            self.db.finish(rt.game_id, m.snapshot())
            await self._drop_ping(rt)
            await self._publish(rt, bump=bump)          # табло: итоговая картинка и расстановка
            await self._send_results(rt)                # итоги: отдельным сообщением
            self.rt.pop(rt.game_id, None)
            self.locks.pop(rt.game_id, None)
            return
        if rearm:
            self._arm(rt)
        self._save(rt)
        await self._publish(rt, bump=bump)
        await self._sync_ping(rt, reset=bump)
        if m.awaiting_minigame and any(e.kind == "minigame" for e in events):
            self._spawn(self._minigame(rt))

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
                    log.info("Джуманджи: удалено старых партий: %d", removed)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Сбой уборки старых партий")
            await asyncio.sleep(86400)

    def _arm(self, rt: Runtime):
        """Запускает таймер хода: у человека 30 секунд, у бота короткая пауза и автоматический бросок."""
        self._cancel(rt)
        rt.seq += 1
        m = rt.match
        if m.over or m.awaiting_minigame:
            rt.deadline = None
            return
        p = m.current_player
        if p.is_bot:
            delay, rt.deadline = BOT_DELAY_SECONDS, None
        else:
            delay = TURN_SECONDS
            rt.deadline = self.bot.clock.now() + timedelta(seconds=delay)
        rt.task = asyncio.create_task(self._timer(rt, rt.seq, delay, p.is_bot))

    async def _timer(self, rt: Runtime, seq: int, delay: float, is_bot: bool):
        try:
            await asyncio.sleep(delay)
            async with self.locks[rt.game_id]:
                m = rt.match
                if seq != rt.seq or m.over or m.awaiting_minigame:
                    return                                         # за это время ход уже сделан
                await self._after(rt, m.roll(timed_out=not is_bot))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Сбой таймера партии #%s", rt.game_id)
            await self._fail_silent(rt)

    # ==================== Мини-игра на клетке ====================

    async def _minigame(self, rt: Runtime):
        m = rt.match
        winners: list = []
        try:
            minis = getattr(self.bot, "mini_games", None)
            humans = {p.pid for p in m.players if p.on_map and not p.is_bot}
            bots = {p.pid: bot_name(p.pid) for p in m.players if p.on_map and p.is_bot}
            if minis is not None and minis.games and humans:
                channel = self.bot.get_channel(rt.channel_id) or await self.bot.fetch_channel(rt.channel_id)
                key = random.choice(list(minis.games))
                rt.minigame_running = True
                try:
                    result = await minis.play(key, channel, players=humans,
                                              pick_seconds=MINI_GAME_PICK_SECONDS, bots=bots)
                    winners = list(result.winners)
                except (KeyError, RuntimeError) as e:
                    log.warning("Мини-игра в партии #%s не запустилась: %s", rt.game_id, e)
                    rt.log.append("⚠️ Мини-игра не началась: в канале уже идёт другая игра.")
                finally:
                    rt.minigame_running = False
            async with self.locks[rt.game_id]:
                if rt.game_id not in self.rt or not m.awaiting_minigame:
                    return
                await self._after(rt, m.finish_minigame(winners), bump=True)   # табло — заново вниз канала
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Сбой мини-игры в партии #%s", rt.game_id)
            await self._fail_silent(rt)

    def _stop_minigame(self, rt: Runtime):
        """Останавливает мини-игру партии (если идёт): партия закончилась или закрыта."""
        minis = getattr(self.bot, "mini_games", None)
        if rt.minigame_running and minis is not None:
            minis.stop(rt.channel_id)

    # ==================== Табло ====================

    def _play_view(self, rt: Runtime) -> Optional[PlayView]:
        """Кнопки под табло: кубик доступен только в ход человека (в ход бота и во время мини-игры он выключен)."""
        m = rt.match
        if m.over:
            return None
        cur = m.current_player
        locked = m.awaiting_minigame or cur.is_bot
        return PlayView(self, rt.game_id, locked=locked,
                        turn_name="" if locked else str(rt.names.get(cur.pid, "")))

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
        if not m.over and not m.awaiting_minigame and not m.current_player.is_bot:
            want = m.current_player.pid
        if reset or want != rt.ping_pid:
            await self._drop_ping(rt)
        if want is None or rt.ping_pid == want:
            return
        try:
            msg = await self.bot.get_partial_messageable(rt.channel_id).send(
                f"🎲 <@{want}>, ваш ход! Нажмите «Бросить кубик» (на ход {TURN_SECONDS} с).",
                allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=want)]))
            rt.ping_id, rt.ping_pid = msg.id, want
            self.db.set_ping(rt.game_id, msg.id)
        except discord.HTTPException:
            log.exception("Не удалось прислать сообщение о ходе (партия #%s)", rt.game_id)

    def _results_text(self, rt: Runtime) -> str:
        m = rt.match
        lines = [f"🏁 **Джуманджи** · партия #{rt.game_id} окончена · карта «{esc(rt.gmap.name)}»", "",
                 "**Итоговые места:**"]
        for i, p in enumerate(m.standings):
            place = MEDALS[i] if i < len(MEDALS) else f"{i + 1}."
            lines.append(f"{place} {self._label(rt, p.pid)}" + (" (вышел)" if p.left else ""))
        if rt.started is not None:
            took = (self.bot.clock.now() - rt.started).total_seconds()
            lines += ["", f"⏱ Длительность партии: {fmt_duration(max(0, took))}"]
        lines += ["", "Для новой партии используйте /jumanji."]
        return "\n".join(lines)[:1990]

    async def _send_results(self, rt: Runtime):
        try:
            await self.bot.get_partial_messageable(rt.channel_id).send(
                self._results_text(rt), allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            log.exception("Не удалось отправить итоги партии #%s", rt.game_id)

    def _label(self, rt: Runtime, pid: int) -> str:
        if pid in BOT_IDS:
            return f"{BOT_EMOJI[BOT_IDS.index(pid)]} {bot_name(pid)}"
        p = rt.match.get(pid)
        color = p.color if (p is not None and p.color >= 0) else 0
        return f"{HUMAN_EMOJI[color % len(HUMAN_EMOJI)]} {esc(rt.names.get(pid, pid))}"

    def _describe(self, rt: Runtime, ev) -> Optional[str]:
        who = self._label(rt, ev.pid) if ev.pid else ""
        return describe(ev, rt.match.track, who, bold=lambda v: f"**{v}**")

    def _log(self, rt: Runtime, events: list):
        rt.log += [t for t in (self._describe(rt, e) for e in events) if t]
        rt.log = rt.log[-LOG_LINES:]

    def _player_line(self, rt: Runtime, p: Player) -> str:
        m = rt.match
        mark = "▶️" if (not m.over and p is m.current_player and not m.awaiting_minigame) else "▫️"
        if p.finished:
            status = f"🏁 финишировал, место {p.place}"
        elif p.left:
            status = "🚪 вышел"
        else:
            status = f"клетка {m.track.label(p.pos)} из {m.track.finish}"
            if m.track.is_side(p.pos):
                status += " · 🟡 особый путь"
            elif p.on_branch:
                status += " · 🟡 пойдёт по особому пути"
            if p.skip:
                status += f" · ⏭ пропуск ×{p.skip}"
            if p.bonus:
                status += f" · {BONUS_LABELS.get(p.bonus, p.bonus)}"
        return f"{mark} {self._label(rt, p.pid)} — {status}"

    def _board_text(self, rt: Runtime) -> str:
        m = rt.match
        lines = [f"🎲 **Джуманджи** · партия #{rt.game_id} · карта «{esc(rt.gmap.name)}»"]
        lines += [self._player_line(rt, p) for p in m.players]
        if m.over:
            lines.append("🏁 **Партия окончена.** Итоговые места — в сообщении ниже.")
        else:
            cur = m.current_player
            if m.awaiting_minigame:
                lines.append("🟣 **Идёт мини-игра**: смотрите сообщения ниже. Кубик пока недоступен.")
            elif cur.is_bot:
                lines.append(f"Ходит {self._label(rt, cur.pid)}…")
            else:
                ts = int(rt.deadline.timestamp()) if rt.deadline else 0
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
            urls = {p.pid: rt.avatars.get(p.pid, "") for p in m.players if p.on_map and not p.is_bot}
            avatars = await load_avatars(urls)
            if rt.map_key is None:
                rt.map_key = map_key(rt.gmap)
            png = await render_board_async(rt.gmap, make_tokens(m, avatars), cache_key=rt.map_key)
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

    # ==================== Ошибки ====================

    async def fail(self, interaction: discord.Interaction, game_id: int, error: BaseException):
        """Нештатный сбой в кнопке: партия закрывается, в консоли Traceback, в Discord извинение."""
        ph = self.ordering.pop(game_id, None)
        if ph is not None and ph.task is not None and ph.task is not asyncio.current_task():
            ph.task.cancel()
        rt = self.rt.pop(game_id, None)
        if rt is not None:
            self._cancel(rt)
            self._stop_minigame(rt)
            await self._drop_ping(rt)
        self._cancel_lobby_timer(game_id)
        self.db.abort(game_id, "error")
        await report_error(interaction, error, closing_text="⚠️ Партия закрыта из-за технической ошибки.")

    async def _fail_silent(self, rt: Runtime):
        """То же для сбоя без взаимодействия (таймер, мини-игра)."""
        self._cancel(rt)
        self._stop_minigame(rt)
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
    ctrl = JumanjiController(bot, Storage(bot.clock))
    ctrl.restore_views()
    ctrl.purge_task = asyncio.create_task(ctrl.purge_loop())
    bot.full_games["jumanji"] = FullGame("jumanji", "Джуманджи", ctrl.launch)
    bot.console_commands["-jmap"] = console_jmap(ctrl)

    @app_commands.command(name="jumanji", description="Джуманджи: открыть набор на партию")
    @app_commands.guild_only()
    async def jumanji(interaction: discord.Interaction):
        await ctrl.new_lobby(interaction)

    bot.tree.add_command(jumanji)