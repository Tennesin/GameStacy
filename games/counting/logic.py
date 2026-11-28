"""Чистая логика «Счётчика чисел»: разбор сообщения, проверка хода, красивые числа.
Про Discord и БД ничего не знает."""
import re
from typing import Optional

from . import rules as R

# «58», «58 - текст», «58-текст», «58 – текст», «58 — текст». Число без ведущих нулей.
COUNT_RE = re.compile(
    r"^\s*([1-9]\d{0,%d})\s*(?:[-–—]\s*(.*?))?\s*$" % (R.COUNTING_MAX_DIGITS - 1), re.S)

def parse_count(text: str) -> Optional[tuple]:
    """(число, комментарий) или None, если сообщение не похоже на попытку счёта."""
    m = COUNT_RE.match(text or "")
    if not m:
        return None
    return int(m.group(1)), (m.group(2) or "").strip()

def check_move(last_number: int, last_user, uid: int, number: int) -> str:
    """'ok' - принято; 'same_user' - тот же человек подряд; 'wrong' - не то число."""
    if last_user is not None and uid == last_user:
        return "same_user"
    if number != last_number + 1:
        return "wrong"
    return "ok"

def fmt_num(n: int) -> str:
    return f"{n:,}".replace(",", " ")

def render(template: str, **values) -> str:
    """Подстановка меток {n}, {user}, {need}. Числа красиво делятся пробелами."""
    for key, value in values.items():
        text = fmt_num(value) if isinstance(value, int) else str(value)
        template = template.replace("{" + key + "}", text)
    return template

def pick_special(n: int) -> Optional[str]:
    """Шаблон особого сообщения для красивого числа или None. Приоритет: MY_LIST, окончания,
    круглые, одинаковые цифры."""
    if n in R.MY_LIST:
        return R.MY_LIST[n]
    s = str(n)
    for ending, text in R.SPECIAL_ENDINGS.items():
        if s.endswith(ending):
            return text
    for step, text in R.ROUND_STEPS:
        if n % step == 0:
            return text
    if len(s) >= R.COUNTING_REPDIGIT_MIN_LEN and len(set(s)) == 1:
        return R.REPDIGIT_TEXT
    return None