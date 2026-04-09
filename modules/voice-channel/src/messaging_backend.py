"""Messaging backend abstraction for the voice channel.

Sibling to calendar_backend.py — same architectural pattern, different
provider category. The voice-channel booking flow calls the abstraction;
only the backend implementation knows how to talk to a specific SMS
provider.

Backends in v1:
    - TwilioBackend: direct Twilio SDK calls. Reads credentials from env
      vars (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER).
      Pattern matches speed-to-lead-gstack/services/sms.py — same Twilio
      account/number is shared via env, so we inherit Kerry's existing
      10DLC registration without depending on her code.
    - LogOnlyBackend: graceful fallback when env vars aren't set yet.
      Logs the would-be SMS body to the voice-channel log instead of
      sending. Lets the booking flow ship before real SMS credentials
      are wired — Alex still gets a Telegram notification on every
      booking, the SMS confirmation is "decorative" until creds land.

Future backends (deferred):
    - ResendEmailBackend (Resend, when speed-to-lead's email path is wired)
    - BandwidthBackend, MessageMediaBackend (Canadian-friendly providers)
    - SignalBackend (E2E messenger for privacy-sensitive tenants)

Per-tenant credential storage in voice_config is a follow-up commit. v1
reads from env vars only — when future tenants need their own creds, the
factory teaches itself to read voice_config first and fall back to env.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("voice-channel.messaging_backend")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SendResult:
    """The structured result of an attempted message send.

    Same shape philosophy as BookingResult: explicit status field, raw
    provider response preserved for forensics, error message human-readable.

    Status values:
        success — message was actually sent to a real provider
        logged  — message was recorded but NOT sent (e.g., LogOnlyBackend
                  fallback when Twilio creds aren't set). Callers MUST NOT
                  tell the recipient "we sent you a message" when status
                  is 'logged' — that would be a verifiable lie. The booking
                  flow uses different confirmation language for this case.
        error   — send attempt failed
    """
    status: str  # "success" | "logged" | "error"
    provider_id: str | None = None  # e.g. Twilio message SID
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, provider_id: str | None = None, raw: dict[str, Any] | None = None) -> "SendResult":
        return cls(status="success", provider_id=provider_id, raw=raw or {})

    @classmethod
    def logged(cls, raw: dict[str, Any] | None = None) -> "SendResult":
        """Result for backends that record-but-don't-send (LogOnlyBackend).

        Distinct from 'success' so callers can speak honestly about whether
        the message actually went out.
        """
        return cls(status="logged", raw=raw or {})

    @classmethod
    def fail(cls, message: str, raw: dict[str, Any] | None = None) -> "SendResult":
        return cls(status="error", error=message, raw=raw or {})


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class MessagingBackend(Protocol):
    """The interface every messaging provider must implement."""

    def send_sms(self, to_phone: str, body: str) -> SendResult: ...


# ---------------------------------------------------------------------------
# TwilioBackend
# ---------------------------------------------------------------------------


class TwilioBackend:
    """MessagingBackend implementation using the Twilio Python SDK.

    Mirrors the pattern from speed-to-lead-gstack/services/sms.py:
    instantiate Client(sid, token), call messages.create(to, from_, body).

    The twilio package is imported lazily inside __init__ so voice-channel
    can run without it installed when LogOnlyBackend is being used.
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
    ) -> None:
        if not all([account_sid, auth_token, from_number]):
            raise ValueError(
                "TwilioBackend requires account_sid, auth_token, and from_number"
            )
        try:
            from twilio.rest import Client  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "TwilioBackend requires the 'twilio' package. "
                "Install it in voice-channel's venv: pip install 'twilio>=9.0.0'"
            ) from e

        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self._client = Client(account_sid, auth_token)

    def send_sms(self, to_phone: str, body: str) -> SendResult:
        if not to_phone:
            return SendResult.fail("to_phone is empty")
        if not body:
            return SendResult.fail("body is empty")

        try:
            msg = self._client.messages.create(
                to=to_phone,
                from_=self.from_number,
                body=body,
            )
            sid = getattr(msg, "sid", None)
            log.info("Twilio SMS sent: sid=%s to=%s len=%d", sid, to_phone, len(body))
            return SendResult.ok(provider_id=sid, raw={"sid": sid})
        except Exception as e:  # noqa: BLE001 — Twilio raises various exception types
            log.exception("Twilio SMS send failed to %s", to_phone)
            return SendResult.fail(str(e))


# ---------------------------------------------------------------------------
# LogOnlyBackend
# ---------------------------------------------------------------------------


class LogOnlyBackend:
    """Graceful fallback when no real messaging provider is configured.

    Logs the would-be SMS body to the voice-channel logger and returns a
    success result. Lets the booking flow stay end-to-end testable before
    Twilio credentials are wired into the systemd unit's environment.

    The owner still gets notified via the separate Telegram path in
    notify.py, so they aren't unaware of bookings — they just don't get
    SMS confirmations to forward to the caller until creds are set.
    """

    def send_sms(self, to_phone: str, body: str) -> SendResult:
        log.warning(
            "[LogOnlyBackend] Would send SMS to %s: %s",
            to_phone,
            body.replace("\n", " | "),
        )
        return SendResult.logged(
            raw={"simulated": True, "to": to_phone, "body": body},
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_messaging_backend() -> MessagingBackend:
    """Return the configured messaging backend.

    Reads Twilio credentials from environment variables. If all three are
    set, returns a TwilioBackend. Otherwise falls back to LogOnlyBackend
    (graceful degradation — booking flow still works, SMS bodies are
    logged instead of sent).

    Future: read per-tenant credentials from voice_config first, fall
    back to env vars second. Adding a new backend type means teaching
    the factory to dispatch on voice_config.messaging_backend ('twilio',
    'bandwidth', etc).
    """
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "").strip()

    if sid and token and from_number:
        try:
            return TwilioBackend(sid, token, from_number)
        except Exception:
            log.exception(
                "Twilio env vars are set but TwilioBackend init failed — "
                "falling back to LogOnlyBackend"
            )
            return LogOnlyBackend()

    missing = [
        name
        for name, val in [
            ("TWILIO_ACCOUNT_SID", sid),
            ("TWILIO_AUTH_TOKEN", token),
            ("TWILIO_FROM_NUMBER", from_number),
        ]
        if not val
    ]
    log.warning(
        "Twilio env vars not fully set (%s missing) — falling back to LogOnlyBackend. "
        "SMS confirmations will be logged instead of sent until credentials are wired.",
        ", ".join(missing),
    )
    return LogOnlyBackend()
