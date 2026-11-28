"""Контроллер «Счётчика чисел»: обработка сообщений канала, консоль -counting, /countstats,
догон после перезапуска, реакция на правку и удаление последнего числа."""
import asyncio
import logging
import time

import discord
from discord import app_commands

from core.locks import LockMap
from core.players import esc
from . import rules as R
from .logic import check_move, fmt_num, parse_count, pick_special, render
from .storage import Storage

log = logging.getLogger("stacy.counting")

NO_PINGS = discord.AllowedMentions.none()
COUNTED_TYPES = (discord.MessageType.default, discord.MessageType.reply)
MEDALS = ["🥇", "🥈", "🥉"]

USAGE = ("Использование:\n"
         "  -counting --setchat ID_канала        назначить канал счётчиком (один на сервер)\n"
         "  -counting --chatremove ID_канала     снять счётчик (прогресс сервера сохраняется)\n"
         "  -counting --status ID_сервера        показать состояние счётчика\n"
         "  -counting --setnumber ID_сервера N   вручную поставить последнее число (N >= 0)")

class CountingController:
    def __init__(self, bot, storage: Storage):
        self.bot = bot
        self.db = storage
        self.locks = LockMap()                  # по блокировке на сервер: быстрые числа не обгоняют друг друга
        self.channels: dict = {}                # channel_id -> guild_id (чтобы не ходить в БД на каждое сообщение)
        self.notice_at: dict = {}               # (guild_id, uid) -> когда в последний раз писали уведомление
        self.warned: set = set()                # каналы, где уже писали в лог о нехватке прав
        self.reload_channels()

    def reload_channels(self):
        self.channels = {r["channel_id"]: r["guild_id"] for r in self.db.active()}

    # ==================== Сообщения ====================

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or message.type not in COUNTED_TYPES:
            return
        gid = self.channels.get(message.channel.id)
        if gid is None:
            return
        await self._handle(gid, message, catchup=False)

    async def _handle(self, gid: int, message: discord.Message, catchup: bool):
        """catchup=True: догон после перезапуска (без уведомлений и особых сообщений)."""
        parsed = parse_count(message.content or "")
        if parsed is None:
            return                                         # обычная болтовня: не трогаем
        number, _comment = parsed
        uid = message.author.id

        async with self.locks[gid]:
            g = self.db.get_guild(gid)
            if g is None or g["channel_id"] != message.channel.id:
                return
            last_id = g["last_message_id"]
            if catchup and last_id and message.id <= last_id:
                return                                     # это сообщение уже учтено живым обработчиком
            verdict = check_move(g["last_number"], g["last_user_id"], uid, number)
            need = g["last_number"] + 1
            if verdict == "ok":
                self.db.record(gid, number, uid, message.id, message.author.display_name)

        # Обращения к Discord - уже вне блокировки, чтобы не тормозить счёт
        if verdict == "ok":
            template = pick_special(number)
            if template and not catchup:
                await self._reply(message, render(template, n=number, user=f"<@{uid}>"))
            return
        await self._reject(message, verdict, need, quiet=catchup)

    async def _reply(self, message: discord.Message, text: str):
        try:
            await message.reply(text, mention_author=False, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            try:                                           # сообщение могли удалить: пишем в канал
                await message.channel.send(text, allowed_mentions=NO_PINGS)
            except discord.HTTPException:
                log.exception("Не удалось отправить особое сообщение (канал %s)", message.channel.id)

    async def _reject(self, message: discord.Message, verdict: str, need: int, quiet: bool):
        try:
            await message.delete()
        except discord.NotFound:
            return                                         # уже удалено
        except discord.Forbidden:
            if message.channel.id not in self.warned:
                self.warned.add(message.channel.id)
                log.warning("Нет права «Управление сообщениями» в канале счётчика %s: "
                            "неверные числа не удаляются", message.channel.id)
            return
        except discord.HTTPException:
            log.exception("Не удалось удалить сообщение в канале счётчика %s", message.channel.id)
            return
        if quiet:
            return

        key, now = (message.guild.id, message.author.id), time.monotonic()
        if now - self.notice_at.get(key, -1e9) < R.COUNTING_NOTICE_COOLDOWN:
            return                                         # спамит: удаляем молча
        if len(self.notice_at) > 500:
            self.notice_at.clear()
        self.notice_at[key] = now

        template = R.TEXT_SAME_USER if verdict == "same_user" else R.TEXT_WRONG
        text = render(template, user=f"<@{message.author.id}>", need=need)
        try:
            await message.channel.send(text, delete_after=R.COUNTING_NOTICE_SECONDS,
                                       allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            log.exception("Не удалось отправить уведомление в канал счётчика %s", message.channel.id)

    async def _announce(self, channel_id: int, text: str, seconds=None):
        try:
            await self.bot.get_partial_messageable(channel_id).send(
                text, delete_after=seconds, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            log.exception("Не удалось написать в канал счётчика %s", channel_id)

    # ==================== Правка и удаление последнего числа ====================

    def _last_row(self, channel_id: int, message_id: int):
        """Строка сервера, если это сообщение - последнее принятое число в канале счётчика."""
        gid = self.channels.get(channel_id)
        if gid is None:
            return None
        g = self.db.get_guild(gid)
        if g is None or g["channel_id"] != channel_id or g["last_message_id"] != message_id:
            return None
        return g

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        g = self._last_row(payload.channel_id, payload.message_id)
        if g is None:
            return
        n = g["last_number"]
        await self._announce(
            payload.channel_id,
            f"🗑 Последнее число было удалено. Счёт не изменился: последнее число - **{fmt_num(n)}**, "
            f"следующее - **{fmt_num(n + 1)}**.", R.COUNTING_EDIT_NOTICE_SECONDS)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        content = payload.data.get("content")
        if content is None or (payload.data.get("author") or {}).get("bot"):
            return                                         # правка без текста (например, подгрузка ссылки)
        g = self._last_row(payload.channel_id, payload.message_id)
        if g is None:
            return
        parsed = parse_count(content)
        if parsed is not None and parsed[0] == g["last_number"]:
            return                                         # поменяли только комментарий: число то же
        n = g["last_number"]
        await self._announce(
            payload.channel_id,
            f"✏️ Число в последнем сообщении изменили. Для счёта действует прежнее: **{fmt_num(n)}**. "
            f"Следующее - **{fmt_num(n + 1)}**.", R.COUNTING_EDIT_NOTICE_SECONDS)

    # ==================== Догон после перезапуска ====================

    async def catch_up(self):
        """Пока бот был выключен, в канале могли появиться числа. Проходим их той же логикой."""
        await self.bot.wait_until_ready()
        for row in self.db.active():
            try:
                await self._catch_up_one(row)
            except Exception:
                log.exception("Сбой догона счётчика (сервер %s)", row["guild_id"])

    async def _catch_up_one(self, row):
        after = max(row["last_message_id"] or 0, row["anchor_id"] or 0)
        if not after:
            return
        channel = await self._get_channel(row["channel_id"])
        if channel is None:
            log.warning("Канал счётчика %s не найден: догон пропущен", row["channel_id"])
            return
        seen = 0
        async for m in channel.history(limit=R.COUNTING_CATCHUP_LIMIT,
                                       after=discord.Object(id=after), oldest_first=True):
            seen += 1
            if m.author.bot or m.type not in COUNTED_TYPES:
                continue
            await self._handle(row["guild_id"], m, catchup=True)
        if seen >= R.COUNTING_CATCHUP_LIMIT:
            log.warning("Догон счётчика (сервер %s) упёрся в лимит %d сообщений",
                        row["guild_id"], R.COUNTING_CATCHUP_LIMIT)

    async def _get_channel(self, cid: int):
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except discord.HTTPException:
                return None
        return ch

    # ==================== Консоль ====================

    async def set_chat(self, cid: int):
        ch = await self._get_channel(cid)
        if not isinstance(ch, discord.TextChannel):
            print("⚠ Канал не найден или это не текстовый канал сервера (ветки и голосовые не подходят).",
                  flush=True)
            return
        gid = ch.guild.id
        perms = ch.permissions_for(ch.guild.me)
        missing = [name for name, ok in (
            ("Просмотр канала", perms.view_channel), ("Отправка сообщений", perms.send_messages),
            ("Управление сообщениями", perms.manage_messages),
            ("Читать историю сообщений", perms.read_message_history)) if not ok]
        if missing:
            print(f"⚠ У бота в #{ch.name} нет прав: {', '.join(missing)}.", flush=True)
            return

        async with self.locks[gid]:
            g = self.db.get_guild(gid)
            if g is not None and g["channel_id"]:
                if g["channel_id"] == cid:
                    print("Этот канал уже назначен счётчиком.", flush=True)
                else:
                    print(f"⚠ На сервере «{ch.guild.name}» счётчик уже стоит в канале {g['channel_id']}. "
                          f"Сначала снимите его: -counting --chatremove {g['channel_id']}", flush=True)
                return
            n = g["last_number"] if g is not None else 0
            if n:
                who = f" Последнее число указал(а) <@{g['last_user_id']}>." if g["last_user_id"] else ""
                text = (f"🔢 **Счётчик снова работает!** Последнее число: **{fmt_num(n)}**.{who}\n"
                        f"Следующее число: **{fmt_num(n + 1)}**.")
            else:
                text = "🔢 **Счётчик запущен!** Начинаем с **1**."
            text += ("\nПишите число, можно с комментарием: `58 - ваш текст`. "
                     "Два числа подряд от одного человека не принимаются.")
            try:
                msg = await ch.send(text, allowed_mentions=NO_PINGS)
            except discord.HTTPException as e:
                print(f"⚠ Не удалось написать в канал (HTTP {e.status}): {e.text}", flush=True)
                return
            self.db.set_channel(gid, cid, msg.id)
            self.channels[cid] = gid
        print(f"✔ Счётчик назначен: «{ch.guild.name}» -> #{ch.name}. Следующее число: {n + 1}.", flush=True)

    async def remove_chat(self, cid: int):
        gid = self.channels.get(cid)
        if gid is None:
            print("⚠ Этот канал не является счётчиком.", flush=True)
            return
        async with self.locks[gid]:
            g = self.db.get_guild(gid)
            self.db.clear_channel(gid)
            self.channels.pop(cid, None)
        n = g["last_number"] if g else 0
        await self._announce(cid, f"🔢 Счётчик отключён. Прогресс сохранён: последнее число - **{fmt_num(n)}**.")
        print(f"✔ Счётчик снят с канала {cid}. Прогресс сервера сохранён (последнее число: {n}).", flush=True)

    async def status(self, gid: int):
        g = self.db.get_guild(gid)
        if g is None:
            print("На этом сервере счётчика ещё не было.", flush=True)
            return
        guild = self.bot.get_guild(gid)
        print(f"Сервер: {guild.name if guild else gid}", flush=True)
        print(f"  Канал: {g['channel_id'] or 'не назначен'}", flush=True)
        print(f"  Последнее число: {g['last_number']} (автор: {g['last_user_id'] or '-'})", flush=True)
        print(f"  Принято чисел всего: {self.db.total(gid)}", flush=True)

    async def set_number(self, gid: int, n: int):
        async with self.locks[gid]:
            self.db.set_number(gid, n)
            g = self.db.get_guild(gid)
        if g["channel_id"]:
            await self._announce(g["channel_id"],
                                 f"🛠 Счёт изменён вручную. Последнее число: **{fmt_num(n)}**, "
                                 f"следующее: **{fmt_num(n + 1)}**.")
        print(f"✔ Сервер {gid}: последнее число = {n}.", flush=True)

    # ==================== /countstats ====================

    async def show_stats(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        g = self.db.get_guild(gid)
        top = self.db.top(gid, R.COUNTING_TOP)
        if g is None or (not top and not g["last_number"]):
            await interaction.response.send_message("На этом сервере счёт ещё не начинался.")
            return

        place = f"<#{g['channel_id']}>" if g["channel_id"] else "сейчас не назначен"
        lines = ["🔢 **Счётчик чисел**",
                 f"Последнее число: **{fmt_num(g['last_number'])}** · следующее: "
                 f"**{fmt_num(g['last_number'] + 1)}**",
                 f"Канал: {place} · принято чисел всего: **{fmt_num(self.db.total(gid))}**", ""]
        if top:
            lines.append("**Кто сколько раз указал число:**")
            for i, r in enumerate(top):
                medal = MEDALS[i] if i < len(MEDALS) else f"{i + 1}."
                lines.append(f"{medal} {esc(r['name'])[:40]} - {fmt_num(r['count'])}")
            if interaction.user.id not in {r["uid"] for r in top}:
                mine = self.db.rank_of(gid, interaction.user.id)
                if mine:
                    lines += ["", f"Ваше место: **{mine[0]}**, чисел: {fmt_num(mine[1])}"]
        else:
            lines.append("Статистика пока пуста.")
        await interaction.response.send_message("\n".join(lines)[:1990], allowed_mentions=NO_PINGS)

# ==================== Консольная команда ====================

def console_counting(ctrl: CountingController):
    async def handler(bot, args: list):
        if not args:
            print(USAGE, flush=True)
            return
        sub = args[0].lower()
        if sub == "--setchat" and len(args) == 2 and args[1].isdigit():
            await ctrl.set_chat(int(args[1]))
        elif sub == "--chatremove" and len(args) == 2 and args[1].isdigit():
            await ctrl.remove_chat(int(args[1]))
        elif sub == "--status" and len(args) == 2 and args[1].isdigit():
            await ctrl.status(int(args[1]))
        elif sub == "--setnumber" and len(args) == 3 and args[1].isdigit() and args[2].isdigit():
            await ctrl.set_number(int(args[1]), int(args[2]))
        else:
            print(USAGE, flush=True)
    return handler

# ==================== Точка входа ====================

def setup(bot):
    """Точка входа игры: вызывается из main.py."""
    ctrl = CountingController(bot, Storage(bot.clock))
    bot.message_handlers.append(ctrl.on_message)
    bot.raw_delete_handlers.append(ctrl.on_raw_message_delete)
    bot.raw_edit_handlers.append(ctrl.on_raw_message_edit)
    bot.console_commands["-counting"] = console_counting(ctrl)
    ctrl.catch_task = asyncio.create_task(ctrl.catch_up())

    @app_commands.command(name="countstats", description="Счётчик чисел: статистика сервера")
    @app_commands.guild_only()
    async def countstats(interaction: discord.Interaction):
        await ctrl.show_stats(interaction)

    bot.tree.add_command(countstats)