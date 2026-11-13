"""Настройки Стейси: .env, пути, часовой пояс, логирование."""

import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
GAMES_DATA_DIR = DATA_DIR / "games"
GAMES_DATA_DIR.mkdir(parents=True, exist_ok=True)

TOKEN = os.environ.get("DISCORD_TOKEN")
DEV_ID = int(os.environ.get("DEV_ID") or 0)
DEV_GUILD_ID = int(os.environ.get("DEV_GUILD_ID") or 0)

# Игровые сутки привязаны к Ташкенту
TZ_NAME = "Asia/Tashkent"
TZ = ZoneInfo(TZ_NAME)

CLOCK_SYNC_SECONDS = 300   # сверка с Discord раз в 5 минут
# Мини-игры
MINI_ROLL_DELAY = 3        # пауза перед каждым броском кубика, сек
MINI_PICK_SECONDS = 30     # сколько длится приём выборов, сек
LOBBY_TIMEOUT_SECONDS = 100

# Подключаемые игры: пакет должен иметь функцию setup(bot)
GAMES = ["games.mini_games", "games.x_o"]   # мини-игры подключаются первыми

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("stacy")