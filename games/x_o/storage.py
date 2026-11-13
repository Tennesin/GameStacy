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
    """
    ALTER TABLE games ADD COLUMN rematch INTEGER NOT NULL DEFAULT 0;   -- 0 нет, 1 ждём, 2 отказ, 3 состоялся
    ALTER TABLE games ADD COLUMN ready_x INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE games ADD COLUMN ready_o INTEGER NOT NULL DEFAULT 0;
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
        """Партии, чьи кнопки нужно восстановить: набор, игра, либо окончена и ждёт ответа про реванш."""
        return self.con.execute(
            "SELECT * FROM games WHERE message_id IS NOT NULL AND "
            "(status IN ('lobby', 'playing') OR (status = 'finished' AND rematch = 1))"
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
                "UPDATE games SET board=?, turn=?, status=?, result=?, rematch=?, updated_utc=? "
                "WHERE game_id=?",
                (board, turn, status, result, 1 if status == "finished" else 0, now, game_id))

    def abort(self, game_id: int, reason: str):
        self._set(game_id, status="aborted", result=reason, turn=None)

    def vote_rematch(self, game_id: int, seat: str):
        """seat: 'X' или 'O' — место игрока в завершённой партии."""
        self._set(game_id, **{"ready_x" if seat == "X" else "ready_o": 1})

    def set_rematch(self, game_id: int, state: int):
        self._set(game_id, rematch=state)

    def restart(self, old_id: int, player_x: int, player_o: int) -> int:
        """Новая партия тех же игроков в том же сообщении."""
        now = self._now()
        old = self.get(old_id)
        with self.con:
            self.con.execute("UPDATE games SET rematch=3, updated_utc=? WHERE game_id=?", (now, old_id))
            cur = self.con.execute(
                "INSERT INTO games (guild_id, channel_id, message_id, status, player_x, player_o, "
                "turn, created_utc, updated_utc) VALUES (?, ?, ?, 'playing', ?, ?, 'X', ?, ?)",
                (old["guild_id"], old["channel_id"], old["message_id"], player_x, player_o, now, now))
        return cur.lastrowid