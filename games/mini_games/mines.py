"""Мини-игра «Осторожно, мины!»."""

import random

from .base import MiniGame

class MinesGame(MiniGame):
    key = "mines"
    title = "Осторожно, мины!"
    pick_min, pick_max = 1, 5
    rules = ("Выберите любое число от **1 до 5** и отправьте его в чат.\n"
             "Стейси будет случайно выбирать числа: кто выбрал попавшееся число, тот выбывает. "
             "Побеждает тот, кто останется последним.")

    def _board(self, exploded: list, alive: set, last, hit: set) -> str:
        lines = ["💣 **Осторожно, мины!**"]
        if last is not None:
            lines.append(f"Взорвалось число **{last}**: " +
                         (("выбыли " + ", ".join(self.tag(u) for u in sorted(hit))) if hit else "никто не выбыл"))
        lines.append("Взорвались: " + (", ".join(map(str, exploded)) or "пока ничего"))
        lines.append("Остались в игре: " + (", ".join(self.tag(u) for u in sorted(alive)) or "никого"))
        return "\n".join(lines)

    async def play(self) -> list:
        alive = set(self.picks)
        pool = list(range(self.pick_min, self.pick_max + 1))
        exploded: list = []
        board = await self.show(None, self._board(exploded, alive, None, set()))
        while True:
            await self.pause()
            n = random.choice(pool)
            pool.remove(n)
            exploded.append(n)
            hit = {u for u in alive if self.picks[u] == n}
            if hit == alive and len(alive) >= 2:
                # Все оставшиеся выбыли бы одновременно: они продержались дольше всех, победа общая
                await self.show(board, self._board(exploded, alive, n, set()))
                await self.channel.send(f"💥 Число {n} накрыло всех оставшихся сразу: победа делится.")
                return sorted(alive)
            alive -= hit
            board = await self.show(board, self._board(exploded, alive, n, hit))
            if len(alive) <= 1:
                break
        return sorted(alive)