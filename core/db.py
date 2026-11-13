"""SQLite: подключение и миграции схемы через PRAGMA user_version."""

import sqlite3
from pathlib import Path

MAIN_MIGRATIONS = [
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);",
]

def connect(path: Path, migrations: list) -> sqlite3.Connection:
    """Открывает базу и применяет недостающие миграции по порядку.
    Миграции нельзя переписывать задним числом: изменения схемы добавляются новым элементом списка."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    version = con.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in enumerate(migrations[version:], start=version + 1):
        con.executescript(sql)
        con.execute(f"PRAGMA user_version={number}")
        con.commit()
    return con