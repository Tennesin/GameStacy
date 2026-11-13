"""Основа мини-игр: приём выборов сообщениями, броски с паузой, объявление победителя."""

import asyncio
import logging
import random
import time
from contextlib import suppress
from dataclasses import dataclass, field

import discord

from config import MINI_PICK_SECONDS, MINI_ROLL_DELAY

log = logging.getLogger("stacy.mini")

@dataclass
class MiniResult:
    winners: list = field(default_factory=list)   # ID победителей (может быть пусто)
    names: dict = field(default_factory=dict)     # ID -> оригинальный ник
    aborted: bool = False                         # True — игра отменена или остановлена

class MiniGame:
    """Шаблон: правила -> приём выборов -> показ выборов -> play() -> reward().
    Наследник задаёт key/title/rules/pick_min/pick_max и пишет play()."""

    key = ""
    title = ""
    rules = ""
    pick_min = 1
    pick_max = 1

    def __init__(self, bot, channel, players=None, pick_seconds=None, roll_delay=None):
        self.bot = bot
        self.channel = channel
        self.players = set(players) if players else None     # None — играет любой, кто напишет
        self.pick_seconds = pick_seconds or MINI_PICK_SECONDS
        self.roll_delay = MINI_ROLL_DELAY if roll_delay is None else roll_delay
        self.picks: dict = {}       # uid -> выбранное число
        self.names: dict = {}       # uid -> оригинальный ник (author.name)
        self.stopped = False        # остановлена командой -mini stop
        self._bg: set = set()

    # ---------- Хуки для наследников ----------

    async def play(self) -> list:
        """Ход игры после приёма выборов. Возвращает список ID победителей."""
        raise NotImplementedError

    def pick_label(self, pick: int) -> str:
        return str(pick)

    async def reward(self, winners: list):
        """Что получает победитель. Пока — только объявление; позже здесь будут очки."""
        if not winners:
            await self.channel.send("🏁 Победителей нет.")
        elif len(winners) == 1:
            await self.channel.send(f"🏆 Победитель: {self.tag(winners[0])}")
        else:
            await self.channel.send("🏆 Победители: " + ", ".join(self.tag(u) for u in winners))

    # ---------- Общие помощники ----------

    def tag(self, uid: int) -> str:
        """«@оригинальный_ник» простым текстом (без пинга)."""
        return f"@{self.names.get(uid, uid)}"

    def parse_pick(self, text: str):
        t = text.strip()
        if t.isdigit() and self.pick_min <= int(t) <= self.pick_max:
            return int(t)
        return None

    async def pause(self):
        await asyncio.sleep(self.roll_delay)

    async def roll(self, sides: int) -> int:
        """Бросок кубика: сначала пауза, потом результат."""
        await self.pause()
        return random.randint(1, sides)

    async def show(self, msg, text: str):
        """Правит сообщение-табло; если его удалили — отправляет новое. Возвращает актуальное сообщение."""
        if msg is None:
            return await self.channel.send(text)
        try:
            await msg.edit(content=text)
            return msg
        except discord.NotFound:
            return await self.channel.send(text)

    def picks_text(self) -> str:
        lines = ["📋 **Выборы игроков:**"]
        for uid, p in self.picks.items():
            lines.append(f"{self.tag(uid)} — {self.pick_label(p)}")
        return "\n".join(lines)

    # ---------- Приём выборов ----------

    def _check(self, m: discord.Message) -> bool:
        if m.channel.id != self.channel.id or m.author.bot:
            return False
        if self.players is not None and m.author.id not in self.players:
            return False
        return self.parse_pick(m.content) is not None

    async def _after_pick(self, msg: discord.Message, status: discord.Message):
        """Скрывает выбор от остальных и обновляет счётчик. Идёт фоном, чтобы не пропускать новые сообщения."""
        try:
            await msg.delete()
        except discord.HTTPException:                       # нет права «Управление сообщениями» и т. п.
            with suppress(discord.HTTPException):
                await msg.add_reaction("✅")
        with suppress(discord.HTTPException):
            await status.edit(content=f"⏳ Принято выборов: {len(self.picks)}")

    async def collect_picks(self):
        status = await self.channel.send("⏳ Принято выборов: 0")
        deadline = time.monotonic() + self.pick_seconds
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                msg = await self.bot.wait_for("message", check=self._check, timeout=left)
            except asyncio.TimeoutError:
                break
            self.picks[msg.author.id] = self.parse_pick(msg.content)   # передумать можно: считается последнее
            self.names[msg.author.id] = msg.author.name
            task = asyncio.create_task(self._after_pick(msg, status))
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)
            if self.players is not None and self.players <= self.picks.keys():
                break                                       # выбрали все ожидаемые игроки
        with suppress(discord.HTTPException):
            await status.edit(content=f"🔒 Приём выборов закрыт. Принято: {len(self.picks)}")

    # ---------- Сценарий ----------

    async def run(self) -> MiniResult:
        await self.channel.send(
            f"🎮 **Мини-игра «{self.title}»**\n{self.rules}\n"
            f"Время на выбор: {self.pick_seconds} с. Передумать можно: считается последнее сообщение.")
        await self.collect_picks()
        if not self.picks:
            await self.channel.send("😴 Никто не сделал выбор. Мини-игра отменена.")
            return MiniResult([], self.names, aborted=True)
        await self.channel.send(self.picks_text())
        winners = await self.play()
        await self.reward(winners)
        return MiniResult(list(winners), self.names)