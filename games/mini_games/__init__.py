"""Мини-игры Стейси. Подключаются как пакет из GAMES; полные игры вызывают их через bot.mini_games.play(...)."""

import asyncio

from .closer import CloserGame
from .manager import MiniGameManager
from .mines import MinesGame
from .pairs import PairsGame

USAGE = ("Использование:\n"
         "  -mini list                                   список мини-игр\n"
         "  -mini run КОД ID_канала [--seconds N]        запустить (играет любой, кто пишет в канал)\n"
         "  -mini stop ID_канала                         остановить игру в канале")

async def _run_and_report(manager: MiniGameManager, key: str, channel, kwargs: dict):
    try:
        result = await manager.play(key, channel, **kwargs)
    except (KeyError, RuntimeError) as e:
        print(f"⚠ {e}", flush=True)
        return
    names = ", ".join(f"@{result.names.get(u, u)}" for u in result.winners) or "нет"
    state = " (прервана)" if result.aborted else ""
    print(f"✔ Мини-игра «{key}» завершена{state}. Победители: {names}", flush=True)

async def console_mini(bot, args: list):
    manager: MiniGameManager = bot.mini_games
    if not args:
        print(USAGE, flush=True)
        return
    sub = args[0].lower()

    if sub == "list":
        for key, cls in manager.games.items():
            print(f"  {key:<8} — {cls.title}", flush=True)

    elif sub == "stop":
        if len(args) < 2 or not args[1].isdigit():
            print(USAGE, flush=True)
            return
        print("Останавливаю…" if manager.stop(int(args[1])) else "В этом канале нет активной мини-игры.",
              flush=True)

    elif sub == "run":
        if len(args) < 3 or not args[2].isdigit():
            print(USAGE, flush=True)
            return
        key, cid = args[1].lower(), int(args[2])
        kwargs = {}
        if "--seconds" in args:
            i = args.index("--seconds")
            if i + 1 >= len(args) or not args[i + 1].isdigit():
                print("⚠ После --seconds нужно число.", flush=True)
                return
            kwargs["pick_seconds"] = int(args[i + 1])
        if key not in manager.games:
            print(f"⚠ Нет мини-игры «{key}». Список: -mini list", flush=True)
            return
        channel = bot.get_channel(cid)
        if channel is None:
            try:
                channel = await bot.fetch_channel(cid)
            except Exception:
                print(f"⚠ Канал {cid} не найден или нет доступа.", flush=True)
                return
        if not hasattr(channel, "send"):
            print("⚠ В этот канал нельзя отправлять сообщения.", flush=True)
            return
        task = asyncio.create_task(_run_and_report(manager, key, channel, kwargs))
        manager.background.add(task)
        task.add_done_callback(manager.background.discard)
    else:
        print(USAGE, flush=True)

def setup(bot):
    manager = MiniGameManager(bot)
    bot.mini_games = manager
    for cls in (PairsGame, MinesGame, CloserGame):
        manager.register(cls)
    bot.console_commands["-mini"] = console_mini