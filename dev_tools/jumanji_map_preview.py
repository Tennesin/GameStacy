"""Предпросмотр карты Джуманджи «как в игре» (Tkinter). Только для разработчика, на сервере не нужен.

Запуск: правый клик по файлу в PyCharm -> Run 'jumanji_map_preview'
или из корня проекта:  python dev_tools/jumanji_map_preview.py [путь_к_карте.json]

Показывает карту так, как её рисует сама игра (games/jumanji/render.py): настоящая партия (Match),
фишки игроков и ботов, лог событий. Загружать карту в БД сервера для этого не нужно."""
import io
import random
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageDraw, ImageTk

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))                       # чтобы работал import games..., core..., config

from games.jumanji.jitter import jittered, new_seed
from games.jumanji.logic import Match, Player, Track
from games.jumanji.models import GameMap, MapFormatError
from games.jumanji.render import BOT_NAMES, HUMAN_COLORS, make_tokens, render_board
from games.jumanji.rules import BONUS_LABELS, BOT_IDS, LOG_LINES, MAX_BOTS, MAX_PLAYERS, TIMEOUTS_TO_LEAVE
from games.jumanji.validator import validate_map
from games.jumanji.texts import describe as describe_event

MAPS_DIR = ROOT / "maps_export"
RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS

HUMAN_NAMES = ["Иван", "Пётр", "Анна", "Мария", "Олег"]
COLOR_NAMES = ["Синий", "Зелёный", "Жёлтый", "Красный", "Оранжевый"]   # порядок как у HUMAN_COLORS в render.py
FAKE_AVATARS = [(200, 80, 80), (80, 160, 220), (90, 190, 120), (230, 190, 60), (170, 110, 220)]

AVATAR_MODES = ["Буквы (без аватарок)", "Разные картинки", "Одинаковые серые (худший случай)"]
SCALE_MODES = ["Вписать в окно", "100%", "Как в чате ПК (≈520 px)", "Как на телефоне (≈360 px)"]

BG = "#2b2d31"

def fake_avatar(color) -> bytes:
    """Условная аватарка: цветной фон и белый силуэт."""
    im = Image.new("RGB", (128, 128), color)
    dr = ImageDraw.Draw(im)
    dr.ellipse((34, 20, 94, 80), fill=(255, 255, 255))
    dr.ellipse((14, 78, 114, 160), fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()

class MapPreview:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.base = None                 # загруженная карта
        self.view_map = None             # что рисуем: карта или её копия с шумом
        self.track = None
        self.match = None
        self.has_noise = False
        self.seed = new_seed()
        self.log: list = []
        self.png = None                  # последняя картинка (байты PNG)
        self.pil = None                  # она же, объектом Pillow
        self.photo = None                # ссылка на ImageTk (иначе картинку удалит сборщик мусора)
        self.path = None

        self.humans_var = tk.StringVar(value="2")
        self.bots_var = tk.StringVar(value="1")
        self.avatar_var = tk.StringVar(value=AVATAR_MODES[1])
        self.scale_var = tk.StringVar(value=SCALE_MODES[0])
        self.noise_var = tk.BooleanVar(value=True)
        self.tp_player = tk.StringVar()
        self.tp_cell = tk.StringVar()
        self.msg_var = tk.StringVar(value="Откройте карту (Ctrl+O).")
        self.name_var = tk.StringVar(value="Карта не открыта")

        self._build_ui()

    # ==================== Интерфейс ====================

    def _build_ui(self):
        root = self.root
        root.title("Предпросмотр карты Джуманджи")
        root.minsize(1000, 640)

        main = ttk.Frame(root)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        view = ttk.Frame(main)
        view.grid(row=0, column=0, sticky="nsew")
        view.columnconfigure(0, weight=1)
        view.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(view, bg=BG, highlightthickness=0)
        sx = ttk.Scrollbar(view, orient="horizontal", command=self.canvas.xview)
        sy = ttk.Scrollbar(view, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<Configure>", lambda e: self.show_image())

        side = ttk.Frame(main, padding=8)
        side.grid(row=0, column=1, sticky="ns")

        box = ttk.LabelFrame(side, text="Карта", padding=6)
        box.pack(fill="x")
        ttk.Label(box, textvariable=self.name_var, wraplength=330).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(4, 0))
        ttk.Button(row, text="Открыть… (Ctrl+O)", command=self.open_file).pack(side="left")
        ttk.Button(row, text="Перечитать файл", command=self.reload).pack(side="left", padx=6)

        box = ttk.LabelFrame(side, text="Партия", padding=6)
        box.pack(fill="x", pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text="Людей:").pack(side="left")
        ttk.Spinbox(row, from_=1, to=MAX_PLAYERS, width=3, textvariable=self.humans_var).pack(side="left", padx=(2, 10))
        ttk.Label(row, text="Ботов:").pack(side="left")
        ttk.Spinbox(row, from_=0, to=MAX_BOTS, width=3, textvariable=self.bots_var).pack(side="left", padx=2)
        ttk.Button(row, text="Новая партия", command=self.new_game).pack(side="left", padx=10)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Аватарки:").pack(side="left")
        cb = ttk.Combobox(row, textvariable=self.avatar_var, values=AVATAR_MODES, state="readonly", width=32)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        self.noise_cb = ttk.Checkbutton(row, text="Применить шум (если карта ровная)",
                                        variable=self.noise_var, command=self.rebuild_view)
        self.noise_cb.pack(side="left")
        ttk.Button(row, text="Новый вариант", command=self.new_variant).pack(side="left", padx=8)

        box = ttk.LabelFrame(side, text="Ходы", padding=6)
        box.pack(fill="x", pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text="🎲 Бросок (Пробел)", command=self.do_roll).pack(side="left")
        ttk.Button(row, text="До хода человека", command=self.run_bots).pack(side="left", padx=6)
        ttk.Button(row, text="Сыграть 10 бросков", command=lambda: self.run_many(10)).pack(side="left")

        box = ttk.LabelFrame(side, text="Поставить фишку (например 12 или 12.3)", padding=6)
        box.pack(fill="x", pady=(6, 0))
        self.tp_box = ttk.Combobox(box, textvariable=self.tp_player, state="readonly", width=24)
        self.tp_box.pack(side="left")
        entry = ttk.Entry(box, textvariable=self.tp_cell, width=7)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self.teleport())
        ttk.Button(box, text="Поставить", command=self.teleport).pack(side="left")

        box = ttk.LabelFrame(side, text="Масштаб (как увидят в Discord)", padding=6)
        box.pack(fill="x", pady=(6, 0))
        cb = ttk.Combobox(box, textvariable=self.scale_var, values=SCALE_MODES, state="readonly", width=32)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.show_image())
        ttk.Button(box, text="Сохранить PNG…", command=self.save_png).pack(side="left", padx=8)

        rep = ttk.Frame(side)
        rep.pack(fill="both", expand=True, pady=(6, 0))
        self.text = tk.Text(rep, width=52, height=22, wrap="word", state="disabled", font=("Segoe UI", 9))
        sb = ttk.Scrollbar(rep, command=self.text.yview)
        self.text.config(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        ttk.Label(root, textvariable=self.msg_var, anchor="w").pack(fill="x", side="bottom", padx=6, pady=2)

        root.bind("<Control-o>", lambda e: self.open_file())
        root.bind("<Control-O>", lambda e: self.open_file())
        root.bind("<space>", self._on_space)

    def _on_space(self, event):
        if isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox)):
            return
        self.do_roll()

    def msg(self, text: str):
        self.msg_var.set(text)

    def show_text(self, text: str):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.config(state="disabled")

    # ==================== Загрузка карты ====================

    def open_file(self, path=None):
        if path is None:
            MAPS_DIR.mkdir(exist_ok=True)
            path = filedialog.askopenfilename(title="Открыть карту", initialdir=str(MAPS_DIR),
                                              filetypes=[("Карта JSON", "*.json"), ("Все файлы", "*.*")])
        if not path:
            return
        self.load(Path(path))

    def reload(self):
        if self.path is None:
            self.msg("Сначала откройте карту.")
            return
        self.load(self.path)

    def load(self, path: Path):
        try:
            gmap = GameMap.from_json(path.read_text(encoding="utf-8"))
        except (OSError, MapFormatError) as e:
            messagebox.showerror("Открыть карту", f"Не удалось прочитать карту:\n{e}")
            return
        rep = validate_map(gmap, runs=300)
        if not rep.ok:
            self.base = self.track = self.match = None
            self.canvas.delete("all")
            self.name_var.set(f"{path.name}: ошибки в карте")
            self.show_text(rep.text(limit=100000))
            self.msg("Карта не прошла проверку: партию запустить нельзя.")
            return
        try:
            self.track = Track(gmap)
        except MapFormatError as e:
            messagebox.showerror("Открыть карту", str(e))
            return
        self.base, self.path = gmap, path
        self.has_noise = any(c.dx or c.dy for c in gmap.cells.values())
        self.noise_cb.config(state="disabled" if self.has_noise else "normal")
        self.name_var.set(f"«{gmap.name}» · {path.name} · маршрут {self.track.main_len} клеток"
                          + (" · шум уже в файле" if self.has_noise else " · карта ровная"))
        self.rebuild_view(new_game=True)
        self.report = rep.text(limit=100000)
        self.msg("Карта открыта. Предупреждения проверки показаны в тексте справа в начале.")
        self.refresh(first_report=rep.text(limit=100000))

    def rebuild_view(self, new_game: bool = False):
        if self.base is None:
            return
        if not self.has_noise and self.noise_var.get():
            self.view_map = jittered(self.base, self.seed)
        else:
            self.view_map = self.base.copy()
        if new_game or self.match is None:
            self.new_game()
        else:
            self.refresh()

    def new_variant(self):
        self.seed = new_seed()
        if self.has_noise:
            self.msg("В файле уже есть шум: новый вариант возможен только у ровной карты.")
            return
        self.rebuild_view()

    # ==================== Партия ====================

    def new_game(self):
        if self.track is None:
            self.msg("Сначала откройте карту.")
            return
        try:
            n_h = max(1, min(MAX_PLAYERS, int(self.humans_var.get())))
            n_b = max(0, min(MAX_BOTS, int(self.bots_var.get())))
        except ValueError:
            self.msg("Число людей и ботов должно быть числом.")
            return
        if n_h + n_b < 2:
            n_b = 2 - n_h
        if n_h + n_b > MAX_PLAYERS:
            n_b = MAX_PLAYERS - n_h
        self.humans_var.set(str(n_h))
        self.bots_var.set(str(n_b))

        players = ([Player(i + 1, HUMAN_NAMES[i]) for i in range(n_h)]
                   + [Player(BOT_IDS[i], BOT_NAMES[i], True) for i in range(n_b)])
        random.shuffle(players)                                  # порядок ходов случайный, как в игре
        self.match = Match(self.track, players)
        self.match.start()
        self.log = ["🎬 Игра началась!"]
        self.tp_box.config(values=[self.nm(p.pid) for p in self.match.players])
        self.tp_player.set(self.nm(self.match.players[0].pid))
        self.refresh()
        self.msg("Новая партия.")

    def seat_of(self, pid: int) -> int:
        return next((i for i, p in enumerate(self.match.players) if p.pid == pid), 0)

    def nm(self, pid: int) -> str:
        """Подпись игрока с цветом: «Синий: Иван» или «Фиолетовый бот»."""
        p = self.match.get(pid)
        if p is not None and p.is_bot:
            return p.name
        seat = p.color if (p is not None and p.color >= 0) else self.seat_of(pid)
        color = COLOR_NAMES[seat] if seat < len(COLOR_NAMES) else f"цвет {seat + 1}"
        return f"{color}: {p.name if p else pid}"

    def do_roll(self):
        m = self.match
        if m is None:
            self.msg("Сначала откройте карту.")
            return
        if m.over:
            self.msg("Партия окончена. Нажмите «Новая партия».")
            return
        self._step()
        self.refresh()

    def _step(self):
        m = self.match
        if m.awaiting_minigame:                                  # мини-игру имитируем: победитель случайный
            humans = [p.pid for p in m.players if p.on_map and not p.is_bot]
            winners = [random.choice(humans)] if humans and random.random() < 0.7 else []
            events = m.finish_minigame(winners)
            self._add_log([None], "🟣 (имитация мини-игры)")
        else:
            events = m.roll()
        self._add_log(events)

    def run_bots(self):
        m = self.match
        if m is None or m.over:
            return
        for _ in range(300):
            if m.over or not (m.awaiting_minigame or m.current_player.is_bot):
                break
            self._step()
        self.refresh()

    def run_many(self, n: int):
        m = self.match
        if m is None or m.over:
            return
        for _ in range(n):
            if m.over:
                break
            self._step()
        self.refresh()

    def teleport(self):
        m = self.match
        if m is None:
            return
        pid = next((p.pid for p in m.players if self.nm(p.pid) == self.tp_player.get()), None)
        if pid is None:
            self.msg("Выберите игрока.")
            return
        text = self.tp_cell.get().strip()
        idx = None
        t = self.track
        if re.fullmatch(r"\d+", text):
            if int(text) < t.main_len:
                idx = int(text)
        elif re.fullmatch(r"\d+\.\d+", text):
            owner, step = (int(x) for x in text.split("."))
            idx = next((i for i in t.owner if t.owner[i] == owner and t.step_no[i] == step), None)
        if idx is None:
            self.msg(f"Нет такой клетки: «{text}». Основной маршрут: 0-{t.main_len - 1}; "
                     "особый путь: «клетка.номер», например 12.3.")
            return
        p = m.get(pid)
        p.pos, p.on_branch = idx, False
        self.log.append(f"📍 (тест) {self.nm(pid)} поставлен на клетку {t.label(idx)}")
        self.log = self.log[-LOG_LINES:]
        self.refresh()

    # ==================== Лог ====================

    def _add_log(self, events: list, raw: str = ""):
        if raw:
            self.log.append(raw)
        for e in events:
            t = self.describe(e) if e is not None else None
            if t:
                self.log.append(t)
        self.log = self.log[-LOG_LINES:]

    def describe(self, ev):
        return describe_event(ev, self.track, self.nm(ev.pid) if ev.pid else "")

    # ==================== Показ ====================

    def _board_text(self) -> str:
        m, t = self.match, self.track
        lines = [f"🎲 Джуманджи · карта «{self.base.name}»"]
        for p in m.players:
            mark = "▶" if (not m.over and p is m.current_player and not m.awaiting_minigame) else "·"
            if p.finished:
                status = f"финишировал, место {p.place}"
            elif p.left:
                status = "вышел"
            else:
                status = f"клетка {t.label(p.pos)} из {t.finish}"
                if t.is_side(p.pos):
                    status += " · особый путь"
                elif p.on_branch:
                    status += " · пойдёт по особому пути"
                if p.skip:
                    status += f" · пропуск ×{p.skip}"
                if p.bonus:
                    status += f" · {BONUS_LABELS.get(p.bonus, p.bonus)}"
            lines.append(f"{mark} {self.nm(p.pid)} — {status}")
        if m.over:
            lines.append("🏁 Партия окончена.")
        elif m.awaiting_minigame:
            lines.append("🟣 Идёт мини-игра (нажмите «Бросок», чтобы закончить её).")
        else:
            lines.append(f"Ход: {self.nm(m.current_player.pid)}"
                         + (" (бот)" if m.current_player.is_bot else ""))
        if self.log:
            lines += [""] + self.log
        return "\n".join(lines)

    def _avatars(self) -> dict:
        mode = self.avatar_var.get()
        out = {}
        if mode == AVATAR_MODES[0]:
            return out
        for p in self.match.players:
            if p.is_bot:
                continue
            if mode == AVATAR_MODES[2]:
                out[p.pid] = fake_avatar((128, 128, 128))
            else:
                out[p.pid] = fake_avatar(FAKE_AVATARS[p.color % len(FAKE_AVATARS)])
        return out

    def refresh(self, first_report: str = ""):
        if self.match is None or self.view_map is None:
            return
        try:
            self.png = render_board(self.view_map, make_tokens(self.match, self._avatars()))
        except Exception as e:                                   # ошибка рисования не должна ронять окно
            import traceback
            traceback.print_exc()
            self.msg(f"Ошибка рисования: {type(e).__name__}: {e} (подробности в консоли)")
            return
        self.pil = Image.open(io.BytesIO(self.png))
        self.pil.load()
        self.show_image()
        text = self._board_text()
        if first_report:
            text = "ПРОВЕРКА КАРТЫ\n" + first_report + "\n\n" + "─" * 30 + "\n\n" + text
        self.show_text(text)

    def show_image(self):
        if self.pil is None:
            return
        w, h = self.pil.size
        mode = self.scale_var.get()
        if mode == SCALE_MODES[0]:
            cw, ch = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)
            s = min(cw / w, ch / h, 1.0)
        elif mode == SCALE_MODES[1]:
            s = 1.0
        elif mode == SCALE_MODES[2]:
            s = 520 / w
        else:
            s = 360 / w
        size = (max(1, int(w * s)), max(1, int(h * s)))
        img = self.pil if size == (w, h) else self.pil.resize(size, RESAMPLE)
        self.photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.config(scrollregion=(0, 0, size[0], size[1]))

    def save_png(self):
        if self.png is None:
            self.msg("Нечего сохранять: карта не открыта.")
            return
        path = filedialog.asksaveasfilename(title="Сохранить картинку", defaultextension=".png",
                                            initialfile="preview.png", filetypes=[("PNG", "*.png")])
        if path:
            Path(path).write_bytes(self.png)
            self.msg(f"Сохранено: {path}")

def main():
    root = tk.Tk()
    root.geometry("1280x800")
    app = MapPreview(root)
    if len(sys.argv) > 1:
        root.after(100, lambda: app.open_file(sys.argv[1]))
    root.mainloop()

if __name__ == "__main__":
    main()