"""Настройки бота. Все секреты берутся из файла .env или переменных окружения."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Простейший загрузчик .env без сторонних библиотек.

    Как и Docker Compose, при повторе ключа берёт последнее значение.
    Уже заданные переменные окружения (например, из Docker) не трогает.
    """
    if not path.exists():
        return
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in values.items():
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str = "", *fallbacks: str) -> str:
    """Значение переменной. Пустые значения и заглушки «change-me…» считаются незаданными.

    fallbacks — альтернативные имена, чтобы подходил .env сайта «Кодверк».
    """
    for key in (name, *fallbacks):
        value = os.environ.get(key, "").strip()
        if value and not value.lower().startswith("change-me"):
            return value
    return default


def flag(name: str, default: bool) -> bool:
    value = env(name)
    return default if not value else value.lower() in ("1", "true", "yes", "on", "да")


def _ids(raw: str) -> list[int]:
    return [int(x) for x in raw.replace(" ", "").split(",") if x.lstrip("-").isdigit()]


# --- Яндекс Вордстат API ---
# Yandex Cloud: API-ключ сервисного аккаунта с ролью search-api.webSearch.user и ID каталога
WORDSTAT_API_KEY = env("WORDSTAT_API_KEY", "", "YC_API_KEY")
WORDSTAT_FOLDER_ID = env("WORDSTAT_FOLDER_ID", "", "YC_FOLDER_ID")
# Старый доступ api.wordstat.yandex.net по OAuth-токену (если ещё работает)
WORDSTAT_TOKEN = env("WORDSTAT_TOKEN")
WORDSTAT_CONFIGURED = bool((WORDSTAT_API_KEY and WORDSTAT_FOLDER_ID) or WORDSTAT_TOKEN)
WORDSTAT_REGIONS = _ids(env("WORDSTAT_REGIONS"))
# Пустой список = вся Россия (все регионы).

# --- Нейросеть ---
# LLM_PROVIDER: "anthropic" (с веб-поиском цен) или "openai" (любой OpenAI-совместимый
# сервис: YandexGPT, GigaChat через прокси, OpenRouter и т. п. — без веб-поиска).
LLM_PROVIDER = env("LLM_PROVIDER", "anthropic").lower()
LLM_API_KEY = env("LLM_API_KEY")
LLM_MODEL = env("LLM_MODEL", "claude-sonnet-5-5")
LLM_BASE_URL = env("LLM_BASE_URL",
                   "https://api.anthropic.com" if LLM_PROVIDER == "anthropic" else "https://api.openai.com/v1")
LLM_WEB_SEARCH = flag("LLM_WEB_SEARCH", True)
LLM_WEB_SEARCH_TOOL = env("LLM_WEB_SEARCH_TOOL", "web_search_20250305")
# SerpAPI (ключ от «Кодверка»): поиск цен, если у нейросети нет своего веб-поиска
SERPAPI_KEY = env("SERPAPI_KEY")

# --- VK ---
VK_GROUP_ID = env("VK_GROUP_ID")                 # число без минуса, например 123456789
VK_GROUP_TOKEN = env("VK_GROUP_TOKEN")           # ключ сообщества (сообщения)
VK_USER_TOKEN = env("VK_USER_TOKEN")             # ключ админа (VK ID) — для поста на стену
# Кому слать отчёт: ID админов или peer_id беседы (в «Кодверке» это VK_NOTIFY_PEER_IDS)
VK_ADMIN_PEER_IDS = _ids(env("VK_ADMIN_PEER_IDS", "", "VK_NOTIFY_PEER_IDS"))
# PUBLISH_MODE: wall — пост на стену; message — отчёт админам в личку от сообщества;
# both — оба варианта; file — только сохранить текст в папку out/.
PUBLISH_MODE = env("PUBLISH_MODE", "message")
VK_API_VERSION = "5.199"

# --- Логика отбора ---
CANDIDATES_PER_RUN = int(env("CANDIDATES_PER_RUN", "30"))
TOP_N = int(env("TOP_N", "3"))
REPEAT_COOLDOWN_WEEKS = int(env("REPEAT_COOLDOWN_WEEKS", "8"))
DEFAULT_COMMISSION_PCT = float(env("DEFAULT_COMMISSION_PCT", "10"))
# Ниши, где средняя комиссия с одной сделки ниже порога, в топ не попадают.
MIN_COMMISSION_RUB = int(env("MIN_COMMISSION_RUB", "15000"))

# --- Хранилище (в Docker это том /data) ---
DATA_DIR = Path(env("DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "niches.db"
OUT_DIR = DATA_DIR / "out"

# --- Расписание (контейнер worker) ---
TZ = env("TZ", "Asia/Novosibirsk")
SCHEDULE_WEEKDAY = int(env("SCHEDULE_WEEKDAY", "1"))     # 1 = понедельник … 7 = воскресенье
SCHEDULE_TIME = env("SCHEDULE_TIME", "09:12")             # ЧЧ:ММ по TZ
SCHEDULE_ENABLED = flag("SCHEDULE_ENABLED", True)
# Демо-режим: бот работает на выдуманных данных — для проверки сайта без ключей.
DEMO_MODE = flag("DEMO_MODE", False)

# --- Сайт ---
SITE_TITLE = env("SITE_TITLE", "Ниши недели")
DOMAIN = env("DOMAIN", "kodverks.ru")
# Ссылка на сообщество для публичной страницы; по умолчанию — сообщество из VK_GROUP_ID
VK_COMMUNITY_URL = env("VK_COMMUNITY_URL", f"https://vk.com/club{VK_GROUP_ID}" if VK_GROUP_ID else "")
# Пароль панели; если не задан — пароль первого администратора «Кодверка»
ADMIN_PASSWORD = env("ADMIN_PASSWORD", "", "FIRST_ADMIN_PASSWORD")
SECRET_KEY = env("SECRET_KEY")
COOKIE_SECURE = flag("COOKIE_SECURE", True)
