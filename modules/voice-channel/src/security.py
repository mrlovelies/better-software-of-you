"""Webhook security helpers for the voice channel.

Pure functions that validate inbound webhook requests before the
server does anything with the payload. Kept isolated from server.py
so the verification logic can be unit-tested without spinning up
the FastAPI app or importing the database/tool layers.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Mapping

from fastapi import HTTPException

log = logging.getLogger("voice-channel.security")


def verify_vapi_signature(headers: Mapping[str, str], raw_body: bytes) -> None:
    """Verify the Vapi webhook signature on an inbound request.

    Vapi signs every webhook payload with HMAC-SHA256 over the raw
    request body, keyed by the shared secret configured as the
    "Server URL Secret" in the Vapi dashboard. The hex digest is sent
    as the ``x-vapi-signature`` header on every request.

    Verification is fail-closed: if ``VAPI_WEBHOOK_SECRET`` is unset
    the request is rejected with 503 unless the operator has explicitly
    set ``VOICE_CHANNEL_ALLOW_UNSIGNED=1`` (local-dev escape hatch
    only — never set this on the Razer).

    The raw body must be the exact bytes received on the wire. JSON
    re-serialization would change whitespace and invalidate the
    digest, so callers must read the body once, verify, and then
    parse from the same bytes.

    Args:
        headers: Case-insensitive header mapping from the inbound
            request (Starlette's ``Request.headers`` works directly).
        raw_body: The untouched request body as bytes.

    Raises:
        HTTPException(401): Header missing or digest doesn't match.
        HTTPException(503): Server not configured to verify (secret
            missing and the allow-unsigned escape hatch is off).
    """
    secret = os.environ.get("VAPI_WEBHOOK_SECRET", "")
    allow_unsigned = os.environ.get("VOICE_CHANNEL_ALLOW_UNSIGNED", "") == "1"

    if not secret:
        if allow_unsigned:
            log.warning(
                "Webhook signature verification is DISABLED "
                "(VOICE_CHANNEL_ALLOW_UNSIGNED=1). "
                "Do not run this way in production."
            )
            return
        raise HTTPException(
            status_code=503,
            detail="Webhook signature secret not configured (VAPI_WEBHOOK_SECRET)",
        )

    provided = (headers.get("x-vapi-signature") or "").strip()
    if not provided:
        raise HTTPException(
            status_code=401,
            detail="Missing x-vapi-signature header",
        )

    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, provided):
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook signature",
        )
