"""Формат карты Джуманджи: ячейки, координаты, чтение и запись JSON, обход маршрута."""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

FORMAT_VERSION = 1
_ID_RE = re.compile(r"^([A-Z])([0-9]{1,2})$")

class MapFormatError(ValueError):
    """Файл карты повреждён или записан не в том формате (или маршрут нельзя построить)."""

# ---------- Координаты ----------

def cell_id(x: int, y: int) -> str:
    """Столбец x и строка y (с нуля) -> «A1»."""
    return f"{chr(65 + x)}{y + 1}"

def norm_id(text) -> str:
    return "" if text is None else str(text).strip().upper()

def parse_cell_id(text) -> Optional[tuple]:
    """«B4» -> (1, 3). Не координата — None."""
    m = _ID_RE.match(norm_id(text))
    if not m:
        return None
    y = int(m.group(2)) - 1
    return (ord(m.group(1)) - 65, y) if y >= 0 else None

# ---------- Карта ----------

@dataclass
class Cell:
    id: str
    next: Optional[str] = None       # следующая ячейка маршрута (у финиша не нужна)
    effect: Optional[str] = None     # jump / back / skip / charge / mini / reset
    target: Optional[str] = None     # цель для jump / back

@dataclass
class GameMap:
    name: str = ""
    width: int = 10
    height: int = 8
    start: Optional[str] = None
    finish: Optional[str] = None
    cells: dict = field(default_factory=dict)      # id -> Cell

    def to_dict(self) -> dict:
        def order(cid):
            pos = parse_cell_id(cid) or (99, 99)
            return pos[1], pos[0]

        cells = []
        for cid in sorted(self.cells, key=order):
            c = self.cells[cid]
            item = {"id": c.id}
            for key in ("next", "effect", "target"):
                if getattr(c, key):
                    item[key] = getattr(c, key)
            cells.append(item)
        return {"format": FORMAT_VERSION, "name": self.name, "width": self.width,
                "height": self.height, "start": self.start, "finish": self.finish, "cells": cells}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=1)

    @classmethod
    def from_dict(cls, data) -> "GameMap":
        """Проверяет только устройство файла. Смысл карты (маршрут, эффекты) проверяет validator."""
        if not isinstance(data, dict):
            raise MapFormatError("Карта должна быть объектом JSON.")
        if data.get("format") != FORMAT_VERSION:
            raise MapFormatError(f"Неизвестная версия формата карты: {data.get('format')!r}.")
        try:
            width, height = int(data["width"]), int(data["height"])
        except (KeyError, TypeError, ValueError):
            raise MapFormatError("В карте не указан размер сетки.")
        raw = data.get("cells", [])
        if not isinstance(raw, list):
            raise MapFormatError("Поле cells должно быть списком.")

        m = cls(name=str(data.get("name") or "").strip(), width=width, height=height,
                start=norm_id(data.get("start")) or None, finish=norm_id(data.get("finish")) or None)
        for item in raw:
            if not isinstance(item, dict) or parse_cell_id(item.get("id")) is None:
                raise MapFormatError(f"Некорректная ячейка в файле: {item!r}")
            cid = norm_id(item["id"])
            if cid in m.cells:
                raise MapFormatError(f"Ячейка {cid} записана дважды.")
            effect = str(item.get("effect") or "").strip().lower() or None
            m.cells[cid] = Cell(cid, norm_id(item.get("next")) or None, effect,
                                norm_id(item.get("target")) or None)
        return m

    @classmethod
    def from_json(cls, text: str) -> "GameMap":
        try:
            data = json.loads(text)
        except ValueError as e:
            raise MapFormatError(f"Файл не похож на JSON: {e}")
        return cls.from_dict(data)

# ---------- Обход маршрута ----------

def trace_path(m: GameMap) -> tuple:
    """Идёт от старта по полям next. Возвращает (список ячеек от старта, текст проблемы или None).
    Проблема есть, если маршрут обрывается, ссылается в пустоту или зацикливается до финиша."""
    cur = m.start
    if not cur or cur not in m.cells:
        return [], "Старт не задан или его нет на карте."
    path, seen = [], set()
    while True:
        if cur in seen:
            return path, f"Маршрут зациклился на ячейке {cur}: до финиша не дойти."
        seen.add(cur)
        path.append(cur)
        if cur == m.finish:
            return path, None
        nxt = m.cells[cur].next
        if not nxt:
            return path, f"Маршрут обрывается на {cur}: не указана следующая ячейка."
        if nxt not in m.cells:
            return path, f"Ячейка {cur} ведёт на несуществующую ячейку {nxt}."
        cur = nxt