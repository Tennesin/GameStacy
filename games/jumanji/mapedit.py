"""Помощники для правки карт Джуманджи: разбор координат, постановка и удаление ячеек и дорог.
Чистые функции без Discord и БД; ими пользуется локальный редактор dev_tools/jumanji_map_editor.py."""
import re

from .models import Cell, GameMap, cell_id, follow_road, parse_cell_id, trace_path
from .rules import BRANCH

_CYR = str.maketrans("АВСЕН", "ABCEH")        # русские буквы-двойники -> латиница
_DASHES = "–—−"

def _span(a: int, b: int):
    step = 1 if b >= a else -1
    return range(a, b + step, step)

def parse_cells(text: str, m: GameMap) -> tuple:
    """«A1 B1 C1», «A1-E1», «A1-A4», через пробел/запятую. Возвращает (список id, текст ошибки или None)."""
    tokens = [t for t in re.split(r"[\s,;]+", str(text).strip()) if t]
    if not tokens:
        return [], "Введите хотя бы одну ячейку, например B4."
    out: list = []
    for raw in tokens:
        tok = raw.strip().upper().translate(_CYR)
        for dash in _DASHES:
            tok = tok.replace(dash, "-")
        if "-" in tok:
            a, _, b = tok.partition("-")
            pa, pb = parse_cell_id(a), parse_cell_id(b)
            if pa is None or pb is None:
                return [], f"Не понял диапазон «{raw}». Пример: A1-E1."
            (x1, y1), (x2, y2) = pa, pb
            if y1 == y2:
                pts = [(x, y1) for x in _span(x1, x2)]
            elif x1 == x2:
                pts = [(x1, y) for y in _span(y1, y2)]
            else:
                return [], f"Диапазон «{raw}» должен идти по одной строке или одному столбцу."
        else:
            p = parse_cell_id(tok)
            if p is None:
                return [], f"«{raw}» не похоже на координату. Пример: B4."
            pts = [p]
        for x, y in pts:
            if x >= m.width or y >= m.height:
                return [], (f"Ячейка {cell_id(x, y)} вне сетки "
                            f"(крайняя - {cell_id(m.width - 1, m.height - 1)}).")
            cid = cell_id(x, y)
            if cid not in out:
                out.append(cid)
    return out, None

def initial_tail(m: GameMap):
    """Конец достроенной цепочки (к нему цепляется следующая поставленная ячейка)."""
    path, problem = trace_path(m)
    return None if (problem is None or not path) else path[-1]

def place_cells(m: GameMap, ids: list, tail):
    """Ставит ячейки по порядку: каждая новая становится «следующей» для предыдущей.
    Первая ячейка пустой карты становится стартом. Возвращает (добавлены, уже_стояли, новый_tail)."""
    added, skipped = [], []
    for cid in ids:
        if cid in m.cells:
            skipped.append(cid)
            continue
        m.cells[cid] = Cell(cid)
        if tail and tail in m.cells and tail != m.finish and not m.cells[tail].next:
            m.cells[tail].next = cid
        tail = cid
        added.append(cid)
        if m.start is None:
            m.start = cid
    return added, skipped, tail

def remove_cell(m: GameMap, cid: str):
    """Удаляет ячейку и чистит все ссылки на неё."""
    m.cells.pop(cid, None)
    for c in m.cells.values():
        if c.next == cid:
            c.next = None
        if c.target == cid:
            c.target = None
    if m.start == cid:
        m.start = None
    if m.finish == cid:
        m.finish = None

def remove_road(m: GameMap, bid: str):
    """Если в ячейке bid начало особого пути, удаляет всю его дорогу (клетки с пометкой side)."""
    b = m.cells.get(bid)
    if b is None or b.effect != BRANCH:
        return
    chain, _exit, _problem = follow_road(m, b.target)
    for cid in chain:
        remove_cell(m, cid)
    b.target = None