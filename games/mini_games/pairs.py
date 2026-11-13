"""Мини-игра «Выбери пару!»."""
from .base import MiniGame

PAIRS = ["1-2", "3-4", "5-6"]

class PairsGame(MiniGame):
    key = "pairs"
    title = "Выбери пару!"
    pick_min, pick_max = 1, 6
    GOAL = 5
    rules = ("Выберите пару кнопкой: **1-2**, **3-4** или **5-6**. Пару могут выбрать несколько игроков.\n"
             "Стейси бросает кубик: выпавшее число даёт 1 очко своей паре. "
             "Если выпало число пары, которую никто не выбрал, бросок пропускается. "
             "Побеждает пара, первой набравшая 5 очков.")

    @staticmethod
    def pair_of(n: int) -> int:
        return (n - 1) // 2

    def pick_label(self, pick: int) -> str:
        return f"пара {PAIRS[self.pair_of(pick)]}"

    def pick_buttons(self) -> list:
        return [(name, i * 2 + 1) for i, name in enumerate(PAIRS)]

    def _board(self, scores: list, last, skipped: bool = False) -> str:
        head = "🎲 **Выбери пару!**"
        if last:
            head += f" · выпало: **{last}**"
            if skipped:
                head += " (эту пару никто не выбрал: бросок пропущен)"
        lines = [head]
        for i, name in enumerate(PAIRS):
            who = ", ".join(self.nm(u) for u, p in self.picks.items() if self.pair_of(p) == i) or "никого"
            bar = "🟩" * scores[i] + "⬜" * (self.GOAL - scores[i])
            lines.append(f"`{name}` {bar} {scores[i]}/{self.GOAL} — {who}")
        return "\n".join(lines)

    async def play(self) -> list:
        scores = [0, 0, 0]
        chosen = {self.pair_of(p) for p in self.picks.values()}     # пары, у которых есть игроки
        board = await self.show(None, self._board(scores, None))
        while True:
            n = await self.roll(6)
            idx = self.pair_of(n)
            if idx not in chosen:                                   # пара без игроков: пропуск броска
                board = await self.show(board, self._board(scores, n, skipped=True))
                continue
            scores[idx] += 1
            board = await self.show(board, self._board(scores, n))
            if scores[idx] >= self.GOAL:
                break
        await self.channel.send(f"🎉 Первой набрала {self.GOAL} очков пара **{PAIRS[idx]}**!")
        return [u for u, p in self.picks.items() if self.pair_of(p) == idx]