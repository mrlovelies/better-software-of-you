#!/usr/bin/env python3
"""Voice Channel — FastAPI webhook server.

Receives tool calls and call lifecycle events from Vapi during live
conversations, dispatches to the tool implementations, and persists
data to SoY's database.

Run on the Razer in the dedicated venv:
    source ~/voice-channel-env/bin/activate
    python3 -m src.server

Or via systemd:
    systemctl --user start soy-voice-channel

Endpoints:
    GET  /                       — health check (returns version + status)
    POST /webhook/tool           — Vapi tool call dispatch (all message types)
    POST /webhook/call           — Vapi call lifecycle events (legacy alias)
    POST /webhook/transcript     — Post-call transcript delivery (legacy alias)
    GET  /webhook/status         — operational health for monitoring
"""

from __future__ import annotations

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

from .vapi_messages import (
    ToolInvocation,
    ToolResult,
    VapiMessage,
    get_call_status,
    get_end_of_call_report,
)
from .tools import dispatch_tool, lookup_caller
from .persistence import (
    find_or_create_contact_by_phone,
    log_contact_interaction,
    log_voice_event,
    update_voice_call_outcome,
    upsert_voice_call,
    write_voice_transcript,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = Path(
    os.environ.get(
        "SOY_DB_PATH",
        str(Path.home() / ".local" / "share" / "software-of-you" / "soy.db"),
    )
)
PORT = int(os.environ.get("VOICE_CHANNEL_PORT", "8790"))
HOST = os.environ.get("VOICE_CHANNEL_HOST", "0.0.0.0")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],  # systemd handles file output via StandardOutput=append:
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
    log.info("Voice Channel webhook server starting on %s:%d", HOST, PORT)
    log.info("Database: %s", DB_PATH)

    if not DB_PATH.exists():
        log.error("Database does not exist at %s — bootstrap SoY first", DB_PATH)
    else:
        config = get_voice_config()
        if config is None:
            log.warning("voice_config not configured. Run migration 058 and populate the row.")
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
    version="0.2.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Dispatch — handles all Vapi message types on /webhook/tool
# ---------------------------------------------------------------------------


DEFAULT_ASSISTANT_ID = "2e6a3d72-baa9-46ff-8009-fc400cffb09f"


async def handle_vapi_message(msg: VapiMessage) -> dict[str, Any]:
    """Dispatch a parsed Vapi message based on its type.

    Returns a dict that will be JSON-serialized as the webhook response.
    For tool calls this MUST include the tool result in the format Vapi
    expects. For other message types the response is acknowledged but
    doesn't carry tool results.
    """
    msg_type = msg.type
    vapi_call_id = msg.vapi_call_id

    log.info(
        "Vapi message: type=%s call=%s from=%s to=%s",
        msg_type,
        vapi_call_id[:8] if vapi_call_id else "?",
        msg.from_number,
        msg.to_number,
    )

    # --- Pre-call assistant request (HAPPENS DURING PHONE RING) ---
    # Vapi hits this BEFORE connecting the caller. We run lookup_caller
    # synchronously, build a personalized first message, and return
    # assistantId + override. Latency hides behind the phone ring pattern
    # — caller hears normal ringing while we do the lookup, then hears
    # a personalized greeting on pickup. Zero perceived dead air.

    if msg_type in ("assistant-request", "assistant_request"):
        return _handle_assistant_request(msg)

    # --- Tool invocations ---

    if msg_type in ("tool-calls", "tool_calls", "function-call"):
        invocations = ToolInvocation.extract_all(msg)
        if not invocations:
            log.warning("Tool message with no invocations: %s", msg_type)
            return {"error": "No tool invocations found in message"}

        # Persist the tool call event
        call_row_id = _ensure_call_row(msg)

        # Build call metadata so tools can access caller context without
        # requiring the LLM to pass it as an argument every time
        call_meta = {
            "from_number": msg.from_number,
            "to_number": msg.to_number,
            "vapi_call_id": vapi_call_id,
            "assistant_id": msg.assistant_id,
        }

        results: list[dict[str, Any]] = []
        for inv in invocations:
            # Log the invocation
            log_voice_event(
                DB_PATH,
                call_id=call_row_id,
                vapi_call_id=vapi_call_id,
                event_type="tool_call",
                tool_name=inv.name,
                data={"arguments": inv.arguments, "tool_call_id": inv.tool_call_id},
            )

            # Dispatch to the tool implementation
            result = dispatch_tool(DB_PATH, inv, call_meta)

            # Log the result
            log_voice_event(
                DB_PATH,
                call_id=call_row_id,
                vapi_call_id=vapi_call_id,
                event_type="tool_result",
                tool_name=inv.name,
                data={
                    "status": result.status,
                    "message": result.message,
                    "data": result.data,
                    "tool_call_id": result.tool_call_id,
                },
            )
            results.append(result.to_vapi_response())

        # Merge multiple results if necessary (tool-calls format returns a list)
        if len(results) == 1:
            return results[0]
        # For multiple tool_calls, merge the results arrays
        merged_results = []
        for r in results:
            if "results" in r:
                merged_results.extend(r["results"])
            elif "result" in r:
                merged_results.append({"result": r["result"]})
        return {"results": merged_results}

    # --- Status update (call lifecycle) ---

    if msg_type == "status-update":
        call_status = get_call_status(msg) or "unknown"
        _ensure_call_row(msg)
        log_voice_event(
            DB_PATH,
            call_id=None,
            vapi_call_id=vapi_call_id,
            event_type="status_update",
            data={"status": call_status},
        )
        log.info("Call %s status: %s", vapi_call_id[:8] if vapi_call_id else "?", call_status)
        return {"acknowledged": True}

    # --- End of call report ---

    if msg_type == "end-of-call-report":
        report = get_end_of_call_report(msg) or {}
        _ensure_call_row(msg)
        _handle_end_of_call(msg, report)
        return {"acknowledged": True}

    # --- Conversation / speech / transcript / analysis updates (log only) ---

    if msg_type in ("conversation-update", "speech-update", "transcript", "analysis"):
        _ensure_call_row(msg)
        log_voice_event(
            DB_PATH,
            call_id=None,
            vapi_call_id=vapi_call_id,
            event_type=msg_type.replace("-", "_"),
            data={"summary": str(msg.raw)[:500]},  # store truncated summary, not full payload
        )
        return {"acknowledged": True}

    # --- Assistant lifecycle ---

    if msg_type.startswith("assistant."):
        _ensure_call_row(msg)
        log_voice_event(
            DB_PATH,
            call_id=None,
            vapi_call_id=vapi_call_id,
            event_type=msg_type.replace(".", "_").replace("-", "_"),
            data={},
        )
        return {"acknowledged": True}

    # --- Anything else: log and acknowledge ---

    log.info("Unhandled Vapi message type: %s", msg_type)
    return {"acknowledged": True, "handled": False, "type": msg_type}


def _handle_assistant_request(msg: VapiMessage) -> dict[str, Any]:
    """Handle Vapi's pre-call assistant-request webhook.

    This is the UX upgrade: Vapi hits us BEFORE connecting the caller,
    passing the caller's phone number. We synchronously look up the
    caller in SoY's data graph, build a personalized first message,
    and return an assistant override. Vapi then answers the call with
    a greeting that already knows who the caller is — no dead air
    after pickup.

    Vapi expects a response shape like:
        {
          "assistantId": "<uuid>",
          "assistantOverrides": {
            "firstMessage": "Hi James, thanks for calling..."
          }
        }

    OR (for tool and general server message routes, but here for
    assistant-request specifically):
        {
          "assistant": { ... full inline assistant config ... }
        }

    We use the first form — keep the static assistant as the baseline
    and only override the first message per call.
    """
    from_number = msg.from_number or ""
    log.info("assistant-request for caller %s", from_number or "(unknown)")

    # Run lookup_caller synchronously. We invoke it directly rather than
    # going through the dispatcher so we can handle it as a pre-call op
    # with no tool_call_id.
    try:
        lookup_result = lookup_caller(
            DB_PATH,
            args={},
            tool_call_id=None,
            call_meta={"from_number": from_number},
        )
    except Exception as e:
        log.exception("lookup_caller failed during assistant-request: %s", e)
        lookup_result = None

    # Log the assistant-request event for audit even though there's no call row yet
    if vapi_call_id := msg.vapi_call_id:
        try:
            _ensure_call_row(msg)
            log_voice_event(
                DB_PATH,
                call_id=None,
                vapi_call_id=vapi_call_id,
                event_type="assistant_request",
                data={
                    "from_number": from_number,
                    "lookup_status": lookup_result.status if lookup_result else "error",
                    "lookup_message": (lookup_result.message if lookup_result else "lookup failed")[:200],
                },
            )
        except Exception:
            log.exception("Could not log assistant_request event")

    # Build the personalized first message
    first_message = _build_personalized_first_message(lookup_result)

    log.info("assistant-request -> firstMessage: %s", first_message[:100])

    return {
        "assistantId": DEFAULT_ASSISTANT_ID,
        "assistantOverrides": {
            "firstMessage": first_message,
        },
    }


def _build_personalized_first_message(lookup_result: ToolResult | None) -> str:
    """Build the first message Vapi will speak on pickup, based on the lookup.

    AI disclosure ("virtual assistant") must appear in every variant — that's
    the PIPEDA + TCPA safety invariant. Even the owner self-test branch
    keeps the disclosure phrase so the transcript audit trail remains
    consistent across every call.

    Variants:
        - Owner self-call: "Hi Alex, this is your virtual assistant — owner test line is up, what are we checking?"
        - Known rich contact: "Hi {name}, this is Alex Somerville's virtual assistant — how can I help today?"
        - Placeholder (called before, no name yet): "Hi, this is Alex Somerville's virtual assistant. Good to hear from you again — can I grab your name?"
        - Unknown: "Hi, this is Alex Somerville's virtual assistant. How can I help you today?"
        - Lookup failed / no result: same as unknown (safe default)
    """
    # Default / safe fallback
    default_msg = "Hi, this is Alex Somerville's virtual assistant. How can I help you today?"

    if lookup_result is None or lookup_result.status != "success":
        return default_msg

    data = lookup_result.data or {}

    # Owner self-call: the SoY operator is calling their own line for testing
    # or admin. Greet them by name and skip the customer-greeting flow.
    # Disclosure ("virtual assistant") still appears for transcript audit
    # consistency.
    if data.get("owner_call"):
        owner_name = data.get("name") or "there"
        owner_first = owner_name.split()[0] if owner_name else "there"
        return (
            f"Hi {owner_first}, this is your virtual assistant. "
            "Owner test line is up — what are we checking?"
        )

    # Unknown caller
    if not data.get("known"):
        return default_msg

    # Known contact but still a placeholder (auto-created from earlier call,
    # no real name yet)
    if data.get("placeholder"):
        return (
            "Hi, this is Alex Somerville's virtual assistant. "
            "Good to hear from you again — can I grab your name?"
        )

    # Rich contact with a real name
    name = data.get("name") or "there"
    # Use first name only for the greeting — "Hi James" feels warmer than "Hi James Andrews"
    first_name = name.split()[0] if name else "there"

    return (
        f"Hi {first_name}, this is Alex Somerville's virtual assistant. "
        "How can I help you today?"
    )


def _ensure_call_row(msg: VapiMessage) -> int | None:
    """Ensure there's a voice_calls row for this Vapi call. Returns its id."""
    if not msg.vapi_call_id:
        return None
    try:
        return upsert_voice_call(
            DB_PATH,
            vapi_call_id=msg.vapi_call_id,
            from_number=msg.from_number,
            to_number=msg.to_number,
            assistant_id=msg.assistant_id,
        )
    except Exception as e:
        log.error("Failed to upsert voice_calls row: %s", e)
        return None


def _handle_end_of_call(msg: VapiMessage, report: dict[str, Any]) -> None:
    """Process an end-of-call-report: persist transcript, link contact, mark outcome."""
    vapi_call_id = msg.vapi_call_id
    if not vapi_call_id:
        return

    duration = report.get("duration_seconds")
    analysis = report.get("analysis") or {}
    artifact = report.get("artifact") or {}
    summary = analysis.get("summary")
    success_eval = analysis.get("successEvaluation")
    ended_reason = report.get("ended_reason")
    cost = report.get("cost")
    cost_cents = int(round(cost * 100)) if isinstance(cost, (int, float)) else None
    cost_breakdown = report.get("cost_breakdown")

    # Match or create a contact for the caller
    contact_id = None
    if msg.from_number:
        contact_id = find_or_create_contact_by_phone(DB_PATH, msg.from_number)

    # Write the transcript for conversation-intelligence to pick up
    transcript_id = write_voice_transcript(
        DB_PATH,
        vapi_call_id=vapi_call_id,
        contact_id=contact_id,
        artifact=artifact,
        duration_seconds=duration,
    )

    # Determine outcome
    if success_eval is True or success_eval == "true":
        outcome = "booked"
    elif success_eval is False or success_eval == "false":
        outcome = "no_booking"
    else:
        outcome = ended_reason or "completed"

    # Patch the voice_calls row with everything we know
    update_voice_call_outcome(
        DB_PATH,
        vapi_call_id=vapi_call_id,
        outcome=outcome,
        outcome_details=summary,
        duration_s=int(duration) if duration else None,
        ended_at=datetime.utcnow().isoformat() + "Z",
        cost_cents=cost_cents,
        cost_breakdown=cost_breakdown if isinstance(cost_breakdown, dict) else None,
        transcript_id=transcript_id,
        recording_url=(artifact.get("recording") or {}).get("url") if isinstance(artifact.get("recording"), dict) else artifact.get("recordingUrl"),
        contact_id=contact_id,
    )

    # Log the interaction on the contact's timeline
    if contact_id:
        log_contact_interaction(
            DB_PATH,
            contact_id=contact_id,
            vapi_call_id=vapi_call_id,
            duration_s=int(duration) if duration else None,
            outcome=outcome,
            summary=summary,
        )

    log.info(
        "End of call: %s — outcome=%s duration=%ss contact=%s transcript=%s",
        vapi_call_id[:8],
        outcome,
        duration,
        contact_id,
        transcript_id,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def root():
    """Simple health check identifying the service."""
    config = get_voice_config()
    return {
        "service": "soy-voice-channel",
        "version": "0.2.0",
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
    """Primary Vapi webhook endpoint — handles all message types.

    Vapi sends a variety of message types to the server URL:
    - tool-calls / function-call: LLM wants to invoke a tool
    - status-update: call lifecycle transitions
    - speech-update, transcript, conversation-update: streaming updates
    - end-of-call-report: final summary with transcript and analysis
    - assistant.started, analysis: lifecycle / metadata
    """
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    msg = VapiMessage.parse(payload)
    try:
        return await handle_vapi_message(msg)
    except Exception as e:
        log.exception("Error handling Vapi message of type %s", msg.type)
        return {"error": str(e), "type": msg.type}


@app.post("/webhook/call")
async def webhook_call(request: Request):
    """Legacy alias — forwards to the main dispatcher.

    Retained in case Vapi or our configuration uses a separate URL for
    call lifecycle events. Currently Vapi routes everything to the main
    serverUrl, but this endpoint exists for flexibility.
    """
    return await webhook_tool(request)


@app.post("/webhook/transcript")
async def webhook_transcript(request: Request):
    """Legacy alias — forwards to the main dispatcher."""
    return await webhook_tool(request)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main():
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
