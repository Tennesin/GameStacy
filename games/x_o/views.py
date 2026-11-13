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
    def __init__(self, ctrl, game_id: int, board: str = EMPTY * 9,
                 locked: bool = False, rematch: bool = False):
        super().__init__(timeout=None)
        self.ctrl, self.game_id = ctrl, game_id
        win = find_win(board) or ()
        for i, ch in enumerate(board):
            style = discord.ButtonStyle.success if i in win else discord.ButtonStyle.secondary
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

        if rematch:                                   # партия окончена, ждём решения про реванш
            again = discord.ui.Button(label="Заново", style=discord.ButtonStyle.success,
                                      custom_id=f"xo:{game_id}:again", row=3)
            again.callback = self._on_again
            quit_ = discord.ui.Button(label="Отказ", style=discord.ButtonStyle.danger,
                                      custom_id=f"xo:{game_id}:quit", row=3)
            quit_.callback = self._on_quit
            self.add_item(again)
            self.add_item(quit_)

    def _make_callback(self, cell: int):
        async def callback(interaction: discord.Interaction):
            await self.ctrl.move(interaction, self.game_id, cell)
        return callback

    async def _on_again(self, interaction: discord.Interaction):
        if await self.ctrl.rematch_vote(interaction, self.game_id):    # True — реванш начался
            self.stop()

    async def _on_quit(self, interaction: discord.Interaction):
        if await self.ctrl.rematch_decline(interaction, self.game_id):  # True — связка распущена
            self.stop()

    async def on_error(self, interaction, error, item):
        await self.ctrl.fail(interaction, self.game_id, error)
        self.stop()