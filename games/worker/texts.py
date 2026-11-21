"""Тексты панели и событий симулятора рабочего. Про Discord-объекты ничего не знает
(кроме разметки времени <t:...>)."""
from datetime import datetime, timedelta

from . import logic as L
from . import rules as R

def bar(value: float) -> str:
    n = int(round(max(0.0, min(100.0, value)) / 10))
    return "▰" * n + "▱" * (10 - n)

def money(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " м."

def ts(dt: datetime, style: str = "R") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>"

def describe(ev) -> str:
    """Строка для списка «пока вас не было» или None, если событие показывать не нужно."""
    k, d = ev.kind, ev.data
    if k == "daily":
        text = f"🧾 Списано за сутки: {money(d['paid'])}"
        if d["short"]:
            text += f"; не хватило {money(d['short'])}, записано в долг"
        return text
    if k == "benefit":
        return f"🤝 Пособие: +{money(d['amount'])}"
    if k == "hospital":
        text = f"🏥 Вас увезли в больницу (здоровье упало слишком низко). Лечение: {money(d['cost'])}"
        if d["debt"]:
            text += f", не хватило {money(d['debt'])} (записано в долг)"
        return text + f"; выписка через {d['hours']} ч со здоровьем {d['health']:.0f}%"
    if k == "forced_sleep":
        return "🛌 Силы на исходе: вы идёте спать и не проснётесь, пока не восстановитесь"
    if k == "interrupted":
        why = "не хватило энергии" if d["why"] == "energy" else (
            "здоровье упало слишком низко" if d["why"] == "health" else "вы ушли сами")
        return f"⚠️ Смена прервана ({why}): оплата снижена"
    if k == "paid":
        text = f"💵 Зарплата: начислено {money(d['gross'])}, НДФЛ {money(d['tax'])}, на руки {money(d['net'])}"
        return text + ("; репутация %+.0f" % d["reputation"] if d["aborted"] else "")
    if k == "shift_void":
        return "⌛ Отработано меньше 15 минут: оплаты нет"
    if k == "finished":
        if d["activity"] == "sleep":
            return "⏰ Вы проснулись"
        if d["activity"] == "hospital":
            return f"🏥 Выписка из больницы: здоровье {d['health']:.0f}%"
    return None

def activity_text(p) -> str:
    act = p.act
    if p.frozen_at is not None:
        return "🧊 Игра заморожена"
    if act is None:
        return "🟢 Свободен"
    end = L.act_end(p)
    tail = f" (закончится {ts(end)})" if end else ""
    if act["kind"] == "sleep":
        if act.get("forced"):
            return "🛌 Принудительный сон: пока энергия не восстановится"
        return "😴 Спите" + (tail if end else ", пока не восстановитесь")
    if act["kind"] == "hospital":
        return "🏥 В больнице" + (f", выписка {ts(end)}" if end else "")
    return f"⚙️ {act.get('title', act['kind'])}{tail}"

def status_text(p, now: datetime, tz) -> str:
    h = R.HOUSING[p.housing]
    lines = [
        "📋 **Моё состояние**",
        f"Сейчас: {activity_text(p)}",
        "",
        f"⚡ Энергия: {bar(p.energy)} {p.energy:.0f}%",
        f"❤️ Здоровье: {bar(p.health)} {p.health:.0f}%",
        f"😊 Счастье: {bar(p.happiness)} {p.happiness:.0f}% (продуктивность ×{L.happy_mult(p.happiness):.2f})",
        f"⭐ Репутация: {p.reputation:.0f} из 100",
        "",
        f"💰 Деньги: {money(p.money)}",
    ]
    if p.debt:
        lines.append(f"📉 Долг: {money(p.debt)}")
        if p.debt_pressure:
            lines.append(f"📞 Вам названивают: давление {p.debt_pressure}/{R.DEBT_PRESSURE_MAX}. "
                         "Чем дольше долг не гасится, тем хуже репутация и настроение.")
    lines.append(f"🏠 {h['title']} · 🍽 {R.FOOD[p.food]['title']}: {money(L.daily_cost(p))} в сутки")
    if p.frozen_at is None:
        lines.append(f"Следующее списание: {ts(L.next_midnight(p.updated, tz))}")
    else:
        lines += ["", f"🧊 Заморожено с {ts(p.frozen_at, 'f')}. Пока игра заморожена, время для рабочего не идёт: "
                      "платежи, долги и смены стоят на месте.",
                  f"Разморозить можно {ts(p.frozen_at + timedelta(hours=R.FREEZE_MIN_HOURS), 'f')} и позже."]
        return "\n".join(lines)

    warns = []
    if p.health < R.HEALTH_CAREER_BLOCK:
        warns.append("🚫 Здоровье ниже 25%: карьера и казино заблокированы. Отдохните и поспите.")
    elif p.health < R.HEALTH_SLOW:
        warns.append("⚠️ Здоровье ниже 65%: энергия тратится вдвое быстрее и восстанавливается вдвое медленнее.")
    elif p.health < R.HEALTH_WARN:
        warns.append("⚠️ Здоровье падает: ниже 65% энергия будет уходить вдвое быстрее.")
    if p.energy < R.ENERGY_FORCED:
        warns.append("⚠️ Энергия на исходе: смены будут прерываться.")
    elif p.energy < R.SLEEP_AVAILABLE_BELOW and p.act is None:
        warns.append("😴 Энергия ниже 50%: можно лечь спать.")
    if warns:
        lines += [""] + warns
    return "\n".join(lines)

def render(screen: str, p, now: datetime, tz, events: list, note: str = "") -> str:
    """Полный текст сообщения панели для экрана screen."""
    if screen == "menu":
        text = ("💼 **Симулятор рабочего**\n"
                f"💰 {money(p.money)} · ⚡ {p.energy:.0f}% · ❤️ {p.health:.0f}% · {activity_text(p)}\n\n"
                "Выберите раздел. Игра идёт в реальном времени, даже когда вы не в сети.")
    elif screen == "career":
        text = "💼 **Карьера**\nРаздел в разработке: здесь будут работа и образование."
    elif screen == "leisure":
        text = "🎭 **Досуг**\nРаздел в разработке: здесь будут магазин и развлечения."
    elif screen == "sleep":
        text = ("😴 **Сон**\nВыберите, сколько спать. Проснуться раньше можно, "
                "кроме принудительного сна. Во сне быстрее всего восстанавливаются силы и здоровье.")
    elif screen == "freeze_confirm":
        text = ("🧊 **Заморозить игру?**\n"
                "Рабочий остановится во времени: платежи, проценты по долгу, смены и сон встанут на паузу. "
                "При разморозке время подгонится без последствий.\n\n"
                f"• Разморозить можно не раньше чем через {R.FREEZE_MIN_HOURS} ч.\n"
                f"• После разморозки повторная заморозка доступна через {R.FREEZE_COOLDOWN_DAYS} сут.\n"
                "• Во время смены, учёбы и лечения заморозка недоступна.")
    else:
        text = status_text(p, now, tz)
    lines = [text]
    shown = [t for t in (describe(e) for e in events) if t][-R.EVENTS_SHOWN:]
    if shown and screen in ("status", "menu"):
        lines += ["", "**Пока вас не было:**"] + shown
    if note:
        lines += ["", f"ℹ️ {note}"]
    return "\n".join(lines)[:1990]