"""Один прогон бота: кандидаты → экономика → Вордстат → балл → топ → публикация."""
from __future__ import annotations

import datetime as dt
import logging

from . import config, storage, vk
from .llm import make_llm
from .scoring import MONTHS_RU, NicheScore, analyze_dynamics, score, season_text
from .wordstat import make_client

log = logging.getLogger("niche_bot")
COMPETITION_RU = ["", "низкая", "ниже средней", "средняя", "высокая", "очень высокая"]


def rub(x: float | None, exact: bool = False) -> str:
    """Сумма в рублях. По умолчанию округляет до тысяч (для оценок), exact — точно (для факта)."""
    value = int(x or 0) if exact else int(round(x or 0, -3))
    return f"{value:,}".replace(",", " ") + " ₽"


def num(x: float | None) -> str:
    return f"{int(x or 0):,}".replace(",", " ")


def build_post(top: list, date: dt.date | None = None) -> str:
    """top — строки forecasts из базы (или объекты с теми же полями)."""
    date = date or dt.date.today()
    lines = [f"📊 Ниши недели — {date.strftime('%d.%m.%Y')}, вся Россия", ""]
    for i, n in enumerate(top, 1):
        yoy = "нет данных о росте" if n["yoy_pct"] is None else f"{n['yoy_pct']:+.0f}% к прошлому году"
        cmin = n["check_min"] * n["commission_pct"] / 100
        cmax = n["check_max"] * n["commission_pct"] / 100
        lines += [
            f"{i}. {n['niche']} — {n['score']}/10",
            f"Спрос: ~{num(n['volume'])} запросов/мес «{n['phrase']}», {yoy}",
            f"Чек: {rub(n['check_min'])}–{rub(n['check_max'])}; комиссия {n['commission_pct']:g}% = "
            f"{rub(cmin)}–{rub(cmax)} с одной сделки",
            f"Конкуренция: {COMPETITION_RU[n['competition']]}",
            f"Сезон: {n['season']}",
            f"Кто платит: {n['who_pays']}",
            "",
        ]
    lines.append("Цифры спроса — Яндекс Вордстат, чек и комиссия — оценка по открытым "
                 "источникам, не гарантия дохода. Перед входом проверьте нишу пробным объявлением.")
    return "\n".join(lines)


def publish_text(text: str, say=log.info) -> bool:
    """Сохраняет текст в файл и отправляет в VK по PUBLISH_MODE. True — если ушло в VK."""
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUT_DIR / f"post_{dt.datetime.now():%Y-%m-%d_%H%M}.txt"
    path.write_text(text, encoding="utf-8")
    say(f"Текст сохранён: {path.name}")
    sent = False
    mode = config.PUBLISH_MODE
    if mode in ("wall", "both"):
        try:
            say(f"Пост на стене: {vk.post_to_wall(text)}")
            sent = True
        except Exception as e:
            say(f"Стена не сработала: {e}")
            if mode == "wall" and config.VK_GROUP_TOKEN and config.VK_ADMIN_PEER_IDS:
                mode = "message"
    if mode in ("message", "both"):
        try:
            say(f"Отправлено админам в VK: {vk.send_to_admins(text)}")
            sent = True
        except Exception as e:
            say(f"Отправка админам не сработала: {e}")
    return sent


def run(trigger: str = "manual", publish: bool = True, demo: bool | None = None) -> int:
    demo = config.DEMO_MODE if demo is None else demo
    con = storage.connect()
    run_id = storage.start_run(con, trigger, demo)

    def say(msg: str) -> None:
        log.info(msg)
        storage.append_log(con, run_id, f"{dt.datetime.now():%H:%M:%S} {msg}")

    try:
        say("Демо-режим: данные выдуманные" if demo else "Боевой режим")
        ws, ai = make_client(demo), make_llm(demo)
        say(f"Подбор ниш: {getattr(ai, 'mode', '—')}")
        month = dt.date.today().month
        exclude = storage.recently_published(con, config.REPEAT_COOLDOWN_WEEKS)

        say("Генерирую кандидатов…")
        cands = ai.candidates(config.CANDIDATES_PER_RUN, MONTHS_RU[month - 1], exclude)
        cands = [c for c in cands if c.get("niche") and c.get("phrase") and c["niche"] not in exclude]
        say(f"Кандидатов: {len(cands)}")

        say("Оцениваю чек и конкуренцию…")
        econ = {}
        for i in range(0, len(cands), 10):
            econ.update(ai.economics(cands[i:i + 10]))

        results: list[NicheScore] = []
        for c in cands:
            e = econ.get(c["niche"])
            if not e:
                say(f"Нет оценки экономики: {c['niche']}")
                continue
            try:
                volume = ws.monthly_volume(c["phrase"])
                yoy, peaks, to_peak = analyze_dynamics(ws.monthly_dynamics(c["phrase"]), month)
            except Exception as ex:
                say(f"Вордстат, «{c['phrase']}»: {ex}")
                continue
            note = e.get("note", "")
            related = ws.related(c["phrase"])
            if related:
                note = (note + "; " if note else "") + "часто ищут: " + ", ".join(related)
            results.append(score(NicheScore(
                niche=c["niche"], category=c.get("category", ""), phrase=c["phrase"],
                who_pays=c.get("who_pays", ""), volume=volume, yoy_pct=yoy,
                peak_months=peaks, months_to_peak=to_peak,
                check_min=int(e.get("avg_check_min") or 0), check_max=int(e.get("avg_check_max") or 0),
                commission_pct=float(e.get("commission_pct") or config.DEFAULT_COMMISSION_PCT),
                competition=max(1, min(5, int(e.get("competition") or 3))),
                confidence=float(e.get("confidence") or 0.5), note=note)))

        if not results:
            raise RuntimeError("Ни одна ниша не оценена — смотрите сообщения выше")

        results.sort(key=lambda n: n.total, reverse=True)
        top = [n for n in results
               if (n.commission_min + n.commission_max) / 2 >= config.MIN_COMMISSION_RUB][:config.TOP_N]
        if not top:
            raise RuntimeError(f"Нет ниш с комиссией от {rub(config.MIN_COMMISSION_RUB)}")
        for n in results:
            storage.save_forecast(con, run_id, n, season_text(n), in_top=n in top, published=False)
        say(f"Оценено ниш: {len(results)}, в топе: {len(top)}")

        text = build_post([r for r in storage.forecasts_for_run(con, run_id) if r["in_top"]])
        sent = False
        if publish:
            sent = publish_text(text, say)
            if sent:
                storage.mark_published(con, run_id)
        storage.finish_run(con, run_id, "ok", text, sent)
        say("Готово")
    except Exception as ex:
        say(f"Ошибка: {ex}")
        storage.finish_run(con, run_id, "error")
    finally:
        con.close()
    return run_id


def republish(run_id: int) -> bool:
    con = storage.connect()
    try:
        r = storage.get_run(con, run_id)
        if not r or not r["post_text"]:
            return False
        lines = []
        sent = publish_text(r["post_text"], lines.append)
        for line in lines:
            storage.append_log(con, run_id, f"{dt.datetime.now():%H:%M:%S} {line}")
        if sent:
            storage.mark_published(con, run_id)
            con.execute("UPDATE runs SET published=1 WHERE id=?", (run_id,))
            con.commit()
        return sent
    finally:
        con.close()
