"""Tests for Vapi webhook signature verification.

Covers the pure HMAC logic in ``verify_vapi_signature`` — both the
happy path (valid signature passes, whitespace is tolerated) and the
fail-closed paths (missing secret, missing header, invalid digest,
tampered body, no accidental bypass).

The function under test lives in ``src/security.py`` deliberately
isolated from ``server.py`` so it can be imported without pulling in
the full FastAPI app, database, tool registry, and persistence layer.

Run with:
    cd modules/voice-channel
    python3 -m pytest tests/test_webhook_signature.py -v

Or run directly (no pytest required):
    python3 modules/voice-channel/tests/test_webhook_signature.py
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
from pathlib import Path

# Make src/ importable when running this file from anywhere
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from fastapi import HTTPException  # noqa: E402
from security import verify_vapi_signature  # noqa: E402


SECRET = "test-secret-12345"
BODY = b'{"type": "tool-calls", "call": {"id": "abc"}}'


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class _Headers(dict):
    """Minimal stand-in for Starlette's case-insensitive Headers mapping."""

    def __init__(self, data=None):
        super().__init__()
        if data:
            for k, v in data.items():
                self[k.lower()] = v

    def get(self, key, default=None):
        return super().get(key.lower(), default)


def _clear_env():
    for k in ("VAPI_WEBHOOK_SECRET", "VOICE_CHANNEL_ALLOW_UNSIGNED"):
        os.environ.pop(k, None)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_signature_passes():
    _clear_env()
    os.environ["VAPI_WEBHOOK_SECRET"] = SECRET
    sig = _sign(SECRET, BODY)
    verify_vapi_signature(_Headers({"x-vapi-signature": sig}), BODY)  # no raise


def test_signature_with_surrounding_whitespace_is_stripped():
    _clear_env()
    os.environ["VAPI_WEBHOOK_SECRET"] = SECRET
    sig = _sign(SECRET, BODY)
    verify_vapi_signature(_Headers({"x-vapi-signature": f"  {sig}  "}), BODY)


# ---------------------------------------------------------------------------
# Failure modes — fail closed
# ---------------------------------------------------------------------------


def test_invalid_signature_raises_401():
    _clear_env()
    os.environ["VAPI_WEBHOOK_SECRET"] = SECRET
    try:
        verify_vapi_signature(_Headers({"x-vapi-signature": "deadbeef"}), BODY)
    except HTTPException as e:
        assert e.status_code == 401
        return
    raise AssertionError("expected HTTPException(401)")


def test_missing_signature_header_raises_401():
    _clear_env()
    os.environ["VAPI_WEBHOOK_SECRET"] = SECRET
    try:
        verify_vapi_signature(_Headers(), BODY)
    except HTTPException as e:
        assert e.status_code == 401
        return
    raise AssertionError("expected HTTPException(401)")


def test_empty_signature_header_raises_401():
    _clear_env()
    os.environ["VAPI_WEBHOOK_SECRET"] = SECRET
    try:
        verify_vapi_signature(_Headers({"x-vapi-signature": "   "}), BODY)
    except HTTPException as e:
        assert e.status_code == 401
        return
    raise AssertionError("expected HTTPException(401)")


def test_tampered_body_fails():
    _clear_env()
    os.environ["VAPI_WEBHOOK_SECRET"] = SECRET
    sig = _sign(SECRET, BODY)
    try:
        verify_vapi_signature(
            _Headers({"x-vapi-signature": sig}),
            BODY + b"-tampered",
        )
    except HTTPException as e:
        assert e.status_code == 401
        return
    raise AssertionError("expected HTTPException(401)")


def test_wrong_secret_fails():
    _clear_env()
    os.environ["VAPI_WEBHOOK_SECRET"] = SECRET
    sig_from_other_secret = _sign("some-other-secret", BODY)
    try:
        verify_vapi_signature(
            _Headers({"x-vapi-signature": sig_from_other_secret}),
            BODY,
        )
    except HTTPException as e:
        assert e.status_code == 401
        return
    raise AssertionError("expected HTTPException(401)")


# ---------------------------------------------------------------------------
# Server misconfiguration — 503
# ---------------------------------------------------------------------------


def test_unset_secret_raises_503():
    _clear_env()  # neither VAPI_WEBHOOK_SECRET nor VOICE_CHANNEL_ALLOW_UNSIGNED
    try:
        verify_vapi_signature(_Headers({"x-vapi-signature": "any"}), BODY)
    except HTTPException as e:
        assert e.status_code == 503
        return
    raise AssertionError("expected HTTPException(503)")


def test_empty_secret_raises_503():
    _clear_env()
    os.environ["VAPI_WEBHOOK_SECRET"] = ""
    try:
        verify_vapi_signature(_Headers({"x-vapi-signature": "any"}), BODY)
    except HTTPException as e:
        assert e.status_code == 503
        return
    raise AssertionError("expected HTTPException(503)")


# ---------------------------------------------------------------------------
# Escape hatch — explicit opt-in only
# ---------------------------------------------------------------------------


def test_allow_unsigned_bypasses_verification():
    _clear_env()
    os.environ["VOICE_CHANNEL_ALLOW_UNSIGNED"] = "1"
    # No secret, no header, no body signature — must pass because the
    # operator explicitly opted out.
    verify_vapi_signature(_Headers(), BODY)


def test_allow_unsigned_only_honored_when_literally_1():
    """Truthy values like 'true' or 'yes' should NOT bypass.

    Strict equality to "1" is the contract, so a typo or environment
    leakage doesn't accidentally disable the check in production.
    """
    _clear_env()
    os.environ["VOICE_CHANNEL_ALLOW_UNSIGNED"] = "true"
    try:
        verify_vapi_signature(_Headers(), BODY)
    except HTTPException as e:
        assert e.status_code == 503
        return
    raise AssertionError("expected HTTPException(503) — 'true' must not bypass")


# ---------------------------------------------------------------------------
# Standalone runner (no pytest required)
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import inspect

    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and inspect.isfunction(fn)
    ]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failures.append((name, str(e)))
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failures.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ERROR {name}: {type(e).__name__}: {e}")

    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} tests passed")
    if failures:
        sys.exit(1)
