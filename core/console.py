"""Консоль Стейси: команды вводятся в терминале, где запущен бот."""

import asyncio
import logging
import shlex
import sys
import threading

log = logging.getLogger("stacy.console")

_tasks: set = set()

def start_console(bot):
    """Вызывать из setup_hook (когда цикл событий уже работает)."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def reader():
        for line in sys.stdin:
            loop.call_soon_threadsafe(queue.put_nowait, line)

    threading.Thread(target=reader, daemon=True).start()
    task = loop.create_task(_console_loop(bot, queue))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

async def _console_loop(bot, queue: asyncio.Queue):
    await bot.wait_until_ready()
    while True:
        line = (await queue.get()).strip()
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError as e:
            print(f"Ошибка разбора команды: {e}", flush=True)
            continue
        handler = bot.console_commands.get(parts[0].lower())
        if handler is None:
            print(f"Неизвестная команда: {parts[0]}. Доступно: {', '.join(bot.console_commands) or 'ничего'}",
                  flush=True)
            continue
        try:
            await handler(bot, parts[1:])
        except Exception:
            log.exception("Ошибка в консольной команде")