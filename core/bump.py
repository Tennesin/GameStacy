"""Решает, пора ли поднять сообщение игры вниз канала (если под ним накопилась болтовня)."""
import logging
import time
import discord

from config import BUMP_AFTER_MESSAGES, BUMP_ENABLED, BUMP_LOOKAHEAD

log = logging.getLogger("stacy.bump")

# Считаем только то, что люди реально пишут; системные сообщения Discord не в счёт
_COUNTED = (discord.MessageType.default, discord.MessageType.reply)

RECHECK_SECONDS = 4          # после ответа «не надо» то же сообщение не проверяем столько секунд
_warned: set = set()         # каналы, где уже писали в лог о нехватке прав
_checked: dict = {}          # (канал, сообщение) -> время последней проверки

async def should_bump(bot, channel_id: int, message_id: int) -> bool:
    """True, если под сообщением message_id набралось BUMP_AFTER_MESSAGES и больше чужих сообщений.
    Любая ошибка (нет прав, сеть) = False: тогда сообщение просто правится как обычно."""
    if not BUMP_ENABLED or not message_id:
        return False

    key, now = (channel_id, message_id), time.monotonic()
    if now - _checked.get(key, -RECHECK_SECONDS) < RECHECK_SECONDS:
        return False                                          # недавно уже смотрели: лишний запрос не нужен

    channel = bot.get_partial_messageable(channel_id)
    me = bot.user.id
    count = 0
    try:
        async for m in channel.history(limit=BUMP_LOOKAHEAD, after=discord.Object(id=message_id)):
            if m.author.id == me or m.type not in _COUNTED:
                continue
            count += 1
            if count >= BUMP_AFTER_MESSAGES:
                return True
    except discord.Forbidden:
        if channel_id not in _warned:
            _warned.add(channel_id)
            log.warning("Нет права «Читать историю сообщений» в канале %s: подъём табло там отключён",
                        channel_id)
    except discord.HTTPException:
        log.exception("Не удалось проверить сообщения под табло (канал %s)", channel_id)

    if len(_checked) > 200:
        _checked.clear()
    _checked[key] = now
    return False