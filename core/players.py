"""Общее для всех игр: «ID» ботов и манекена, безопасный вывод ников."""
import discord

BOT_IDS = (-101, -102, -103, -104)     # у настоящих людей ID положительные
DUMMY_ID = -1                          # манекен «Тяни верёвку»

def is_bot_id(pid: int) -> bool:
    return pid in BOT_IDS

def esc(text) -> str:
    """Ник простым текстом: без тегов и разметки Markdown."""
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(text)))