"""Консольная команда -work: служебные операции симулятора рабочего (сброс прогресса, выдача денег,
обновление рынка вакансий)."""
import logging

from config import TZ
from . import market as M

log = logging.getLogger("stacy.worker")

USAGE = ("Использование:\n"
         "  -work reset server ID_сервера                 сбросить прогресс ВСЕХ игроков сервера и рынок вакансий\n"
         "  -work reset user ID_сервера ID_пользователя   сбросить прогресс одного игрока\n"
         "  -work give ID_сервера ID_пользователя СУММА   выдать игроку деньги (целое число монет)\n"
         "  -work market ID_сервера [full]                обновить рынок вакансий; с full все свободные вакансии\n"
         "                                                удаляются и рынок создаётся заново\n"
         "Перед сбросом бот спросит подтверждение (yes/no) и сделает копию базы этого сервера.")

def _guild_title(bot, gid: int) -> str:
    guild = bot.get_guild(gid)
    return f"«{guild.name}» ({gid})" if guild else f"{gid} (такого сервера нет среди серверов бота)"

async def _reset(bot, ctrl, args: list):
    """args: ["reset", "server"|"user", ID_сервера, (ID_пользователя)]."""
    if len(args) < 3 or not args[2].isdigit():
        print(USAGE, flush=True)
        return
    scope, gid = args[1].lower(), int(args[2])
    uid = None
    if scope == "user" and len(args) == 4 and args[3].isdigit():
        uid = int(args[3])
    elif not (scope == "server" and len(args) == 3):
        print(USAGE, flush=True)
        return

    ask = getattr(bot, "console_ask", None)
    if ask is None:
        print("⚠ Консоль не умеет задавать вопросы: обновите core/console.py.", flush=True)
        return
    if not ctrl.registry.exists(gid):
        print(f"⚠ Для сервера {gid} в симуляторе рабочего нет данных: сбрасывать нечего.", flush=True)
        return

    title = _guild_title(bot, gid)
    store = ctrl.registry.get(gid)
    if uid is None:
        what = f"ВСЕХ игроков сервера {title} (человек: {store.count_players()}) и рынок вакансий"
    else:
        p = store.get_player(uid)
        if p is None:
            print(f"⚠ Игрока {uid} на сервере {title} нет: сбрасывать нечего.", flush=True)
            return
        what = (f"игрока {uid} на сервере {title}: деньги {p.money} м., долг {p.debt} м., "
                f"репутация {p.reputation:.0f}")

    print(f"ВНИМАНИЕ: будет безвозвратно удалён прогресс {what}.", flush=True)
    print("Перед удалением будет сделана резервная копия базы этого сервера.", flush=True)
    answer = await ask("Продолжить? (yes/no): ")
    if answer.lower() != "yes":
        print("Отменено: ничего не изменено.", flush=True)
        return

    stamp = bot.clock.local_now().strftime("%Y%m%d_%H%M%S")
    try:
        store = ctrl.registry.get(gid)       # заново: пока ждали ответ, подключение могло уйти из кэша
        backup = store.backup(ctrl.registry.base / "backups", stamp)
        if uid is None:
            removed = store.reset_all()
        else:
            async with ctrl.locks[(gid, uid)]:
                store = ctrl.registry.get(gid)
                removed = int(store.reset_player(uid))
    except Exception as e:
        log.exception("Сброс симулятора рабочего не выполнен")
        print(f"⚠ Сброс не выполнен: {type(e).__name__}: {e}", flush=True)
        return

    if uid is None:
        print(f"✔ Сервер {title} сброшен: удалено игроков {removed}, рынок вакансий очищен.", flush=True)
    else:
        print(f"✔ Игрок {uid} на сервере {title} сброшен.", flush=True)
    print(f"  Резервная копия: {backup}", flush=True)

async def _give(bot, ctrl, args: list):
    """args: ["give", ID_сервера, ID_пользователя, СУММА]."""
    if len(args) != 4 or not (args[1].isdigit() and args[2].isdigit() and args[3].isdigit()):
        print(USAGE, flush=True)
        return
    gid, uid, amount = int(args[1]), int(args[2]), int(args[3])
    if amount <= 0:
        print("⚠ Сумма должна быть положительным целым числом.", flush=True)
        return
    if not ctrl.registry.exists(gid):
        print(f"⚠ Для сервера {gid} в симуляторе рабочего нет данных.", flush=True)
        return

    title = _guild_title(bot, gid)
    try:
        async with ctrl.locks[(gid, uid)]:      # та же блокировка, что и у нажатий игрока
            store = ctrl.registry.get(gid)
            p = store.get_player(uid)
            if p is None:
                print(f"⚠ Игрока {uid} на сервере {title} нет: он ещё не открывал /work.", flush=True)
                return
            before = p.money
            p.money += amount
            store.save_player(p)
    except Exception as e:
        log.exception("Выдача денег в симуляторе рабочего не выполнена")
        print(f"⚠ Деньги не выданы: {type(e).__name__}: {e}", flush=True)
        return
    print(f"✔ Игроку {uid} на сервере {title} выдано {amount} м.: было {before}, стало {before + amount}.",
          flush=True)

async def _market(bot, ctrl, args: list):
    """args: ["market", ID_сервера, ("full")]."""
    if len(args) not in (2, 3) or not args[1].isdigit() or (len(args) == 3 and args[2].lower() != "full"):
        print(USAGE, flush=True)
        return
    gid, clear = int(args[1]), len(args) == 3
    if not ctrl.registry.exists(gid):
        print(f"⚠ Для сервера {gid} в симуляторе рабочего нет данных.", flush=True)
        return

    title = _guild_title(bot, gid)
    try:
        store = ctrl.registry.get(gid)          # дальше нет await: клики игроков не вклиниваются
        now = bot.clock.now()
        n_once, n_perm = M.force_refresh(store, now, TZ, ctrl.rng, clear=clear)
        once, perm = store.count_alive(int(now.timestamp()))
    except Exception as e:
        log.exception("Обновление рынка вакансий не выполнено")
        print(f"⚠ Рынок не обновлён: {type(e).__name__}: {e}", flush=True)
        return
    mode = "рынок создан заново" if clear else "рынок дополнен до цели"
    print(f"✔ Сервер {title}: {mode}. Добавлено: разовых {n_once}, постоянных {n_perm}. "
          f"Сейчас живых вакансий: {once + perm} (разовых {once}, постоянных {perm}).", flush=True)
    if not clear and not (n_once or n_perm):
        print("  Ничего не добавлено: живых вакансий уже не меньше цели. "
              "Чтобы заменить их все, используйте: -work market ID_сервера full", flush=True)

def console_work(ctrl):
    """Возвращает обработчик консольной команды -work для контроллера ctrl."""
    async def handler(bot, args: list):
        if not args:
            print(USAGE, flush=True)
            return
        sub = args[0].lower()
        if sub == "reset":
            await _reset(bot, ctrl, args)
        elif sub == "give":
            await _give(bot, ctrl, args)
        elif sub == "market":
            await _market(bot, ctrl, args)
        else:
            print(USAGE, flush=True)
    return handler