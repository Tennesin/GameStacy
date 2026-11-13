"""Мини-игра «Тяни верёвку!»."""
import asyncio
import random
import time
from contextlib import suppress

import discord

from .base import MiniGame

DUMMY = -1                                   # «ID» манекена (настоящие ID Discord всегда положительные)
TEAMS = [("🟥", "Красные"), ("🟦", "Синие")]

class TugGame(MiniGame):
    key = "tug"
    title = "Тяни верёвку!"
    pick_min, pick_max = 1, 1                # участие подтверждается сообщением «1»
    GOAL = 3                                 # очков для победы
    SIDES = 6                                # грани кубика
    THROW_SECONDS = 15                       # время на бросок после сигнала
    rules = ("Чтобы участвовать, отправьте в чат число **1**.\n"
             "Стейси случайно разделит всех на две команды: 🟥 Красные и 🟦 Синие. "
             "Если игроков нечётное количество, к меньшей команде добавится манекен, который тоже бросает кубик.\n"
             "После сигнала **БРОСОК!** у вас будет 10 секунд: напишите любое сообщение в чат, "
             "и ваш кубик (1-6) будет брошен. Кто не успел, получает 0.\n"
             "Команда с большей суммой выигрывает раунд. Если очко должно достаться команде, "
             "которая сейчас проигрывает в счёте, оно не прибавляется ей, а снимается с лидера (верёвка тянется назад). "
             "Побеждает команда, первой набравшая 3 очка. Ничья в раунде: верёвка не двигается.")

    def pick_label(self, pick: int) -> str:
        return "участвует"

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

    async def _ack(self, msg: discord.Message):
        with suppress(discord.HTTPException):
            await msg.add_reaction("🎲")

    async def _collect_rolls(self, rollers: set) -> dict:
        """Ждёт сообщений игроков до THROW_SECONDS; на каждого игрока одно сообщение = один бросок."""
        rolls: dict = {}

        def check(m: discord.Message) -> bool:
            return (m.channel.id == self.channel.id and not m.author.bot
                    and m.author.id in rollers and m.author.id not in rolls)

        deadline = time.monotonic() + self.THROW_SECONDS
        while len(rolls) < len(rollers):                      # все бросили: ждать до конца времени незачем
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                msg = await self.bot.wait_for("message", check=check, timeout=left)
            except asyncio.TimeoutError:
                break
            rolls[msg.author.id] = random.randint(1, self.SIDES)
            task = asyncio.create_task(self._ack(msg))
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)
        return rolls

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
        scores = [0, 0]

        board = await self.show(None, self._board(teams, scores, []))
        rnd = 0
        while True:
            rnd += 1
            await self.pause()
            await self.channel.send(f"🎲 **БРОСОК!** Напишите любое сообщение, чтобы бросить кубик "
                                    f"(время: {self.THROW_SECONDS} с).")
            rolls = await self._collect_rolls(rollers)
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

        icon, name = TEAMS[win]
        winners = [u for u in teams[win] if u != DUMMY]
        if winners:
            await self.channel.send(f"🎉 Верёвку перетянула команда {icon} **{name}**!")
        else:
            await self.channel.send(f"🤖 Верёвку перетянул один манекен ({icon} {name}). Людям не повезло.")
        return winners