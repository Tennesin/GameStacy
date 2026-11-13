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
        self.background: set = set() # удерживает ссылки на фоновые задачи консоли

    def register(self, cls):
        self.games[cls.key] = cls

    async def play(self, key: str, channel, players=None, **kwargs) -> MiniResult:
        """Проводит мини-игру и возвращает результат. Основная игра вызывает именно этот метод.
        players — множество ID участников (None — играет любой в канале)."""
        cls = self.games.get(key)
        if cls is None:
            raise KeyError(f"Нет мини-игры «{key}»")
        if channel.id in self.active:
            raise RuntimeError("В этом канале уже идёт мини-игра")

        game: MiniGame = cls(self.bot, channel, players, **kwargs)
        task = asyncio.create_task(game.run())      # отдельная задача, чтобы stop() не отменял вызывающего
        self.active[channel.id] = (task, game)
        try:
            return await task
        except asyncio.CancelledError:
            if game.stopped:                        # остановили командой, вызывающая игра продолжает работу
                await channel.send("🛑 Мини-игра остановлена.")
                return MiniResult([], game.names, aborted=True)
            task.cancel()                           # отменили самого вызывающего
            raise
        except Exception:
            log.exception("Сбой мини-игры «%s»", key)
            await channel.send(TECH_ERROR_TEXT.format(dev=DEV_ID))
            return MiniResult([], game.names, aborted=True)
        finally:
            self.active.pop(channel.id, None)

    def stop(self, channel_id: int) -> bool:
        item = self.active.get(channel_id)
        if item is None:
            return False
        task, game = item
        game.stopped = True
        task.cancel()
        return True