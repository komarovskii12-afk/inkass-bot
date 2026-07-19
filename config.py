"""Настройки бота. Все секреты берутся из переменных окружения (Render)."""
import os
import re
from zoneinfo import ZoneInfo


def _ids(name: str) -> set[int]:
    raw = os.getenv(name, "").replace(" ", "")
    return {int(x) for x in raw.split(",") if x}


# --- Telegram ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Белые списки. Владельцы (админы) видят отчёты и управляют пунктами.
ADMIN_IDS = _ids("ADMIN_IDS")
WORKER_IDS = _ids("WORKER_IDS")

# --- Время ---
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
TZ = ZoneInfo(TIMEZONE)

# --- Webhook (Render) ---
# Если BASE_WEBHOOK_URL задан — бот работает через webhook (нужно для Render).
# Если пусто — включается polling (удобно для локального запуска).
# Render сам публикует внешний адрес сервиса в RENDER_EXTERNAL_URL.
# Его и берём в первую очередь: fromService "host" в render.yaml отдаёт
# внутренний хост приватной сети, который Telegram снаружи не резолвит.
BASE_WEBHOOK_URL = (
    os.getenv("RENDER_EXTERNAL_URL") or os.getenv("BASE_WEBHOOK_URL", "")
).strip().rstrip("/")
# Если адрес пришёл без схемы — добавим https://
if BASE_WEBHOOK_URL and not BASE_WEBHOOK_URL.startswith(("http://", "https://")):
    BASE_WEBHOOK_URL = "https://" + BASE_WEBHOOK_URL
WEBHOOK_PATH = "/webhook"

# Telegram разрешает в secret_token только A-Z, a-z, 0-9, "_" и "-".
# Render генерирует значение со спецсимволами, поэтому чистим его.
_raw_secret = os.getenv("WEBHOOK_SECRET", "change-me")
WEBHOOK_SECRET = re.sub(r"[^A-Za-z0-9_-]", "", _raw_secret)[:256] or "inkass-webhook-secret"
PORT = int(os.getenv("PORT", "10000"))

# --- База данных ---
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot.db")

# --- Справочник валют и номиналов ---
# Сейчас только доллары, номиналы 100 и 50. Чтобы добавить валюту/номинал —
# допиши сюда, менять код бота не нужно.
CURRENCIES: dict[str, list[int]] = {
    "USD": [100, 50],
}

# Пункты, которыми заполнить базу при первом запуске (через запятую).
# Например: DEFAULT_POINTS="Центр,Вокзал,Аэропорт"
DEFAULT_POINTS = [x.strip() for x in os.getenv("DEFAULT_POINTS", "").split(",") if x.strip()]


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def is_allowed(uid: int) -> bool:
    return uid in ADMIN_IDS or uid in WORKER_IDS
