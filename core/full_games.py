"""Реестр полных игр и консольная команда -game (запуск набора из терминала)."""
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

import discord

log = logging.getLogger("stacy.games")

@dataclass(frozen=True)
class FullGame:
    key: str                                   # код игры: "jumanji"
    title: str                                 # название для людей
    launch: Callable[..., Awaitable[int]]      # async launch(channel, home_id=None) -> ID партии

USAGE = ("Использование:\n"
         "  -game list                                       список полных игр\n"
         "  -game run КОД ID_канала [--gameid ID_канала]     открыть набор в канале или ветке;\n"
         "                                                   --gameid: играть можно только в этом\n"
         "                                                   канале и его ветках")

async def _get_channel(bot, cid: int):
    channel = bot.get_channel(cid)
    if channel is None:
        try:
            channel = await bot.fetch_channel(cid)
        except discord.HTTPException:
            return None
    return channel

async def console_game(bot, args: list):
    games = bot.full_games
    if not args:
        print(USAGE, flush=True)
        return
    sub = args[0].lower()

    if sub == "list":
        for key, g in games.items():
            print(f"  {key:<10} — {g.title}", flush=True)
        print(f"Игр: {len(games)}", flush=True)

    elif sub == "run":
        if len(args) < 3 or not args[2].isdigit():
            print(USAGE, flush=True)
            return
        game = games.get(args[1].lower())
        if game is None:
            print(f"⚠ Нет игры «{args[1]}». Список: -game list", flush=True)
            return

        home_id = None
        if "--gameid" in args:
            i = args.index("--gameid")
            if i + 1 >= len(args) or not args[i + 1].isdigit():
                print("⚠ После --gameid нужен ID канала.", flush=True)
                return
            home_id = int(args[i + 1])

        channel = await _get_channel(bot, int(args[2]))
        if channel is None or getattr(channel, "guild", None) is None or not hasattr(channel, "send"):
            print(f"⚠ Канал {args[2]} не найден, недоступен или в него нельзя писать.", flush=True)
            return
        if home_id and channel.id != home_id and getattr(channel, "parent_id", None) != home_id:
            print("⚠ Канал для табло должен быть самим каналом --gameid или его веткой.", flush=True)
            return
        if not channel.permissions_for(channel.guild.me).send_messages:
            print("⚠ У бота нет права писать в этот канал.", flush=True)
            return

        try:
            game_id = await game.launch(channel, home_id)
        except discord.HTTPException as e:
            print(f"⚠ Discord отклонил запуск (HTTP {e.status}): {e.text}", flush=True)
            return
        print(f"✔ «{game.title}»: набор открыт, партия #{game_id}, канал «{channel}».", flush=True)
    else:
        print(USAGE, flush=True)