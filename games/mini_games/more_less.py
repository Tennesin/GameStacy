"""Мини-игра «Больше или Меньше?»."""
from .base import MiniGame

MORE, LESS = "more", "less"
WORDS = {"больше": MORE, ">": MORE, "меньше": LESS, "<": LESS}
THRESHOLD = 5               # граница прогноза
DIE_SIDES = 10              # кубик 1-10
ROUND_PICK_SECONDS = 15     # время на новый прогноз в следующих раундах
HISTORY_SHOWN = 12

class MoreLessGame(MiniGame):
    key = "highlow"
    title = "Больше или Меньше?"
    rules = ("Прогнозируйте, какое число выпадет на кубике (1-10): **больше** или **меньше** 5. "
             "Напишите в чат слово **больше** или **меньше** (можно также `>` или `<`).\n"
             "Стейси бросает кубик: кто ошибся, тот выбывает. Если выпало ровно 5, ничего не происходит, "
             "кубик бросается снова с теми же прогнозами. "
             "После каждого броска, который кого-то проверил, выжившие делают новый прогноз "
             "(15 секунд; кто не успел, выбывает).\n"
             "Побеждает тот, кто останется последним. Если выбыли все, победителей нет.")

    def parse_pick(self, text: str):
        return WORDS.get(text.strip().casefold())

    def pick_label(self, pick) -> str:
        return "больше 5" if pick == MORE else "меньше 5"

    def _board(self, history: list, alive: set, last, out: set, note: str = "") -> str:
        lines = ["🔮 **Больше или Меньше?**"]
        if last is not None:
            if last == THRESHOLD:
                lines.append(f"Выпало **{last}**: ничего не происходит")
            else:
                side = "больше" if last > THRESHOLD else "меньше"
                lost = ("выбыли " + ", ".join(self.nm(u) for u in sorted(out))) if out else "никто не выбыл"
                lines.append(f"Выпало **{last}** ({side} {THRESHOLD}): {lost}")
        if history:
            tail = ", ".join(map(str, history[-HISTORY_SHOWN:]))
            lines.append("Броски: " + ("… " if len(history) > HISTORY_SHOWN else "") + tail)
        else:
            lines.append("Броски: пока нет")
        lines.append("Остались в игре: " + (", ".join(self.nm(u) for u in sorted(alive)) or "никого"))
        if note:
            lines.append(note)
        return "\n".join(lines)

    async def _next_predictions(self, alive: set):
        """Новый приём прогнозов только от выживших (используется общий механизм приёма из base.py)."""
        self.players = set(alive)
        self.picks = {}
        self.pick_seconds = ROUND_PICK_SECONDS
        await self.collect_picks()

    async def play(self) -> list:
        alive = set(self.picks)
        history: list = []
        board = await self.show(None, self._board(history, alive, None, set()))
        while True:
            n = await self.roll(DIE_SIDES)
            history.append(n)
            out: set = set()
            if n != THRESHOLD:
                right = MORE if n > THRESHOLD else LESS
                out = {u for u in alive if self.picks.get(u) != right}
                alive -= out
            board = await self.show(board, self._board(history, alive, n, out))
            if len(alive) <= 1:
                break
            if n == THRESHOLD:
                continue                                    # ничего не произошло: прогнозы остаются

            await self.channel.send(f"🔮 Новый раунд! Выжившие, сделайте прогноз: **больше** или **меньше** "
                                    f"({ROUND_PICK_SECONDS} с).")
            await self._next_predictions(alive)
            silent = alive - self.picks.keys()               # не успели с прогнозом
            if silent:
                alive -= silent
                note = "⏱ Не успели с прогнозом и выбыли: " + ", ".join(self.nm(u) for u in sorted(silent))
                board = await self.show(board, self._board(history, alive, n, out, note))
                if len(alive) <= 1:
                    break
        return sorted(alive)