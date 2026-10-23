from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()


def _b(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in {"1", "true", "yes", "y", "on"}


def _i(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _ids(key: str, default: str = "") -> list[int]:
    raw = os.getenv(key, default).replace(" ", "")
    return [int(x) for x in raw.split(",") if x.lstrip("-").isdigit()]


BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS: list[int] = _ids("ADMIN_IDS")

FREE_MODE: bool = _b("FREE_MODE", True)
NOTIFY_ADMIN_ON_JOIN: bool = _b("NOTIFY_ADMIN_ON_JOIN", True)

MAX_BINS_PER_JOB: int = _i("MAX_BINS_PER_JOB", 5000)
DEFAULT_CONCURRENCY: int = _i("DEFAULT_CONCURRENCY", 25)
MAX_CONCURRENCY: int = _i("MAX_CONCURRENCY", 80)
REQUEST_TIMEOUT: float = float(os.getenv("REQUEST_TIMEOUT", "12"))
MAX_JOBS_PER_USER: int = _i("MAX_JOBS_PER_USER", 1)
PROGRESS_INTERVAL: float = float(os.getenv("PROGRESS_INTERVAL", "2.5"))
CACHE_TTL_HOURS: int = _i("CACHE_TTL_HOURS", 24)
ONLY_LIVE_IN_RESULT: bool = _b("ONLY_LIVE_IN_RESULT", True)

CUSTOM_BIN_URL: str = os.getenv("CUSTOM_BIN_URL", "").strip()
CUSTOM_BIN_HEADERS: str = os.getenv("CUSTOM_BIN_HEADERS", "").strip()

GLOBAL_PROXIES: list[str] = [
    p.strip()
    for p in os.getenv("GLOBAL_PROXIES", "").replace("\n", ",").split(",")
    if p.strip()
]
ADMIN_ONLY_PROXIES: bool = _b("ADMIN_ONLY_PROXIES", True)
PROXY_TEST_URL: str = os.getenv("PROXY_TEST_URL", "https://api.ipify.org?format=json")

DB_PATH: str = os.getenv("DB_PATH", "binbot.sqlite3")
BOT_NAME: str = os.getenv("BOT_NAME", "BIN SENTINEL")
SUPPORT_HANDLE: str = os.getenv("SUPPORT_HANDLE", "")

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is missing. Copy .env.example to .env and fill it in.")
if not ADMIN_IDS:
    raise SystemExit("ADMIN_IDS is empty. Put your Telegram numeric ID in .env.")