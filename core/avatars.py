"""Загрузка аватарок с CDN Discord для табло. Кэш в памяти маленький и ограниченный."""
import asyncio
import logging
import time
from collections import OrderedDict
from urllib.parse import urlsplit

import aiohttp

log = logging.getLogger("stacy.jumanji")

ALLOWED_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
MAX_BYTES = 500_000
CACHE_LIMIT = 64               # аватарок в памяти (по ~20 КБ)
TIMEOUT_SECONDS = 6
FAIL_PAUSE_SECONDS = 300       # неудачную ссылку не пробуем повторно столько секунд

_cache: OrderedDict = OrderedDict()      # url -> bytes
_failed: dict = {}                       # url -> до какого момента (monotonic) не пробуем

def avatar_url(user) -> str:
    """Ссылка на статичную PNG-аватарку 128 px (у анимированных — первый кадр).
    Подходит и для обычного аватара, и для стандартного (если своего нет)."""
    return user.display_avatar.replace(size=128, format="png").url

def _allowed(url: str) -> bool:
    try:
        p = urlsplit(url)
    except ValueError:
        return False
    return p.scheme == "https" and p.hostname in ALLOWED_HOSTS

async def _download(session: aiohttp.ClientSession, url: str):
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
    except Exception as e:                                   # сеть и таймауты не должны ронять игру
        log.warning("Аватарка не скачалась (%s): %s", type(e).__name__, e)
        return None
    return data if 0 < len(data) <= MAX_BYTES else None

async def load_avatars(urls: dict) -> dict:
    """{pid: ссылка} -> {pid: байты картинки}. Кого не удалось скачать — в результате нет."""
    out, need = {}, {}
    now = time.monotonic()
    for pid, url in urls.items():
        if not url:
            continue
        if url in _cache:
            _cache.move_to_end(url)
            out[pid] = _cache[url]
        elif _allowed(url) and _failed.get(url, 0) <= now:
            need[pid] = url

    if need:
        uniq = list(set(need.values()))
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            results = await asyncio.gather(*(_download(session, u) for u in uniq))
        got = dict(zip(uniq, results))
        for url, data in got.items():
            if data is None:
                _failed[url] = time.monotonic() + FAIL_PAUSE_SECONDS
            else:
                _cache[url] = data
        while len(_cache) > CACHE_LIMIT:
            _cache.popitem(last=False)
        for pid, url in need.items():
            if got.get(url) is not None:
                out[pid] = got[url]
    return out