"""Собственная БД игры: data/games/counting.db.
Миграции только ДОБАВЛЯТЬ новым элементом списка, старые не менять."""
from pathlib import Path
from typing import Optional

from config import GAMES_DATA_DIR
from core.db import connect

MIGRATIONS = [
    """
    CREATE TABLE guilds (
        guild_id        INTEGER PRIMARY KEY,   -- один счётчик на сервер
        channel_id      INTEGER,               -- NULL: счётчик снят, прогресс сохранён
        last_number     INTEGER NOT NULL DEFAULT 0,
        last_user_id    INTEGER,
        last_message_id INTEGER,               -- сообщение с последним принятым числом
        anchor_id       INTEGER,               -- сообщение бота при назначении канала (точка для догона)
        updated_utc     TEXT NOT NULL
    );
    CREATE UNIQUE INDEX ix_counting_channel ON guilds (channel_id) WHERE channel_id IS NOT NULL;
    CREATE TABLE user_stats (
        guild_id   INTEGER NOT NULL,
        uid        INTEGER NOT NULL,
        name       TEXT NOT NULL,
        count      INTEGER NOT NULL DEFAULT 0,
        updated_ts INTEGER NOT NULL,           -- когда человек последний раз указал число
        PRIMARY KEY (guild_id, uid)
    );
    """,
]

class Storage:
    def __init__(self, clock, path: Optional[Path] = None):
        self.clock = clock
        self.con = connect(path or (GAMES_DATA_DIR / "counting.db"), MIGRATIONS)

    def _now(self) -> str:
        return self.clock.now().isoformat()

    def _ts(self) -> int:
        return int(self.clock.now().timestamp())

    # ---------- Серверы ----------

    def get_guild(self, guild_id: int):
        return self.con.execute("SELECT * FROM guilds WHERE guild_id=?", (guild_id,)).fetchone()

    def active(self) -> list:
        """Серверы, где счётчик сейчас привязан к каналу."""
        return self.con.execute("SELECT * FROM guilds WHERE channel_id IS NOT NULL").fetchall()

    def set_channel(self, guild_id: int, channel_id: int, anchor_id: Optional[int]):
        """Привязывает счётчик к каналу. Прогресс (число, автор, статистика) не трогается."""
        with self.con:
            self.con.execute(
                "INSERT INTO guilds (guild_id, channel_id, anchor_id, updated_utc) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, "
                "anchor_id=excluded.anchor_id, updated_utc=excluded.updated_utc",
                (guild_id, channel_id, anchor_id, self._now()))

    def clear_channel(self, guild_id: int):
        with self.con:
            self.con.execute("UPDATE guilds SET channel_id=NULL, anchor_id=NULL, updated_utc=? "
                             "WHERE guild_id=?", (self._now(), guild_id))

    def set_number(self, guild_id: int, number: int):
        """Ручная правка счёта (консоль): автор последнего числа сбрасывается."""
        with self.con:
            self.con.execute(
                "INSERT INTO guilds (guild_id, last_number, updated_utc) VALUES (?, ?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET last_number=excluded.last_number, "
                "last_user_id=NULL, last_message_id=NULL, updated_utc=excluded.updated_utc",
                (guild_id, number, self._now()))

    # ---------- Принятое число ----------

    def record(self, guild_id: int, number: int, uid: int, message_id: int, name: str):
        """Одна транзакция: новое состояние счёта + +1 в статистику автора."""
        with self.con:
            self.con.execute(
                "UPDATE guilds SET last_number=?, last_user_id=?, last_message_id=?, updated_utc=? "
                "WHERE guild_id=?", (number, uid, message_id, self._now(), guild_id))
            self.con.execute(
                "INSERT INTO user_stats (guild_id, uid, name, count, updated_ts) VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(guild_id, uid) DO UPDATE SET count=count+1, name=excluded.name, "
                "updated_ts=excluded.updated_ts", (guild_id, uid, name, self._ts()))

    # ---------- Статистика ----------

    def top(self, guild_id: int, limit: int) -> list:
        """При равенстве выше тот, кто раньше набрал своё число."""
        return self.con.execute(
            "SELECT uid, name, count FROM user_stats WHERE guild_id=? "
            "ORDER BY count DESC, updated_ts ASC, uid ASC LIMIT ?", (guild_id, limit)).fetchall()

    def rank_of(self, guild_id: int, uid: int):
        """(место, число раз) или None, если человек ещё не считал."""
        me = self.con.execute("SELECT count, updated_ts FROM user_stats WHERE guild_id=? AND uid=?",
                              (guild_id, uid)).fetchone()
        if me is None:
            return None
        ahead = self.con.execute(
            "SELECT COUNT(*) FROM user_stats WHERE guild_id=? AND (count > ? "
            "OR (count = ? AND updated_ts < ?) OR (count = ? AND updated_ts = ? AND uid < ?))",
            (guild_id, me["count"], me["count"], me["updated_ts"],
             me["count"], me["updated_ts"], uid)).fetchone()[0]
        return ahead + 1, me["count"]

    def total(self, guild_id: int) -> int:
        row = self.con.execute("SELECT COALESCE(SUM(count), 0) FROM user_stats WHERE guild_id=?",
                               (guild_id,)).fetchone()
        return int(row[0])