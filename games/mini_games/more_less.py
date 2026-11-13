"""Мини-игра «Больше или Меньше?»: прогноз делается кнопками."""
import asyncio
from contextlib import suppress

import discord

from .base import MiniGame

MORE, LESS = "more", "less"
THRESHOLD = 5               # граница прогноза
DIE_SIDES = 10              # кубик 1-10
ROUND_PICK_SECONDS = 15     # время на новый прогноз в следующих раундах
HISTORY_SHOWN = 12

class PredictView(discord.ui.View):
    """Две кнопки: «Меньше 5» и «Больше 5». Повторное нажатие меняет прогноз."""

    def __init__(self, game: "MoreLessGame", players):
        super().__init__(timeout=None)
        self.game = game
        self.players = players                # None — может нажимать любой; иначе множество допущенных ID
        self.picks: dict = {}                 # uid -> MORE / LESS
        self.names: dict = {}                 # uid -> ник
        self.done = asyncio.Event()
        self.open = True

    async def _pick(self, interaction: discord.Interaction, value: str):
        uid = interaction.user.id
        if not self.open:
            await interaction.response.send_message("Приём прогнозов закрыт.", ephemeral=True)
            return
        if self.players is not None and uid not in self.players:
            await interaction.response.send_message("Вы выбыли или не участвуете в этой игре.", ephemeral=True)
            return
        self.picks[uid] = value
        self.names[uid] = interaction.user.display_name
        await interaction.response.send_message(
            f"Ваш прогноз: **{self.game.pick_label(value)}**. Пока идёт приём, его можно изменить.",
            ephemeral=True)
        if self.players is not None and self.players <= self.picks.keys():
            self.done.set()                                   # прогноз дали все выжившие

    @discord.ui.button(label="Меньше 5", emoji="⬇️", style=discord.ButtonStyle.danger)
    async def less_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, LESS)

    @discord.ui.button(label="Больше 5", emoji="⬆️", style=discord.ButtonStyle.success)
    async def more_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, MORE)

class MoreLessGame(MiniGame):
    key = "highlow"
    title = "Больше или Меньше?"
    rules = ("Прогнозируйте, какое число выпадет на кубике (1-10): **больше** или **меньше** 5. "
             "Нажмите одну из двух кнопок под сообщением.\n"
             "Стейси бросает кубик: кто ошибся, тот выбывает. Если выпало ровно 5, ничего не происходит, "
             "кубик бросается снова с теми же прогнозами. "
             "После каждого броска, который кого-то проверил, выжившие делают новый прогноз "
             "(15 секунд; кто не успел, выбывает).\n"
             "Побеждает тот, кто останется последним. Если выбыли все, победителей нет.")

    prompt = "🔮 **Ваш прогноз:** выпадет число больше или меньше 5?"

    def pick_label(self, pick) -> str:
        return "больше 5" if pick == MORE else "меньше 5"

    def _board(self, history: list, alive: set, last, out: set, note: str = "") -> str:
        lines = ["🔮 **Больше или Меньше?**"]
        if last is not None:
            if last == THRESHOLD:
                lines.append(f"Выпало **{last}**: ничего не происходит")
            else:
                side = "больше" if last > THRESHOLD else "меньше"
                lost = ("выбыли " + ", ".join(self.nm(u) for u in sorted(out))) if out else "никто не выбыл"
                lines.append(f"Выпало **{last}** ({side} {THRESHOLD}): {lost}")
        if history:
            tail = ", ".join(map(str, history[-HISTORY_SHOWN:]))
            lines.append("Броски: " + ("… " if len(history) > HISTORY_SHOWN else "") + tail)
        else:
            lines.append("Броски: пока нет")
        lines.append("Остались в игре: " + (", ".join(self.nm(u) for u in sorted(alive)) or "никого"))
        if note:
            lines.append(note)
        return "\n".join(lines)

    # ---------- Приём прогнозов (заменяет приём сообщений из base.py) ----------

    async def collect_picks(self):
        """Сообщение с двумя кнопками. Работает и для первого прогноза, и для следующих раундов."""
        view = PredictView(self, self.players)
        msg = await self.channel.send(f"{self.prompt}\nВремя: {self.pick_seconds} с.", view=view)
        try:
            await asyncio.wait_for(view.done.wait(), timeout=self.pick_seconds)
        except asyncio.TimeoutError:
            pass
        finally:                                              # в том числе при остановке игры командой
            view.open = False
            view.stop()
            self.picks = dict(view.picks)
            self.names.update(view.names)
            with suppress(discord.HTTPException):
                await msg.edit(content=f"🔒 Приём прогнозов закрыт. Принято: {len(self.picks)}", view=None)

    async def _next_predictions(self, alive: set):
        """Новый приём прогнозов только от выживших."""
        self.players = set(alive)
        self.picks = {}
        self.pick_seconds = ROUND_PICK_SECONDS
        self.prompt = (f"🔮 **Новый раунд!** Выжившие, сделайте прогноз: "
                       f"выпадет число больше или меньше {THRESHOLD}?")
        await self.collect_picks()

    async def play(self) -> list:
        alive = set(self.picks)
        history: list = []
        board = await self.show(None, self._board(history, alive, None, set()))
        while True:
            n = await self.roll(DIE_SIDES)
            history.append(n)
            out: set = set()
            if n != THRESHOLD:
                right = MORE if n > THRESHOLD else LESS
                out = {u for u in alive if self.picks.get(u) != right}
                alive -= out
            board = await self.show(board, self._board(history, alive, n, out))
            if len(alive) <= 1:
                break
            if n == THRESHOLD:
                continue                                    # ничего не произошло: прогнозы остаются

            await self._next_predictions(alive)
            silent = alive - self.picks.keys()               # не успели с прогнозом
            if silent:
                alive -= silent
                note = "⏱ Не успели с прогнозом и выбыли: " + ", ".join(self.nm(u) for u in sorted(silent))
                board = await self.show(board, self._board(history, alive, n, out, note))
                if len(alive) <= 1:
                    break
        return sorted(alive)