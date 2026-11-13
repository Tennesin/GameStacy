"""Мини-игра «Тяни верёвку!»: броски кубика делаются кнопкой 🎲."""
import asyncio
import random
from contextlib import suppress

import discord

from .base import MiniGame

DUMMY = -1                                   # «ID» манекена (настоящие ID Discord всегда положительные)
TEAMS = [("🟥", "Красные"), ("🟦", "Синие")]

class RollView(discord.ui.View):
    """Одна кнопка 🎲 на всю игру. Между раундами блокируется, на время раунда включается."""

    def __init__(self, game: "TugGame", rollers: set):
        super().__init__(timeout=None)
        self.game = game
        self.rollers = rollers                # ID людей, которые вправе бросать
        self.rolls: dict = {}                 # uid -> выпавшее число
        self.all_rolled = asyncio.Event()
        self.open = False
        self.rnd = 0

    def begin(self, rnd: int):
        """Новый раунд: кнопка снова доступна, броски обнулены."""
        self.rnd = rnd
        self.rolls = {}
        self.all_rolled.clear()
        self.open = True
        self.roll_button.disabled = False

    def close(self) -> dict:
        """Конец приёма бросков: кнопка недоступна. Возвращает броски раунда."""
        self.open = False
        self.roll_button.disabled = True
        return dict(self.rolls)

    @discord.ui.button(emoji="🎲", style=discord.ButtonStyle.success)
    async def roll_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid not in self.rollers:
            await interaction.response.send_message("Вы не участвуете в этой игре.", ephemeral=True)
            return
        if not self.open:
            await interaction.response.send_message("Приём бросков сейчас закрыт.", ephemeral=True)
            return
        if uid in self.rolls:
            await interaction.response.send_message("Вы уже бросили кубик в этом раунде.", ephemeral=True)
            return

        self.rolls[uid] = random.randint(1, self.game.SIDES)
        text = self.game._throw_text(self.rnd, len(self.rolls), len(self.rollers))
        await interaction.response.edit_message(content=text, view=self)
        if len(self.rolls) >= len(self.rollers):          # сигнал только после ответа на нажатие,
            self.all_rolled.set()                          # чтобы правка игры не обогнала правку кнопки

class TugGame(MiniGame):
    key = "tug"
    title = "Тяни верёвку!"
    pick_min, pick_max = 1, 1                # участие подтверждается сообщением «1»
    GOAL = 3                                 # очков для победы
    SIDES = 6                                # грани кубика
    THROW_SECONDS = 15                       # время на бросок после сигнала
    rules = ("Чтобы участвовать, нажмите кнопку **«Участвовать»**.\n"
             "Стейси случайно разделит всех на две команды: 🟥 Красные и 🟦 Синие. "
             "Если игроков нечётное количество, к меньшей команде добавится манекен, который тоже бросает кубик.\n"
             "Под сообщением **БРОСОК!** появится кнопка 🎲: нажмите её в течение 15 секунд, "
             "и ваш кубик (1-6) будет брошен. Кто не успел, получает 0. Когда нажали все, раунд завершается досрочно.\n"
             "Команда с большей суммой выигрывает раунд. Если очко должно достаться команде, "
             "которая сейчас проигрывает в счёте, оно не прибавляется ей, а снимается с лидера (верёвка тянется назад). "
             "Побеждает команда, первой набравшая 3 очка. Ничья в раунде: верёвка не двигается.")

    def pick_label(self, pick: int) -> str:
        return "участвует"

    def pick_buttons(self) -> list:
        return [("Участвовать", 1)]

    # ---------- Оформление ----------

    def _team_line(self, idx: int, teams: list) -> str:
        icon, name = TEAMS[idx]
        return f"{icon} **{name}:** " + ", ".join(self.nm(u) for u in teams[idx])

    def _rope(self, scores: list) -> str:
        """Верёвка: маркер смещается к команде, которая ведёт."""
        pos = scores[0] - scores[1]
        cells = ["▫️"] * (2 * self.GOAL + 1)
        cells[self.GOAL - pos] = "🪢"
        return f"{TEAMS[0][0]} " + "".join(cells) + f" {TEAMS[1][0]}"

    def _rolls_line(self, idx: int, teams: list, rolls: dict) -> str:
        parts = [f"{self.nm(u)} **{rolls[u]}**" if u in rolls else f"{self.nm(u)} —" for u in teams[idx]]
        return f"{TEAMS[idx][0]} " + ", ".join(parts)

    def _board(self, teams: list, scores: list, details: list) -> str:
        lines = ["🪢 **Тяни верёвку!**",
                 self._team_line(0, teams),
                 self._team_line(1, teams),
                 f"Счёт: {TEAMS[0][0]} **{scores[0]}** — **{scores[1]}** {TEAMS[1][0]} (до {self.GOAL})",
                 self._rope(scores)]
        text = "\n".join(lines + ([""] + details if details else []))
        if len(text) > 1900 and details:                      # лимит Discord 2000 символов: оставляем только итог раунда
            text = "\n".join(lines + ["", details[0]])
        return text

    def _throw_text(self, rnd: int, done: int, total: int, closed: bool = False) -> str:
        head = "🎲 **БРОСОК!**" + (f" x{rnd}" if rnd > 1 else "")
        if closed:
            return f"{head}\nПриём бросков закрыт: бросили {done} из {total}."
        return f"{head}\nНажмите кнопку 🎲 (до {self.THROW_SECONDS} с). Бросили: {done}/{total}"

    async def _set_throw(self, msg, text: str, view: RollView):
        """Правит сообщение с кнопкой; если его удалили, отправляет заново."""
        if msg is not None:
            try:
                await msg.edit(content=text, view=view)
                return msg
            except discord.NotFound:
                pass
        return await self.channel.send(text, view=view)

    # ---------- Логика ----------

    def _apply(self, scores: list, winner: int) -> str:
        """Меняет счёт по итогам раунда и возвращает пояснение."""
        loser = 1 - winner
        w_icon, w_name = TEAMS[winner]
        l_icon, l_name = TEAMS[loser]
        if scores[loser] > scores[winner]:
            scores[loser] -= 1
            return f"{w_icon} {w_name} тянут назад: {l_icon} {l_name} теряют очко"
        scores[winner] += 1
        return f"{w_icon} {w_name} получают очко"

    async def play(self) -> list:
        players = list(self.picks)
        random.shuffle(players)
        half = (len(players) + 1) // 2
        teams = [players[:half], players[half:]]
        has_dummy = len(teams[0]) > len(teams[1])
        if has_dummy:                                         # нечётное число игроков: манекен в меньшую команду
            teams[1].append(DUMMY)
            self.names[DUMMY] = "Манекен 🤖"
        random.shuffle(teams)                                 # чтобы манекен не всегда оказывался у синих
        rollers = set(players)
        total = len(rollers)
        scores = [0, 0]

        view = RollView(self, rollers)
        throw_msg = None
        board = await self.show(None, self._board(teams, scores, []))
        await self.pause()
        rnd = 0
        try:
            while True:
                rnd += 1
                # 1. Кнопка включена, ждём нажатий (или конца времени)
                view.begin(rnd)
                throw_msg = await self._set_throw(throw_msg, self._throw_text(rnd, 0, total), view)
                try:
                    await asyncio.wait_for(view.all_rolled.wait(), timeout=self.THROW_SECONDS)
                except asyncio.TimeoutError:
                    pass

                # 2. Кнопка выключена, показываем итоги
                rolls = view.close()
                throw_msg = await self._set_throw(
                    throw_msg, self._throw_text(rnd, len(rolls), total, closed=True), view)
                if has_dummy:
                    rolls[DUMMY] = random.randint(1, self.SIDES)

                totals = [sum(rolls.get(u, 0) for u in t) for t in teams]
                if totals[0] == totals[1]:
                    verdict = "ничья: верёвка не сдвинулась"
                else:
                    verdict = self._apply(scores, 0 if totals[0] > totals[1] else 1)
                details = [f"**Раунд {rnd}:** {TEAMS[0][0]} {totals[0]} — {totals[1]} {TEAMS[1][0]} · {verdict}",
                           self._rolls_line(0, teams, rolls),
                           self._rolls_line(1, teams, rolls)]
                board = await self.show(board, self._board(teams, scores, details))

                win = next((i for i in (0, 1) if scores[i] >= self.GOAL), None)
                if win is not None:
                    break
                await self.pause()                            # кнопка остаётся недоступной, пока игроки читают итоги
        finally:
            view.stop()                                       # в том числе при остановке игры командой
            if throw_msg is not None:
                with suppress(discord.HTTPException):
                    await throw_msg.edit(view=None)

        icon, name = TEAMS[win]
        winners = [u for u in teams[win] if u != DUMMY]
        if winners:
            await self.channel.send(f"🎉 Верёвку перетянула команда {icon} **{name}**!")
        else:
            await self.channel.send(f"🤖 Верёвку перетянул один манекен ({icon} {name}). Людям не повезло.")
        return winners