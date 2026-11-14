"""Редактор карт Джуманджи (только для разработчика).."""
import asyncio
import io
import logging
import re
import time
from collections import defaultdict

import discord

from config import DEV_ID
from core.errors import report_error
from core.scope import ignore, in_scope
from .models import Cell, GameMap, MapFormatError, cell_id, parse_cell_id, trace_path
from .render import render_board_async
from .rules import EFFECT_ICONS, EFFECT_TITLES, GRID_MAX_H, GRID_MAX_W, MAP_NAME_MAX, TARGET_EFFECTS
from .validator import validate_map

log = logging.getLogger("stacy.jumanji.editor")

NO_PINGS = discord.AllowedMentions.none()
EFFECT_ACTS = {"e_skip": "skip", "e_charge": "charge", "e_mini": "mini", "e_reset": "reset"}
NEW_MAP_NAME = "Новая карта"

_CYR = str.maketrans("АВСЕН", "ABCEH")        # русские буквы-двойники -> латиница
_DASHES = "–—−"

class EditorError(Exception):
    """Понятная причина, по которой редактор не открылся."""

def esc(text) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(text)))

# ==================== Чистые функции (без Discord и БД) ====================

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

# ==================== Окна и кнопки ====================

class InputModal(discord.ui.Modal):
    """Окно с одним полем. handler(interaction, текст) - асинхронная функция."""

    def __init__(self, title: str, label: str, handler, *, placeholder: str = "", default: str = "",
                 paragraph: bool = False, max_length: int = 100):
        super().__init__(title=title[:45])
        self.handler = handler
        self.field = discord.ui.TextInput(
            label=label[:45], placeholder=placeholder or None, default=default or None,
            required=True, max_length=max_length,
            style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short)
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        await self.handler(interaction, self.field.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await report_error(interaction, error)

class EditorView(discord.ui.View):
    """Три панели под одним сообщением. custom_id: jme:ID_сессии:действие."""

    def __init__(self, ctrl: "EditorController", sid: int, panel: str):
        super().__init__(timeout=None)
        self.ctrl, self.sid = ctrl, sid
        S = discord.ButtonStyle

        def add(label, act, row, style=S.secondary, emoji=None):
            b = discord.ui.Button(label=label, emoji=emoji, style=style,
                                  custom_id=f"jme:{sid}:{act}", row=row)
            b.callback = self._make(act)
            self.add_item(b)

        if panel == "menu":
            add("Сохранить", "save", 0, S.success)
            add("Проверить", "check", 0)
            add("Экспортировать", "export", 0, S.primary)
            add("Переименовать", "rename", 0)
            add("Назад", "back", 1)
            add("Закрыть редактор", "close", 1, S.danger)
        elif panel == "cell":
            add("Прыжок", "e_jump", 0, S.success, EFFECT_ICONS["jump"])
            add("Откат", "e_back", 0, S.danger, EFFECT_ICONS["back"])
            add("Пропуск", "e_skip", 0, S.secondary, EFFECT_ICONS["skip"])
            add("Заряд", "e_charge", 0, S.secondary, EFFECT_ICONS["charge"])
            add("Мини-игра", "e_mini", 0, S.secondary, EFFECT_ICONS["mini"])
            add("Сброс", "e_reset", 1, S.secondary, EFFECT_ICONS["reset"])
            add("Без эффекта", "e_none", 1)
            add("Связать", "link", 1, S.primary)
            add("Старт", "setstart", 1, S.secondary, "🚩")
            add("Финиш", "setfinish", 1, S.secondary, "🏁")
            add("Очистить ячейку", "clear", 2, S.danger)
            add("Назад", "back", 2)
        else:
            add("Меню", "menu", 0, S.secondary, "☰")
            add("Поставить", "place", 0, S.success)
            add("Выбрать", "select", 0, S.primary)

    def _make(self, act: str):
        async def callback(interaction: discord.Interaction):
            await self.ctrl.action(interaction, self.sid, act)
        return callback

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)

# ==================== Контроллер ====================

class EditorController:
    def __init__(self, bot, storage):
        self.bot = bot
        self.db = storage
        self.locks = defaultdict(asyncio.Lock)

    # ---------- Запуск и восстановление ----------

    def restore_views(self):
        for s in self.db.editors():
            if s["message_id"]:
                self.bot.add_view(self._view(s), message_id=s["message_id"])

    def _view(self, s) -> EditorView:
        panel = s["panel"]
        if panel == "cell" and not s["selected"]:
            panel = "main"
        return EditorView(self, s["session_id"], panel)

    def _prepare_map(self, name):
        """Карта для редактора: существующий черновик, копия готовой карты или новая пустая."""
        drafts = self.db.list_drafts()
        maps = self.db.list_maps()
        if name:
            name = name.strip()
            if len(name) > MAP_NAME_MAX:
                raise EditorError(f"Название длиннее {MAP_NAME_MAX} символов.")
            try:
                row = self.db.get_draft(name)
                if row:
                    m = GameMap.from_json(row["json"])
                    m.name = row["name"]
                    return m
                for r in maps:
                    if r["name"].casefold() == name.casefold():
                        m = GameMap.from_json(self.db.get_map(r["map_id"])["json"])
                        m.name = r["name"]
                        self.db.save_draft(m.name, m.to_json())
                        return m
            except MapFormatError as e:
                raise EditorError(f"Карта повреждена: {e}")
        else:
            taken = {r["name"].casefold() for r in drafts} | {r["name"].casefold() for r in maps}
            name, i = NEW_MAP_NAME, 2
            while name.casefold() in taken:
                name = f"{NEW_MAP_NAME} {i}"
                i += 1
        m = GameMap(name=name, width=GRID_MAX_W, height=GRID_MAX_H)
        self.db.save_draft(m.name, m.to_json())
        return m

    async def open(self, channel, owner_id: int, home_id=None, name=None) -> int:
        """Открывает редактор в канале. Возвращает ID сессии. Бросает EditorError."""
        m = self._prepare_map(name)
        for s in self.db.editors():
            if s["map_name"].casefold() == m.name.casefold():
                raise EditorError(f"Карта «{m.name}» уже открыта в редакторе (сессия #{s['session_id']}). "
                                  "Закройте её: -jmap close ID_сессии.")
        sid = self.db.create_editor(channel.guild.id, channel.id, home_id, owner_id,
                                    m.name, initial_tail(m))
        try:
            s = self.db.get_editor(sid)
            png = await render_board_async(m, selected=None, show_grid=True)
            msg = await channel.send(content=self._text(s, m), file=self._file(png),
                                     view=self._view(s), allowed_mentions=NO_PINGS)
        except Exception:
            self.db.close_editor(sid)
            raise
        self.db.update_editor(sid, message_id=msg.id)
        return sid

    # ---------- Текст и картинка ----------

    @staticmethod
    def _file(png: bytes) -> discord.File:
        return discord.File(io.BytesIO(png), filename=f"editor_{time.time_ns()}.png")

    @staticmethod
    def _cell_info(m: GameMap, cid: str) -> str:
        c = m.cells[cid]
        bits = [f"Выбрана **{cid}**"]
        if cid == m.start:
            bits.append("🚩 старт")
        if cid == m.finish:
            bits.append("🏁 финиш")
        if c.effect in EFFECT_TITLES:
            e = f"{EFFECT_ICONS[c.effect]} {EFFECT_TITLES[c.effect]}"
            if c.effect in TARGET_EFFECTS:
                e += f" → {c.target}" if c.target else " → ⚠ цель не указана"
            bits.append(e)
        elif c.effect:
            bits.append(f"⚠ неизвестный эффект «{c.effect}»")
        if cid != m.finish:
            bits.append("далее: " + (c.next or "⚠ не указана"))
        return " · ".join(bits)

    def _text(self, s, m: GameMap, note: str = "") -> str:
        path, problem = trace_path(m)
        if not path:
            route = "пока нет"
        elif problem is None:
            route = f"{len(path)} яч., доходит до финиша ✅"
        else:
            route = f"{len(path)} яч. от старта, до финиша пока не доведён"
        sel = s["selected"] if s["selected"] in m.cells else None
        lines = [
            f"🛠 **Редактор карты** «{esc(m.name)}» · сессия #{s['session_id']}",
            f"Сетка {m.width}×{m.height} · ячеек: {len(m.cells)} · "
            f"старт: {m.start or '—'} · финиш: {m.finish or '—'}",
            f"Маршрут: {route}",
        ]
        panel = s["panel"]
        if panel == "cell" and sel:
            lines.append(self._cell_info(m, sel))
            lines.append("🟢 прыжок вперёд · 🔴 откат · 🟠 пропуск хода · 🔵 доп. бросок · "
                         "🟡 мини-игра · ⚫ сброс на старт")
        elif panel == "menu":
            lines.append("Сохранить — файл-копия карты. Проверить — проверка без экспорта. "
                         "Экспортировать — проверка и добавление в список карт для игроков.")
        else:
            lines.append("Поставить — добавить ячейки по порядку маршрута (A1 B1 C1 или A1-E1): "
                         "каждая новая идёт после предыдущей. Выбрать — открыть ячейку для настройки.")
        if note:
            lines.append("ℹ️ " + note)
        return "\n".join(lines)[:1990]

    async def _redraw(self, interaction: discord.Interaction, s, m: GameMap, note: str = ""):
        """Картинка + текст + кнопки. Ответ на interaction уже должен быть дан (defer)."""
        kw = {}
        try:
            png = await render_board_async(m, selected=s["selected"], show_grid=True)
            kw["attachments"] = [self._file(png)]
        except Exception:
            log.exception("Не удалось нарисовать карту редактора #%s", s["session_id"])
            note = (note + " " if note else "") + "⚠ картинку нарисовать не удалось."
        await interaction.edit_original_response(
            content=self._text(s, m, note), view=self._view(s), allowed_mentions=NO_PINGS, **kw)

    async def _commit(self, interaction: discord.Interaction, sid: int, m: GameMap, note: str = ""):
        """Сохраняет карту в черновик и перерисовывает сообщение."""
        s = self.db.get_editor(sid)
        self.db.save_draft(s["map_name"], m.to_json())
        await interaction.response.defer()
        await self._redraw(interaction, s, m, note)

    async def _panel(self, interaction: discord.Interaction, sid: int, m: GameMap, panel: str):
        """Смена набора кнопок без перерисовки картинки."""
        self.db.update_editor(sid, panel=panel)
        s = self.db.get_editor(sid)
        await interaction.response.edit_message(content=self._text(s, m), view=self._view(s))

    @staticmethod
    async def _err(interaction: discord.Interaction, text: str):
        await interaction.response.send_message(text, ephemeral=True)

    # ---------- Вход в обработчик ----------

    def _load(self, s):
        row = self.db.get_draft(s["map_name"])
        if row is None:
            return None
        try:
            m = GameMap.from_json(row["json"])
        except MapFormatError:
            return None
        m.name = row["name"]
        return m

    async def _enter(self, interaction: discord.Interaction, sid: int):
        """(сессия, карта) или None, если нажатие отклонено (ответ уже дан)."""
        s = self.db.get_editor(sid)
        if s is None:
            await self._err(interaction, "Эта сессия редактора закрыта.")
            return None
        uid = interaction.user.id
        if uid != DEV_ID and uid != s["owner_id"]:
            await self._err(interaction, "Редактор доступен только разработчику.")
            return None
        if not in_scope(interaction, s["home_channel_id"]):
            await ignore(interaction, s["home_channel_id"])
            return None
        m = self._load(s)
        if m is None:
            await self._err(interaction, "Черновик карты не найден или повреждён.")
            return None
        return s, m

    # ---------- Кнопки ----------

    async def action(self, interaction: discord.Interaction, sid: int, act: str):
        async with self.locks[sid]:
            got = await self._enter(interaction, sid)
            if got is None:
                return
            s, m = got
            cid = s["selected"] if s["selected"] in m.cells else None

            if act == "menu":
                await self._panel(interaction, sid, m, "menu")

            elif act == "back":
                if s["panel"] == "cell":
                    self.db.update_editor(sid, selected=None, panel="main")
                    await interaction.response.defer()
                    await self._redraw(interaction, self.db.get_editor(sid), m)
                else:
                    await self._panel(interaction, sid, m, "main")

            elif act == "place":
                await interaction.response.send_modal(InputModal(
                    "Поставить ячейки", "Координаты (A1 B1 C1 или A1-E1)",
                    lambda i, t: self.submit_place(i, sid, t),
                    placeholder="A1-E1", paragraph=True, max_length=400))

            elif act == "select":
                await interaction.response.send_modal(InputModal(
                    "Выбрать ячейку", "Координата (например B4)",
                    lambda i, t: self.submit_select(i, sid, t), placeholder="B4", max_length=10))

            elif act == "save":
                self.db.save_draft(s["map_name"], m.to_json())
                name = re.sub(r"[^A-Za-z0-9_-]+", "_", m.name).strip("_") or "map"
                file = discord.File(io.BytesIO(m.to_json().encode("utf-8")), filename=f"{name}.json")
                await interaction.response.send_message(
                    f"💾 Черновик «{esc(m.name)}» сохранён (он и так сохраняется после каждого действия). "
                    "Копия во вложении.", file=file, ephemeral=True)

            elif act in ("check", "export"):
                await interaction.response.defer(ephemeral=True, thinking=True)
                rep = await asyncio.to_thread(validate_map, m)
                text = rep.text()
                if act == "export":
                    if rep.ok:
                        map_id = self.db.export_map(m.name, m.to_json())
                        text += f"\n\n📤 Карта «{m.name}» добавлена в список для игроков (ID {map_id})."
                    else:
                        text += "\n\nЭкспорт не выполнен."
                await interaction.followup.send(text[:1990], ephemeral=True)

            elif act == "rename":
                await interaction.response.send_modal(InputModal(
                    "Переименовать карту", "Новое название",
                    lambda i, t: self.submit_rename(i, sid, t),
                    default=m.name, max_length=MAP_NAME_MAX))

            elif act == "close":
                self.db.close_editor(sid)
                self.locks.pop(sid, None)
                await interaction.response.edit_message(
                    content=f"🛠 Редактор закрыт. Черновик «{esc(m.name)}» сохранён.", view=None)

            elif cid is None:
                await self._err(interaction, "Сначала выберите ячейку («Назад» → «Выбрать»).")

            elif act in EFFECT_ACTS or act == "e_none":
                if cid in (m.start, m.finish):
                    await self._err(interaction, "На старте и финише эффектов быть не может.")
                    return
                c = m.cells[cid]
                c.effect, c.target = EFFECT_ACTS.get(act), None
                note = f"{cid}: " + ("эффект снят" if c.effect is None
                                     else f"{EFFECT_ICONS[c.effect]} {EFFECT_TITLES[c.effect]}")
                await self._commit(interaction, sid, m, note)

            elif act in ("e_jump", "e_back"):
                if cid in (m.start, m.finish):
                    await self._err(interaction, "На старте и финише эффектов быть не может.")
                    return
                kind = act[2:]
                await interaction.response.send_modal(InputModal(
                    "Куда прыгнуть?" if kind == "jump" else "Куда откатить?", "Ячейка-цель (например F4)",
                    lambda i, t: self.submit_target(i, sid, kind, t), placeholder="F4", max_length=10))

            elif act == "link":
                if cid == m.finish:
                    await self._err(interaction, "У финиша нет следующей ячейки.")
                    return
                await interaction.response.send_modal(InputModal(
                    "Следующая ячейка", "Куда ведёт (например C4)",
                    lambda i, t: self.submit_target(i, sid, "link", t), placeholder="C4", max_length=10))

            elif act == "setstart":
                if cid == m.finish:
                    await self._err(interaction, "Финиш не может быть стартом.")
                    return
                m.start = cid
                m.cells[cid].effect = m.cells[cid].target = None
                await self._commit(interaction, sid, m, f"Старт: {cid}")

            elif act == "setfinish":
                if cid == m.start:
                    await self._err(interaction, "Старт не может быть финишем.")
                    return
                m.finish = cid
                c = m.cells[cid]
                c.effect = c.target = c.next = None
                await self._commit(interaction, sid, m, f"Финиш: {cid}")

            elif act == "clear":
                remove_cell(m, cid)
                tail = s["tail"] if s["tail"] != cid else None
                self.db.update_editor(sid, selected=None, panel="main", tail=tail)
                await self._commit(interaction, sid, m, f"Ячейка {cid} удалена")

    # ---------- Окна ----------

    async def submit_place(self, interaction: discord.Interaction, sid: int, text: str):
        async with self.locks[sid]:
            got = await self._enter(interaction, sid)
            if got is None:
                return
            s, m = got
            ids, error = parse_cells(text, m)
            if error:
                await self._err(interaction, error)
                return
            added, skipped, tail = place_cells(m, ids, s["tail"])
            if not added:
                await self._err(interaction, "Все эти ячейки уже стоят на карте.")
                return
            self.db.update_editor(sid, tail=tail)
            note = "Добавлено: " + ", ".join(added)
            if skipped:
                note += " · уже стояли: " + ", ".join(skipped)
            await self._commit(interaction, sid, m, note[:300])

    async def submit_select(self, interaction: discord.Interaction, sid: int, text: str):
        async with self.locks[sid]:
            got = await self._enter(interaction, sid)
            if got is None:
                return
            s, m = got
            ids, error = parse_cells(text, m)
            if error:
                await self._err(interaction, error)
                return
            if len(ids) != 1:
                await self._err(interaction, "Укажите одну ячейку, например B4.")
                return
            if ids[0] not in m.cells:
                await self._err(interaction, f"Ячейки {ids[0]} нет на карте. Сначала поставьте её.")
                return
            self.db.update_editor(sid, selected=ids[0], panel="cell")
            await interaction.response.defer()
            await self._redraw(interaction, self.db.get_editor(sid), m)

    async def submit_target(self, interaction: discord.Interaction, sid: int, kind: str, text: str):
        """kind: jump / back (эффект с целью) или link (следующая ячейка)."""
        async with self.locks[sid]:
            got = await self._enter(interaction, sid)
            if got is None:
                return
            s, m = got
            cid = s["selected"]
            if cid not in m.cells:
                await self._err(interaction, "Ячейка уже не выбрана или удалена.")
                return
            ids, error = parse_cells(text, m)
            if error:
                await self._err(interaction, error)
                return
            if len(ids) != 1:
                await self._err(interaction, "Укажите одну ячейку, например F4.")
                return
            t = ids[0]
            if t not in m.cells:
                await self._err(interaction, f"Ячейки {t} нет на карте.")
                return
            if t == cid:
                await self._err(interaction, "Цель не может совпадать с самой ячейкой.")
                return
            c = m.cells[cid]
            if kind == "link":
                if cid == m.finish:
                    await self._err(interaction, "У финиша нет следующей ячейки.")
                    return
                c.next = t
                note = f"{cid} → следующая {t}"
            else:
                if cid in (m.start, m.finish):
                    await self._err(interaction, "На старте и финише эффектов быть не может.")
                    return
                c.effect, c.target = kind, t
                note = f"{cid}: {EFFECT_ICONS[kind]} {EFFECT_TITLES[kind]} → {t}"
            await self._commit(interaction, sid, m, note)

    async def submit_rename(self, interaction: discord.Interaction, sid: int, text: str):
        async with self.locks[sid]:
            got = await self._enter(interaction, sid)
            if got is None:
                return
            s, m = got
            new, old = text.strip(), s["map_name"]
            if not new:
                await self._err(interaction, "Название не может быть пустым.")
                return
            if len(new) > MAP_NAME_MAX:
                await self._err(interaction, f"Название длиннее {MAP_NAME_MAX} символов.")
                return
            if new.casefold() != old.casefold() and self.db.get_draft(new):
                await self._err(interaction, "Черновик с таким названием уже есть.")
                return
            m.name = new
            self.db.rename_draft(old, new, m.to_json())
            self.db.update_editor(sid, map_name=new)
            await self._commit(interaction, sid, m, f"Новое название: {esc(new)}")

    # ---------- Консоль ----------

    async def console_edit(self, bot, args: list):
        """-jmap edit ID_канала [--gameid ID_канала] [название]"""
        usage = "Использование: -jmap edit ID_канала [--gameid ID_канала] [название карты]"
        if not args or not args[0].isdigit():
            print(usage, flush=True)
            return
        if not DEV_ID:
            print("⚠ В .env не задан DEV_ID: редактор некому доверить.", flush=True)
            return
        cid, rest, home_id = int(args[0]), list(args[1:]), None
        if "--gameid" in rest:
            i = rest.index("--gameid")
            if i + 1 >= len(rest) or not rest[i + 1].isdigit():
                print("⚠ После --gameid нужен ID канала.", flush=True)
                return
            home_id = int(rest[i + 1])
            del rest[i:i + 2]
        name = " ".join(rest).strip() or None

        channel = bot.get_channel(cid)
        if channel is None:
            try:
                channel = await bot.fetch_channel(cid)
            except discord.HTTPException:
                channel = None
        if channel is None or getattr(channel, "guild", None) is None or not hasattr(channel, "send"):
            print(f"⚠ Канал {cid} не найден, недоступен или в него нельзя писать.", flush=True)
            return
        if home_id and channel.id != home_id and getattr(channel, "parent_id", None) != home_id:
            print("⚠ Канал редактора должен быть самим каналом --gameid или его веткой.", flush=True)
            return
        perms = channel.permissions_for(channel.guild.me)
        if not (perms.send_messages and perms.attach_files):
            print("⚠ Боту нужны права «Отправлять сообщения» и «Прикреплять файлы» в этом канале.", flush=True)
            return
        try:
            sid = await self.open(channel, DEV_ID, home_id, name)
        except EditorError as e:
            print(f"⚠ {e}", flush=True)
            return
        except discord.HTTPException as e:
            print(f"⚠ Discord отклонил запуск (HTTP {e.status}): {e.text}", flush=True)
            return
        print(f"✔ Редактор открыт: сессия #{sid}, канал «{channel}».", flush=True)

    def console_sessions(self):
        rows = self.db.editors()
        for s in rows:
            print(f"  #{s['session_id']:<4} «{s['map_name']}»  канал {s['channel_id']}", flush=True)
        print(f"Открытых редакторов: {len(rows)}", flush=True)

    async def console_close(self, sid: int):
        s = self.db.get_editor(sid)
        if s is None:
            print("⚠ Такой сессии нет.", flush=True)
            return
        self.db.close_editor(sid)
        self.locks.pop(sid, None)
        if s["message_id"]:
            try:
                chan = self.bot.get_partial_messageable(s["channel_id"])
                await chan.get_partial_message(s["message_id"]).edit(content="🛠 Редактор закрыт.", view=None)
            except discord.HTTPException:
                pass
        print("✔ Сессия закрыта (черновик сохранён).", flush=True)