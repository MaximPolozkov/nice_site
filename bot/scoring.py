"""Оценка ниши по 5 факторам. Каждый фактор 0–10, итог — взвешенная сумма."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

WEIGHTS = {"commission": 0.30, "demand": 0.25, "trend": 0.15, "season": 0.15, "competition": 0.15}
MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
             "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def _clamp(x: float) -> float:
    return max(0.0, min(10.0, x))


def _log_scale(value: float, lo: float, hi: float) -> float:
    if value <= 0:
        return 0.0
    return _clamp((math.log10(value) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)) * 10)


@dataclass
class NicheScore:
    niche: str
    category: str
    phrase: str
    who_pays: str
    volume: int
    yoy_pct: float | None
    peak_months: list[int]
    months_to_peak: int | None
    check_min: int
    check_max: int
    commission_pct: float
    competition: int
    confidence: float
    note: str
    parts: dict = field(default_factory=dict)
    total: float = 0.0

    @property
    def commission_min(self) -> int:
        return int(self.check_min * self.commission_pct / 100)

    @property
    def commission_max(self) -> int:
        return int(self.check_max * self.commission_pct / 100)


def analyze_dynamics(rows: list[tuple[str, int]], today_month: int):
    """Возвращает (рост год к году в %, месяцы пика, месяцев до ближайшего пика)."""
    if len(rows) < 15:
        return None, [], None
    counts = [c for _, c in rows]
    last3, prev_year3 = sum(counts[-3:]), sum(counts[-15:-12])
    yoy = (last3 / prev_year3 - 1) * 100 if prev_year3 else None

    by_month: dict[int, list[int]] = {}
    for date, c in rows[-24:]:
        by_month.setdefault(int(date[5:7]), []).append(c)
    avg = {m: sum(v) / len(v) for m, v in by_month.items()}
    mean = sum(avg.values()) / len(avg)
    best = max(avg.values())
    if best < mean * 1.25:                     # спрос ровный весь год
        return yoy, [], 0
    # месяцы пика по порядку, начиная с ближайшего
    peaks = sorted((m for m, v in avg.items() if v >= best * 0.85),
                   key=lambda m: (m - today_month) % 12)
    to_peak = min((p - today_month) % 12 for p in peaks)
    return yoy, peaks, to_peak


def score(n: NicheScore) -> NicheScore:
    p = {}
    p["demand"] = _log_scale(n.volume, 1_000, 1_000_000)                 # 1 тыс → 0, 1 млн → 10
    p["commission"] = _log_scale((n.commission_min + n.commission_max) / 2, 5_000, 200_000)
    p["trend"] = 5.0 if n.yoy_pct is None else _clamp(5 + n.yoy_pct / 8)  # +40% → 10, −40% → 0
    if n.months_to_peak is None:
        p["season"] = 5.0
    elif not n.peak_months:
        p["season"] = 7.0                                                # ровный спрос
    elif 1 <= n.months_to_peak <= 3:
        p["season"] = 10.0                                               # пора готовиться
    elif n.months_to_peak == 0:
        p["season"] = 8.0                                                # пик уже идёт
    elif n.months_to_peak <= 5:
        p["season"] = 6.0
    else:
        p["season"] = 3.0                                                # не сезон
    p["competition"] = _clamp((5 - n.competition) * 2.5)
    total = sum(p[k] * w for k, w in WEIGHTS.items())
    total *= 0.85 + 0.15 * max(0.0, min(1.0, n.confidence))              # штраф за неуверенность
    n.parts = {k: round(v, 1) for k, v in p.items()}
    n.total = round(total, 1)
    return n


def season_text(n: NicheScore) -> str:
    if not n.peak_months:
        return "спрос ровный круглый год" if n.months_to_peak == 0 else "мало данных"
    peaks = ", ".join(MONTHS_RU[m - 1] for m in n.peak_months)
    if n.months_to_peak == 0:
        return f"пик сейчас ({peaks})"
    if n.months_to_peak <= 3:
        return f"пик через {n.months_to_peak} мес. ({peaks}) — самое время готовиться"
    return f"пик: {peaks}"
