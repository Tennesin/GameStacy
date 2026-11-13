"""Чистая логика крестиков-ноликов. Про Discord и БД ничего не знает."""

from typing import Optional

EMPTY = " "
MARKS = {"X": "❌", "O": "⭕"}
LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8),
         (0, 3, 6), (1, 4, 7), (2, 5, 8),
         (0, 4, 8), (2, 4, 6))

def find_win(board: str) -> Optional[tuple]:
    """Выигрышная линия (три индекса) или None."""
    for a, b, c in LINES:
        if board[a] != EMPTY and board[a] == board[b] == board[c]:
            return (a, b, c)
    return None

def is_full(board: str) -> bool:
    return EMPTY not in board