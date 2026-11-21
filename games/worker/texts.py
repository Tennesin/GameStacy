"""Тексты панели и событий симулятора рабочего."""
from datetime import datetime, timedelta

from . import logic as L
from . import market as M
from . import rules as R

PERK_TEXT = {1: "+2,5% к зарплате", 2: "+1,5% к сопротивлению негативным эффектам",
             0: "+2% к сопротивлению расходу энергии"}
SHOW_EVENTS_ON = (
    "status", "menu", "career", "jobs", "leisure", "market",
    "venue", "ent", "hospital", "housing", "myjob", "edu"
)
WD_NAMES = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

def bar(value: float) -> str:
    n = int(round(max(0.0, min(100.0, value)) / 10))
    return "▰" * n + "▱" * (10 - n)

def money(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " м."

def ts(dt: datetime, style: str = "R") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>"

def dur(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} ч {m} мин"
    return f"{h} ч" if h else f"{m} мин"

def title_of(prof) -> str:
    return R.PROFESSIONS.get(prof, {}).get("title", "работа")

def buff_desc(kind: str, power: float) -> str:
    pct = f"{power * 100:.0f}%"
    return {"stamina": f"−{pct} к расходу энергии",
            "learning": f"+{pct} к скорости опыта и обучения",
            "recovery": f"+{pct} к восстановлению энергии и здоровья"}[kind]

def effect_text(item: dict) -> str:
    parts = []
    for key, name in (("energy", "энергии"), ("health", "здоровья"), ("happy", "счастья")):
        v = item.get(key)
        if v:
            parts.append(f"{v:+g} {name}")
    return ", ".join(parts) or "—"

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
        if d.get("reason") == "soft":
            tail = " Прогресс сохранён." if d.get("activity") == "study" else ""
            if d.get("why") == "money":
                return "⚠️ Учёба прервана: не хватает денег на оплату." + tail
            if d.get("why") == "health":
                return "⚠️ Учёба прервана: здоровье слишком низкое." + tail
            return "⚠️ Занятие прервано: энергия на исходе." + tail
        why = "не хватило энергии" if d["why"] == "energy" else (
            "здоровье упало слишком низко" if d["why"] == "health" else "вы ушли сами")
        return f"⚠️ Смена прервана ({why}): оплата снижена"
    if k == "paid":
        who = f" ({title_of(d['prof'])})" if d.get("prof") else ""
        text = (f"💵 Зарплата{who}: начислено {money(d['gross'])}, НДФЛ {money(d['tax'])}, "
                f"на руки {money(d['net'])}")
        return text + ("; репутация %+.0f" % d["reputation"] if d["aborted"] else "")
    if k == "level_up":
        return (f"🆙 Опыт «{title_of(d['prof'])}»: уровень {d['level']}! "
                f"{PERK_TEXT[d['level'] % 3]}")
    if k == "shift_void":
        return "⌛ Отработано меньше 15 минут: оплаты нет"
    if k == "buff_end":
        c = R.COURSES.get(d["code"])
        return f"⏳ Эффект «{c['title'] if c else d['buff']}» закончился"
    if k == "finished":
        if d["activity"] == "sleep":
            return "⏰ Вы проснулись"
        if d["activity"] == "hospital":
            return f"🏥 Выписка из больницы: здоровье {d['health']:.0f}%"
        if d["activity"] == "work":
            return "✅ Смена закончена"
        if d["activity"] == "errand":
            if d.get("venue"):
                return f"🛒 Вы пришли: {R.VENUES[d['venue']]['title']}"
            return "🏠 Вы вернулись домой"
        if d["activity"] == "leisure":
            return "🎭 Занятие закончилось"
        if d["activity"] == "study":
            return "📚 Занятие закончено"
        if d["activity"] == "course":
            c = R.COURSES[d["course"]]
            return (f"🎓 Курс «{c['title']}» закончен: {buff_desc(c['kind'], c['power'])} "
                    f"на {c['hours']} ч")
    if k == "absence":
        return (f"🚫 Прогул на постоянной работе ({d['absences']} из {d['limit']}): "
                "репутация снижена, недельная премия сгорела")
    if k == "fired":
        return (f"📤 Вас уволили за прогулы («{title_of(d['prof'])}»). "
                f"Новую постоянную работу можно взять через {R.JOB_HIRE_COOLDOWN_HOURS} ч")
    if k == "week_bonus":
        return (f"🎁 Недельная премия: начислено {money(d['gross'])}, НДФЛ {money(d['tax'])}, "
                f"на руки {money(d['net'])}")
    if k == "edu_done":
        e = R.EDUCATION.get(d["edu"])
        return f"🎓 Получено: {e['title'] if e else d['edu']}! Теперь вам доступны новые вакансии"
    return None

def activity_text(p) -> str:
    act = p.act
    if p.frozen_at is not None:
        return "🧊 Игра заморожена"
    if act is None:
        if p.venue:
            return f"🛒 В магазине: {R.VENUES[p.venue]['title']}"
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

def buffs_text(p) -> str:
    if not p.buffs:
        return ""
    parts = []
    for kind, b in p.buffs.items():
        parts.append(f"{buff_desc(kind, b['power'])} (ещё {dur(b['left'] * R.QUANTUM_MINUTES)})")
    return "✨ **Эффекты:** " + "; ".join(parts)

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
    lines.append(f"🏠 {h['title']} (счастье тянется к {h['base']:.0f}%) · "
                 f"🍽 {R.FOOD[p.food]['title']}: {money(L.daily_cost(p))} в сутки")
    if p.job:
        lines.append(f"💼 Постоянная работа: {title_of(p.job['prof'])} "
                     f"(прогулов {p.job['absences']} из {R.JOB_FIRE_ABSENCES})")
    exps = [f"{title_of(k)} — ур. {L.exp_level(v)}" for k, v in p.exp.items() if L.exp_level(v) > 0]
    if exps:
        lines += ["", "🛠 **Опыт:** " + ", ".join(exps)]
    if p.education:
        lines.append("🎓 **Образование:** " + ", ".join(
            R.EDUCATION[c]["title"] for c in p.education if c in R.EDUCATION))
    bt = buffs_text(p)
    if bt:
        lines.append(bt)
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
    elif p.energy < R.SLEEP_AVAILABLE_BELOW and p.act is None and not p.venue:
        warns.append("😴 Энергия ниже 50%: можно лечь спать.")
    if p.venue:
        warns.append("🛒 Вы в магазине: пока вы здесь, спать и работать нельзя. Нажмите «В магазин» и затем «Уйти».")
    if warns:
        lines += [""] + warns
    return "\n".join(lines)

# ---------- Досуг ----------

def leisure_text(p) -> str:
    return ("🎭 **Досуг**\n"
            f"💰 {money(p.money)} · ⚡ {p.energy:.0f}% · 😊 {p.happiness:.0f}%\n\n"
            "🛒 **Рынок**: еда и лекарства (на каждую площадку нужно сходить)\n"
            "🎭 **Развлечения**: поднимают счастье, чем дороже, тем лучше\n"
            "🏥 **Больница**: добровольные курсы с временными эффектами\n"
            "🏠 **Жильё**: чем дороже, тем выше «планка» счастья")

def market_text(p) -> str:
    lines = ["🛒 **Рынок**", "Выберите, куда пойти. Цены вы увидите на месте. "
             f"Дорога туда и обратно занимает около {2 * R.ERRAND_QUANTA * R.QUANTUM_MINUTES} минут "
             "и немного тратит энергию.", ""]
    for code, v in R.VENUES.items():
        names = [i["title"] for _c, i in L.goods_of(code)]
        extra = f" (дороже на {round((v['markup'] - 1) * 100)}%)" if v["markup"] > 1 else ""
        lines.append(f"{v['emoji']} **{v['title']}**{extra}: " + ", ".join(names))
    return "\n".join(lines)

def venue_text(p) -> str:
    v = R.VENUES[p.venue]
    lines = [f"{v['emoji']} **{v['title']}**", f"💰 У вас {money(p.money)}.", ""]
    if v["markup"] > 1:
        lines += [f"Здесь всё дороже на {round((v['markup'] - 1) * 100)}%, зато есть и еда, и лекарства.", ""]
    for code, item in L.goods_of(p.venue):
        left = L.good_limit(code) - p.day_buys.get(code, 0)
        lines.append(f"• **{item['title']}** — {money(L.good_price(p.venue, code))} · "
                     f"{effect_text(item)} · сегодня осталось: {max(0, left)}")
    lines += ["", "Выберите товар в списке. Закончив, нажмите «Уйти из магазина»: дорога домой тоже занимает время."]
    return "\n".join(lines)

def ent_text(p) -> str:
    lines = ["🎭 **Развлечения**", f"💰 {money(p.money)} · ⚡ {p.energy:.0f}%",
             f"Первые {R.DIM_TIERS[0][0]:.0f} ч одного занятия за сутки дают полный эффект, "
             "дальше отдача падает.", ""]
    for code, e in R.ENTERTAINMENT.items():
        used_h = p.day_ent.get(code, 0) * R.QUANTUM_MINUTES / 60
        cost = "бесплатно" if not e["cost"] else f"{e['cost']} м./ч"
        eff = f"счастье {e['happy']:+g}/ч"
        if e["health"]:
            eff += f", здоровье {e['health']:+g}/ч"
        if e["energy"]:
            eff += f", энергия {e['energy']:+g}/ч"
        used = f" · сегодня уже {used_h:g} ч" if used_h else ""
        lines.append(f"• **{e['title']}** — {cost} · {eff}{used}")
    lines += ["", f"Начать можно при энергии не ниже {R.LEISURE_MIN_ENERGY:.0f}%."]
    return "\n".join(lines)

def hospital_text(p) -> str:
    lines = ["🏥 **Больница**",
             "Принудительное лечение (при здоровье ниже 5%) проходит само. Здесь добровольные курсы: "
             "они стоят денег, занимают время и дают временный эффект после занятия.", ""]
    bt = buffs_text(p)
    if bt:
        lines += [bt, ""]
    for kind, kname in R.BUFF_KINDS.items():
        lines.append(f"**{kname}**")
        for _code, c in R.COURSES.items():
            if c["kind"] == kind:
                lines.append(f"• {c['title']} — {money(c['cost'])}: {buff_desc(kind, c['power'])}, "
                             f"{c['hours']} ч, занятие {c['lesson']} ч")
    lines += ["", "Один эффект каждого вида: более сильный заменяет слабый, тот же курс продлевает срок."]
    return "\n".join(lines)

def housing_text(p, now: datetime, tz) -> str:
    h = R.HOUSING[p.housing]
    lines = ["🏠 **Жильё**",
             f"Сейчас: {h['title']} — {money(h['rent'])}/сут., счастье тянется к {h['base']:.0f}%.", ""]
    for code, x in R.HOUSING.items():
        mark = "▶ " if code == p.housing else "• "
        lines.append(f"{mark}**{x['title']}** — {money(x['rent'])}/сут. · планка счастья {x['base']:.0f}% · "
                     f"сон ×{x['sleep']:.2f}, лечение сном ×{x['heal']:.2f}")
    lines += ["", f"Переезд стоит {R.HOUSING_MOVE_DAYS} суток аренды нового жилья; "
                  f"чаще раза в {R.HOUSING_MOVE_COOLDOWN_HOURS} ч переезжать нельзя."]
    if p.last_move:
        until = p.last_move + timedelta(hours=R.HOUSING_MOVE_COOLDOWN_HOURS)
        if now < until:
            lines.append(f"Следующий переезд {ts(until)}.")
    return "\n".join(lines)

# ---------- Рынок вакансий ----------

def vacancy_line(p, v) -> str:
    pr = R.PROFESSIONS[v.prof]
    mark = "🔒" if L.lock_reason(p, v) else "🟢"
    kind = "разовая" if v.kind == "once" else "постоянная"
    return (f"{mark} **№{v.id}** {pr['title']} · {kind} · {dur(v.shift_min)} · "
            f"{v.wage:.0f} м./ч · ⏳ <t:{v.expires_ts}:R>")

def jobs_text(p, vacs: list, now: datetime, tz) -> str:
    head = ("🔨 **Рынок вакансий**\n"
            "Список общий для сервера: кто успел, тому и работа. Сверху самые свежие. "
            f"Следующее обновление {ts(M.next_window(now, tz))}.\n"
            "🟢 доступна вам · 🔒 пока недоступна (причина внутри карточки)")
    if not vacs:
        return head + "\n\nСейчас вакансий нет: загляните после обновления."
    return head + "\n\n" + "\n".join(vacancy_line(p, v) for v in vacs)

def job_text(p, v, now: datetime) -> str:
    pr = R.PROFESSIONS[v.prof]
    lvl = L.exp_level(p.exp.get(v.prof, 0.0))
    pk = L.perks(lvl)
    gross, left, finishes = L.estimate(p, v)
    kind = ("разовая подработка (начинается сразу, вакансия исчезнет с рынка)" if v.kind == "once"
            else "постоянная работа")
    lines = [
        f"📄 **Вакансия №{v.id}: {pr['title']}**",
        f"Тип: {kind}",
        f"Смена: {dur(v.shift_min)} · дорога в одну сторону: {dur(v.commute_min)} "
        f"· проезд: {v.fare} м. в каждую сторону",
        f"Ставка: {v.wage:.1f} м./ч (брутто). С вашим счастьем (×{L.happy_mult(p.happiness):.2f}) "
        f"и опытом (+{pk['bonus'] * 100:.1f}%) полная смена принесёт ≈ {money(int(gross))} до НДФЛ",
        f"Нагрузка в час: энергия {v.rate_energy:+.1f}, здоровье {v.rate_health:+.2f}, "
        f"счастье {v.rate_happy:+.2f}",
        f"Ваш опыт: уровень {lvl} из {R.EXP_MAX_LEVEL} "
        f"(сопротивление негативному {pk['res_neg'] * 100:.1f}%, расходу энергии {pk['res_energy'] * 100:.1f}%)",
        f"Исчезнет {ts(datetime.fromtimestamp(v.expires_ts, tz=L.UTC))}",
        "",
    ]
    if finishes:
        lines.append(f"⚡ Прикидка: к концу смены энергии останется около {left:.0f}%.")
    else:
        lines.append(f"⚠️ Вы вряд ли доработаете: прикидка энергии к концу смены {left:.0f}%, "
                     f"а ниже {R.ENERGY_FORCED:.0f}% смену прервут (оплата снизится, репутация упадёт).")
    if v.kind == "perm":
        off = ", ".join(WD_NAMES[x] for x in v.days_off) or "нет"
        lines.insert(3, f"Выходные: {off}. После подписания договора условия не меняются; "
                        f"{R.JOB_FIRE_ABSENCES} прогула — увольнение.")
    ok, why = L.can_take(p, v, int(now.timestamp()))
    if not ok:
        lines.append(f"🚫 Сейчас взять нельзя: {why}")
    return "\n".join(lines)

def myjob_text(p, now: datetime, tz) -> str:
    job = p.job
    if job is None:
        lines = ["📑 **Моя работа**",
                 "У вас нет постоянной работы. Договор можно подписать на рынке вакансий (раздел «Работа»)."]
        if p.job_cooldown and now < p.job_cooldown:
            lines.append(f"Новую постоянную работу можно взять {ts(p.job_cooldown)}.")
        return "\n".join(lines)
    local = now.astimezone(tz)
    off = ", ".join(WD_NAMES[x] for x in job["days_off"]) or "нет"
    if local.weekday() in job["days_off"]:
        today = "сегодня выходной"
    elif job.get("worked_day") == local.date().isoformat():
        today = "сегодня вы уже выходили на смену"
    else:
        today = "сегодня рабочий день: можно выходить на смену"
    lvl = L.exp_level(p.exp.get(job["prof"], 0.0))
    lines = [
        f"📑 **Моя работа: {title_of(job['prof'])}**",
        f"Смена: {dur(job['shift_min'])} · дорога в одну сторону: {dur(job['commute_min'])} · "
        f"проезд: {job['fare']} м. в каждую сторону",
        f"Ставка: {job['wage']:.1f} м./ч (брутто, фиксирована договором) · ваш опыт: уровень {lvl}",
        f"Выходные: {off} · {today}",
        f"Прогулов: {job['absences']} из {R.JOB_FIRE_ABSENCES} (на третьем увольняют; "
        "неделя без прогулов снимает один).",
        f"За эту неделю заработано по договору: {money(int(job.get('week_gross', 0)))}; "
        f"недельная премия {R.JOB_WEEK_BONUS * 100:.0f}% выплачивается в воскресенье в полночь, "
        "если не было прогулов и ухода со смены раньше срока.",
        "",
        "Прогул — рабочий день без смены (считается в полночь по Ташкенту). Не прогул: день найма, больница, "
        "здоровье ниже 25%, принудительный сон.",
    ]
    return "\n".join(lines)

def edu_unlocks(code: str) -> str:
    """Какие профессии требуют эту программу."""
    return ", ".join(pr["title"] for pr in R.PROFESSIONS.values() if code in pr["edu"])

def edu_text(p) -> str:
    lines = ["🎓 **Образование**",
             "Учёба идёт по часам, оплата списывается по минутам, пока вы учитесь. Закончить можно в любой "
             "момент: штрафов нет, прогресс сохраняется. Если кончатся деньги или силы, занятие прервётся само.",
             f"💰 {money(p.money)} · ⚡ {p.energy:.0f}%", ""]
    for code, e in R.EDUCATION.items():
        if code in p.education:
            lines.append(f"✅ **{e['title']}** — получено")
            continue
        need_min = e["hours"] * 60
        done = min(need_min, p.edu_progress.get(code, 0))
        base = (f"**{e['title']}** — {e['hours']} ч · {e['fee']} м./ч "
                f"(≈ {money(e['hours'] * e['fee'])} всего)")
        unlocks = edu_unlocks(code)
        tail = f" · нужно для: {unlocks}" if unlocks else ""
        missing = L.edu_missing(p, code)
        if missing:
            lines.append(f"🔒 {base} · сначала: {', '.join(missing)}{tail}")
        else:
            prog = f" · пройдено {done / 60:.1f} из {e['hours']} ч ({done * 100 // need_min}%)" if done else ""
            lines.append(f"📖 {base}{prog}{tail}")
    return "\n".join(lines)

def render(screen: str, p, now: datetime, tz, events: list, note: str = "",
           jobs=None, vac=None, can: bool = False) -> str:
    """Полный текст сообщения панели для экрана screen."""
    if screen == "menu":
        text = ("💼 **Симулятор рабочего**\n"
                f"💰 {money(p.money)} · ⚡ {p.energy:.0f}% · ❤️ {p.health:.0f}% · {activity_text(p)}\n\n"
                "Выберите раздел. Игра идёт в реальном времени, даже когда вы не в сети.")
    elif screen == "career":
        text = ("💼 **Карьера**\nРабота: подработки и постоянные места на общем рынке вакансий; "
                "«Моя работа» — ваш договор и выход на смену; «Образование» — учёба, которая открывает "
                "новые вакансии.")
    elif screen == "edu":
        text = edu_text(p)
    elif screen == "jobs":
        text = jobs_text(p, jobs or [], now, tz)
    elif screen == "job" and vac is not None:
        text = job_text(p, vac, now)
    elif screen == "abort_confirm":
        text = ("🚪 **Уйти со смены?**\nДосрочный уход считается срывом:\n"
                f"• оплата за отработанное ×{R.ABORT_PAY_VOLUNTARY};\n"
                f"• опыт ×{R.EXP_ABORT_MULT};\n"
                f"• репутация −{R.REP_ABORT_VOLUNTARY:.0f}.\n"
                "Дорога домой останется. Если отработано меньше 15 минут, оплаты не будет.")
    elif screen == "leisure":
        text = leisure_text(p)
    elif screen == "market":
        text = market_text(p)
    elif screen == "venue" and p.venue:
        text = venue_text(p)
    elif screen == "ent":
        text = ent_text(p)
    elif screen == "hospital":
        text = hospital_text(p)
    elif screen == "housing":
        text = housing_text(p, now, tz)
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
    elif screen == "myjob":
        text = myjob_text(p, now, tz)
    elif screen == "quit_confirm":
        text = ("📤 **Уволиться?**\n"
                f"После ухода новую постоянную работу можно взять только через {R.JOB_HIRE_COOLDOWN_HOURS} ч. "
                "Недельная премия за текущую неделю сгорит. Вакансия обратно на рынок не вернётся.")
    else:
        text = status_text(p, now, tz)
    lines = [text]
    shown = [t for t in (describe(e) for e in events) if t][-R.EVENTS_SHOWN:]
    if shown and screen in SHOW_EVENTS_ON:
        lines += ["", "**Пока вас не было:**"] + shown
    if note:
        lines += ["", f"ℹ️ {note}"]
    return "\n".join(lines)[:1990]