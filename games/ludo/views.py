"""Кнопки Ludo. Постоянные: timeout=None и фиксированные custom_id вида ludo:ID_партии:действие."""
from contextlib import suppress
import discord

from core.errors import is_expired, report_error
from .rules import LUDO_FINISH, LUDO_MAX_BOTS

def pick_text(mv) -> str:
    """Подпись кнопки выбора фигуры по ходу Move: «Фигура 2: выйти», «Фигура 1: +4», «Фигура 3: домой»."""
    if mv.frm == 0:
        action = "выйти"
    elif mv.to == LUDO_FINISH:
        action = "домой"
    else:
        action = f"+{mv.to - mv.frm}"
    text = f"Фигура {mv.piece + 1}: {action}"
    return text + " · взятие" if mv.captures else text

class LobbyView(discord.ui.View):
    """Лобби: число ботов, присоединиться / выйти / играть. Собирается из строки БД."""

    def __init__(self, ctrl, game_id: int, g):
        super().__init__(timeout=None)
        self.ctrl, self.game_id = ctrl, game_id

        self.bots_sel = discord.ui.Select(
            custom_id=f"ludo:{game_id}:bots", row=0, placeholder="Сколько ботов",
            options=[discord.SelectOption(label=f"Ботов: {n}", value=str(n), default=(n == g["bots"]))
                     for n in range(1, LUDO_MAX_BOTS + 1)])
        self.bots_sel.callback = self._on_bots
        self.add_item(self.bots_sel)

        def button(label, style, cid, cb):
            b = discord.ui.Button(label=label, style=style, custom_id=f"ludo:{game_id}:{cid}", row=1)
            b.callback = cb
            self.add_item(b)

        button("Присоединиться", discord.ButtonStyle.primary, "join", self._on_join)
        button("Выйти", discord.ButtonStyle.secondary, "leave", self._on_leave)
        button("▶ Играть", discord.ButtonStyle.success, "play", self._on_play)

    async def _on_bots(self, i: discord.Interaction):
        await self.ctrl.lobby_bots(i, self.game_id, self.bots_sel.values[0])

    async def _on_join(self, i: discord.Interaction):
        await self.ctrl.lobby_join(i, self.game_id)

    async def _on_leave(self, i: discord.Interaction):
        await self.ctrl.lobby_leave(i, self.game_id)

    async def _on_play(self, i: discord.Interaction):
        await self.ctrl.lobby_play(i, self.game_id)

    async def on_error(self, interaction, error, item):
        if is_expired(error):
            return
        await self.ctrl.fail(interaction, self.game_id, error)
        self.stop()

class PlayView(discord.ui.View):
    """Под табло идущей партии.
    options=None: кнопка «Бросить кубик» (подписана именем ходящего; locked - выключена) и «Выйти».
    options=[Move, ...] (фаза выбора): кнопки выбора фигуры (до 4) и «Выйти»."""

    def __init__(self, ctrl, game_id: int, locked: bool = False, turn_name: str = "", options=None):
        super().__init__(timeout=None)
        self.ctrl, self.game_id = ctrl, game_id

        leave = discord.ui.Button(label="Выйти", style=discord.ButtonStyle.secondary,
                                  custom_id=f"ludo:{game_id}:pleave", row=1 if options else 0)
        leave.callback = self._on_leave

        if options:
            for mv in options[:4]:
                if mv.captures:
                    style = discord.ButtonStyle.danger
                elif mv.to == LUDO_FINISH:
                    style = discord.ButtonStyle.success
                else:
                    style = discord.ButtonStyle.primary
                btn = discord.ui.Button(label=pick_text(mv), style=style,
                                        emoji="💥" if mv.captures else None,
                                        custom_id=f"ludo:{game_id}:pick{mv.piece}", row=0)
                btn.callback = self._make_pick(mv.piece)
                self.add_item(btn)
        else:
            # Кнопка живая только в ход человека и подписана его именем: остальные сразу видят, чей ход
            label = f"Бросок: {turn_name}"[:80] if (turn_name and not locked) else "Бросить кубик"
            roll = discord.ui.Button(label=label, emoji="🎲",
                                     style=discord.ButtonStyle.secondary if locked else discord.ButtonStyle.success,
                                     custom_id=f"ludo:{game_id}:roll", row=0, disabled=locked)
            roll.callback = self._on_roll
            self.add_item(roll)
        self.add_item(leave)

    def _make_pick(self, piece: int):
        async def callback(i: discord.Interaction):
            await self.ctrl.pick(i, self.game_id, piece)
        return callback

    async def _on_roll(self, i: discord.Interaction):
        await self.ctrl.roll(i, self.game_id)

    async def _on_leave(self, i: discord.Interaction):
        await self.ctrl.play_leave(i, self.game_id)

    async def on_error(self, interaction, error, item):
        if is_expired(error):
            return
        await self.ctrl.fail(interaction, self.game_id, error)
        self.stop()

class ConfirmLeaveView(discord.ui.View):
    """Скрытое окно «Выйти? Да / Нет»."""

    def __init__(self, ctrl, game_id: int, lobby: bool):
        super().__init__(timeout=60)
        self.ctrl, self.game_id, self.lobby = ctrl, game_id, lobby
        self.origin = None          # взаимодействие, которое открыло это окно (для правки по тайм-ауту)

    @discord.ui.button(label="Да", style=discord.ButtonStyle.danger)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        if self.lobby:
            await self.ctrl.lobby_leave_confirm(interaction, self.game_id)
        else:
            await self.ctrl.play_leave_confirm(interaction, self.game_id)

    @discord.ui.button(label="Нет", style=discord.ButtonStyle.secondary)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Хорошо, вы остаётесь в игре.", view=None)

    async def on_timeout(self):
        if self.origin is not None:
            with suppress(discord.HTTPException):
                await self.origin.edit_original_response(
                    content="Время вышло: вы остались в игре.", view=None)

    async def on_error(self, interaction, error, item):
        if is_expired(error):
            return
        await report_error(interaction, error)
        self.stop()