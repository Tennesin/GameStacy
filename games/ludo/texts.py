"""Тексты событий партии Ludo для лога под табло. Про Discord и БД ничего не знает.

Числа времени и лимиты берутся из rules.py. Номера фигур для игроков: piece + 1 (как на кнопках)."""
from typing import Callable, Optional

from .rules import LUDO_PICK_SECONDS, LUDO_TIMEOUTS_TO_LEAVE

def describe(ev, who: str, label: Callable = str, bold=str) -> Optional[str]:
    """Строка для лога по событию движка или None (turn, pick и обычный game_over отдельных строк не имеют).
    who - готовая подпись ходящего; label(pid) - подпись другого игрока (для взятия);
    bold - как выделять числа (в Discord: lambda v: f"**{v}**")."""
    k, d = ev.kind, ev.data
    n = d.get("piece", 0) + 1

    if k == "roll":
        text = f"🎲 {who}: выпало {bold(d['d'])}"
        return text + " — шестёрка!" if d["d"] == 6 else text
    if k == "three_sixes":
        return f"⛔ {who}: три шестёрки подряд, ход сгорает"
    if k == "no_moves":
        return f"🚫 {who}: ходов нет"
    if k == "auto":
        if d.get("why") == "timeout":
            return f"⏱ {who}: время на выбор ({LUDO_PICK_SECONDS} с) вышло, фигура выбрана автоматически"
        return None                                      # единственный ход: событие хода скажет всё
    if k == "exit":
        return f"🚪 {who}: фигура {n} выходит из базы"
    if k == "move":
        return f"➡️ {who}: фигура {n} идёт вперёд на {bold(d['to'] - d['from'])}"
    if k == "capture":
        return (f"💥 {who} берёт фигуру {d['piece'] + 1} игрока {label(d['victim'])}: "
                "она возвращается в базу")
    if k == "home_column":
        return f"🏠 {who}: фигура {n} заходит в домашнюю колонку"
    if k == "block":
        return f"🧱 {who}: фигуры встали вместе и образовали блок (его нельзя ни пройти, ни занять)"
    if k == "piece_home":
        return f"🏁 {who}: фигура {n} дошла до центра"
    if k == "finished":
        return f"🏆 {who} привёл все фигуры домой! Место: {bold(d['place'])}"
    if k == "bonus_roll":
        return f"🎲 {who} бросает ещё раз"
    if k == "timeout":
        return f"⏱ {who} не успел (пропусков подряд: {d['count']} из {LUDO_TIMEOUTS_TO_LEAVE})"
    if k == "left":
        if d.get("reason") == "timeout":
            return (f"⏱ {who} выбывает: слишком много пропущенных ходов "
                    f"({LUDO_TIMEOUTS_TO_LEAVE} подряд)")
        return f"🚪 {who} покидает игру"
    if k == "game_over" and d.get("reason") == "deadlock":
        return "⚠️ Ни у кого не осталось возможных ходов: партия завершена"
    return None