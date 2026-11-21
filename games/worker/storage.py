"""Хранилище симулятора рабочего: отдельный файл БД на каждый сервер."""
import json
import sqlite3
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from config import GAMES_DATA_DIR
from core.db import connect
from .logic import Player
from .market import Vacancy

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
    """
    CREATE TABLE vacancies (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        kind        TEXT NOT NULL,            -- once / perm
        prof        TEXT NOT NULL,
        shift_min   INTEGER NOT NULL,
        commute_min INTEGER NOT NULL,
        heavy       REAL NOT NULL,
        wage        REAL NOT NULL,
        rate_energy REAL NOT NULL,
        rate_health REAL NOT NULL,
        rate_happy  REAL NOT NULL,
        fare        INTEGER NOT NULL,
        min_rep     INTEGER NOT NULL,
        days_off    TEXT NOT NULL DEFAULT '',
        appears_ts  INTEGER NOT NULL,         -- начало окна обновления (UTC, секунды)
        expires_ts  INTEGER NOT NULL,
        taken_by    INTEGER,
        taken_ts    INTEGER
    );
    CREATE INDEX ix_vac_alive ON vacancies (taken_by, expires_ts);
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """,
]

_VAC_COLS = ("kind", "prof", "shift_min", "commute_min", "heavy", "wage", "rate_energy", "rate_health",
             "rate_happy", "fare", "min_rep", "days_off", "appears_ts", "expires_ts")
_KEEP_SECONDS = 86400            # взятые и истёкшие вакансии физически удаляются через сутки

def _vac(row) -> Vacancy:
    off = tuple(int(x) for x in row["days_off"].split(",") if x != "")
    return Vacancy(row["id"], row["kind"], row["prof"], row["shift_min"], row["commute_min"], row["heavy"],
                   row["wage"], row["rate_energy"], row["rate_health"], row["rate_happy"], row["fare"],
                   row["min_rep"], off, row["appears_ts"], row["expires_ts"], row["taken_by"])

class GuildStorage:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.con = connect(path, MIGRATIONS)

    # ---------- Игроки ----------

    def get_player(self, uid: int) -> Optional[Player]:
        row = self.con.execute("SELECT state_json FROM players WHERE uid=?", (uid,)).fetchone()
        return Player.restore(json.loads(row["state_json"])) if row else None

    # ---------- Сброс (консоль) ----------

    def count_players(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM players").fetchone()[0]

    def reset_player(self, uid: int) -> bool:
        with self.con:
            cur = self.con.execute("DELETE FROM players WHERE uid=?", (uid,))
        return cur.rowcount > 0

    def reset_all(self) -> int:
        """Удаляет всех игроков и рынок вакансий. Возвращает число удалённых игроков."""
        with self.con:
            cur = self.con.execute("DELETE FROM players")
            removed = cur.rowcount
            self.con.execute("DELETE FROM vacancies")
            self.con.execute("DELETE FROM meta WHERE key='market_window'")
            self.con.execute("DELETE FROM sqlite_sequence WHERE name='vacancies'")
        return removed

    def backup(self, folder: Path, stamp: str) -> Path:
        """Корректная копия базы (с учётом журнала WAL). Возвращает путь к файлу."""
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{self.path.stem}_{stamp}.bak"
        dst = sqlite3.connect(dest)
        try:
            self.con.backup(dst)
        finally:
            dst.close()
        return dest

    def save_player(self, p: Player):
        """Коммитит ВСЮ открытую транзакцию (в том числе claim_vacancy, сделанный перед ним)."""
        stamp = p.updated.isoformat()
        with self.con:
            self.con.execute(
                "INSERT INTO players (uid, state_json, updated_utc, frozen, created_utc) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(uid) DO UPDATE SET "
                "state_json=excluded.state_json, updated_utc=excluded.updated_utc, frozen=excluded.frozen",
                (p.uid, json.dumps(p.snapshot(), ensure_ascii=False), stamp,
                 int(p.frozen_at is not None), stamp))

    # ---------- Рынок вакансий ----------

    def market_window(self) -> Optional[int]:
        row = self.con.execute("SELECT value FROM meta WHERE key='market_window'").fetchone()
        return int(row["value"]) if row else None

    def count_alive(self, now_ts: int) -> tuple:
        """(разовых, постоянных) живых вакансий."""
        rows = self.con.execute(
            "SELECT kind, COUNT(*) AS n FROM vacancies "
            "WHERE taken_by IS NULL AND appears_ts<=? AND expires_ts>? GROUP BY kind",
            (now_ts, now_ts)).fetchall()
        got = {r["kind"]: r["n"] for r in rows}
        return got.get("once", 0), got.get("perm", 0)

    def refresh_market(self, window_ts: int, now_ts: int, rows: list):
        """Одна транзакция: уборка старых, новые вакансии и отметка «последнее окно»."""
        marks = ", ".join(f":{c}" for c in _VAC_COLS)
        with self.con:
            self.con.execute(
                "DELETE FROM vacancies WHERE expires_ts<? OR (taken_by IS NOT NULL AND taken_ts<?)",
                (now_ts - _KEEP_SECONDS, now_ts - _KEEP_SECONDS))
            self.con.executemany(
                f"INSERT INTO vacancies ({', '.join(_VAC_COLS)}) VALUES ({marks})", rows)
            self.con.execute(
                "INSERT INTO meta VALUES ('market_window', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(window_ts),))

    def list_vacancies(self, now_ts: int) -> list:
        """Живые вакансии: самые свежие сверху."""
        rows = self.con.execute(
            "SELECT * FROM vacancies WHERE taken_by IS NULL AND appears_ts<=? AND expires_ts>? "
            "ORDER BY appears_ts DESC, id DESC", (now_ts, now_ts)).fetchall()
        return [_vac(r) for r in rows]

    def get_vacancy(self, vac_id: int) -> Optional[Vacancy]:
        row = self.con.execute("SELECT * FROM vacancies WHERE id=?", (vac_id,)).fetchone()
        return _vac(row) if row else None

    def claim_vacancy(self, vac_id: int, uid: int, now_ts: int) -> bool:
        """Атомарно забирает вакансию: «только если ещё свободна и жива». НЕ коммитит: вызывающий обязан сразу
        сохранить игрока (save_player), тогда вакансия и состояние игрока фиксируются одной транзакцией."""
        cur = self.con.execute(
            "UPDATE vacancies SET taken_by=?, taken_ts=? "
            "WHERE id=? AND taken_by IS NULL AND appears_ts<=? AND expires_ts>?",
            (uid, now_ts, vac_id, now_ts, now_ts))
        return cur.rowcount == 1

    def close(self):
        self.con.close()

class StorageRegistry:
    """ID сервера -> хранилище."""

    def __init__(self, base: Optional[Path] = None, limit: int = 6):
        self.base = Path(base) if base else GAMES_DATA_DIR / "worker"
        self.base.mkdir(parents=True, exist_ok=True)
        self.limit = limit
        self._cache: OrderedDict = OrderedDict()

    def exists(self, guild_id: int) -> bool:
        """Есть ли файл базы этого сервера (не создаёт его)."""
        return (self.base / f"{int(guild_id)}.db").exists()

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