"""Собственные часы Стейси: опорная точка + monotonic, сверка с временем Discord."""

import logging
import time
from datetime import date, datetime, timedelta, timezone, tzinfo
from email.utils import parsedate_to_datetime
from typing import Optional

import aiohttp

log = logging.getLogger("stacy.clock")

DISCORD_TIME_URL = "https://discord.com/api/v10/gateway"

def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d} д {h} ч {m} мин"
    if h:
        return f"{h} ч {m} мин"
    if m:
        return f"{m} мин {s} с"
    return f"{s} с"

async def fetch_discord_time() -> Optional[datetime]:
    """Время по заголовку Date ответа Discord (точность до секунды). None — не вышло."""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(DISCORD_TIME_URL) as resp:
                return parsedate_to_datetime(resp.headers["Date"]).astimezone(timezone.utc)
    except Exception as e:
        log.warning("Не удалось получить время Discord: %s", e)
        return None

class Clock:
    def __init__(self, tz: tzinfo, db):
        self.tz = tz
        self.db = db
        self._anchor = datetime.now(timezone.utc)   # до первой сверки — время ОС
        self._mono = time.monotonic()
        self._last = self._anchor                   # защита от хода назад
        self.source = "не синхронизировано"
        self.last_sync: Optional[datetime] = None
        self.drift = 0.0                            # на сколько секунд пришлось поправить часы
        self.previous_seen = self._load_seen()      # когда Стейси «видели» в прошлый раз

    # ---------- Чтение времени ----------

    def now(self) -> datetime:
        """Игровое время в UTC. Никогда не уменьшается."""
        t = self._anchor + timedelta(seconds=time.monotonic() - self._mono)
        if t < self._last:
            t = self._last
        self._last = t
        return t

    def local_now(self) -> datetime:
        return self.now().astimezone(self.tz)

    def game_day(self) -> date:
        """Игровые сутки: календарная дата по часовому поясу Ташкента."""
        return self.local_now().date()

    # ---------- Синхронизация ----------

    async def sync(self):
        real = await fetch_discord_time()
        source = "Discord"
        if real is None:
            real, source = datetime.now(timezone.utc), "время ОС (Discord недоступен)"
        else:
            real += timedelta(seconds=0.5)          # заголовок усечён до секунды: берём середину
        mono = time.monotonic()
        first = self.last_sync is None
        drift = (real - self.now()).total_seconds()

        self._anchor, self._mono = real, mono       # при drift < 0 now() постоит, пока реальное не догонит
        self.drift, self.source, self.last_sync = drift, source, real

        if first:
            log.info("Часы синхронизированы (%s). Поправка к времени ОС: %+.1f с.", source, drift)
            if self.previous_seen:
                slept = (real - self.previous_seen).total_seconds()
                log.info("Стейси не работала: %s (последний раз была на связи %s).",
                         fmt_duration(max(0, slept)),
                         self.previous_seen.astimezone(self.tz).strftime("%d.%m.%Y %H:%M:%S"))
            else:
                log.info("Это первый запуск: прошлых остановок в базе нет.")
        elif abs(drift) > 2:
            log.warning("Дрейф часов %+.1f с, часы скорректированы (%s).", drift, source)
        else:
            log.debug("Сверка часов: дрейф %+.1f с.", drift)
        self.save_seen()

    # ---------- Сохранение момента остановки ----------

    def _load_seen(self) -> Optional[datetime]:
        row = self.db.execute("SELECT value FROM meta WHERE key='last_seen_utc'").fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def save_seen(self):
        with self.db:
            self.db.execute(
                "INSERT INTO meta VALUES ('last_seen_utc', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (self.now().isoformat(),))