"""Запуск и остановка мини-игр. Одна мини-игра на канал."""
import asyncio
import logging

from config import DEV_ID
from core.errors import TECH_ERROR_TEXT
from .base import MiniGame, MiniResult

log = logging.getLogger("stacy.mini")

class MiniGameManager:
    def __init__(self, bot):
        self.bot = bot
        self.games: dict = {}        # key -> класс мини-игры
        self.active: dict = {}       # channel_id -> (задача, объект игры)
        self.starters: dict = {}     # channel_id -> ID того, кто запустил (None — запуск из консоли)
        self.background: set = set() # удерживает ссылки на фоновые задачи

    def register(self, cls):
        self.games[cls.key] = cls

    async def play(self, key: str, channel, players=None, starter=None, **kwargs) -> MiniResult:
        """Проводит мини-игру и возвращает результат. Основная игра вызывает именно этот метод.
        players — множество ID участников (None — играет любой в канале).
        starter — ID запустившего (для права остановки).
        kwargs — pick_seconds, roll_delay, log (передаются в конструктор игры)."""
        cls = self.games.get(key)
        if cls is None:
            raise KeyError(f"Нет мини-игры «{key}»")
        if channel.id in self.active:
            raise RuntimeError("В этом канале уже идёт мини-игра")

        game: MiniGame = cls(self.bot, channel, players, **kwargs)
        task = asyncio.create_task(game.run())      # отдельная задача, чтобы stop() не отменял вызывающего
        self.active[channel.id] = (task, game)
        self.starters[channel.id] = starter
        try:
            return await task
        except asyncio.CancelledError:
            if game.stopped:                        # остановили командой, вызывающая игра продолжает работу
                game.trace("=== Игра остановлена командой")
                await channel.send("🛑 Мини-игра остановлена.")
                return MiniResult([], game.names, aborted=True)
            game.trace("=== Игра отменена вместе с вызывающей задачей")
            task.cancel()                           # отменили самого вызывающего
            raise
        except Exception as e:
            game.trace(f"!!! ОШИБКА В ИГРЕ: {type(e).__name__}: {e} (подробный Traceback ниже)")
            log.exception("Сбой мини-игры «%s»", key)
            await channel.send(TECH_ERROR_TEXT.format(dev=DEV_ID))
            return MiniResult([], game.names, aborted=True)
        finally:
            self.active.pop(channel.id, None)
            self.starters.pop(channel.id, None)

    def stop(self, channel_id: int) -> bool:
        item = self.active.get(channel_id)
        if item is None:
            return False
        task, game = item
        game.trace("Получена команда остановки")
        game.stopped = True
        task.cancel()
        return True