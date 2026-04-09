"""Vapi server URL message types and parsing.

Vapi POSTs JSON to our webhook URL during a call. Every payload is wrapped
in `{"message": {...}}` and the inner message has a `type` field that
determines what we do with it.

Discovered message types from real Vapi traffic (2026-04-09 first call):

| Type                  | When it fires                                          |
|-----------------------|--------------------------------------------------------|
| status-update         | Call lifecycle (queued, ringing, in-progress, ended)   |
| speech-update         | User/bot started/stopped speaking                      |
| conversation-update   | Running conversation state with messages so far        |
| transcript            | Live transcription chunks (partial and final)          |
| function-call         | LLM wants to invoke one of our tools                   |
| tool-calls            | Newer format for LLM tool invocation                   |
| end-of-call-report    | Final summary with full transcript and analysis        |
| assistant.started     | Assistant has taken the call                           |
| analysis              | Vapi's auto-summary (success eval, etc)                |

This module provides:
- VapiMessage: a generic wrapper for parsing the inbound payload
- Specific helpers for the message types we care about
- Tool call response builder (the structured response format the LLM reads)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Top-level message wrapper
# ---------------------------------------------------------------------------


@dataclass
class VapiMessage:
    """A parsed Vapi webhook payload.

    Vapi wraps everything in {"message": {...}}. We pull the inner message
    out, identify the type, and provide convenient accessors for the fields
    we use in dispatch + persistence.
    """

    type: str
    raw: dict[str, Any]
    call: dict[str, Any]
    timestamp: int | None

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> VapiMessage:
        """Parse a raw webhook POST body into a VapiMessage."""
        msg = payload.get("message", payload)  # tolerate both shapes
        return cls(
            type=msg.get("type", "unknown"),
            raw=msg,
            call=msg.get("call", {}),
            timestamp=msg.get("timestamp"),
        )

    # --- Convenient accessors used across dispatch ---

    @property
    def vapi_call_id(self) -> str | None:
        return self.call.get("id")

    @property
    def assistant_id(self) -> str | None:
        return self.call.get("assistantId") or (
            self.call.get("assistant", {}).get("id") if isinstance(self.call.get("assistant"), dict) else None
        )

    @property
    def from_number(self) -> str | None:
        """E.164 phone number of the caller."""
        customer = self.call.get("customer") or {}
        return customer.get("number")

    @property
    def to_number(self) -> str | None:
        """E.164 phone number that was dialed (our Vapi number)."""
        phone = self.call.get("phoneNumber") or {}
        return phone.get("number")


# ---------------------------------------------------------------------------
# Tool / function call helpers
# ---------------------------------------------------------------------------


@dataclass
class ToolInvocation:
    """A single tool call extracted from a Vapi message.

    Vapi has historically used two formats:
    - `function-call`: single tool with `functionCall: {name, parameters}`
    - `tool-calls`: list with `toolCallList: [{id, function: {name, arguments}}]`

    Both are normalized to this dataclass for downstream dispatch.
    """

    name: str
    arguments: dict[str, Any]
    tool_call_id: str | None = None

    @classmethod
    def extract_all(cls, msg: VapiMessage) -> list[ToolInvocation]:
        """Pull all tool invocations from a Vapi message, regardless of format."""
        invocations: list[ToolInvocation] = []

        # Newer format: tool-calls
        if msg.type in ("tool-calls", "tool_calls"):
            for tc in msg.raw.get("toolCallList", []) or msg.raw.get("toolCalls", []):
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                # Vapi sometimes sends arguments as JSON string, sometimes as dict
                if isinstance(args, str):
                    import json
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                invocations.append(
                    cls(
                        name=fn.get("name", "unknown"),
                        arguments=args or {},
                        tool_call_id=tc.get("id"),
                    )
                )

        # Older format: function-call
        elif msg.type == "function-call":
            fc = msg.raw.get("functionCall", {})
            params = fc.get("parameters", {})
            if isinstance(params, str):
                import json
                try:
                    params = json.loads(params)
                except json.JSONDecodeError:
                    params = {}

            invocations.append(
                cls(
                    name=fc.get("name", "unknown"),
                    arguments=params or {},
                    tool_call_id=None,
                )
            )

        return invocations


@dataclass
class ToolResult:
    """Structured response returned to Vapi after a tool call.

    The status field is critical for the no-hallucinated-bookings safety
    invariant — the assistant's system prompt is told to NEVER confirm any
    booking unless status == 'success'.
    """

    status: str  # 'success' | 'error' | 'pending'
    data: dict[str, Any] | None = None
    message: str = ""
    tool_call_id: str | None = None

    def to_vapi_response(self) -> dict[str, Any]:
        """Format the result the way Vapi expects in the webhook response.

        For tool-calls format, Vapi wants:
            {"results": [{"toolCallId": "...", "result": "..."}]}

        For function-call format, Vapi wants:
            {"result": "..."}

        We return a structured payload Vapi can interpret either way.
        The `result` field is a string the LLM reads as the tool's output.
        """
        # Render the result as a human-readable string the LLM can use directly.
        # The status is included so the LLM can read it explicitly.
        import json
        result_str = json.dumps(
            {
                "status": self.status,
                "message": self.message,
                "data": self.data or {},
            }
        )

        if self.tool_call_id:
            return {
                "results": [
                    {
                        "toolCallId": self.tool_call_id,
                        "result": result_str,
                    }
                ]
            }
        return {"result": result_str}

    @classmethod
    def success(cls, message: str, data: dict[str, Any] | None = None, tool_call_id: str | None = None) -> ToolResult:
        return cls(status="success", message=message, data=data or {}, tool_call_id=tool_call_id)

    @classmethod
    def error(cls, message: str, tool_call_id: str | None = None) -> ToolResult:
        return cls(status="error", message=message, data={}, tool_call_id=tool_call_id)

    @classmethod
    def pending(cls, message: str, tool_call_id: str | None = None) -> ToolResult:
        return cls(status="pending", message=message, data={}, tool_call_id=tool_call_id)


# ---------------------------------------------------------------------------
# Status / lifecycle helpers
# ---------------------------------------------------------------------------


def get_call_status(msg: VapiMessage) -> str | None:
    """For status-update messages, extract the new call status.

    Common values: queued, ringing, in-progress, forwarding, ended
    """
    if msg.type != "status-update":
        return None
    return msg.raw.get("status")


def get_end_of_call_report(msg: VapiMessage) -> dict[str, Any] | None:
    """For end-of-call-report messages, extract the report payload.

    Contains: analysis (summary, successEvaluation), artifact (messages,
    transcript, recordingUrl), endedReason, durationSeconds, cost.
    """
    if msg.type != "end-of-call-report":
        return None
    return {
        "analysis": msg.raw.get("analysis", {}),
        "artifact": msg.raw.get("artifact", {}),
        "ended_reason": msg.raw.get("endedReason"),
        "duration_seconds": msg.raw.get("durationSeconds"),
        "cost": msg.raw.get("cost"),
        "cost_breakdown": msg.raw.get("costBreakdown"),
    }
