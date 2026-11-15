"""Выгрузка карт Джуманджи из локальной БД в JSON-файлы.
Запуск из корня проекта:  python export_maps.py
Файлы появятся в папке maps_export/ (maps_ — готовые карты, drafts_ — черновики)."""
import re
import sqlite3
from pathlib import Path

DB = Path("data/games/jumanji.db")
OUT = Path("maps_export")

def main():
    if not DB.exists():
        raise SystemExit(f"Нет базы: {DB}")
    OUT.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    n = 0
    for table, prefix in (("maps", "maps"), ("map_drafts", "drafts")):
        for name, text in con.execute(f"SELECT name, json FROM {table} ORDER BY name"):
            n += 1
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "map"
            path = OUT / f"{prefix}_{n}_{safe}.json"
            path.write_text(text, encoding="utf-8")
            print(f"{path}   (карта «{name}»)")
    con.close()
    print(f"Выгружено файлов: {n}")

if __name__ == "__main__":
    main()