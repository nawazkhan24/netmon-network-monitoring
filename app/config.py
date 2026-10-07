"""All settings are read from environment variables / the .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _num(name: str, default, cast=int):
    raw = os.getenv(name, "")
    # allow inline comments such as "15   # seconds"
    raw = raw.split("#")[0].strip()
    try:
        return cast(raw) if raw else default
    except ValueError:
        return default


def _text(name: str) -> str:
    return os.getenv(name, "").split("#")[0].strip()


DB_PATH = os.getenv("NETMON_DB", str(BASE_DIR / "netmon.db"))
STATIC_DIR = BASE_DIR / "static"

POLL_INTERVAL = _num("POLL_INTERVAL_SECONDS", 15)
PING_COUNT = _num("PING_COUNT", 4)
FAIL_THRESHOLD = _num("FAIL_THRESHOLD", 2)
RETENTION_DAYS = _num("RETENTION_DAYS", 7)

CPU_THRESHOLD = _num("CPU_THRESHOLD", 85, float)
RAM_THRESHOLD = _num("RAM_THRESHOLD", 90, float)
LOSS_THRESHOLD = _num("LOSS_THRESHOLD", 20, float)
LATENCY_THRESHOLD_MS = _num("LATENCY_THRESHOLD_MS", 200, float)

TELEGRAM_BOT_TOKEN = _text("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _text("TELEGRAM_CHAT_ID")

SMTP_HOST = _text("SMTP_HOST")
SMTP_PORT = _num("SMTP_PORT", 587)
SMTP_USER = _text("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
EMAIL_TO = _text("EMAIL_TO")
