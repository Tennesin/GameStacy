"""Тексты событий партии. Общие для бота и для dev_tools (предпросмотр карты)."""
from .rules import BONUS_LABELS, TIMEOUTS_TO_LEAVE

_TEMPLATES = {
    "blocked": "⛔ {n}: до финиша {left}, а выпало {d} — остаётся на месте",
    "move": "➡️ {n} идёт на клетку {to}",
    "branch": "🟡 {n} встаёт на начало особого пути: следующий ход пойдёт по нему",
    "rejoin": "🟡 {n} сходит с особого пути",
    "jump": "🟢 {n} перепрыгивает на клетку {to}",
    "back": "🔴 {n} откатывается на клетку {to}",
    "reset": "⚫ {n} возвращается на старт",
    "skip_set": "🟠 {n} пропустит следующий ход",
    "skip_used": "⏭ {n} пропускает ход",
    "charge": "🔵 {n} бросает ещё раз",
    "charge_denied": "🔵 {n}: лимит дополнительных бросков исчерпан",
    "bonus_roll": "🎲 {n} бросает ещё раз (бонус)",
    "minigame": "🟣 Клетка мини-игры! Играют все",
    "reward": "🎁 Награда: {title}",
    "reward_none": "🎁 Победителей нет: награды не будет",
    "reward_move": "🎁 {n}: клетка {frm} → {to}",
    "bonus_set": "🎁 {n}: {bonus}",
    "finished": "🏁 {n} финиширует! Место: {place}",
    "timeout": "⏱ {n} не бросил кубик ({count} из {limit})",
    "joined": "➕ {n} присоединяется к игре",
}

def describe(ev, track, who: str, bold=str):
    """Строка для лога по событию движка или None (turn, game_over: отдельных строк нет).
    who — уже готовая подпись игрока; bold — как выделять числа (в Discord: lambda v: f"**{v}**")."""
    k, d = ev.kind, ev.data
    if k == "roll":
        extra = f" ×2 = {bold(d['total'])}" if d.get("mult", 1) > 1 else ""
        return f"🎲 {who}: выпало {bold(d['d'])}{extra}"
    if k == "left":
        return (f"⏱ {who} выбывает: слишком много пропущенных ходов" if d.get("reason") == "timeout"
                else f"🚪 {who} покидает игру")
    tpl = _TEMPLATES.get(k)
    if tpl is None:
        return None
    return tpl.format(
        n=who, d=bold(d.get("d", "")), left=d.get("left", ""),
        to=track.label(d["to"]) if "to" in d else "",
        frm=track.label(d["from"]) if "from" in d else "",
        title=d.get("title", ""), place=d.get("place", ""), count=d.get("count", ""),
        limit=TIMEOUTS_TO_LEAVE, bonus=BONUS_LABELS.get(d.get("bonus"), d.get("bonus", "")))