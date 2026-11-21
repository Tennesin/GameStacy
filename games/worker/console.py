"""Консольная команда -work: служебные операции симулятора рабочего (сейчас - сброс прогресса)."""
import logging

log = logging.getLogger("stacy.worker")

USAGE = ("Использование:\n"
         "  -work reset server ID_сервера                 сбросить прогресс ВСЕХ игроков сервера и рынок вакансий\n"
         "  -work reset user ID_сервера ID_пользователя   сбросить прогресс одного игрока\n"
         "Перед сбросом бот спросит подтверждение (yes/no) и сделает копию базы этого сервера.")

def console_work(ctrl):
    """Возвращает обработчик консольной команды -work для контроллера ctrl."""
    async def handler(bot, args: list):
        if len(args) < 3 or args[0].lower() != "reset" or not args[2].isdigit():
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

        guild = bot.get_guild(gid)
        title = f"«{guild.name}» ({gid})" if guild else f"{gid} (такого сервера нет среди серверов бота)"
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
    return handler