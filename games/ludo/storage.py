"""Собственная БД игры: data/games/ludo.db. Состояние пишется после каждого действия.
Миграции только ДОБАВЛЯТЬ новым элементом списка, старые не менять."""
import json
from datetime import timedelta
from pathlib import Path
from typing import Optional

from config import GAMES_DATA_DIR
from core.db import connect
from .rules import LUDO_DEFAULT_BOTS, LUDO_MAX_BOTS

MIGRATIONS = [
    """
    CREATE TABLE games (
        game_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id          INTEGER NOT NULL,
        channel_id        INTEGER NOT NULL,   -- где лежит табло (канал или ветка)
        home_channel_id   INTEGER,            -- канал-хозяин (--gameid); NULL - без ограничения
        message_id        INTEGER,
        host_id           INTEGER NOT NULL,   -- 0 - запуск из консоли, хостом станет первый присоединившийся
        status            TEXT NOT NULL,      -- lobby / playing / finished / aborted
        bots              INTEGER NOT NULL DEFAULT 1,   -- настройка лобби (0-3)
        state_json        TEXT,               -- Match.snapshot()
        turn_deadline_utc TEXT,               -- когда истекает время на бросок или выбор
        ping_id           INTEGER,            -- сообщение «ваш ход» с тегом
        result            TEXT,
        created_utc       TEXT NOT NULL,
        updated_utc       TEXT NOT NULL,
        started_utc       TEXT                -- когда партия началась (для длительности в итогах)
    );
    CREATE TABLE game_players (
        game_id    INTEGER NOT NULL,
        pid        INTEGER NOT NULL,          -- ID в Discord; у ботов отрицательный
        name       TEXT NOT NULL,
        avatar_url TEXT NOT NULL DEFAULT '',
        is_bot     INTEGER NOT NULL DEFAULT 0,
        seat       INTEGER NOT NULL,          -- порядок входа в лобби (угол доски задаёт Match)
        PRIMARY KEY (game_id, pid)
    );
    CREATE INDEX ix_ludo_games_status ON games (status);
    """,
]

_GAME_FIELDS = {"channel_id", "home_channel_id", "message_id", "host_id", "status", "bots",
                "state_json", "turn_deadline_utc", "result", "ping_id"}

class Storage:
    def __init__(self, clock, path: Optional[Path] = None):
        self.clock = clock
        self.con = connect(path or (GAMES_DATA_DIR / "ludo.db"), MIGRATIONS)

    def _now(self) -> str:
        return self.clock.now().isoformat()

    # ---------- Партии ----------

    def _set(self, game_id: int, **fields):
        bad = set(fields) - _GAME_FIELDS
        if bad:
            raise ValueError(f"Неизвестные поля партии: {sorted(bad)}")
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.con:
            self.con.execute(f"UPDATE games SET {cols}, updated_utc=? WHERE game_id=?",
                             (*fields.values(), self._now(), game_id))

    def create_game(self, guild_id: int, channel_id: int, host_id: int, home_id=None,
                    bots: int = LUDO_DEFAULT_BOTS) -> int:
        now = self._now()
        bots = max(0, min(LUDO_MAX_BOTS, int(bots)))
        with self.con:
            cur = self.con.execute(
                "INSERT INTO games (guild_id, channel_id, home_channel_id, host_id, status, bots, "
                "created_utc, updated_utc) VALUES (?, ?, ?, ?, 'lobby', ?, ?, ?)",
                (guild_id, channel_id, home_id, host_id, bots, now, now))
        return cur.lastrowid

    def get(self, game_id: int):
        return self.con.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()

    def active(self) -> list:
        """Партии, чьи кнопки нужно восстановить после перезапуска."""
        return self.con.execute(
            "SELECT * FROM games WHERE message_id IS NOT NULL AND status IN ('lobby', 'playing')"
        ).fetchall()

    def set_message(self, game_id: int, message_id: int, channel_id: Optional[int] = None):
        if channel_id is None:
            self._set(game_id, message_id=message_id)
        else:
            self._set(game_id, message_id=message_id, channel_id=channel_id)

    def set_host(self, game_id: int, host_id: int):
        self._set(game_id, host_id=host_id)

    def set_ping(self, game_id: int, message_id: Optional[int]):
        self._set(game_id, ping_id=message_id)

    def update_settings(self, game_id: int, bots: Optional[int] = None):
        """Настройки лобби: сейчас только число ботов (0..LUDO_MAX_BOTS)."""
        if bots is not None:
            self._set(game_id, bots=max(0, min(LUDO_MAX_BOTS, int(bots))))

    # ---------- Участники ----------

    def players(self, game_id: int) -> list:
        return self.con.execute(
            "SELECT * FROM game_players WHERE game_id=? ORDER BY seat", (game_id,)).fetchall()

    def add_player(self, game_id: int, pid: int, name: str, avatar_url: str = "",
                   is_bot: bool = False) -> bool:
        """False - этот участник уже записан."""
        with self.con:
            seat = self.con.execute("SELECT COALESCE(MAX(seat), -1) + 1 FROM game_players WHERE game_id=?",
                                    (game_id,)).fetchone()[0]
            cur = self.con.execute("INSERT OR IGNORE INTO game_players VALUES (?, ?, ?, ?, ?, ?)",
                                   (game_id, pid, name, avatar_url or "", int(is_bot), seat))
            added = cur.rowcount > 0
            if added:
                self.con.execute("UPDATE games SET updated_utc=? WHERE game_id=?", (self._now(), game_id))
        return added

    def remove_player(self, game_id: int, pid: int) -> bool:
        """Для выхода из лобби. Из идущей партии игроки не удаляются: они остаются в Match как вышедшие."""
        with self.con:
            cur = self.con.execute("DELETE FROM game_players WHERE game_id=? AND pid=?", (game_id, pid))
            removed = cur.rowcount > 0
            if removed:
                self.con.execute("UPDATE games SET updated_utc=? WHERE game_id=?", (self._now(), game_id))
        return removed

    def update_player(self, game_id: int, pid: int, name: str, avatar_url: str):
        """Обновить ник и аватарку (человек мог их сменить)."""
        with self.con:
            self.con.execute("UPDATE game_players SET name=?, avatar_url=? WHERE game_id=? AND pid=?",
                             (name, avatar_url or "", game_id, pid))

    # ---------- Ход партии ----------

    def start(self, game_id: int, bots: list, snapshot: dict, deadline: Optional[str]) -> str:
        """Запуск партии одной транзакцией: боты садятся за стол, сохраняется состояние.
        bots: [(pid, имя)]. Возвращает время старта (ISO)."""
        now = self._now()
        with self.con:
            seat = self.con.execute("SELECT COALESCE(MAX(seat), -1) FROM game_players WHERE game_id=?",
                                    (game_id,)).fetchone()[0]
            for pid, name in bots:
                seat += 1
                self.con.execute("INSERT INTO game_players VALUES (?, ?, ?, '', 1, ?)",
                                 (game_id, pid, name, seat))
            self.con.execute(
                "UPDATE games SET status='playing', state_json=?, turn_deadline_utc=?, started_utc=?, "
                "updated_utc=? WHERE game_id=?",
                (json.dumps(snapshot), deadline, now, now, game_id))
        return now

    def save_state(self, game_id: int, snapshot: dict, deadline: Optional[str]):
        self._set(game_id, state_json=json.dumps(snapshot), turn_deadline_utc=deadline)

    def finish(self, game_id: int, snapshot: dict, result: str = "done"):
        self._set(game_id, status="finished", state_json=json.dumps(snapshot),
                  turn_deadline_utc=None, result=result)

    def abort(self, game_id: int, reason: str):
        self._set(game_id, status="aborted", result=reason, turn_deadline_utc=None)

    @staticmethod
    def state_of(row) -> Optional[dict]:
        return json.loads(row["state_json"]) if row["state_json"] else None

    # ---------- Уборка ----------

    def purge(self, days: int = 30) -> int:
        """Удаляет завершённые и закрытые партии старше N дней. Возвращает их число."""
        cutoff = (self.clock.now() - timedelta(days=days)).isoformat()
        with self.con:
            ids = [r[0] for r in self.con.execute(
                "SELECT game_id FROM games WHERE status IN ('finished', 'aborted') AND updated_utc <= ?",
                (cutoff,))]
            for gid in ids:
                self.con.execute("DELETE FROM game_players WHERE game_id=?", (gid,))
                self.con.execute("DELETE FROM games WHERE game_id=?", (gid,))
        return len(ids)