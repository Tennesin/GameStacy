"""GameStacy: запуск бота, часы, подключение игр и корректная остановка."""

import asyncio
import importlib
import discord
from discord import app_commands

from config import CLOCK_SYNC_SECONDS, DATA_DIR, DEV_GUILD_ID, GAMES, TOKEN, TZ, TZ_NAME, log
from core.clock import Clock
from core.db import MAIN_MIGRATIONS, connect
from core.errors import report_error
from core.console import start_console

async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await report_error(interaction, getattr(error, "original", error))

def register_commands(bot: "StacyBot"):
    @bot.tree.command(name="time", description="Игровое и реальное время Стейси")
    async def time_cmd(interaction: discord.Interaction):
        c = bot.clock
        local = c.local_now()
        synced = c.last_sync.astimezone(TZ).strftime("%H:%M:%S") if c.last_sync else "—"
        await interaction.response.send_message(
            f"🕒 Игровое время ({TZ_NAME}): **{local:%d.%m.%Y %H:%M:%S}**\n"
            f"Игровые сутки: {local:%d.%m.%Y}\n"
            f"UTC: {c.now():%H:%M:%S}\n"
            f"Источник: {c.source}; последняя сверка: {synced}; поправка: {c.drift:+.1f} с",
            ephemeral=True)

class StacyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True          # привилегированный
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = on_tree_error
        self.db = connect(DATA_DIR / "stacy.db", MAIN_MIGRATIONS)
        self.clock = Clock(TZ, self.db)
        self.console_commands = {}
        self._clock_task = None

    async def setup_hook(self):
        await self.clock.sync()                                 # мгновенная сверка при запуске
        self._clock_task = asyncio.create_task(self._clock_loop())
        start_console(self)

        register_commands(self)
        for module in GAMES:
            importlib.import_module(module).setup(self)
            log.info("Игра подключена: %s", module)

        if DEV_GUILD_ID:
            guild = discord.Object(id=DEV_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Команды синхронизированы с тестовым сервером %s", DEV_GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Команды синхронизированы глобально (появятся не сразу)")

    async def _clock_loop(self):
        while True:
            await asyncio.sleep(CLOCK_SYNC_SECONDS)
            try:
                await self.clock.sync()
            except Exception:
                log.exception("Сбой сверки часов")

    async def on_ready(self):
        log.info("Стейси запущена как %s", self.user)

    async def close(self):
        if self._clock_task:
            self._clock_task.cancel()
        self.clock.save_seen()                                  # момент остановки для следующего запуска
        log.info("Стейси остановлена.")
        await super().close()

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Не задан DISCORD_TOKEN: создайте файл .env со строкой DISCORD_TOKEN=...")
    StacyBot().run(TOKEN, log_handler=None)