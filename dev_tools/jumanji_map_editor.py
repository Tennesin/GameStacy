"""Локальный редактор карт Джуманджи (Tkinter). Только для разработчика, на сервере не нужен.

Запуск: правый клик по файлу в PyCharm -> Run 'jumanji_map_editor'
или из корня проекта:  python dev_tools/jumanji_map_editor.py [путь_к_карте.json]

Использует те же модели, проверку и «шум», что и сама игра, поэтому результат полностью совместим
с командой -jmap import. Горячие клавиши с буквами рассчитаны на Windows (работают в любой раскладке)."""
import math
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))                       # чтобы работал import games..., core..., config

from games.jumanji.jitter import jittered, new_seed
from games.jumanji.mapedit import initial_tail, parse_cells, place_cells, remove_cell, remove_road
from games.jumanji.models import (Cell, GameMap, MapFormatError, cell_id, follow_road, parse_cell_id,
                                  side_chains, trace_path)
from games.jumanji.rules import (BRANCH, EFFECTS, EFFECT_TITLES, GRID_DEFAULT_H, GRID_DEFAULT_W, GRID_MAX_H,
                                 GRID_MAX_W, MAP_NAME_MAX, TARGET_EFFECTS)
from games.jumanji.validator import validate_map

MAPS_DIR = ROOT / "maps_export"                         # папка по умолчанию в окнах «Открыть/Сохранить»
PAD = 30                                                # поля вокруг сетки (для подписей координат)
UNDO_LIMIT = 200

BG = "#2b2d31"
GRID_DOT = "#4a4d55"
GRID_TEXT = "#7d828c"
NORMAL = "#d6d9de"
RING = "#969aa0"
START_RING, FINISH_RING = "#50c8dc", "#ffc428"
LINK_ON, LINK_OFF = "#8c929c", "#555961"
EFFECT_COLORS = {"jump": "#3ba55d", "back": "#ed4245", "skip": "#f0932b",
                 "charge": "#33a0f0", "mini": "#aa3cff", "reset": "#18191c", "branch": "#facc15"}
EFFECT_ICONS = {"jump": "↑", "back": "↓", "skip": "II", "charge": "+", "mini": "?", "reset": "×",
                "branch": "Y"}
SIDE_FILL, SIDE_STRIPE, SIDE_RING, SIDE_LINK = "#fde68a", "#d4a017", "#b7791f", "#d6aa28"
EFFECT_KEYS = {eff: str(i) for i, eff in enumerate(EFFECTS, start=1)}      # jump -> "1" и т. д.

HELP_TEXT = """МЫШЬ
  Инструмент «Рисовать путь»:
    клик по пустой клетке - поставить ячейку (цепляется к концу маршрута);
    зажать и вести - поставить цепочку ячеек;
    клик по готовой ячейке - сделать её концом маршрута; если у текущего конца нет
    «следующей», он соединится с этой ячейкой (так маршрут замыкается на финише).
  Инструмент «Выбрать»: клик - выбрать ячейку.
  Инструмент «Связать»: клик по A, затем по B - «A ведёт в B».
  Инструмент «Особый путь»:
    1) клик по ячейке основного маршрута - это начало особого пути;
    2) клики или протяжка по пустым клеткам - клетки дороги по порядку;
    3) клик по ячейке основного маршрута - выход дороги, путь готов.
    Esc - отменить построение. Повторный клик по началу пересоздаёт дорогу.
  Правая кнопка мыши - удалить ячейку (в любом режиме).

КЛАВИШИ (по выбранной ячейке)
  1 Прыжок   2 Откат   3 Пропуск   4 Заряд   5 Мини-игра   6 Сброс   7 Особый путь   0 Без эффекта
  (для прыжка и отката после нажатия кликните по ячейке-цели; Esc - отмена)
  S - старт, F - финиш, Del - удалить ячейку
  Ctrl+Z / Ctrl+Y - отменить / вернуть, Ctrl+S - сохранить, Ctrl+Shift+S - сохранить как,
  Ctrl+O - открыть, Ctrl+N - новая, Ctrl+E - проверить

ОСОБЫЙ ПУТЬ
  На клетках дороги эффектов, старта и финиша быть не может. Выход дороги не может вести на финиш.
  Если на начало пути поставить другой эффект или удалить ячейку, дорога удаляется вместе с её клетками.
  Выход можно переназначить инструментом «Связать» на последней клетке дороги.

ПРЕДПРОСМОТР ШУМА
  Показывает карту так, как увидят игроки. «Новый вариант» - другой случайный вариант.
  В этом режиме редактировать нельзя: выключите галочку.
  «Экспорт для сервера» сохраняет именно показанный вариант.

СОХРАНИТЬ и ЭКСПОРТ
  «Сохранить» пишет ровную карту (для дальнейшей правки).
  «Экспорт для сервера» пишет карту с шумом для команды -jmap import."""

def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])

def _arc(a, b, bend: float = 0.22) -> list:
    """Дуга Безье от a к b (для прыжков и откатов)."""
    length = _dist(a, b) or 1.0
    nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
    cx = (a[0] + b[0]) / 2 + nx * length * bend
    cy = (a[1] + b[1]) / 2 + ny * length * bend
    pts = []
    for i in range(25):
        t = i / 24
        u = 1 - t
        pts.append((u * u * a[0] + 2 * u * t * cx + t * t * b[0],
                    u * u * a[1] + 2 * u * t * cy + t * t * b[1]))
    return pts

def _stripes(c, x: float, y: float, R: float):
    """Диагональные полоски внутри круга клетки особого пути."""
    for o in (-0.6 * R, -0.2 * R, 0.2 * R, 0.6 * R):
        h = math.sqrt(max(R * R - o * o, 0.0))
        px, py = x + o * 0.7071, y + o * 0.7071
        c.create_line(px - h * 0.7071, py + h * 0.7071, px + h * 0.7071, py - h * 0.7071,
                      fill=SIDE_STRIPE, width=3)

class MapEditor:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.map = GameMap(name="Новая карта", width=GRID_DEFAULT_W, height=GRID_DEFAULT_H)
        self.view = self.map                            # что рисуем: карта или её «зашумлённая» копия
        self.preview_map = None
        self.seed = new_seed()
        self.tail = None                                # конец достроенной цепочки
        self.selected = None
        self.path = None                                # файл, в который сохраняем
        self.dirty = False
        self.undo_stack: list = []
        self.redo_stack: list = []
        self.pending = None                             # "jump" / "back": ждём клик по цели
        self.link_from = None                           # инструмент «Связать»: первая ячейка
        self.road = None                                # построение особого пути
        self.drag_before = None
        self.drag_last = None
        self.S = 48.0
        self._loading = False

        self.tool = tk.StringVar(value="route")
        self.preview = tk.BooleanVar(value=False)
        self.name_var = tk.StringVar(value=self.map.name)
        self.w_var = tk.StringVar(value=str(self.map.width))
        self.h_var = tk.StringVar(value=str(self.map.height))
        self.coords_var = tk.StringVar()
        self.msg_var = tk.StringVar(value="Готово. Кликайте по сетке, чтобы ставить ячейки.")
        self.hover_var = tk.StringVar()

        self._build_ui()
        self.sync_widgets()
        self.redraw()

    # ==================== Интерфейс ====================

    def _build_ui(self):
        root = self.root
        root.minsize(980, 640)

        menu = tk.Menu(root)
        file_m = tk.Menu(menu, tearoff=0)
        file_m.add_command(label="Новая карта", accelerator="Ctrl+N", command=self.new_map)
        file_m.add_command(label="Открыть…", accelerator="Ctrl+O", command=self.open_file)
        file_m.add_command(label="Сохранить", accelerator="Ctrl+S", command=self.save)
        file_m.add_command(label="Сохранить как…", accelerator="Ctrl+Shift+S",
                           command=lambda: self.save(as_new=True))
        file_m.add_separator()
        file_m.add_command(label="Экспорт для сервера…", command=self.export)
        file_m.add_separator()
        file_m.add_command(label="Выход", command=self.on_close)
        menu.add_cascade(label="Файл", menu=file_m)
        edit_m = tk.Menu(menu, tearoff=0)
        edit_m.add_command(label="Отменить", accelerator="Ctrl+Z", command=self.undo)
        edit_m.add_command(label="Вернуть", accelerator="Ctrl+Y", command=self.redo)
        edit_m.add_command(label="Проверить карту", accelerator="Ctrl+E", command=self.check)
        menu.add_cascade(label="Правка", menu=edit_m)
        menu.add_command(label="Справка", command=lambda: messagebox.showinfo("Справка", HELP_TEXT))
        root.config(menu=menu)

        main = ttk.Frame(root)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(main, bg=BG, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        side = ttk.Frame(main, padding=8)
        side.grid(row=0, column=1, sticky="ns")

        # --- карта
        box = ttk.LabelFrame(side, text="Карта", padding=6)
        box.pack(fill="x")
        ttk.Entry(box, textvariable=self.name_var, width=34).grid(row=0, column=0, columnspan=5, sticky="we")
        ttk.Label(box, text="Сетка:").grid(row=1, column=0, pady=4, sticky="w")
        ttk.Spinbox(box, from_=1, to=GRID_MAX_W, width=4, textvariable=self.w_var).grid(row=1, column=1)
        ttk.Label(box, text="×").grid(row=1, column=2)
        ttk.Spinbox(box, from_=1, to=GRID_MAX_H, width=4, textvariable=self.h_var).grid(row=1, column=3)
        ttk.Button(box, text="Применить", command=self.apply_size).grid(row=1, column=4, padx=(6, 0))
        self.name_var.trace_add("write", self.on_name)

        # --- инструмент
        box = ttk.LabelFrame(side, text="Инструмент", padding=6)
        box.pack(fill="x", pady=(6, 0))
        for text, val in (("Рисовать путь", "route"), ("Выбрать", "select"), ("Связать", "link"),
                          ("Особый путь", "road")):
            ttk.Radiobutton(box, text=text, value=val, variable=self.tool,
                            command=self.on_tool).pack(side="left", padx=4)

        # --- выбранная ячейка
        box = ttk.LabelFrame(side, text="Выбранная ячейка", padding=6)
        box.pack(fill="x", pady=(6, 0))
        self.info = ttk.Label(box, text="", wraplength=300, justify="left")
        self.info.pack(fill="x", pady=(0, 4))
        grid = ttk.Frame(box)
        grid.pack(fill="x")
        for i, eff in enumerate(EFFECTS):
            tk.Button(grid, text=f"{EFFECT_ICONS[eff]} {EFFECT_TITLES[eff]} ({EFFECT_KEYS[eff]})",
                      bg=EFFECT_COLORS[eff], fg=("#222222" if eff == BRANCH else "white"),
                      activeforeground="white", activebackground=EFFECT_COLORS[eff], takefocus=0, width=17,
                      command=lambda e=eff: self.set_effect(e)).grid(row=i // 2, column=i % 2, padx=2, pady=2)
        extra = ttk.Frame(box)
        extra.pack(fill="x", pady=(4, 0))
        for text, cmd in (("Без эффекта (0)", lambda: self.set_effect(None)),
                          ("Старт (S)", self.set_start), ("Финиш (F)", self.set_finish),
                          ("Удалить (Del)", lambda: self.delete_cell(self.selected))):
            ttk.Button(extra, text=text, takefocus=0, command=cmd).pack(side="left", padx=2)

        # --- координаты
        box = ttk.LabelFrame(side, text="Поставить по координатам (A1 B1 C1 или A1-E1)", padding=6)
        box.pack(fill="x", pady=(6, 0))
        entry = ttk.Entry(box, textvariable=self.coords_var, width=28)
        entry.pack(side="left")
        entry.bind("<Return>", lambda e: self.place_by_text())
        ttk.Button(box, text="Поставить", command=self.place_by_text).pack(side="left", padx=(6, 0))

        # --- предпросмотр
        box = ttk.LabelFrame(side, text="Предпросмотр шума (как увидят игроки)", padding=6)
        box.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(box, text="Показать", variable=self.preview,
                        command=self.toggle_preview, takefocus=0).pack(side="left")
        ttk.Button(box, text="Новый вариант", command=self.new_variant).pack(side="left", padx=8)

        # --- проверка и экспорт
        row = ttk.Frame(side)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Проверить (Ctrl+E)", command=self.check).pack(side="left")
        ttk.Button(row, text="Экспорт для сервера…", command=self.export).pack(side="left", padx=6)

        rep = ttk.Frame(side)
        rep.pack(fill="both", expand=True, pady=(6, 0))
        self.report = tk.Text(rep, width=44, height=12, wrap="word", state="disabled",
                              font=("Segoe UI", 9))
        sb = ttk.Scrollbar(rep, command=self.report.yview)
        self.report.config(yscrollcommand=sb.set)
        self.report.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bar = ttk.Frame(root)
        bar.pack(fill="x", side="bottom")
        ttk.Label(bar, textvariable=self.msg_var, anchor="w").pack(side="left", padx=6, pady=2)
        ttk.Label(bar, textvariable=self.hover_var, anchor="e").pack(side="right", padx=6)

        c = self.canvas
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<Button-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Button-3>", self.on_right)
        c.bind("<Motion>", self.on_motion)
        c.bind("<Leave>", lambda e: self.hover_var.set(""))
        root.bind("<KeyPress>", self.on_key)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def msg(self, text: str):
        self.msg_var.set(text)

    def show_report(self, text: str):
        self.report.config(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", text)
        self.report.config(state="disabled")

    def sync_widgets(self):
        """Подтягивает поля интерфейса из карты (после открытия, отмены и т. п.)."""
        self._loading = True
        self.name_var.set(self.map.name)
        self.w_var.set(str(self.map.width))
        self.h_var.set(str(self.map.height))
        self._loading = False
        self.update_title()

    def update_title(self):
        mark = " *" if self.dirty else ""
        file = f" [{self.path.name}]" if self.path else ""
        self.root.title(f"Редактор карт Джуманджи — {self.map.name}{file}{mark}")

    # ==================== Состояние: отмена, изменения ====================

    def state(self) -> tuple:
        return self.map.to_json(), self.tail

    def commit(self, before: tuple):
        """Фиксирует изменение, если оно было: кладёт прежнее состояние в стек отмены."""
        if self.state() == before:
            self.redraw()
            return
        self.undo_stack.append(before)
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()
        self.dirty = True
        self.update_title()
        self.redraw()

    def restore(self, st: tuple):
        js, tail = st
        self.map = GameMap.from_json(js)
        self.tail = tail
        if self.selected not in self.map.cells:
            self.selected = None
        self.link_from = None
        self.pending = None
        self.road = None
        self.dirty = True
        self.sync_widgets()
        self.redraw()

    def undo(self):
        if self.road is not None:
            self.cancel_road()
            return
        if not self.can_edit():
            return
        if not self.undo_stack:
            self.msg("Отменять нечего.")
            return
        self.redo_stack.append(self.state())
        self.restore(self.undo_stack.pop())
        self.msg("Отменено.")

    def redo(self):
        if self.road is not None:
            self.cancel_road()
            return
        if not self.can_edit():
            return
        if not self.redo_stack:
            self.msg("Возвращать нечего.")
            return
        self.undo_stack.append(self.state())
        self.restore(self.redo_stack.pop())
        self.msg("Возвращено.")

    def can_edit(self, allow_road: bool = False) -> bool:
        if self.preview.get():
            self.msg("Выключите «Предпросмотр шума», чтобы редактировать.")
            return False
        if self.road is not None and not allow_road:
            self.msg("Идёт построение особого пути: кликните по ячейке выхода или нажмите Esc.")
            return False
        return True

    def on_name(self, *_):
        if self._loading:
            return
        self.map.name = self.name_var.get().strip()
        self.dirty = True
        self.update_title()

    # ==================== Геометрия ====================

    def cell_at(self, x: float, y: float):
        gx = math.floor((x - PAD) / self.S)
        gy = math.floor((y - PAD) / self.S)
        if 0 <= gx < self.map.width and 0 <= gy < self.map.height:
            return cell_id(gx, gy)
        return None

    def center(self, cid: str) -> tuple:
        gx, gy = parse_cell_id(cid)
        c = self.view.cells.get(cid)
        dx, dy = (c.dx, c.dy) if c else (0.0, 0.0)
        return PAD + (gx + 0.5 + dx) * self.S, PAD + (gy + 0.5 + dy) * self.S

    # ==================== Рисование ====================

    def redraw(self):
        c = self.canvas
        c.delete("all")
        m = self.map
        cw, ch = c.winfo_width(), c.winfo_height()
        if cw < 60 or ch < 60:
            return
        self.S = max(18.0, min(72.0, (cw - 2 * PAD) / m.width, (ch - 2 * PAD) / m.height))
        S = self.S
        show = self.preview.get() and self.preview_map is not None
        self.view = self.preview_map if show else m
        v = self.view
        R = S * 0.36

        path, _problem = trace_path(m)
        index = {cid: i for i, cid in enumerate(path)}
        chains, _bad = side_chains(m, path)
        side_label = {}                                              # клетка дороги -> «12.3»
        for bid, chain, _exit in chains:
            for k, cid in enumerate(chain, start=1):
                side_label[cid] = f"{index[bid]}.{k}"

        # подписи и точки сетки
        font_small = ("Segoe UI", max(7, int(S * 0.2)))
        for gx in range(m.width):
            c.create_text(PAD + (gx + 0.5) * S, PAD / 2, text=chr(65 + gx), fill=GRID_TEXT, font=font_small)
        for gy in range(m.height):
            c.create_text(PAD / 2, PAD + (gy + 0.5) * S, text=str(gy + 1), fill=GRID_TEXT, font=font_small)
        if not show:
            for gx in range(m.width):
                for gy in range(m.height):
                    if cell_id(gx, gy) not in m.cells:
                        x, y = PAD + (gx + 0.5) * S, PAD + (gy + 0.5) * S
                        c.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill=GRID_DOT, outline="")

        drawable = {cid for cid in v.cells if parse_cell_id(cid) is not None}

        # стрелки прыжков и откатов
        for cid, cell in v.cells.items():
            if cell.effect in TARGET_EFFECTS and cid in drawable and cell.target in drawable:
                a, b = self.center(cid), self.center(cell.target)
                pts = _arc(a, b)
                lo, hi = 0, len(pts) - 1
                while lo < hi and _dist(pts[lo], a) < R + 2:
                    lo += 1
                while hi > lo and _dist(pts[hi], b) < R + 3:
                    hi -= 1
                pts = pts[lo:hi + 1]
                if len(pts) >= 2:
                    c.create_line(*[p for pt in pts for p in pt], fill=EFFECT_COLORS[cell.effect],
                                  width=3, arrow=tk.LAST, arrowshape=(12, 14, 5))

        # пунктир «начало особого пути -> первая клетка дороги»
        for cid, cell in v.cells.items():
            if cell.effect == BRANCH and cid in drawable and cell.target in drawable:
                a, b = self.center(cid), self.center(cell.target)
                length = _dist(a, b)
                if length > 2 * R + 6:
                    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
                    c.create_line(a[0] + ux * (R + 1), a[1] + uy * (R + 1),
                                  b[0] - ux * (R + 2), b[1] - uy * (R + 2),
                                  fill=EFFECT_COLORS[BRANCH], width=3, dash=(6, 4),
                                  arrow=tk.LAST, arrowshape=(10, 12, 4))

        # стрелки маршрута (внутри дороги жёлтые)
        for cid, cell in v.cells.items():
            if cid == v.finish or not cell.next or cid not in drawable or cell.next not in drawable:
                continue
            a, b = self.center(cid), self.center(cell.next)
            length = _dist(a, b)
            if length <= 2 * R + 6:
                continue
            ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            if cid in side_label:
                color = SIDE_LINK
            else:
                color = LINK_ON if cid in index else LINK_OFF
            c.create_line(a[0] + ux * (R + 1), a[1] + uy * (R + 1),
                          b[0] - ux * (R + 2), b[1] - uy * (R + 2),
                          fill=color, width=2, arrow=tk.LAST, arrowshape=(10, 12, 4))

        # ячейки
        font_icon = ("Segoe UI", max(8, int(S * 0.3)), "bold")
        orphans_matter = m.start in m.cells
        for cid, cell in v.cells.items():
            if cid not in drawable:
                continue
            x, y = self.center(cid)
            on_route = cid in index or cid in side_label
            if cid == v.finish:
                fill, ring, ring_w, text, fg = "#f0f0f0", FINISH_RING, 4, "F", "#1e1e1e"
            elif cid == v.start:
                fill, ring, ring_w, text, fg = NORMAL, START_RING, 4, "S", "#c82828"
            elif cell.side:
                fill, ring, ring_w, text, fg = SIDE_FILL, SIDE_RING, 2, "", "#222222"
            elif cell.effect in EFFECT_COLORS:
                fill, ring, ring_w = EFFECT_COLORS[cell.effect], RING, 2
                text = EFFECT_ICONS[cell.effect]
                fg = "#222222" if cell.effect == BRANCH else "white"
            else:
                fill, ring, ring_w, text, fg = NORMAL, RING, 2, "", "#222222"
            dash = (4, 3) if (orphans_matter and not on_route) else None
            c.create_oval(x - R, y - R, x + R, y + R, fill=fill, outline=ring, width=ring_w, dash=dash)
            if cell.side:
                _stripes(c, x, y, R)
            if text:
                c.create_text(x, y, text=text, fill=fg, font=font_icon)
            if on_route:
                label = str(index[cid]) if cid in index else side_label[cid]
                c.create_text(x - R * 0.85, y - R * 0.95, text=label, fill="#aeb3bd",
                              font=("Segoe UI", max(7, int(S * 0.19))))

        if show:
            return                                                   # в предпросмотре только картинка

        # выбор, начало строящегося пути, конец маршрута, первая ячейка связи
        if self.selected in m.cells:
            x, y = self.center(self.selected)
            c.create_oval(x - R - 5, y - R - 5, x + R + 5, y + R + 5, outline="white", width=3)
        if self.road is not None and self.road["branch"] in m.cells:
            x, y = self.center(self.road["branch"])
            c.create_oval(x - R - 8, y - R - 8, x + R + 8, y + R + 8, outline=EFFECT_COLORS[BRANCH],
                          width=3, dash=(5, 3))
        if self.link_from in m.cells:
            x, y = self.center(self.link_from)
            c.create_oval(x - R - 5, y - R - 5, x + R + 5, y + R + 5, outline=START_RING, width=3, dash=(5, 3))
        if self.tail in m.cells and self.tool.get() == "route":
            x, y = self.center(self.tail)
            c.create_oval(x + R * 0.55, y + R * 0.55, x + R * 1.1, y + R * 1.1,
                          fill="#ffd740", outline="#1e1e1e")
        self.update_info()

    def update_info(self):
        m = self.map
        cid = self.selected
        if cid not in m.cells:
            self.info.config(text="Ячейка не выбрана")
            return
        cell = m.cells[cid]
        path, _ = trace_path(m)
        bits = [f"Ячейка {cid}"]
        if cid in path:
            bits.append(f"№ на маршруте: {path.index(cid)}")
        if cid == m.start:
            bits.append("старт")
        if cid == m.finish:
            bits.append("финиш")
        if cell.side:
            bits.append("клетка особого пути (эффектов нет)")
        if cell.effect in EFFECT_TITLES:
            e = EFFECT_TITLES[cell.effect]
            if cell.effect in TARGET_EFFECTS:
                e += f" → {cell.target}" if cell.target else " → цель не указана!"
            elif cell.effect == BRANCH:
                chain, exit_id, problem = follow_road(m, cell.target)
                e += (f" → дорога {' '.join(chain)} → выход {exit_id}" if problem is None
                      else f" → {problem}")
            bits.append("эффект: " + e)
        if cid != m.finish:
            bits.append("далее: " + (cell.next or "не указана!"))
        self.info.config(text="\n".join(bits))

    # ==================== Мышь ====================

    def on_motion(self, e):
        cid = self.cell_at(e.x, e.y)
        if cid is None:
            self.hover_var.set("")
            return
        cell = self.map.cells.get(cid)
        extra = ""
        if cell is not None:
            if cell.side:
                extra += " · особый путь"
            if cell.effect in EFFECT_TITLES:
                extra += f" · {EFFECT_TITLES[cell.effect]}"
            if cid == self.map.start:
                extra += " · старт"
            if cid == self.map.finish:
                extra += " · финиш"
        self.hover_var.set(cid + extra)

    def on_press(self, e):
        self.canvas.focus_set()
        if not self.can_edit(allow_road=True):
            return
        cid = self.cell_at(e.x, e.y)
        if cid is None:
            return
        if self.pending:
            self.apply_target(cid)
            return
        tool = self.tool.get()
        if tool == "route":
            self.drag_before = self.state()
            self.drag_last = cid
            self.route_click(cid)
        elif tool == "select":
            self.selected = cid if cid in self.map.cells else None
            self.redraw()
        elif tool == "road":
            self.drag_last = cid
            self.road_press(cid)
        else:
            self.link_click(cid)

    def on_drag(self, e):
        if self.preview.get():
            return
        if self.tool.get() == "road":
            if self.road is None:
                return
            cid = self.cell_at(e.x, e.y)
            if cid is None or cid == self.drag_last:
                return
            self.drag_last = cid
            if cid not in self.map.cells:                            # при протяжке только кладём клетки
                self.road_click(cid)
            return
        if self.drag_before is None or self.tool.get() != "route":
            return
        cid = self.cell_at(e.x, e.y)
        if cid is None or cid == self.drag_last:
            return
        self.drag_last = cid
        if cid not in self.map.cells:                                # по уже стоящим ячейкам просто проходим
            self.route_click(cid)

    def on_release(self, e):
        before, self.drag_before, self.drag_last = self.drag_before, None, None
        if before is not None:
            self.commit(before)

    def on_right(self, e):
        self.canvas.focus_set()
        if not self.can_edit():
            return
        cid = self.cell_at(e.x, e.y)
        if cid in self.map.cells:
            self.delete_cell(cid)

    def route_click(self, cid: str):
        """Рисование пути (без записи в стек отмены: её делает on_release)."""
        m = self.map
        if cid in m.cells:
            if m.cells[cid].side:
                self.msg("Это клетка особого пути: основной маршрут через неё не продолжить.")
                return
            t = self.tail
            if (t in m.cells and t != cid and t != m.finish and not m.cells[t].next
                    and cid != m.start):
                m.cells[t].next = cid                                # дотягиваем маршрут до готовой ячейки
                self.msg(f"{t} → {cid}: ячейки соединены.")
            else:
                self.msg(f"Маршрут будет продолжаться от {cid}.")
            self.tail = cid
            self.selected = cid
        else:
            _added, _skipped, self.tail = place_cells(m, [cid], self.tail)
            self.selected = cid
            self.msg(f"Поставлена {cid}.")
        self.redraw()

    def link_click(self, cid: str):
        m = self.map
        if cid not in m.cells:
            self.link_from = None
            self.msg("Связь отменена: нужна готовая ячейка.")
            self.redraw()
            return
        if self.link_from is None:
            if cid == m.finish:
                self.msg("У финиша нет следующей ячейки.")
                return
            self.link_from = cid
            self.selected = cid
            self.msg(f"{cid} → …  Кликните по ячейке, куда она ведёт.")
            self.redraw()
            return
        src = self.link_from
        if cid == src:
            self.msg("Ячейка не может вести сама на себя.")
            return
        before = self.state()
        m.cells[src].next = cid
        self.link_from = None
        self.selected = cid
        self.msg(f"{src} → {cid}")
        self.commit(before)

    def road_press(self, cid: str):
        """Клик инструментом «Особый путь»: начало дороги, клетка дороги или выход."""
        if self.road is None:
            if cid not in self.map.cells:
                self.msg("Начало особого пути - готовая ячейка основного маршрута. Сначала поставьте её.")
                return
            self.begin_road(cid)
        else:
            self.road_click(cid)

    def begin_road(self, cid: str):
        m = self.map
        cell = m.cells[cid]
        if cell.side:
            self.msg("Начало особого пути должно быть на основном маршруте, а не на дороге.")
            return
        if cid in (m.start, m.finish):
            self.msg("На старте и финише эффектов быть не может.")
            return
        before = self.state()
        remove_road(m, cid)                                          # старая дорога этой ячейки пересоздаётся
        cell.effect, cell.target = BRANCH, None
        self.pending = self.link_from = None
        self.road = {"branch": cid, "before": before, "dirty": self.dirty}
        self.selected = cid
        self.msg(f"Особый путь от {cid}: кликайте по пустым клеткам (дорога по порядку), "
                 "затем по ячейке основного маршрута (выход). Esc - отмена.")
        self.redraw()

    def road_click(self, cid: str):
        m, r = self.map, self.road
        bid = r["branch"]
        if cid not in m.cells:                                       # новая клетка дороги
            m.cells[cid] = Cell(cid, side=True)
            if r["cells"]:
                m.cells[r["cells"][-1]].next = cid
            else:
                m.cells[bid].target = cid
            r["cells"].append(cid)
            self.selected = cid
            self.msg(f"Клетка дороги {cid} (всего {len(r['cells'])}). Выход - клик по ячейке маршрута.")
            self.redraw()
            return

        # клик по готовой ячейке: это выход
        cell = m.cells[cid]
        if cid == bid:
            self.msg("Выход не может совпадать с началом пути.")
        elif cell.side:
            self.msg("Эта клетка уже принадлежит особому пути.")
        elif cid == m.finish:
            self.msg("Выход не может вести на финиш: финиш берётся только точным броском.")
        elif not r["cells"]:
            self.msg("Сначала поставьте хотя бы одну клетку дороги.")
        else:
            path, _problem = trace_path(m)
            if cid not in path:
                self.msg("Выход должен лежать на основном маршруте (от старта).")
                return
            m.cells[r["cells"][-1]].next = cid
            self.finish_road(cid)

    def finish_road(self, exit_id: str):
        r, self.road = self.road, None
        self.msg(f"Особый путь от {r['branch']} готов: {len(r['cells'])} клеток, выход {exit_id}.")
        self.commit(r["before"])

    def cancel_road(self):
        r, self.road = self.road, None
        if r is None:
            return
        self.restore(r["before"])
        self.dirty = r["dirty"]
        self.update_title()
        self.msg("Построение особого пути отменено.")

    def on_tool(self):
        if self.road is not None:
            self.cancel_road()
        self.pending = None
        self.link_from = None
        self.canvas.config(cursor="")
        if self.tool.get() == "road":
            self.msg("Особый путь: кликните по ячейке основного маршрута, это будет начало дороги.")
        self.redraw()

    # ==================== Правки выбранной ячейки ====================

    def need_selected(self):
        if self.selected not in self.map.cells:
            self.msg("Сначала выберите ячейку.")
            return None
        return self.selected

    def set_effect(self, eff):
        if not self.can_edit():
            return
        cid = self.need_selected()
        if cid is None:
            return
        m = self.map
        if m.cells[cid].side:
            self.msg("На клетках особого пути эффектов быть не может.")
            return
        if eff is not None and cid in (m.start, m.finish):
            self.msg("На старте и финише эффектов быть не может.")
            return
        if eff == BRANCH:
            self.tool.set("road")
            self.on_tool()
            self.begin_road(cid)
            return
        if eff in TARGET_EFFECTS:
            self.pending = eff
            self.canvas.config(cursor="crosshair")
            self.msg(f"{EFFECT_TITLES[eff]}: кликните по ячейке-цели (Esc — отмена).")
            return
        before = self.state()
        remove_road(m, cid)                                          # был особый путь: дорога удаляется
        cell = m.cells[cid]
        cell.effect, cell.target = eff, None
        self.msg(f"{cid}: " + (EFFECT_TITLES[eff] if eff else "эффект снят"))
        self.commit(before)

    def apply_target(self, cid: str):
        m, eff, src = self.map, self.pending, self.selected
        if cid not in m.cells:
            self.msg("Цель должна быть уже поставленной ячейкой. Esc — отмена.")
            return
        if cid == src:
            self.msg("Цель не может совпадать с самой ячейкой.")
            return
        if m.cells[cid].side:
            self.msg("Цель должна лежать на основном маршруте, а не на особом пути.")
            return
        before = self.state()
        remove_road(m, src)                                          # был особый путь: дорога удаляется
        cell = m.cells[src]
        cell.effect, cell.target = eff, cid
        self.pending = None
        self.canvas.config(cursor="")
        self.msg(f"{src}: {EFFECT_TITLES[eff]} → {cid}")
        self.commit(before)

    def cancel_pending(self):
        if self.road is not None:
            self.cancel_road()
            return
        if self.pending or self.link_from:
            self.pending, self.link_from = None, None
            self.canvas.config(cursor="")
            self.msg("Отменено.")
            self.redraw()

    def set_start(self):
        if not self.can_edit():
            return
        cid = self.need_selected()
        if cid is None:
            return
        m = self.map
        if m.cells[cid].side:
            self.msg("Старт не может быть на клетке особого пути.")
            return
        if cid == m.finish:
            self.msg("Финиш не может быть стартом.")
            return
        before = self.state()
        remove_road(m, cid)
        m.start = cid
        m.cells[cid].effect = m.cells[cid].target = None
        self.msg(f"Старт: {cid}")
        self.commit(before)

    def set_finish(self):
        if not self.can_edit():
            return
        cid = self.need_selected()
        if cid is None:
            return
        m = self.map
        if m.cells[cid].side:
            self.msg("Финиш не может быть на клетке особого пути.")
            return
        if cid == m.start:
            self.msg("Старт не может быть финишем.")
            return
        before = self.state()
        remove_road(m, cid)
        m.finish = cid
        cell = m.cells[cid]
        cell.effect = cell.target = cell.next = None
        self.msg(f"Финиш: {cid}")
        self.commit(before)

    def delete_cell(self, cid):
        if not self.can_edit() or cid not in self.map.cells:
            if cid is None:
                self.msg("Сначала выберите ячейку.")
            return
        before = self.state()
        remove_road(self.map, cid)                                   # у начала особого пути уходит и дорога
        remove_cell(self.map, cid)
        self.tail = initial_tail(self.map)
        if self.selected == cid:
            self.selected = None
        if self.link_from == cid:
            self.link_from = None
        self.pending = None
        self.canvas.config(cursor="")
        self.msg(f"Ячейка {cid} удалена.")
        self.commit(before)

    def place_by_text(self):
        if not self.can_edit():
            return
        ids, error = parse_cells(self.coords_var.get(), self.map)
        if error:
            self.msg(error)
            return
        before = self.state()
        added, skipped, self.tail = place_cells(self.map, ids, self.tail)
        if not added:
            self.msg("Все эти ячейки уже стоят на карте.")
            return
        self.selected = added[-1]
        self.msg("Добавлено: " + ", ".join(added) + (" · уже стояли: " + ", ".join(skipped) if skipped else ""))
        self.commit(before)
        self.coords_var.set("")
        self.canvas.focus_set()

    def apply_size(self):
        if not self.can_edit():
            return
        try:
            w, h = int(self.w_var.get()), int(self.h_var.get())
        except ValueError:
            self.msg("Размер должен быть числами.")
            return
        if not (1 <= w <= GRID_MAX_W and 1 <= h <= GRID_MAX_H):
            self.msg(f"Допустимо от 1×1 до {GRID_MAX_W}×{GRID_MAX_H}.")
            return
        outside = []
        for cid in self.map.cells:
            p = parse_cell_id(cid)
            if p is None or p[0] >= w or p[1] >= h:
                outside.append(cid)
        if outside:
            self.msg("Нельзя уменьшить: за границами остались ячейки " + ", ".join(sorted(outside)[:12]))
            return
        before = self.state()
        self.map.width, self.map.height = w, h
        self.msg(f"Размер сетки: {w}×{h}")
        self.commit(before)

    # ==================== Клавиатура ====================

    def on_key(self, e):
        focus = self.root.focus_get()
        if isinstance(focus, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox, tk.Text)):
            return
        ctrl, shift, k, sym = bool(e.state & 0x4), bool(e.state & 0x1), e.keycode, e.keysym
        if ctrl:                                                     # коды клавиш Windows: не зависят от раскладки
            if k == 90:
                self.redo() if shift else self.undo()
            elif k == 89:
                self.redo()
            elif k == 83:
                self.save(as_new=shift)
            elif k == 79:
                self.open_file()
            elif k == 78:
                self.new_map()
            elif k == 69:
                self.check()
            return
        if sym in ("Delete", "BackSpace"):
            self.delete_cell(self.selected)
        elif sym == "Escape":
            self.cancel_pending()
        elif len(sym) == 1 and sym.isdigit() or (sym.startswith("KP_") and sym[3:].isdigit()):
            n = int(sym[-1])
            if n == 0:
                self.set_effect(None)
            elif n <= len(EFFECTS):
                self.set_effect(EFFECTS[n - 1])
        elif k == 83:
            self.set_start()
        elif k == 70:
            self.set_finish()

    # ==================== Предпросмотр, проверка, экспорт ====================

    def _build_preview(self):
        self.preview_map = jittered(self.map, self.seed)

    def toggle_preview(self):
        if self.preview.get():
            if self.road is not None:
                self.cancel_road()
            self.pending = self.link_from = None
            self.canvas.config(cursor="")
            self._build_preview()
            self.msg("Предпросмотр: так карта выглядит у игроков. Редактирование отключено.")
        else:
            self.preview_map = None
            self.msg("Предпросмотр выключен.")
        self.redraw()

    def new_variant(self):
        self.seed = new_seed()
        if self.preview.get():
            self._build_preview()
            self.redraw()
        self.msg("Новый вариант шума." if self.preview.get() else "Новый вариант выбран (включите «Показать»).")

    def check(self):
        rep = validate_map(self.map)
        self.show_report(rep.text(limit=100000))
        self.msg("Проверка пройдена." if rep.ok else f"Ошибок: {len(rep.errors)}.")
        return rep

    def export(self):
        if self.road is not None:
            self.cancel_road()
        m = self.map
        if not m.name.strip() or len(m.name.strip()) > MAP_NAME_MAX:
            messagebox.showerror("Экспорт", f"Название карты должно быть от 1 до {MAP_NAME_MAX} символов.")
            return
        rep = self.check()
        if not rep.ok:
            messagebox.showerror("Экспорт", "В карте есть ошибки (список справа). Экспорт невозможен.")
            return
        self.pending = self.link_from = None
        self.canvas.config(cursor="")
        self.preview.set(True)
        self._build_preview()
        self.redraw()
        if not messagebox.askyesno(
                "Экспорт для сервера",
                "Показан предпросмотр: так карта будет выглядеть у игроков.\n\n"
                "Экспортировать именно этот вариант?\n(Нет — можно нажать «Новый вариант» и попробовать снова.)"):
            return
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", m.name).strip("_") or "map"
        MAPS_DIR.mkdir(exist_ok=True)
        path = filedialog.asksaveasfilename(
            title="Экспорт для сервера", initialdir=str(MAPS_DIR), initialfile=f"maps_{safe}.json",
            defaultextension=".json", filetypes=[("Карта JSON", "*.json")])
        if not path:
            return
        final = jittered(m, self.seed)
        Path(path).write_text(final.to_json(), encoding="utf-8")
        name = Path(path).name
        self.msg(f"Экспортировано: {path}")
        messagebox.showinfo(
            "Экспорт готов",
            f"Файл сохранён:\n{path}\n\nНа сервер (PowerShell на ПК):\n"
            f"  scp \"{path}\" stacy:~/stacy/maps_import/\n\n"
            f"В консоли бота:\n  -jmap import /home/ubuntu/stacy/maps_import/{name}")

    # ==================== Файлы ====================

    def confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel("Несохранённые изменения", "Сохранить изменения в карте?")
        if ans is None:
            return False
        return self.save() if ans else True

    def load_map(self, m: GameMap, path):
        for cell in m.cells.values():
            cell.dx = cell.dy = 0.0                                  # рабочая карта всегда ровная
        self.map = m
        self.path = Path(path) if path else None
        self.tail = initial_tail(m)
        self.selected = None
        self.pending = self.link_from = None
        self.road = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.preview.set(False)
        self.preview_map = None
        self.dirty = False
        self.canvas.config(cursor="")
        self.sync_widgets()
        self.show_report("")
        self.redraw()

    def new_map(self):
        if not self.confirm_discard():
            return
        self.load_map(GameMap(name="Новая карта", width=GRID_DEFAULT_W, height=GRID_DEFAULT_H), None)
        self.msg("Новая карта.")

    def open_file(self, path=None):
        if not self.confirm_discard():
            return
        if path is None:
            MAPS_DIR.mkdir(exist_ok=True)
            path = filedialog.askopenfilename(title="Открыть карту", initialdir=str(MAPS_DIR),
                                              filetypes=[("Карта JSON", "*.json"), ("Все файлы", "*.*")])
        if not path:
            return
        try:
            m = GameMap.from_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, MapFormatError) as err:
            messagebox.showerror("Открыть карту", f"Не удалось прочитать карту:\n{err}")
            return
        if not m.name:
            m.name = Path(path).stem
        self.load_map(m, path)
        self.msg(f"Открыта карта «{m.name}».")

    def save(self, as_new: bool = False) -> bool:
        """Сохраняет ровную карту (без шума). True — сохранено."""
        path = self.path
        if as_new or path is None:
            MAPS_DIR.mkdir(exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", self.map.name).strip("_") or "map"
            chosen = filedialog.asksaveasfilename(
                title="Сохранить карту", initialdir=str(MAPS_DIR), initialfile=f"{safe}.json",
                defaultextension=".json", filetypes=[("Карта JSON", "*.json")])
            if not chosen:
                return False
            path = Path(chosen)
        try:
            path.write_text(self.map.to_json(), encoding="utf-8")
        except OSError as err:
            messagebox.showerror("Сохранение", f"Не удалось сохранить файл:\n{err}")
            return False
        self.path = path
        self.dirty = False
        self.update_title()
        self.msg(f"Сохранено: {path}")
        return True

    def on_close(self):
        if self.confirm_discard():
            self.root.destroy()

def main():
    root = tk.Tk()
    root.geometry("1220x780")
    editor = MapEditor(root)
    if len(sys.argv) > 1:
        root.after(100, lambda: editor.open_file(sys.argv[1]))
    root.mainloop()

if __name__ == "__main__":
    main()