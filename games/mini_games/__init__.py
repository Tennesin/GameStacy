"""Мини-игры Стейси. Подключаются как пакет из GAMES; полные игры вызывают их через bot.mini_games.play(...)."""
import asyncio
import logging

import discord
from discord import app_commands

from config import DEV_ID
from core.errors import report_error
from .closer import CloserGame
from .manager import MiniGameManager
from .mines import MinesGame
from .more_less import MoreLessGame
from .pairs import PairsGame
from .tug import TugGame

log = logging.getLogger("stacy.mini")

USAGE = ("Использование:\n"
         "  -mini list                                   список мини-игр\n"
         "  -mini run КОД ID_канала [--seconds N] [--log True|False]\n"
         "                                               запустить (играет любой, кто нажмёт кнопку);\n"
         "                                               --log True — подробный лог событий игры в консоль\n"
         "                                               (по умолчанию False; просто --log = True)\n"
         "  -mini stop ID_канала                         остановить игру в канале")

_TRUE = {"true", "1", "yes", "on", "да", "y"}
_FALSE = {"false", "0", "no", "off", "нет", "n"}

# ---------- Консоль (инструмент разработчика) ----------

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
        if "--log" in args:
            i = args.index("--log")
            val = args[i + 1].lower() if i + 1 < len(args) else None
            if val is None or val.startswith("--"):
                kwargs["log"] = True                      # просто «--log» без значения
            elif val in _TRUE:
                kwargs["log"] = True
            elif val in _FALSE:
                kwargs["log"] = False
            else:
                print("⚠ После --log нужно True или False.", flush=True)
                return
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

# ---------- Слеш-команды: /mini и /mini_stop ----------

def _short(text: str) -> str:
    """Первая строка правил для описания пункта списка (лимит Discord — 100 символов)."""
    first = text.split("\n")[0].replace("**", "")
    return first if len(first) <= 100 else first[:99] + "…"

async def _run_from_menu(manager: MiniGameManager, key: str, channel, starter_id: int):
    try:
        result = await manager.play(key, channel, starter=starter_id)
        log.info("Мини-игра «%s» завершена (прервана=%s), победителей: %d",
                 key, result.aborted, len(result.winners))
    except (KeyError, RuntimeError) as e:
        log.warning("Запуск мини-игры «%s» не удался: %s", key, e)

class GameSelectView(discord.ui.View):
    """Скрытое меню выбора мини-игры (видно только тому, кто вызвал /mini)."""

    def __init__(self, manager: MiniGameManager):
        super().__init__(timeout=120)
        self.manager = manager
        options = [discord.SelectOption(label=cls.title, value=key, description=_short(cls.rules))
                   for key, cls in manager.games.items()]
        self.select = discord.ui.Select(placeholder="Выберите мини-игру", options=options)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        manager = self.manager
        key = self.select.values[0]
        cls = manager.games[key]
        channel = interaction.channel

        if channel is None or not hasattr(channel, "send"):
            await interaction.response.edit_message(content="⚠ Сюда нельзя отправлять сообщения.", view=None)
            return
        if channel.id in manager.active:
            await interaction.response.edit_message(
                content="⚠ В этом канале уже идёт мини-игра. Дождитесь её конца.", view=None)
            return
        me = interaction.guild.me if interaction.guild else None
        if me is not None and not channel.permissions_for(me).send_messages:
            await interaction.response.edit_message(
                content="⚠ У бота нет права писать в этот канал.", view=None)
            return

        task = asyncio.create_task(_run_from_menu(manager, key, channel, interaction.user.id))
        manager.background.add(task)
        task.add_done_callback(manager.background.discard)
        await interaction.response.edit_message(content=f"✅ Запускаю «{cls.title}»…", view=None)
        self.stop()

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)
        self.stop()

def setup(bot):
    manager = MiniGameManager(bot)
    bot.mini_games = manager
    for cls in (PairsGame, MinesGame, CloserGame, TugGame, MoreLessGame):
        manager.register(cls)
    bot.console_commands["-mini"] = console_mini

    @app_commands.command(name="mini", description="Мини-игры: выбрать игру и запустить её в этом канале")
    @app_commands.guild_only()
    async def mini_cmd(interaction: discord.Interaction):
        if interaction.channel_id in manager.active:
            await interaction.response.send_message("В этом канале уже идёт мини-игра.", ephemeral=True)
            return
        await interaction.response.send_message("Выберите мини-игру:", view=GameSelectView(manager),
                                                ephemeral=True)

    @app_commands.command(name="mini_stop", description="Остановить мини-игру в этом канале")
    @app_commands.guild_only()
    async def mini_stop_cmd(interaction: discord.Interaction):
        cid = interaction.channel_id
        if cid not in manager.active:
            await interaction.response.send_message("В этом канале нет активной мини-игры.", ephemeral=True)
            return
        uid = interaction.user.id
        allowed = (uid == manager.starters.get(cid)
                   or interaction.permissions.manage_messages
                   or uid == DEV_ID)
        if not allowed:
            await interaction.response.send_message(
                "Остановить игру может тот, кто её запустил, или модератор.", ephemeral=True)
            return
        manager.stop(cid)
        await interaction.response.send_message("Останавливаю…", ephemeral=True)

    bot.tree.add_command(mini_cmd)
    bot.tree.add_command(mini_stop_cmd)