"""Формат карты Джуманджи: ячейки, координаты, чтение и запись JSON, обход маршрута."""
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .rules import BRANCH

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
    effect: Optional[str] = None     # jump / back / skip / charge / mini / reset / branch
    target: Optional[str] = None     # цель для jump / back; для branch — первая клетка дороги
    dx: float = 0.0                  # визуальный сдвиг внутри клетки сетки (доли клетки), только для картинки
    dy: float = 0.0
    side: bool = False               # клетка особого пути: в основной маршрут не входит, эффектов нет

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
            if c.side:
                item["side"] = True
            if c.dx or c.dy:
                item["dx"], item["dy"] = round(c.dx, 3), round(c.dy, 3)
            cells.append(item)
        return {"format": FORMAT_VERSION, "name": self.name, "width": self.width,
                "height": self.height, "start": self.start, "finish": self.finish, "cells": cells}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=1)

    def copy(self) -> "GameMap":
        return GameMap.from_dict(self.to_dict())

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
            try:
                dx = max(-0.5, min(0.5, float(item.get("dx") or 0)))
                dy = max(-0.5, min(0.5, float(item.get("dy") or 0)))
            except (TypeError, ValueError):
                raise MapFormatError(f"Некорректный сдвиг у ячейки {cid}.")
            m.cells[cid] = Cell(cid, norm_id(item.get("next")) or None, effect,
                                norm_id(item.get("target")) or None, dx, dy,
                                side=bool(item.get("side")))
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
    """Идёт от старта по полям next."""
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

def follow_road(m: GameMap, first: Optional[str]) -> tuple:
    """Идёт по клеткам дороги от первой клетки."""
    chain, seen, cur = [], set(), first
    while True:
        if not cur:
            if chain:
                return chain, None, (f"дорога обрывается на {chain[-1]}: не указана следующая ячейка "
                                     "(последняя клетка дороги должна вести на обычный маршрут).")
            return chain, None, "не указана первая клетка дороги."
        c = m.cells.get(cur)
        if c is None:
            return chain, None, f"дорога ведёт на несуществующую ячейку {cur}."
        if not c.side:
            if not chain:
                return chain, None, f"первая клетка дороги ({cur}) не помечена как клетка особого пути."
            return chain, cur, None
        if cur in seen:
            return chain, None, f"дорога зациклилась на ячейке {cur}: до обычного маршрута не дойти."
        seen.add(cur)
        chain.append(cur)
        cur = c.next

def side_chains(m: GameMap, path: list) -> tuple:
    """Все особые пути карты в порядке их начал на основном маршруте (path)."""
    on_main = set(path)
    chains, problems, owner = [], [], {}
    for bid in path[1:-1]:                              # на старте и финише эффектов нет
        b = m.cells[bid]
        if b.effect != BRANCH:
            continue
        chain, exit_id, problem = follow_road(m, b.target)
        if problem:
            problems.append(f"Особый путь от {bid}: {problem}")
        clash = [cid for cid in chain if cid in owner]
        if clash:
            problems.append(f"Особый путь от {bid} использует клетки пути от {owner[clash[0]]} "
                            f"({', '.join(clash[:5])}): у каждого пути должны быть свои клетки.")
        else:
            for cid in chain:
                owner[cid] = bid
        if exit_id is not None:
            if exit_id == path[-1]:
                problems.append(f"Особый путь от {bid} выходит прямо на финиш: финиш берётся только точным броском.")
            elif exit_id not in on_main:
                problems.append(f"Особый путь от {bid} выходит на {exit_id}, которой нет на основном маршруте.")
        chains.append((bid, chain, exit_id))
    return chains, problems