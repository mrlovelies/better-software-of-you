#!/usr/bin/env python3
"""Voice Channel — FastAPI webhook server.

Receives tool calls and call lifecycle events from Vapi during live
conversations, dispatches to the tool implementations, and returns
structured responses.

Run on the Razer in the dedicated venv:
    source ~/voice-channel-env/bin/activate
    python3 -m voice_channel.server

Or via systemd:
    systemctl --user start soy-voice-channel

Endpoints:
    GET  /                       — health check (returns version + status)
    POST /webhook/tool           — Vapi tool call dispatch
    POST /webhook/call           — Vapi call lifecycle events (started, ended)
    POST /webhook/transcript     — Post-call transcript delivery
    GET  /webhook/status         — operational health for monitoring
"""

import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = Path(
    os.environ.get(
        "SOY_DB_PATH",
        str(Path.home() / ".local" / "share" / "software-of-you" / "soy.db"),
    )
)
LOG_PATH = Path(
    os.environ.get(
        "VOICE_CHANNEL_LOG",
        str(Path.home() / ".local" / "share" / "software-of-you" / "voice-channel.log"),
    )
)
PORT = int(os.environ.get("VOICE_CHANNEL_PORT", "8790"))
HOST = os.environ.get("VOICE_CHANNEL_HOST", "0.0.0.0")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH) if LOG_PATH.parent.exists() else logging.NullHandler(),
    ],
)
log = logging.getLogger("voice-channel")


# ---------------------------------------------------------------------------
# Database access
# ---------------------------------------------------------------------------


def get_db() -> sqlite3.Connection:
    """Open a connection to the SoY database."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def get_voice_config() -> dict[str, Any] | None:
    """Load the per-install voice config (singleton row)."""
    db = get_db()
    try:
        row = db.execute("SELECT * FROM voice_config WHERE id = 1").fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError as e:
        log.error("voice_config table not found — has migration 058 run? %s", e)
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler — startup and shutdown."""
    log.info("Voice Channel webhook server starting on %s:%d", HOST, PORT)
    log.info("Database: %s", DB_PATH)

    if not DB_PATH.exists():
        log.error("Database does not exist at %s — bootstrap SoY first", DB_PATH)
    else:
        config = get_voice_config()
        if config is None:
            log.warning(
                "voice_config not configured. Run migration 058 and populate the row."
            )
        else:
            log.info(
                "Loaded config for: %s (phone: %s)",
                config.get("business_name"),
                config.get("phone_number") or "(not set)",
            )

    yield

    log.info("Voice Channel webhook server shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SoY Voice Channel",
    description="Webhook integration layer between Vapi and SoY's data graph",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Models (placeholder — will be expanded as we wire up Vapi's actual schema)
# ---------------------------------------------------------------------------


class ToolCallRequest(BaseModel):
    """Vapi tool call payload (skeleton — refine against actual Vapi docs in week 1)."""

    tool_name: str = Field(..., description="The name of the tool being invoked")
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = Field(None, description="Vapi call ID for correlation")


class ToolCallResponse(BaseModel):
    """Structured response returned to Vapi.

    The status field is critical for the no-hallucinated-bookings safety
    invariant — the LLM is prompted to never confirm a booking unless
    status == 'success'.
    """

    status: str = Field(..., description="'success' | 'error' | 'pending'")
    data: dict[str, Any] = Field(default_factory=dict)
    message: str = Field("", description="Human-readable result for the LLM")


# ---------------------------------------------------------------------------
# Routes — placeholders. Real implementations land in week 1.
# ---------------------------------------------------------------------------


@app.get("/")
async def root():
    """Simple health check identifying the service."""
    config = get_voice_config()
    return {
        "service": "soy-voice-channel",
        "version": "0.1.0",
        "status": "alive",
        "configured": config is not None and bool(config.get("vapi_api_key")),
        "business_name": config.get("business_name") if config else None,
    }


@app.get("/webhook/status")
async def webhook_status():
    """Operational health check for monitoring (watchdog hits this)."""
    db_ok = DB_PATH.exists()
    config = get_voice_config()
    config_ok = config is not None
    enabled = bool(config.get("enabled")) if config else False

    healthy = db_ok and config_ok and enabled

    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "healthy": healthy,
            "checks": {
                "database": db_ok,
                "config_loaded": config_ok,
                "module_enabled": enabled,
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )


@app.post("/webhook/tool")
async def webhook_tool(request: Request):
    """Vapi tool call dispatch.

    Vapi calls this endpoint when the agent's LLM decides to invoke a tool
    during a live conversation. We dispatch to the appropriate tool
    implementation, query SoY's database, and return a structured response.

    PLACEHOLDER — week 1 work. Real dispatch logic will live in src/tools.py
    and be called from here based on the tool_name in the request.
    """
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    log.info("Tool call received: %s", payload)

    # PLACEHOLDER — return a dummy response so the endpoint is testable
    return ToolCallResponse(
        status="error",
        message="Tool dispatch not implemented yet — see src/tools.py",
        data={"received": payload},
    )


@app.post("/webhook/call")
async def webhook_call(request: Request):
    """Vapi call lifecycle events (started, answered, ended).

    PLACEHOLDER — week 1 work. Will create/update voice_calls rows
    and trigger post-call processing on call end.
    """
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    log.info("Call event received: %s", payload)

    return {"status": "received", "implementation": "pending"}


@app.post("/webhook/transcript")
async def webhook_transcript(request: Request):
    """Post-call transcript delivery from Vapi.

    PLACEHOLDER — week 2 work. Will write the transcript into SoY's
    transcripts table where conversation-intelligence picks it up
    automatically for commitment extraction and coaching analysis.
    """
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    log.info("Transcript received for call: %s", payload.get("call_id"))

    return {"status": "received", "implementation": "pending"}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main():
    """Run the webhook server via uvicorn."""
    import uvicorn

    log.info("Starting voice-channel webhook on %s:%d", HOST, PORT)
    uvicorn.run(
        "src.server:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
