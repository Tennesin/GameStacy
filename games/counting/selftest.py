"""Самопроверка логики «Счётчика чисел». Запуск из корня проекта:  python -m games.counting.selftest"""
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import rules as R
from .logic import check_move, parse_count, pick_special, render
from .storage import Storage

class FakeClock:
    def now(self):
        return datetime.now(timezone.utc)

def test_parse():
    assert parse_count("58") == (58, "")
    assert parse_count("58 - Я покакал") == (58, "Я покакал")
    assert parse_count("58-текст") == (58, "текст")
    assert parse_count("58 – текст") == (58, "текст")
    assert parse_count("58 — текст") == (58, "текст")
    assert parse_count("  58 -  ") == (58, "")
    assert parse_count("58 - строка1\nстрока2") == (58, "строка1\nстрока2")
    for bad in ("", "привет", "058", "0", "58 текст", "100 человек", "5 8", "-5", "1" * 16, "58."):
        assert parse_count(bad) is None, bad
    assert parse_count("9" * 15) == (10 ** 15 - 1, "")
    print("разбор сообщения: OK")

def test_check():
    assert check_move(0, None, 1, 1) == "ok"
    assert check_move(5, 1, 2, 6) == "ok"
    assert check_move(5, 1, 1, 6) == "same_user"
    assert check_move(5, 1, 1, 9) == "same_user"       # тот же человек важнее неверного числа
    assert check_move(5, 1, 2, 7) == "wrong"
    assert check_move(5, 1, 2, 5) == "wrong"
    print("проверка хода: OK")

def test_special():
    assert pick_special(1337) == R.MY_LIST[1337]
    assert pick_special(666) == R.SPECIAL_ENDINGS["666"]
    assert pick_special(1666) == R.SPECIAL_ENDINGS["666"]
    assert pick_special(7777) == R.SPECIAL_ENDINGS["777"]
    assert pick_special(1000) == R.ROUND_STEPS[1][1]          # не «одинаковые цифры»
    assert pick_special(500) == R.ROUND_STEPS[2][1]
    assert pick_special(300) == R.ROUND_STEPS[3][1]
    assert pick_special(10000) == R.ROUND_STEPS[0][1]
    assert pick_special(111) == R.REPDIGIT_TEXT and pick_special(2222) == R.REPDIGIT_TEXT
    assert pick_special(11) is None and pick_special(58) is None and pick_special(99) is None
    assert render("{user}: {n}", n=1234567, user="<@1>") == "<@1>: 1 234 567"
    print("красивые числа: OK")

def test_storage():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        st = Storage(FakeClock(), Path(tmp) / "c.db")
        try:
            assert st.get_guild(1) is None
            st.set_channel(1, 100, 555)
            g = st.get_guild(1)
            assert g["channel_id"] == 100 and g["last_number"] == 0 and g["anchor_id"] == 555
            st.record(1, 1, 10, 1001, "A")
            st.record(1, 2, 20, 1002, "B")
            st.record(1, 3, 10, 1003, "A2")
            g = st.get_guild(1)
            assert g["last_number"] == 3 and g["last_user_id"] == 10 and g["last_message_id"] == 1003
            top = st.top(1, 10)
            assert [(r["uid"], r["count"], r["name"]) for r in top] == [(10, 2, "A2"), (20, 1, "B")]
            assert st.rank_of(1, 10) == (1, 2) and st.rank_of(1, 20) == (2, 1) and st.rank_of(1, 99) is None
            assert st.total(1) == 3
            st.clear_channel(1)                                        # прогресс сохраняется
            g = st.get_guild(1)
            assert g["channel_id"] is None and g["last_number"] == 3 and st.active() == []
            st.set_channel(1, 200, 777)
            assert st.get_guild(1)["last_number"] == 3 and st.total(1) == 3
            st.set_number(1, 50)
            g = st.get_guild(1)
            assert g["last_number"] == 50 and g["last_user_id"] is None and g["channel_id"] == 200
            st.set_channel(2, 200, None)                               # один канал - один счётчик
            raise AssertionError("уникальный индекс не сработал")
        except Exception as e:
            assert "UNIQUE" in str(e).upper(), e
        finally:
            st.con.close()
    print("хранилище: OK")

def main():
    test_parse()
    test_check()
    test_special()
    test_storage()
    print("Все проверки пройдены.")

if __name__ == "__main__":
    main()