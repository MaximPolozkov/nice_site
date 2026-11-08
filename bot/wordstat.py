"""Клиент API Яндекс Вордстата.

Два варианта доступа:
- Yandex Cloud (основной с 2026 года): https://searchapi.api.cloud.yandex.net/v2/wordstat
  ключ сервисного аккаунта (Api-Key) + ID каталога. Нужна роль search-api.webSearch.user.
- старый api.wordstat.yandex.net с OAuth-токеном — оставлен для совместимости.

Методы: topRequests (частота за 30 дней), dynamics (динамика по месяцам).
"""
from __future__ import annotations

import datetime as dt
import random
import time

import requests

from . import config

CLOUD_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat"
LEGACY_URL = "https://api.wordstat.yandex.net/v1"


class WordstatError(RuntimeError):
    pass


def _month_range(months: int) -> tuple[dt.date, dt.date]:
    """Первый день месяца months назад и последний день прошлого месяца."""
    last_month_end = dt.date.today().replace(day=1) - dt.timedelta(days=1)
    start = last_month_end.replace(day=1)
    for _ in range(months - 1):
        start = (start - dt.timedelta(days=1)).replace(day=1)
    return start, last_month_end


class WordstatClient:
    def __init__(self, regions: list[int] | None = None):
        self.cloud = bool(config.WORDSTAT_API_KEY and config.WORDSTAT_FOLDER_ID)
        if not self.cloud and not config.WORDSTAT_TOKEN:
            raise WordstatError("Вордстат не настроен: задайте в .env WORDSTAT_API_KEY и WORDSTAT_FOLDER_ID "
                                "(Yandex Cloud)")
        self.regions = regions or []
        self.session = requests.Session()
        self._top: dict[str, list[tuple[str, int]]] = {}

    def _post(self, method: str, payload: dict) -> dict:
        if self.cloud:
            url = f"{CLOUD_URL}/{method}"
            payload["folderId"] = config.WORDSTAT_FOLDER_ID
            if self.regions:
                payload["regions"] = [str(r) for r in self.regions]
            headers = {"Authorization": f"Api-Key {config.WORDSTAT_API_KEY}"}
        else:
            url = f"{LEGACY_URL}/{method}"
            if self.regions:
                payload["regions"] = self.regions
            headers = {"Authorization": f"Bearer {config.WORDSTAT_TOKEN}"}
        headers["Content-Type"] = "application/json;charset=utf-8"
        for attempt in range(4):
            try:
                resp = self.session.post(url, json=payload, headers=headers, timeout=30)
            except requests.exceptions.SSLError as e:
                raise WordstatError(f"ошибка SSL при обращении к {url.split('/')[2]} — {e.__class__.__name__}") from e
            except requests.RequestException as e:
                raise WordstatError(f"нет связи с {url.split('/')[2]}: {e.__class__.__name__}") from e
            if resp.status_code == 429:          # превышен секундный лимит
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (401, 403):
                raise WordstatError(f"доступ запрещён (HTTP {resp.status_code}): проверьте ключ, ID каталога "
                                    f"и роль search-api.webSearch.user — {resp.text[:200]}")
            if resp.status_code != 200:
                raise WordstatError(f"{method}: HTTP {resp.status_code} {resp.text[:300]}")
            time.sleep(0.25)                      # бережём секундную квоту
            return resp.json()
        raise WordstatError(f"{method}: лимит запросов, попробуйте позже")

    def monthly_volume(self, phrase: str) -> int:
        """Сколько запросов с этой фразой было за последние 30 дней."""
        data = self._post("topRequests", {"phrase": phrase})
        items = data.get("results") or data.get("topRequests") or []
        self._top[phrase] = [(i["phrase"], int(i["count"])) for i in items]
        if data.get("totalCount") not in (None, ""):
            return int(data["totalCount"])
        return int(items[0]["count"]) if items else 0

    def related(self, phrase: str, limit: int = 3) -> list[str]:
        """Самые частые уточнения фразы за 30 дней (без лишнего запроса — из monthly_volume)."""
        return [p for p, _ in self._top.get(phrase, []) if p.strip().lower() != phrase.lower()][:limit]

    def monthly_dynamics(self, phrase: str, months: int = 25) -> list[tuple[str, int]]:
        """Число запросов по месяцам за последние ~2 года: [(YYYY-MM, count), ...]."""
        start, end = _month_range(months)
        if self.cloud:
            payload = {"phrase": phrase, "period": "PERIOD_MONTHLY",
                       "fromDate": f"{start.isoformat()}T00:00:00Z", "toDate": f"{end.isoformat()}T00:00:00Z"}
        else:
            payload = {"phrase": phrase, "period": "monthly",
                       "fromDate": start.isoformat(), "toDate": end.isoformat()}
        data = self._post("dynamics", payload)
        rows = data.get("results") or data.get("dynamics") or []
        return sorted((row["date"][:7], int(row["count"])) for row in rows)


class DemoWordstat:
    """Правдоподобные выдуманные данные для пробного запуска без ключей."""

    def related(self, phrase: str, limit: int = 3) -> list[str]:
        return [f"{phrase} цена", f"{phrase} под ключ", f"{phrase} отзывы"][:limit]

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
    return DemoWordstat() if demo else WordstatClient(config.WORDSTAT_REGIONS)
