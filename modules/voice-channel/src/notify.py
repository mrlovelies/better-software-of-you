"""Owner notification dispatcher for the voice channel.

When the voice agent does something the SoY operator should know about
(a booking landed, a booking failed verification, a caller was transferred,
etc.), this module routes the notification to the configured channels.

v1 only implements the telegram channel — direct urllib HTTP POST to the
Telegram Bot API, matching the pattern used by modules/ambient-research/
health.py:103-121. The bot's identity (BOT_TOKEN) is the SoY-wide bot,
but the chat_id is read per-tenant from voice_config.owner_telegram_chat_id
with TELEGRAM_OWNER_ID env var as fallback.

The function signature accepts a `channels` list so future commits can
add sms/email/slack branches without changing call sites in the booking
tool. Per Alex's stated requirement: "telegram fine for now, but it'll
need options for the user to configure their chosen method of notification."
The plumbing is set up; the dispatcher just only knows one channel today.

Future channels (deferred):
    - "sms": uses messaging_backend.send_sms() to text the owner's cell
    - "email": uses a future ResendBackend
    - "slack": webhook POST to the configured Slack URL
    - "discord": webhook POST to the configured Discord URL
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("voice-channel.notify")


def _get_owner_telegram_chat_id(db_path: Path) -> str | None:
    """Read the owner's Telegram chat ID, voice_config first then env fallback."""
    try:
        db = sqlite3.connect(db_path)
        try:
            row = db.execute(
                "SELECT owner_telegram_chat_id FROM voice_config WHERE id = 1"
            ).fetchone()
            if row and row[0]:
                return str(row[0])
        finally:
            db.close()
    except sqlite3.OperationalError as e:
        log.warning("voice_config lookup for telegram chat id failed: %s", e)

    env_id = os.environ.get("TELEGRAM_OWNER_ID", "").strip()
    return env_id or None


def _send_via_telegram(chat_id: str, text: str) -> bool:
    """POST a message to api.telegram.org/bot{TOKEN}/sendMessage.

    Uses Markdown parse mode (matches the ambient-research pattern). Falls
    back to plain text on retry if Markdown rendering fails (Telegram
    rejects unbalanced markdown characters).
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        log.warning("TELEGRAM_BOT_TOKEN not set — can't send owner notification")
        return False
    if not chat_id:
        log.warning("No telegram chat_id available — can't send owner notification")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def _post(payload: dict[str, Any]) -> bool:
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            log.warning("Telegram HTTP %s: %s", e.code, body[:200])
            return False
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram send failed: %s", e)
            return False

    # First try with Markdown
    if _post({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}):
        return True

    # Retry as plain text in case the body has unbalanced markdown
    return _post({"chat_id": chat_id, "text": text})


def notify_owner(
    db_path: Path,
    *,
    subject: str,
    body: str,
    channels: list[str] | None = None,
) -> dict[str, bool]:
    """Send a notification to the SoY owner across one or more channels.

    Args:
        db_path: Path to the SoY database (used to read voice_config).
        subject: Short title for the notification (rendered as bold first line).
        body: Main message body.
        channels: List of channel names to deliver on. v1 only implements
                  ["telegram"]. Future: "sms", "email", "slack", "discord".
                  Defaults to ["telegram"] if None.

    Returns:
        Dict mapping channel name to success bool, e.g. {"telegram": True}.
        The caller can decide how to handle partial failures.
    """
    if channels is None:
        channels = ["telegram"]

    results: dict[str, bool] = {}

    for channel in channels:
        if channel == "telegram":
            chat_id = _get_owner_telegram_chat_id(db_path)
            if not chat_id:
                log.warning("Telegram channel requested but no chat_id available")
                results["telegram"] = False
                continue
            text = f"*{subject}*\n\n{body}" if subject else body
            sent = _send_via_telegram(chat_id, text)
            results["telegram"] = sent
            if sent:
                log.info(
                    "Owner Telegram notification sent (chat_id=%s, %d chars)",
                    chat_id,
                    len(text),
                )
            else:
                log.warning("Owner Telegram notification FAILED (chat_id=%s)", chat_id)
        else:
            log.warning(
                "Notification channel '%s' not implemented in v1 — skipping",
                channel,
            )
            results[channel] = False

    return results
