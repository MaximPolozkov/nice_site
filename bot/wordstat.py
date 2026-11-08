"""Клиент официального API Яндекс Вордстата.

Документация: https://yandex.ru/support2/wordstat/ru/content/api-structure
Методы: /v1/topRequests (частота за 30 дней), /v1/dynamics (динамика по месяцам).
"""
from __future__ import annotations

import datetime as dt
import random
import time

import requests

from . import config

BASE_URL = "https://api.wordstat.yandex.net"


class WordstatError(RuntimeError):
    pass


class WordstatClient:
    def __init__(self, token: str, regions: list[int] | None = None):
        if not token:
            raise WordstatError("Не задан WORDSTAT_TOKEN в .env")
        self.token = token
        self.regions = regions or []
        self.session = requests.Session()

    def _post(self, method: str, payload: dict) -> dict:
        if self.regions:
            payload["regions"] = self.regions
        for attempt in range(4):
            resp = self.session.post(
                f"{BASE_URL}{method}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json;charset=utf-8",
                },
                timeout=30,
            )
            if resp.status_code == 429:          # превышен секундный лимит
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                raise WordstatError(f"{method}: HTTP {resp.status_code} {resp.text[:300]}")
            time.sleep(0.4)                       # бережём секундную квоту
            return resp.json()
        raise WordstatError(f"{method}: лимит запросов, попробуйте позже")

    def monthly_volume(self, phrase: str) -> int:
        """Сколько запросов с этой фразой было за последние 30 дней."""
        data = self._post("/v1/topRequests", {"phrase": phrase})
        if "totalCount" in data:
            return int(data["totalCount"])
        items = data.get("topRequests") or []
        return int(items[0]["count"]) if items else 0

    def monthly_dynamics(self, phrase: str, months: int = 25) -> list[tuple[str, int]]:
        """Число запросов по месяцам за последние ~2 года: [(YYYY-MM, count), ...]."""
        today = dt.date.today()
        last_month_end = today.replace(day=1) - dt.timedelta(days=1)
        start = last_month_end.replace(day=1)
        for _ in range(months - 1):
            start = (start - dt.timedelta(days=1)).replace(day=1)
        data = self._post("/v1/dynamics", {
            "phrase": phrase,
            "period": "monthly",
            "fromDate": start.isoformat(),
            "toDate": last_month_end.isoformat(),
        })
        return [(row["date"][:7], int(row["count"])) for row in data.get("dynamics", [])]


class DemoWordstat:
    """Правдоподобные выдуманные данные для пробного запуска без ключей."""

    def monthly_volume(self, phrase: str) -> int:
        rnd = random.Random(phrase)
        return int(10 ** rnd.uniform(3.3, 5.6))

    def monthly_dynamics(self, phrase: str, months: int = 25) -> list[tuple[str, int]]:
        rnd = random.Random(phrase + "dyn")
        peak = rnd.randint(1, 12)
        growth = rnd.uniform(-0.25, 0.4)
        base = self.monthly_volume(phrase)
        today = dt.date.today()
        cur = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
        rows = []
        for i in range(months):
            month = cur.month
            dist = min(abs(month - peak), 12 - abs(month - peak))
            season = 1.6 - dist * 0.15
            trend = 1 - growth * i / 12          # i = 0 — самый свежий месяц
            rows.append((cur.strftime("%Y-%m"), max(1, int(base * season * trend * rnd.uniform(0.9, 1.1)))))
            cur = (cur - dt.timedelta(days=1)).replace(day=1)
        return list(reversed(rows))


def make_client(demo: bool):
    return DemoWordstat() if demo else WordstatClient(config.WORDSTAT_TOKEN, config.WORDSTAT_REGIONS)
