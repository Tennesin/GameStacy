"""Консольная команда -jmap: список, импорт и удаление готовых карт Джуманджи."""
import asyncio
from pathlib import Path

from .models import GameMap, MapFormatError
from .validator import validate_map

JMAP_USAGE = ("Использование:\n"
              "  -jmap list                   список готовых карт\n"
              "  -jmap import ПУТЬ_К_JSON     проверить карту и добавить в список для игроков\n"
              "                               (путь в кавычках или с прямыми слэшами)\n"
              "  -jmap delete ID_карты        удалить готовую карту из списка")

def console_jmap(ctrl):
    """Возвращает обработчик консольной команды -jmap для контроллера ctrl."""
    async def handler(bot, args: list):
        if not args:
            print(JMAP_USAGE, flush=True)
            return
        sub = args[0].lower()
        if sub == "list":
            rows = ctrl.db.list_maps()
            for r in rows:
                print(f"  {r['map_id']:<4} {r['name']}", flush=True)
            print(f"Карт: {len(rows)}", flush=True)
        elif sub == "import" and len(args) >= 2:
            path = Path(" ".join(args[1:]).strip('"'))
            try:
                gmap = GameMap.from_json(path.read_text(encoding="utf-8"))
            except (OSError, MapFormatError) as e:
                print(f"⚠ Не удалось прочитать карту: {e}", flush=True)
                return
            rep = await asyncio.to_thread(validate_map, gmap)
            print(rep.text(), flush=True)
            if rep.ok:
                map_id = ctrl.db.export_map(gmap.name, gmap.to_json())
                print(f"✔ Карта «{gmap.name}» добавлена (ID {map_id}).", flush=True)
        elif sub == "delete" and len(args) >= 2 and args[1].isdigit():
            print("✔ Удалена." if ctrl.db.delete_map(int(args[1])) else "⚠ Такой карты нет.", flush=True)
        else:
            print(JMAP_USAGE, flush=True)
    return handler