"""Контроллер крестиков-ноликов: правила хода, сообщения, восстановление после перезапуска."""
import asyncio
import logging
import random
from collections import defaultdict
from datetime import datetime
import discord
from datetime import datetime, timedelta
from discord import app_commands

from config import LOBBY_TIMEOUT_SECONDS
from core.bump import should_bump
from core.errors import report_error
from .logic import MARKS, find_win, is_full
from .storage import Storage
from .views import BoardView, LobbyView

log = logging.getLogger("stacy.xo")
NO_PINGS = discord.AllowedMentions.none()
PLAY_IDLE_SECONDS = 1800        # партия без ходов закрывается через 30 минут
REMATCH_IDLE_SECONDS = 600      # реванш без ответа — через 10 минут
SWEEP_EVERY_SECONDS = 120

def mention(uid) -> str:
    return f"<@{uid}>"

def render(g, note: str = "") -> str:
    """Текст сообщения по строке БД."""
    head = f"**Крестики-нолики** · партия #{g['game_id']}"
    status = g["status"]
    if status == "lobby":
        who = (f"Участвует: {mention(g['player_x'])}. Нужен ещё один игрок."
               if g["player_x"] else "Нужны два игрока. Нажмите «Участвовать».")
        return "\n".join([head, who])
    if status == "aborted":
        if g["result"] == "timeout":
            return "\n".join([head, "⌛ Время ожидания вышло: игроки не набрались. Набор закрыт. "
                                    "Для новой партии используйте /xo."])
        if g["result"] == "idle":
            return "\n".join([head, "⌛ Партия закрыта: слишком долго не было ходов. "
                                    "Для новой партии используйте /xo."])
        return "\n".join([head, "⚠️ Партия закрыта."])

    players = f"{MARKS['X']} {mention(g['player_x'])}   {MARKS['O']} {mention(g['player_o'])}"
    extra = ""
    if status == "playing":
        current = g["player_x"] if g["turn"] == "X" else g["player_o"]
        tail = f"Ход: {MARKS[g['turn']]} {mention(current)}"
    else:
        if g["result"] == "draw":
            tail = "🤝 Ничья."
        else:
            winner = g["player_x"] if g["result"] == "X" else g["player_o"]
            tail = f"🏆 Победитель: {MARKS[g['result']]} {mention(winner)}"
        if g["rematch"] == 1:                          # идёт голосование за реванш
            mark_x = "✅" if g["ready_x"] else "⏳"
            mark_o = "✅" if g["ready_o"] else "⏳"
            extra = (f"Реванш? {mark_x} {mention(g['player_x'])}   "
                     f"{mark_o} {mention(g['player_o'])}")
    return "\n".join(x for x in (head, note, players, tail, extra) if x)

class XOController:
    def __init__(self, bot, storage: Storage, lobby_timeout: int = None):
        self.bot = bot
        self.db = storage
        self.lobby_timeout = lobby_timeout or LOBBY_TIMEOUT_SECONDS   # сколько ждём второго игрока, сек
        self.locks = defaultdict(asyncio.Lock)      # по блокировке на партию: клики не обгоняют друг друга
        self.timers: dict = {}                      # game_id -> задача таймера набора
        self.lobby_views: dict = {}                 # game_id -> View набора (чтобы остановить при закрытии)

    # ---------- Запуск и восстановление ----------

    async def new_lobby(self, interaction: discord.Interaction):
        game_id = self.db.create(interaction.guild_id, interaction.channel_id)
        view = LobbyView(self, game_id)
        await interaction.response.send_message(render(self.db.get(game_id)), view=view)
        msg = await interaction.original_response()
        self.db.set_message(game_id, msg.id)
        self.lobby_views[game_id] = view
        self._schedule_expiry(game_id, self.lobby_timeout)

    def restore_views(self):
        """После перезапуска заново подключает кнопки незавершённых партий."""
        for g in self.db.active():
            if g["status"] == "lobby":
                view = LobbyView(self, g["game_id"])
                self.lobby_views[g["game_id"]] = view
                # сколько времени набора осталось с момента создания (оно могло истечь, пока бот был выключен)
                created = datetime.fromisoformat(g["created_utc"])
                left = self.lobby_timeout - (self.bot.clock.now() - created).total_seconds()
                self._schedule_expiry(g["game_id"], max(0.0, left))
            elif g["status"] == "playing":
                view = BoardView(self, g["game_id"], g["board"])
            else:                                       # окончена, ждёт ответа про реванш
                view = BoardView(self, g["game_id"], g["board"], locked=True, rematch=True)
            self.bot.add_view(view, message_id=g["message_id"])

    async def sweep_loop(self):
        """Раз в пару минут закрывает брошенные партии и неотвеченные реванши."""
        await self.bot.wait_until_ready()
        while True:
            try:
                await self._sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Сбой уборки брошенных партий")
            await asyncio.sleep(SWEEP_EVERY_SECONDS)

    async def _sweep(self):
        now = self.bot.clock.now()
        cut_play = (now - timedelta(seconds=PLAY_IDLE_SECONDS)).isoformat()
        cut_rematch = (now - timedelta(seconds=REMATCH_IDLE_SECONDS)).isoformat()
        for row in self.db.stale(cut_play, cut_rematch):
            gid = row["game_id"]
            async with self.locks[gid]:
                g = self.db.get(gid)                              # за время ожидания блокировки всё могло измениться
                if g is None:
                    continue
                note = ""
                if g["status"] == "playing" and g["updated_utc"] <= cut_play:
                    self.db.abort(gid, "idle")
                elif g["status"] == "finished" and g["rematch"] == 1 and g["updated_utc"] <= cut_rematch:
                    self.db.set_rematch(gid, 2)
                    note = "⌛ Реванш не состоялся: ответа не дождались."
                else:
                    continue
                g = self.db.get(gid)
                if not g["message_id"]:
                    continue
                try:
                    msg = self.bot.get_partial_messageable(g["channel_id"]).get_partial_message(g["message_id"])
                    # доска остаётся на экране (кнопки заблокированы), чтобы был виден итог
                    await msg.edit(content=render(g, note),
                                   view=BoardView(self, gid, g["board"], locked=True))
                except discord.HTTPException:
                    log.exception("Не удалось обновить сообщение закрытой партии #%s", gid)

    # ---------- Показ сообщения партии (правка или подъём вниз канала) ----------

    def _default_view(self, g):
        """Кнопки по состоянию партии (нужны, когда сообщение приходится отправлять заново)."""
        gid = g["game_id"]
        if g["status"] == "lobby":
            view = LobbyView(self, gid)
            self.lobby_views[gid] = view
            return view
        if g["status"] == "playing":
            return BoardView(self, gid, g["board"])
        return BoardView(self, gid, g["board"], locked=True, rematch=g["rematch"] == 1)

    async def _resend(self, g, content: str, view) -> bool:
        """Новое сообщение внизу канала, потом удаление старого. True — получилось."""
        chan = self.bot.get_partial_messageable(g["channel_id"])
        try:
            new = await chan.send(content, view=view, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            log.exception("Не удалось поднять сообщение партии #%s", g["game_id"])
            return False
        old = g["message_id"]
        self.db.set_message(g["game_id"], new.id)
        try:
            await chan.get_partial_message(old).delete()
        except discord.HTTPException:
            pass
        return True

    async def _show(self, interaction: discord.Interaction, game_id: int, view=None, note: str = ""):
        """Единая точка обновления сообщения партии после нажатия кнопки."""
        g = self.db.get(game_id)
        content = render(g, note)
        await interaction.response.defer()                       # подтверждение нажатия, 3 секунды не жмут

        if g["message_id"] and await should_bump(self.bot, g["channel_id"], g["message_id"]):
            if await self._resend(g, content, view if view is not None else self._default_view(g)):
                return
        kw = {"view": view} if view is not None else {}
        await interaction.edit_original_response(content=content, **kw)

    # ---------- Таймер набора ----------

    def _schedule_expiry(self, game_id: int, delay: float):
        self._cancel_timer(game_id)
        self.timers[game_id] = asyncio.create_task(self._expire(game_id, delay))

    def _cancel_timer(self, game_id: int):
        task = self.timers.pop(game_id, None)
        if task is not None:
            task.cancel()

    async def _expire(self, game_id: int, delay: float):
        """По истечении времени закрывает набор, если игра так и не началась."""
        try:
            await asyncio.sleep(delay)
            async with self.locks[game_id]:
                g = self.db.get(game_id)
                if g is None or g["status"] != "lobby":
                    return                                  # игра уже началась или закрыта
                self.db.abort(game_id, "timeout")
                view = self.lobby_views.pop(game_id, None)
                if view is not None:
                    view.stop()
                if g["message_id"]:
                    try:
                        channel = self.bot.get_partial_messageable(g["channel_id"])
                        await channel.get_partial_message(g["message_id"]).edit(
                            content=render(self.db.get(game_id)), view=None)
                    except discord.HTTPException:
                        log.exception("Не удалось обновить сообщение закрытого набора #%s", game_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Сбой таймера набора #%s", game_id)
        finally:
            if self.timers.get(game_id) is asyncio.current_task():
                self.timers.pop(game_id, None)

    # ---------- Набор игроков ----------

    async def join(self, interaction: discord.Interaction, game_id: int) -> bool:
        """True — набор завершён и партия началась."""
        async with self.locks[game_id]:
            g = self.db.get(game_id)
            if g is None or g["status"] != "lobby":
                await interaction.response.send_message("Этот набор уже закрыт.", ephemeral=True)
                return False
            uid = interaction.user.id
            if uid == g["player_x"]:
                await interaction.response.send_message(
                    "Вы уже участвуете. Ждём второго игрока.", ephemeral=True)
                return False
            if g["player_x"] is None:
                self.db.join_first(game_id, uid)
                await self._show(interaction, game_id)
                return False

            # Второй игрок найден: бросок монеты решает, кто X
            first, second = g["player_x"], uid
            if random.random() < 0.5:
                first, second = second, first
            self.db.start(game_id, first, second)
            self._cancel_timer(game_id)                 # набор завершён: таймер больше не нужен
            self.lobby_views.pop(game_id, None)
            await self._show(interaction, game_id, BoardView(self, game_id), "🪙 Монета брошена!")
            return True

    # ---------- Ход ----------

    async def move(self, interaction: discord.Interaction, game_id: int, cell: int):
        async with self.locks[game_id]:
            g = self.db.get(game_id)
            if g is None or g["status"] != "playing":
                await interaction.response.send_message("Партия уже завершена.", ephemeral=True)
                return
            uid = interaction.user.id
            if uid not in (g["player_x"], g["player_o"]):
                await interaction.response.send_message("Вы не участвуете в этой партии.", ephemeral=True)
                return
            mark = g["turn"]
            expected = g["player_x"] if mark == "X" else g["player_o"]
            if uid != expected:
                await interaction.response.send_message("Сейчас не ваш ход.", ephemeral=True)
                return
            board = g["board"]
            if board[cell] != " ":
                await interaction.response.send_message("Эта клетка уже занята.", ephemeral=True)
                return

            board = board[:cell] + mark + board[cell + 1:]
            if find_win(board):
                status, result, turn = "finished", mark, None
            elif is_full(board):
                status, result, turn = "finished", "draw", None
            else:
                status, result, turn = "playing", None, ("O" if mark == "X" else "X")

            self.db.make_move(game_id, uid, cell, board, turn, status, result)
            finished = status == "finished"
            view = BoardView(self, game_id, board, locked=finished, rematch=finished)
            await self._show(interaction, game_id, view)

    # ---------- Реванш ----------

    def _seat(self, g, uid):
        """'X' / 'O' — место игрока в этой партии, либо None, если он посторонний."""
        if uid == g["player_x"]:
            return "X"
        if uid == g["player_o"]:
            return "O"
        return None

    async def rematch_vote(self, interaction: discord.Interaction, game_id: int) -> bool:
        """Нажата «Заново». True — оба согласны и началась новая партия."""
        async with self.locks[game_id]:
            g = self.db.get(game_id)
            if g is None or g["status"] != "finished" or g["rematch"] != 1:
                await interaction.response.send_message("Предложение реванша уже закрыто.", ephemeral=True)
                return False
            seat = self._seat(g, interaction.user.id)
            if seat is None:
                await interaction.response.send_message("Вы не участвуете в этой партии.", ephemeral=True)
                return False
            if g["ready_x" if seat == "X" else "ready_o"]:
                await interaction.response.send_message(
                    "Вы уже нажали «Заново». Ждём второго игрока.", ephemeral=True)
                return False

            self.db.vote_rematch(game_id, seat)
            g = self.db.get(game_id)
            if not (g["ready_x"] and g["ready_o"]):
                await self._show(interaction, game_id,
                                 BoardView(self, game_id, g["board"], locked=True, rematch=True))
                return False

            # Согласны оба: новая партия, монета решает, кто X
            first, second = g["player_x"], g["player_o"]
            if random.random() < 0.5:
                first, second = second, first
            new_id = self.db.restart(game_id, first, second)
            await self._show(interaction, new_id, BoardView(self, new_id),
                             "🔁 Реванш! 🪙 Монета брошена заново!")
            return True

    async def rematch_decline(self, interaction: discord.Interaction, game_id: int) -> bool:
        """Нажата «Отказ». True — связка распущена."""
        async with self.locks[game_id]:
            g = self.db.get(game_id)
            if g is None or g["status"] != "finished" or g["rematch"] != 1:
                await interaction.response.send_message("Предложение реванша уже закрыто.", ephemeral=True)
                return False
            if self._seat(g, interaction.user.id) is None:
                await interaction.response.send_message("Вы не участвуете в этой партии.", ephemeral=True)
                return False

            self.db.set_rematch(game_id, 2)
            note = (f"🚫 {mention(interaction.user.id)} отказался(лась) от реванша. "
                    "Для новой партии используйте /xo.")
            await self._show(interaction, game_id,
                             BoardView(self, game_id, g["board"], locked=True), note)
            return True

    # ---------- Ошибки ----------

    async def fail(self, interaction: discord.Interaction, game_id: int, error: BaseException):
        """Нештатный сбой: сессия закрывается, в Discord извинение, в консоли Traceback."""
        self._cancel_timer(game_id)
        self.lobby_views.pop(game_id, None)
        self.db.abort(game_id, "error")
        await report_error(interaction, error,
                           closing_text="⚠️ Партия закрыта из-за технической ошибки.")

def setup(bot):
    """Точка входа игры: вызывается из main.py."""
    ctrl = XOController(bot, Storage(bot.clock))
    ctrl.restore_views()
    ctrl.sweep_task = asyncio.create_task(ctrl.sweep_loop())

    @app_commands.command(name="xo", description="Крестики-нолики: открыть набор на партию")
    @app_commands.guild_only()
    async def xo(interaction: discord.Interaction):
        await ctrl.new_lobby(interaction)

    bot.tree.add_command(xo)