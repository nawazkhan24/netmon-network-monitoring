"""Sending notifications: Telegram and/or Email. Both are optional (configured in .env)."""
import logging
import smtplib
from email.message import EmailMessage
from typing import Dict, Optional

import requests

from . import config

log = logging.getLogger("netmon.alerts")

ICONS = {"CRITICAL": "🔴", "WARNING": "🟡", "RESOLVED": "✅", "INFO": "ℹ️"}


def telegram_configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def email_configured() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD and config.EMAIL_TO)


def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        if r.ok:
            return True
        log.error("Telegram error %s: %s", r.status_code, r.text[:200])
    except requests.RequestException as exc:
        # never log the URL: it contains the bot token
        log.error("Telegram request failed: %s", exc.__class__.__name__)
    return False


def send_email(subject: str, body: str) -> bool:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_USER
    msg["To"] = config.EMAIL_TO
    msg.set_content(body)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception as exc:
        log.error("Email failed: %s", exc.__class__.__name__)
    return False


def notify(severity: str, title: str, message: str) -> Dict[str, Optional[bool]]:
    """Send to every configured channel. Returns {'telegram': True/False/None, 'email': ...}
    (None means the channel is not configured)."""
    log.info("ALERT [%s] %s", severity, message)
    text = f"{ICONS.get(severity, '')} {title}\n{message}"
    return {
        "telegram": send_telegram(text) if telegram_configured() else None,
        "email": send_email(f"[NetMon] {title}", message) if email_configured() else None,
    }
