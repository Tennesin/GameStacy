"""Хранилище симулятора рабочего: отдельный файл БД на каждый сервер (data/games/worker/<ID_сервера>.db).
Миграции только ДОБАВЛЯТЬ новым элементом списка, старые не менять.
Подключения кэшируются с ограничением. Не держите GuildStorage через await: при вытеснении
из кэша подключение закрывается."""
import json
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from config import GAMES_DATA_DIR
from core.db import connect
from .logic import Player

MIGRATIONS = [
    """
    CREATE TABLE players (
        uid         INTEGER PRIMARY KEY,
        state_json  TEXT NOT NULL,            -- Player.snapshot()
        updated_utc TEXT NOT NULL,            -- до какого момента рассчитано состояние
        frozen      INTEGER NOT NULL DEFAULT 0,
        created_utc TEXT NOT NULL
    );
    """,
]

class GuildStorage:
    def __init__(self, path: Path):
        self.con = connect(path, MIGRATIONS)

    def get_player(self, uid: int) -> Optional[Player]:
        row = self.con.execute("SELECT state_json FROM players WHERE uid=?", (uid,)).fetchone()
        return Player.restore(json.loads(row["state_json"])) if row else None

    def save_player(self, p: Player):
        stamp = p.updated.isoformat()
        with self.con:
            self.con.execute(
                "INSERT INTO players (uid, state_json, updated_utc, frozen, created_utc) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(uid) DO UPDATE SET "
                "state_json=excluded.state_json, updated_utc=excluded.updated_utc, frozen=excluded.frozen",
                (p.uid, json.dumps(p.snapshot(), ensure_ascii=False), stamp,
                 int(p.frozen_at is not None), stamp))

    def close(self):
        self.con.close()

class StorageRegistry:
    """ID сервера -> хранилище. Файл создаётся и мигрируется при первом обращении."""

    def __init__(self, base: Optional[Path] = None, limit: int = 6):
        self.base = Path(base) if base else GAMES_DATA_DIR / "worker"
        self.base.mkdir(parents=True, exist_ok=True)
        self.limit = limit
        self._cache: OrderedDict = OrderedDict()

    def get(self, guild_id: int) -> GuildStorage:
        st = self._cache.get(guild_id)
        if st is not None:
            self._cache.move_to_end(guild_id)
            return st
        st = GuildStorage(self.base / f"{int(guild_id)}.db")
        self._cache[guild_id] = st
        while len(self._cache) > self.limit:
            _gid, old = self._cache.popitem(last=False)
            old.close()
        return st

    def close_all(self):
        for st in self._cache.values():
            st.close()
        self._cache.clear()