"""Блокировки по ключу (ID партии). Блокировка живёт, пока ею кто-то пользуется, и потом удаляется сама."""
import asyncio
import weakref

class LockMap:
    def __init__(self):
        self._locks = weakref.WeakValueDictionary()

    def __getitem__(self, key) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def pop(self, key, default=None):
        """Для совместимости со старым кодом: блокировки убираются сами."""
        return default