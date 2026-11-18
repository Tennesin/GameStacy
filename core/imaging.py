"""Общее для игр с картинками (Pillow): загрузчик шрифта и ОДИН семафор рендера на весь бот.
На сервере 1 ГБ памяти: две картинки одновременно (из разных игр) рисовать нельзя."""
import asyncio
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_ROOT = Path(__file__).resolve().parents[1]
FONT_PATHS = [
    _ROOT / "assets" / "fonts" / "DejaVuSans-Bold.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
]

@lru_cache(maxsize=8)
def font(size: int):
    """(шрифт, умеет_ли_кириллицу)."""
    for p in FONT_PATHS:
        try:
            return ImageFont.truetype(str(p), size), True
        except OSError:
            continue
    try:
        return ImageFont.load_default(size), False
    except TypeError:                                         # старая Pillow: размер не поддерживается
        return ImageFont.load_default(), False

_lock = None

async def render_in_thread(func, *args):
    """Выполняет тяжёлую синхронную отрисовку в потоке; не больше одной за раз на весь бот."""
    global _lock
    if _lock is None:
        _lock = asyncio.Semaphore(1)
    async with _lock:
        return await asyncio.to_thread(func, *args)