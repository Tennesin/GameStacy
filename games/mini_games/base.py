"""Основа мини-игр: приём выборов кнопками, броски с паузой, объявление победителя."""
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
    names: dict = field(default_factory=dict)     # ID -> ник на сервере
    aborted: bool = False                         # True — игра отменена или остановлена

class PickView(discord.ui.View):
    """Кнопки выбора. Ответ на нажатие скрытый: в канале ничего не появляется,
    чужие выборы не видны. Повторное нажатие меняет выбор."""

    def __init__(self, game: "MiniGame"):
        super().__init__(timeout=None)
        self.game = game
        self.picks: dict = {}          # uid -> выбранное значение
        self.names: dict = {}          # uid -> ник на сервере
        self.dirty = False             # были изменения с последней правки счётчика
        self.open = True
        self.done = asyncio.Event()    # выбрали все ожидаемые игроки
        for i, (label, value) in enumerate(game.pick_buttons()):
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary,
                                    row=i // game.pick_row_width)
            btn.callback = self._make_callback(label, value)
            self.add_item(btn)

    def _make_callback(self, label: str, value: int):
        async def callback(interaction: discord.Interaction):
            game = self.game
            uid = interaction.user.id
            who = f"{interaction.user.display_name} ({uid})"
            if not self.open:
                game.trace(f"{who} нажал «{label}», но приём выборов уже закрыт")
                await interaction.response.send_message("Приём выборов закрыт.", ephemeral=True)
                return
            if game.players is not None and uid not in game.players:
                game.trace(f"{who} нажал «{label}», но не входит в список игроков: отказано")
                await interaction.response.send_message("Вы не участвуете в этой игре.", ephemeral=True)
                return
            old = self.picks.get(uid)
            self.picks[uid] = value
            self.names[uid] = interaction.user.display_name
            self.dirty = True
            if old is None:
                game.trace(f"{who} нажал «{label}»: выбор принят ({game.pick_label(value)}); "
                           f"всего выборов: {len(self.picks)}")
            else:
                game.trace(f"{who} нажал «{label}»: выбор изменён "
                           f"({game.pick_label(old)} -> {game.pick_label(value)})")
            await interaction.response.send_message(
                f"Ваш выбор: **{game.pick_label(value)}**. Пока идёт приём, его можно изменить.",
                ephemeral=True)
            if game.players is not None and game.players <= self.picks.keys():
                self.done.set()
        return callback

    async def on_error(self, interaction, error, item):
        who = f"{interaction.user.display_name} ({interaction.user.id})"
        self.game.trace(f"ОШИБКА при нажатии кнопки у {who}: {type(error).__name__}: {error}")
        log.error("Ошибка кнопки выбора", exc_info=error)

class MiniGame:
    """Шаблон: правила -> приём выборов -> показ выборов -> play() -> reward().
    Наследник задаёт key/title/rules/pick_min/pick_max и пишет play()."""

    key = ""
    title = ""
    rules = ""
    pick_min = 1
    pick_max = 1
    pick_row_width = 5          # кнопок в ряду (максимум 5)

    def __init__(self, bot, channel, players=None, pick_seconds=None, roll_delay=None, log=False):
        self.bot = bot
        self.channel = channel
        self.players = set(players) if players else None     # None — играет любой, кто нажмёт кнопку
        self.pick_seconds = pick_seconds or MINI_PICK_SECONDS
        self.roll_delay = MINI_ROLL_DELAY if roll_delay is None else roll_delay
        self.log_enabled = bool(log)                          # подробный лог в консоль (только из консоли)
        self.picks: dict = {}       # uid -> выбранное число
        self.names: dict = {}       # uid -> ник на сервере
        self.stopped = False        # остановлена командой
        self._bg: set = set()

    # ---------- Лог в консоль ----------

    def trace(self, text: str):
        """Строка в консоль, если включён --log. Иначе ничего не делает."""
        if not self.log_enabled:
            return
        stamp = self.bot.clock.local_now().strftime("%H:%M:%S")
        print(f"[mini:{self.key} #{self.channel.id}] {stamp} {text}", flush=True)

    def label(self, uid) -> str:
        """«Ник (ID)» для строк лога."""
        return f"{self.names.get(uid, '?')} ({uid})"

    # ---------- Хуки для наследников ----------

    async def play(self) -> list:
        """Ход игры после приёма выборов. Возвращает список ID победителей."""
        raise NotImplementedError

    def pick_label(self, pick: int) -> str:
        return str(pick)

    def pick_buttons(self) -> list:
        """[(подпись кнопки, значение выбора)]. По умолчанию — по кнопке на каждое число диапазона."""
        return [(str(n), n) for n in range(self.pick_min, self.pick_max + 1)]

    async def reward(self, winners: list):
        """Что получает победитель."""
        ping = discord.AllowedMentions(users=True, roles=False, everyone=False)
        if not winners:
            await self.channel.send("🏁 Победителей нет.")
        elif len(winners) == 1:
            await self.channel.send(f"🏆 Победитель: {self.mention(winners[0])}",
                                    allowed_mentions=ping)
        else:
            await self.channel.send("🏆 Победители: " + ", ".join(self.mention(u) for u in winners),
                                    allowed_mentions=ping)

    # ---------- Общие помощники ----------

    def nm(self, uid: int) -> str:
        """Ник на сервере простым текстом: без «@» и без тега."""
        raw = str(self.names.get(uid, uid))
        return discord.utils.escape_mentions(discord.utils.escape_markdown(raw))

    def mention(self, uid: int) -> str:
        """Настоящий тег игрока (пингует)."""
        return f"<@{uid}>"

    def parse_pick(self, text: str):
        """Оставлено для совместимости: разбор числа из текста (кнопки им не пользуются)."""
        t = text.strip()
        if t.isdigit() and self.pick_min <= int(t) <= self.pick_max:
            return int(t)
        return None

    async def pause(self):
        await asyncio.sleep(self.roll_delay)

    async def roll(self, sides: int) -> int:
        """Бросок кубика: сначала пауза, потом результат."""
        await self.pause()
        n = random.randint(1, sides)
        self.trace(f"Бросок кубика (1-{sides}): выпало {n}")
        return n

    async def show(self, msg, text: str):
        """Правит сообщение-табло; если его удалили — отправляет новое. Возвращает актуальное сообщение."""
        if msg is None:
            return await self.channel.send(text)
        try:
            await msg.edit(content=text)
            return msg
        except discord.NotFound:
            self.trace("Сообщение-табло было удалено: отправляю новое")
            return await self.channel.send(text)

    def picks_text(self) -> str:
        lines = ["📋 **Выборы игроков:**"]
        for uid, p in self.picks.items():
            lines.append(f"{self.nm(uid)} — {self.pick_label(p)}")
        return "\n".join(lines)

    # ---------- Приём выборов (кнопки) ----------

    async def collect_picks(self):
        view = PickView(self)
        ends = int(self.bot.clock.now().timestamp()) + self.pick_seconds
        head = f"👇 **Сделайте выбор кнопкой.** Приём закрывается <t:{ends}:R>."

        def text() -> str:
            return f"{head}\n⏳ Принято выборов: {len(view.picks)}"

        msg = await self.channel.send(text(), view=view)
        self.trace(f"Приём выборов открыт на {self.pick_seconds} с")
        reason = "время вышло"
        deadline = time.monotonic() + self.pick_seconds
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                try:
                    await asyncio.wait_for(view.done.wait(), timeout=min(1.0, left))
                    reason = "выбрали все ожидаемые игроки"
                    break
                except asyncio.TimeoutError:
                    pass
                if view.dirty:                              # счётчик правим не чаще раза в секунду
                    view.dirty = False
                    with suppress(discord.HTTPException):
                        await msg.edit(content=text())
        except asyncio.CancelledError:
            reason = "остановка командой"
            raise
        finally:                                            # в том числе при остановке игры командой
            view.open = False
            view.stop()
            self.picks = dict(view.picks)
            self.names.update(view.names)
            self.trace(f"Приём выборов закрыт ({reason}). Принято: {len(self.picks)}")
            for uid, p in self.picks.items():
                self.trace(f"  итоговый выбор: {self.label(uid)} -> {self.pick_label(p)}")
            with suppress(discord.HTTPException):
                await msg.edit(content=f"🔒 Приём выборов закрыт. Принято: {len(self.picks)}", view=None)

    # ---------- Сценарий ----------

    async def run(self) -> MiniResult:
        who = "любой желающий" if self.players is None else f"только {sorted(self.players)}"
        self.trace(f"=== Запуск «{self.title}» в канале {self.channel.id}; "
                   f"время на выбор: {self.pick_seconds} с; задержка бросков: {self.roll_delay} с; игроки: {who}")
        await self.channel.send(
            f"🎮 **Мини-игра «{self.title}»**\n{self.rules}\n"
            f"Время на выбор: {self.pick_seconds} с. Передумать можно: считается последний выбор.")
        await self.collect_picks()
        if not self.picks:
            self.trace("Никто не сделал выбор: игра отменена")
            await self.channel.send("😴 Никто не сделал выбор. Мини-игра отменена.")
            return MiniResult([], self.names, aborted=True)
        await self.channel.send(self.picks_text())
        self.trace("Начало игры (play)")
        winners = await self.play()
        self.trace("Победители: " + (", ".join(self.label(u) for u in winners) or "нет"))
        await self.reward(winners)
        self.trace("=== Игра завершена")
        return MiniResult(list(winners), self.names)