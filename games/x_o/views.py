"""Кнопки крестиков-ноликов. Постоянные: timeout=None и фиксированные custom_id."""

import discord

from .logic import EMPTY, MARKS, find_win

class LobbyView(discord.ui.View):
    def __init__(self, ctrl, game_id: int):
        super().__init__(timeout=None)
        self.ctrl, self.game_id = ctrl, game_id
        btn = discord.ui.Button(label="Участвовать", style=discord.ButtonStyle.success,
                                custom_id=f"xo:{game_id}:join")
        btn.callback = self._on_join
        self.add_item(btn)

    async def _on_join(self, interaction: discord.Interaction):
        if await self.ctrl.join(interaction, self.game_id):     # True — игра началась
            self.stop()

    async def on_error(self, interaction, error, item):
        await self.ctrl.fail(interaction, self.game_id, error)
        self.stop()

class BoardView(discord.ui.View):
    def __init__(self, ctrl, game_id: int, board: str = EMPTY * 9, locked: bool = False):
        super().__init__(timeout=None)
        self.ctrl, self.game_id = ctrl, game_id
        win = find_win(board) or ()
        for i, ch in enumerate(board):
            if i in win:
                style = discord.ButtonStyle.success
            elif ch == "X":
                style = discord.ButtonStyle.danger
            elif ch == "O":
                style = discord.ButtonStyle.primary
            else:
                style = discord.ButtonStyle.secondary
            btn = discord.ui.Button(
                style=style,
                label="\u200b" if ch == EMPTY else None,
                emoji=None if ch == EMPTY else MARKS[ch],
                custom_id=f"xo:{game_id}:{i}",
                row=i // 3,
                disabled=locked or ch != EMPTY,
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, cell: int):
        async def callback(interaction: discord.Interaction):
            await self.ctrl.move(interaction, self.game_id, cell)
        return callback

    async def on_error(self, interaction, error, item):
        await self.ctrl.fail(interaction, self.game_id, error)
        self.stop()