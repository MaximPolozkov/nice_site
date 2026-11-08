"""Работа с нейросетью: генерация ниш-кандидатов и оценка чека/конкуренции."""
from __future__ import annotations

import json
import random
import re

import requests

from . import config

RULES = """Ты аналитик ниш для агентской модели: команда находит клиентов для
поставщиков и исполнителей по всей России и получает комиссию с закрытой сделки.
Подходят ниши, где: высокий средний чек (от ~50 000 ₽), есть много исполнителей
или поставщиков, которым нужны клиенты, не нужен свой склад или производство.
Запрещено предлагать: товары и услуги, требующие лицензии у посредника или
запрещённые к рекламе (алкоголь, табак, лекарства и медпрепараты, оружие,
финансовые и кредитные услуги, азартные игры, БАДы), а также схемы, нарушающие
правила Авито или VK. Не выдумывай факты: если не уверен в цифре — дай диапазон
и снизь confidence."""


class LLMError(RuntimeError):
    pass


def _extract_json(text: str):
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = match.group(1) if match else text
    start = min([i for i in (raw.find("["), raw.find("{")) if i != -1], default=-1)
    if start == -1:
        raise LLMError(f"В ответе нет JSON: {text[:200]}")
    return json.loads(raw[start:raw.rfind("]" if raw[start] == "[" else "}") + 1])


def serp_prices(niches: list[dict]) -> str:
    """Сниппеты с ценами из поиска SerpAPI — для нейросети без своего веб-поиска.

    Ищем по Яндексу и исключаем Авито: нужны цены производителей и подрядчиков.
    """
    lines = []
    for n in niches:
        try:
            resp = requests.get("https://serpapi.com/search.json", params={
                "engine": "yandex", "text": f"{n['phrase']} цена под ключ -site:avito.ru",
                "lr": 225, "api_key": config.SERPAPI_KEY}, timeout=30)
            items = resp.json().get("organic_results", [])[:5] if resp.status_code == 200 else []
        except (requests.RequestException, ValueError):
            items = []
        snippets = [f"{i.get('title', '')}: {i.get('snippet', '')}"[:300] for i in items]
        if snippets:
            lines.append(f"### {n['niche']}\n" + "\n".join(f"- {x}" for x in snippets))
    return "\n\n".join(lines)


class LLM:
    def __init__(self):
        if config.LLM_PROVIDER not in ("anthropic", "openai"):
            raise LLMError("Боту ниш нужна нейросеть: задайте в .env LLM_PROVIDER=anthropic "
                           "или openai (сейчас — «%s»)" % (config.LLM_PROVIDER or "не задан"))
        if not config.LLM_API_KEY:
            raise LLMError("Не задан LLM_API_KEY в .env")

    def ask(self, prompt: str, web_search: bool = False, max_tokens: int = 4000) -> str:
        if config.LLM_PROVIDER == "anthropic":
            body = {
                "model": config.LLM_MODEL,
                "max_tokens": max_tokens,
                "system": RULES,
                "messages": [{"role": "user", "content": prompt}],
            }
            if web_search and config.LLM_WEB_SEARCH:
                body["tools"] = [{"type": config.LLM_WEB_SEARCH_TOOL,
                                  "name": "web_search", "max_uses": 5}]
            resp = requests.post(
                f"{config.LLM_BASE_URL.rstrip('/')}/v1/messages",
                headers={"x-api-key": config.LLM_API_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json=body, timeout=300)
            if resp.status_code != 200:
                raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
            return "".join(b.get("text", "") for b in resp.json()["content"] if b["type"] == "text")

        # OpenAI-совместимый провайдер (YandexGPT, OpenRouter и т. п.)
        resp = requests.post(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={"model": config.LLM_MODEL, "max_tokens": max_tokens,
                  "messages": [{"role": "system", "content": RULES},
                               {"role": "user", "content": prompt}]},
            timeout=300)
        if resp.status_code != 200:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]

    def candidates(self, n: int, month_name: str, exclude: list[str]) -> list[dict]:
        prompt = f"""Сейчас {month_name}. Предложи {n} разных ниш для агентских сделок
по всей России. Учитывай сезон: ниши, к которым пора готовиться в ближайшие
1–4 месяца, особенно ценны. Не повторяй: {', '.join(exclude) or 'нет исключений'}.
Смешай направления: стройка/ремонт/монтаж, опт для бизнеса, клиенты для
дорогих услуг (B2B и B2C).

Для каждой ниши дай поисковую фразу для Яндекс Вордстата — 2–3 слова, как
ищет покупатель, без названия города и без операторов (например «баня бочка»).

Ответ — только JSON-массив:
[{{"niche": "Бани-бочки под ключ", "category": "стройка", "phrase": "баня бочка",
   "who_pays": "производители бань", "why": "одна фраза почему сейчас"}}]"""
        return _extract_json(self.ask(prompt, max_tokens=6000))

    def economics(self, niches: list[dict]) -> dict[str, dict]:
        names = "\n".join(f"- {n['niche']}" for n in niches)
        own_search = config.LLM_PROVIDER == "anthropic" and config.LLM_WEB_SEARCH
        context = serp_prices(niches) if (not own_search and config.SERPAPI_KEY) else ""
        if context:
            names += "\n\nРезультаты поиска цен (опирайся на них, повышай confidence, если цены сходятся):\n" + context
        prompt = f"""Для каждой ниши оцени по рынку России (если можешь — проверь цены
поиском по сайтам производителей и подрядчиков, не по Авито):
- avg_check_min, avg_check_max — типичный чек одной сделки в рублях;
- commission_pct — реалистичная комиссия посреднику, %;
- competition — 1 (почти нет посредников и рекламы) … 5 (рынок перегрет);
- confidence — 0…1, насколько ты уверен в цифрах;
- note — одна короткая фраза: на чём основана оценка.

Ниши:
{names}

Ответ — только JSON-объект, ключ — название ниши ровно как в списке:
{{"Бани-бочки под ключ": {{"avg_check_min": 250000, "avg_check_max": 500000,
  "commission_pct": 10, "competition": 3, "confidence": 0.7, "note": "..."}}}}"""
        return _extract_json(self.ask(prompt, web_search=own_search, max_tokens=8000))


class DemoLLM:
    """Выдуманные ответы, чтобы проверить работу бота без ключей."""

    POOL = [
        ("Бани-бочки под ключ", "стройка", "баня бочка", "производители бань"),
        ("Навесы и козырьки из поликарбоната", "стройка", "навес из поликарбоната", "цеха металлоконструкций"),
        ("Каркасные дома", "стройка", "каркасный дом под ключ", "строительные компании"),
        ("Кухни на заказ", "услуги", "кухня на заказ", "мебельные цеха"),
        ("Септики для частного дома", "стройка", "септик для частного дома", "монтажные бригады"),
        ("Упаковка для общепита оптом", "опт", "упаковка для общепита", "производители упаковки"),
        ("Банкротство физлиц", "услуги", "банкротство физических лиц", "юридические компании"),
        ("Внедрение CRM для малого бизнеса", "услуги", "внедрение crm", "интеграторы CRM"),
        ("Бытовки и модульные дома", "стройка", "бытовка купить", "производители бытовок"),
        ("Спецодежда оптом", "опт", "спецодежда оптом", "швейные производства"),
        ("Остекление балконов", "стройка", "остекление балкона", "оконные компании"),
        ("Тёплые полы и отопление дома", "стройка", "отопление частного дома", "монтажники отопления"),
    ]

    def candidates(self, n, month_name, exclude):
        return [{"niche": a, "category": b, "phrase": c, "who_pays": d,
                 "why": "демо-данные"} for a, b, c, d in self.POOL if a not in exclude][:n]

    def economics(self, niches):
        out = {}
        for n in niches:
            rnd = random.Random(n["niche"])
            lo = rnd.choice([60, 90, 120, 200, 300]) * 1000
            out[n["niche"]] = {"avg_check_min": lo, "avg_check_max": lo * 2,
                               "commission_pct": rnd.choice([7, 10, 12, 15]),
                               "competition": rnd.randint(1, 5),
                               "confidence": 0.5, "note": "демо-оценка"}
        return out


def make_llm(demo: bool):
    return DemoLLM() if demo else LLM()
