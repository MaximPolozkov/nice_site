"""Расписание еженедельного запуска."""
from __future__ import annotations

import datetime as dt

from . import config

WEEKDAYS_RU = ["", "по понедельникам", "по вторникам", "по средам", "по четвергам",
               "по пятницам", "по субботам", "по воскресеньям"]


def _slot_time() -> dt.time:
    h, m = config.SCHEDULE_TIME.split(":")
    return dt.time(int(h), int(m))


def is_due(now: dt.datetime, last_scheduled_date: str | None) -> bool:
    """Пора ли запускать: нужный день недели, время наступило, сегодня ещё не запускали.

    Если сервер был выключен в момент запуска, прогон сделается в тот же день позже.
    """
    if now.isoweekday() != config.SCHEDULE_WEEKDAY:
        return False
    if now.time() < _slot_time():
        return False
    return last_scheduled_date != now.date().isoformat()


def next_run(now: dt.datetime) -> dt.datetime:
    slot = _slot_time()
    days = (config.SCHEDULE_WEEKDAY - now.isoweekday()) % 7
    cand = dt.datetime.combine(now.date() + dt.timedelta(days=days), slot, tzinfo=now.tzinfo)
    if cand <= now:
        cand += dt.timedelta(days=7)
    return cand


def describe() -> str:
    return f"{WEEKDAYS_RU[config.SCHEDULE_WEEKDAY]} в {config.SCHEDULE_TIME} ({config.TZ})"
