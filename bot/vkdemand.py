"""Бесплатная оценка спроса через VK API (newsfeed.search).

Считаем, сколько публичных постов во ВКонтакте упоминают фразу за последние 30 дней
и за те же 30 дней год назад. Это не поисковый спрос, как в Вордстате, а активность
обсуждения: в неё попадают и покупатели, и продавцы. Зато бесплатно и официально.

Подходит ключ пользователя (VK_USER_TOKEN) или сервисный ключ приложения (VK_SERVICE_TOKEN).
"""
from __future__ import annotations

import time

import requests

from . import config

API = "https://api.vk.com/method/newsfeed.search"
DAY = 24 * 3600


class VKDemandError(RuntimeError):
    pass


class VKDemand:
    unit = "постов во VK"

    def __init__(self):
        self.token = config.VK_SERVICE_TOKEN or config.VK_USER_TOKEN
        if not self.token:
            raise VKDemandError("Для бесплатной оценки спроса нужен VK_USER_TOKEN или VK_SERVICE_TOKEN в .env")
        self.session = requests.Session()

    def _count(self, phrase: str, start: int, end: int) -> int:
        for attempt in range(3):
            try:
                data = self.session.post(API, data={
                    "q": phrase, "start_time": start, "end_time": end, "count": 1,
                    "access_token": self.token, "v": config.VK_API_VERSION}, timeout=30).json()
            except (requests.RequestException, ValueError) as e:
                raise VKDemandError(f"нет связи с VK: {e.__class__.__name__}") from e
            if "error" in data:
                code = data["error"].get("error_code")
                if code == 6:                       # слишком много запросов в секунду
                    time.sleep(1 + attempt)
                    continue
                raise VKDemandError(f"VK {code}: {data['error'].get('error_msg')}")
            time.sleep(0.4)                         # не больше 3 запросов в секунду
            resp = data.get("response", {})
            return int(resp.get("total_count", resp.get("count", 0)) or 0)
        raise VKDemandError("VK: лимит запросов, попробуйте позже")

    def monthly_volume(self, phrase: str) -> int:
        now = int(time.time())
        self._last = (phrase, self._count(phrase, now - 30 * DAY, now))
        return self._last[1]

    def yoy_pct(self, phrase: str) -> float | None:
        """Рост к тем же 30 дням год назад, %. None — если год назад постов почти не было."""
        now = int(time.time())
        current = self._last[1] if getattr(self, "_last", (None,))[0] == phrase else self.monthly_volume(phrase)
        before = self._count(phrase, now - 395 * DAY, now - 365 * DAY)
        if before < 5:
            return None
        return (current / before - 1) * 100

    def related(self, phrase: str, limit: int = 3) -> list[str]:
        return []
