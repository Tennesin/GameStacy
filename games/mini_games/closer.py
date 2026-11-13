"""Мини-игра «Будь ближе!»."""

from .base import MiniGame

class CloserGame(MiniGame):
    key = "closer"
    title = "Будь ближе!"
    pick_min, pick_max = 1, 12
    GOAL = 5
    rules = ("Загадайте любое число от **1 до 12** и отправьте его в чат.\n"
             "Стейси бросает кубик (1-12). Тот, чьё число совпало или оказалось ближе всех к выпавшему, "
             "получает 1 балл; если таких несколько, балл получают все. "
             "Побеждает тот, кто первым наберёт 5 баллов.")

    def _board(self, scores: dict, last, near: list) -> str:
        lines = ["🎯 **Будь ближе!**" + (f" · выпало: **{last}**" if last else "")]
        order = sorted(scores, key=lambda u: (-scores[u], self.names.get(u, "").casefold()))
        for u in order:
            mark = " ⬅️ +1" if u in near else ""
            lines.append(f"{self.tag(u)} (загадал {self.picks[u]}): **{scores[u]}/{self.GOAL}**{mark}")
        return "\n".join(lines)

    async def play(self) -> list:
        scores = {u: 0 for u in self.picks}
        board = await self.show(None, self._board(scores, None, []))
        while True:
            n = await self.roll(12)
            dist = {u: abs(p - n) for u, p in self.picks.items()}
            best = min(dist.values())
            near = [u for u, d in dist.items() if d == best]
            for u in near:
                scores[u] += 1
            board = await self.show(board, self._board(scores, n, near))
            done = [u for u in near if scores[u] >= self.GOAL]
            if done:
                return done