"""Мини-игра «Осторожно, мины!»."""
import random

from .base import MiniGame

HISTORY_SHOWN = 12     # сколько последних бросков показывать в табло

class MinesGame(MiniGame):
    key = "mines"
    title = "Осторожно, мины!"
    pick_min, pick_max = 1, 5
    rules = ("Выберите кнопкой любое число от **1 до 5**.\n"
             "Стейси будет случайно выбирать числа: кто выбрал попавшееся число, тот выбывает. "
             "Одно и то же число может выпасть несколько раз. "
             "Побеждает тот, кто останется последним. "
             "Если все оставшиеся выбыли одновременно, победителей нет.")

    def _board(self, history: list, alive: set, last, hit: set) -> str:
        lines = ["💣 **Осторожно, мины!**"]
        if last is not None:
            lines.append(f"Взорвалось число **{last}**: " +
                         (("выбыли " + ", ".join(self.nm(u) for u in sorted(hit))) if hit else "никто не выбыл"))
        if history:
            tail = ", ".join(map(str, history[-HISTORY_SHOWN:]))
            lines.append("Броски: " + ("… " if len(history) > HISTORY_SHOWN else "") + tail)
        else:
            lines.append("Броски: пока нет")
        lines.append("Остались в игре: " + (", ".join(self.nm(u) for u in sorted(alive)) or "никого"))
        return "\n".join(lines)

    async def play(self) -> list:
        alive = set(self.picks)
        history: list = []
        board = await self.show(None, self._board(history, alive, None, set()))
        while True:
            await self.pause()
            n = random.randint(self.pick_min, self.pick_max)    # без «выбывания» клеток: шанс всегда одинаковый
            history.append(n)
            hit = {u for u in alive if self.picks[u] == n}
            alive -= hit
            board = await self.show(board, self._board(history, alive, n, hit))
            if not alive:
                await self.channel.send(f"💥 Число {n} накрыло всех оставшихся сразу: победителей нет.")
                return []
            if len(alive) <= 1:
                break
        return sorted(alive)