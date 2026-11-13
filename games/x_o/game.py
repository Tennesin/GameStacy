"""Контроллер крестиков-ноликов: правила хода, сообщения, восстановление после перезапуска."""

import asyncio
import random
from collections import defaultdict

import discord
from discord import app_commands

from core.errors import report_error
from .logic import MARKS, find_win, is_full
from .storage import Storage
from .views import BoardView, LobbyView

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
    def __init__(self, bot, storage: Storage):
        self.bot = bot
        self.db = storage
        self.locks = defaultdict(asyncio.Lock)      # по блокировке на партию: клики не обгоняют друг друга

    # ---------- Запуск и восстановление ----------

    async def new_lobby(self, interaction: discord.Interaction):
        game_id = self.db.create(interaction.guild_id, interaction.channel_id)
        await interaction.response.send_message(render(self.db.get(game_id)),
                                                view=LobbyView(self, game_id))
        msg = await interaction.original_response()
        self.db.set_message(game_id, msg.id)

    def restore_views(self):
        """После перезапуска заново подключает кнопки незавершённых партий."""
        for g in self.db.active():
            if g["status"] == "lobby":
                view = LobbyView(self, g["game_id"])
            elif g["status"] == "playing":
                view = BoardView(self, g["game_id"], g["board"])
            else:                                       # окончена, ждёт ответа про реванш
                view = BoardView(self, g["game_id"], g["board"], locked=True, rematch=True)
            self.bot.add_view(view, message_id=g["message_id"])

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
                await interaction.response.edit_message(content=render(self.db.get(game_id)))
                return False

            # Второй игрок найден: бросок монеты решает, кто X
            first, second = g["player_x"], uid
            if random.random() < 0.5:
                first, second = second, first
            self.db.start(game_id, first, second)
            await interaction.response.edit_message(
                content=render(self.db.get(game_id), "🪙 Монета брошена!"),
                view=BoardView(self, game_id))
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
            await interaction.response.edit_message(content=render(self.db.get(game_id)), view=view)

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
                await interaction.response.edit_message(
                    content=render(g),
                    view=BoardView(self, game_id, g["board"], locked=True, rematch=True))
                return False

            # Согласны оба: новая партия, монета решает, кто X
            first, second = g["player_x"], g["player_o"]
            if random.random() < 0.5:
                first, second = second, first
            new_id = self.db.restart(game_id, first, second)
            await interaction.response.edit_message(
                content=render(self.db.get(new_id), "🔁 Реванш! 🪙 Монета брошена заново!"),
                view=BoardView(self, new_id))
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
            await interaction.response.edit_message(
                content=render(self.db.get(game_id), note),
                view=BoardView(self, game_id, g["board"], locked=True))
            return True

    # ---------- Ошибки ----------

    async def fail(self, interaction: discord.Interaction, game_id: int, error: BaseException):
        """Нештатный сбой: сессия закрывается, в Discord извинение, в консоли Traceback."""
        self.db.abort(game_id, "error")
        await report_error(interaction, error,
                           closing_text="⚠️ Партия закрыта из-за технической ошибки.")

def setup(bot):
    """Точка входа игры: вызывается из main.py."""
    ctrl = XOController(bot, Storage(bot.clock))
    ctrl.restore_views()

    @app_commands.command(name="xo", description="Крестики-нолики: открыть набор на партию")
    @app_commands.guild_only()
    async def xo(interaction: discord.Interaction):
        await ctrl.new_lobby(interaction)

    bot.tree.add_command(xo)