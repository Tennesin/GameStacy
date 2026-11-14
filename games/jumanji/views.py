"""Кнопки Джуманджи. Постоянные: timeout=None и фиксированные custom_id вида jmj:ID_партии:действие."""
import discord

from .rules import MAX_BOTS

class LobbyView(discord.ui.View):
    """Лобби: боты, карта, режим, присоединиться / выйти / играть. Собирается из строки БД."""

    def __init__(self, ctrl, game_id: int, g, maps):
        super().__init__(timeout=None)
        self.ctrl, self.game_id = ctrl, game_id

        self.bots_sel = discord.ui.Select(
            custom_id=f"jmj:{game_id}:bots", row=0, placeholder="Сколько ботов",
            options=[discord.SelectOption(label=f"Ботов: {n}", value=str(n), default=(n == g["bots"]))
                     for n in range(1, MAX_BOTS + 1)])
        self.bots_sel.callback = self._on_bots
        self.add_item(self.bots_sel)

        if maps:
            self.map_sel = discord.ui.Select(
                custom_id=f"jmj:{game_id}:map", row=1, placeholder="Выберите карту",
                options=[discord.SelectOption(label=str(r["name"])[:100], value=str(r["map_id"]),
                                              default=(r["map_id"] == g["map_id"])) for r in maps])
        else:
            self.map_sel = discord.ui.Select(
                custom_id=f"jmj:{game_id}:map", row=1, placeholder="Готовых карт пока нет", disabled=True,
                options=[discord.SelectOption(label="—", value="0")])
        self.map_sel.callback = self._on_map
        self.add_item(self.map_sel)

        def button(label, style, cid, cb):
            b = discord.ui.Button(label=label, style=style, custom_id=f"jmj:{game_id}:{cid}", row=2)
            b.callback = cb
            self.add_item(b)

        button("Режим: одиночная" if g["solo"] else "Режим: мультиплеер",
               discord.ButtonStyle.secondary, "mode", self._on_mode)
        button("Присоединиться", discord.ButtonStyle.primary, "join", self._on_join)
        button("Выйти", discord.ButtonStyle.secondary, "leave", self._on_leave)
        button("▶ Играть", discord.ButtonStyle.success, "play", self._on_play)

    async def _on_bots(self, i: discord.Interaction):
        await self.ctrl.lobby_settings(i, self.game_id, "bots", self.bots_sel.values[0])

    async def _on_map(self, i: discord.Interaction):
        await self.ctrl.lobby_settings(i, self.game_id, "map", self.map_sel.values[0])

    async def _on_mode(self, i: discord.Interaction):
        await self.ctrl.lobby_settings(i, self.game_id, "mode", None)

    async def _on_join(self, i: discord.Interaction):
        await self.ctrl.lobby_join(i, self.game_id)

    async def _on_leave(self, i: discord.Interaction):
        await self.ctrl.lobby_leave(i, self.game_id)

    async def _on_play(self, i: discord.Interaction):
        await self.ctrl.lobby_play(i, self.game_id)

    async def on_error(self, interaction, error, item):
        await self.ctrl.fail(interaction, self.game_id, error)
        self.stop()

class PlayView(discord.ui.View):
    """Под табло идущей партии: кубик, присоединиться, выйти."""

    def __init__(self, ctrl, game_id: int, locked: bool = False):
        super().__init__(timeout=None)
        self.ctrl, self.game_id = ctrl, game_id

        roll = discord.ui.Button(label="Бросить кубик", emoji="🎲", style=discord.ButtonStyle.success,
                                 custom_id=f"jmj:{game_id}:roll", row=0, disabled=locked)
        roll.callback = self._on_roll
        join = discord.ui.Button(label="Присоединиться", style=discord.ButtonStyle.primary,
                                 custom_id=f"jmj:{game_id}:pjoin", row=0)
        join.callback = self._on_join
        leave = discord.ui.Button(label="Выйти", style=discord.ButtonStyle.secondary,
                                  custom_id=f"jmj:{game_id}:pleave", row=0)
        leave.callback = self._on_leave
        for b in (roll, join, leave):
            self.add_item(b)

    async def _on_roll(self, i: discord.Interaction):
        await self.ctrl.roll(i, self.game_id)

    async def _on_join(self, i: discord.Interaction):
        await self.ctrl.play_join(i, self.game_id)

    async def _on_leave(self, i: discord.Interaction):
        await self.ctrl.play_leave(i, self.game_id)

    async def on_error(self, interaction, error, item):
        await self.ctrl.fail(interaction, self.game_id, error)
        self.stop()