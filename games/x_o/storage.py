"""Собственная БД игры: data/games/x_o.db. Состояние пишется после каждого действия."""

from config import GAMES_DATA_DIR
from core.db import connect

MIGRATIONS = [
    """
    CREATE TABLE games (
        game_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id    INTEGER NOT NULL,
        channel_id  INTEGER NOT NULL,
        message_id  INTEGER,
        status      TEXT NOT NULL,       -- lobby / playing / finished / aborted
        player_x    INTEGER,             -- в лобби здесь первый нажавший, после жеребьёвки — игрок X
        player_o    INTEGER,
        board       TEXT NOT NULL DEFAULT '         ',
        turn        TEXT,                -- 'X' или 'O'
        result      TEXT,                -- X / O / draw / error
        created_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL
    );
    CREATE TABLE moves (
        game_id INTEGER NOT NULL,
        n       INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        cell    INTEGER NOT NULL,
        ts_utc  TEXT NOT NULL,
        PRIMARY KEY (game_id, n)
    );
    """,
]

class Storage:
    def __init__(self, clock):
        self.clock = clock
        self.con = connect(GAMES_DATA_DIR / "x_o.db", MIGRATIONS)

    def _now(self) -> str:
        return self.clock.now().isoformat()

    def _set(self, game_id: int, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.con:
            self.con.execute(f"UPDATE games SET {cols}, updated_utc=? WHERE game_id=?",
                             (*fields.values(), self._now(), game_id))

    def create(self, guild_id: int, channel_id: int) -> int:
        now = self._now()
        with self.con:
            cur = self.con.execute(
                "INSERT INTO games (guild_id, channel_id, status, created_utc, updated_utc) "
                "VALUES (?, ?, 'lobby', ?, ?)", (guild_id, channel_id, now, now))
        return cur.lastrowid

    def get(self, game_id: int):
        return self.con.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()

    def active(self) -> list:
        return self.con.execute(
            "SELECT * FROM games WHERE status IN ('lobby', 'playing') AND message_id IS NOT NULL"
        ).fetchall()

    def set_message(self, game_id: int, message_id: int):
        self._set(game_id, message_id=message_id)

    def join_first(self, game_id: int, user_id: int):
        self._set(game_id, player_x=user_id)

    def start(self, game_id: int, player_x: int, player_o: int):
        self._set(game_id, status="playing", player_x=player_x, player_o=player_o, turn="X")

    def make_move(self, game_id: int, user_id: int, cell: int, board: str,
                  turn, status: str, result):
        with self.con:
            n = self.con.execute("SELECT COUNT(*) FROM moves WHERE game_id=?",
                                 (game_id,)).fetchone()[0] + 1
            now = self._now()
            self.con.execute("INSERT INTO moves VALUES (?, ?, ?, ?, ?)",
                             (game_id, n, user_id, cell, now))
            self.con.execute(
                "UPDATE games SET board=?, turn=?, status=?, result=?, updated_utc=? WHERE game_id=?",
                (board, turn, status, result, now, game_id))

    def abort(self, game_id: int, reason: str):
        self._set(game_id, status="aborted", result=reason, turn=None)