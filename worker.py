"""Фоновый процесс: запускает бота по расписанию и по кнопке с сайта.

Сайт не запускает долгий прогон сам — он кладёт заявку в базу,
а worker забирает её в течение ~15 секунд.
"""
import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from bot import config, engine, storage
from bot.scheduling import is_due

log = logging.getLogger("worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    tz = ZoneInfo(config.TZ)
    con = storage.connect()
    storage.fail_stale_runs(con)
    log.info("Worker запущен. Расписание: %s, день %s, %s (%s)%s",
             "вкл" if config.SCHEDULE_ENABLED else "выкл", config.SCHEDULE_WEEKDAY,
             config.SCHEDULE_TIME, config.TZ, ", ДЕМО-РЕЖИМ" if config.DEMO_MODE else "")
    while True:
        try:
            req = storage.take_request(con)
            if req:
                log.info("Ручной запуск по заявке #%s", req["id"])
                engine.run("manual", publish=bool(req["publish"]))
            elif config.SCHEDULE_ENABLED and is_due(dt.datetime.now(tz), storage.last_scheduled_run_date(con)):
                log.info("Запуск по расписанию")
                engine.run("schedule", publish=True)
        except Exception:
            log.exception("Ошибка в цикле worker")
        time.sleep(15)


if __name__ == "__main__":
    main()
