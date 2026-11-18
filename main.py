"""GameStacy: запуск бота, часы, подключение игр и корректная остановка."""
import hashlib
import asyncio
import importlib
import discord
from discord import app_commands

from config import CLOCK_SYNC_SECONDS, DATA_DIR, GAMES, TOKEN, TZ, TZ_NAME, log
from core.clock import Clock
from core.db import MAIN_MIGRATIONS, connect
from core.errors import report_error
from core.console import start_console
from core.full_games import console_game

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
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, max_messages=None)
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = on_tree_error
        self.db = connect(DATA_DIR / "stacy.db", MAIN_MIGRATIONS)
        self.clock = Clock(TZ, self.db)
        self.console_commands = {}
        self.full_games = {}
        self.console_commands["-game"] = console_game
        self._clock_task = None

    async def setup_hook(self):
        await self.clock.sync()                                 # мгновенная сверка при запуске
        self._clock_task = asyncio.create_task(self._clock_loop())
        start_console(self)

        register_commands(self)
        for module in GAMES:
            importlib.import_module(module).setup(self)
            log.info("Игра подключена: %s", module)

        # Глобальные команды: видны на всех серверах, где есть бот
        await self._sync_commands_if_changed()

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

    async def _sync_commands_if_changed(self):
        """Синхронизирует команды с Discord, только если изменился их набор (имена и описания)."""
        sig = repr(sorted((c.name, c.description, getattr(c, "guild_only", None))
                          for c in self.tree.get_commands()))
        digest = hashlib.sha256(sig.encode("utf-8")).hexdigest()
        row = self.db.execute("SELECT value FROM meta WHERE key='tree_digest'").fetchone()
        if row and row[0] == digest:
            log.info("Набор команд не менялся: синхронизация пропущена")
            return
        await self.tree.sync()
        with self.db:
            self.db.execute("INSERT INTO meta VALUES ('tree_digest', ?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (digest,))
        log.info("Команды синхронизированы глобально (на новых серверах могут появиться не сразу)")

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Не задан DISCORD_TOKEN: создайте файл .env со строкой DISCORD_TOKEN=...")
    StacyBot().run(TOKEN, log_handler=None)