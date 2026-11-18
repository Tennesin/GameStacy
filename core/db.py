"""SQLite: подключение и миграции схемы через PRAGMA user_version."""

import sqlite3
from pathlib import Path

MAIN_MIGRATIONS = [
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);",
]

def connect(path: Path, migrations: list) -> sqlite3.Connection:
    """Открывает базу и применяет недостающие миграции по порядку."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    version = con.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in enumerate(migrations[version:], start=version + 1):
        try:
            con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={number};\nCOMMIT;")
        except Exception:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
    return con