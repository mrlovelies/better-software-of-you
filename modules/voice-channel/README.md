# Voice Channel

**Voice agent integration for SoY.** Answers phone calls via Vapi, books appointments against your SoY calendar, and captures every conversation into your data graph.

> **Read [`CLAUDE.md`](./CLAUDE.md) first.** It's the canonical design doc — architecture, decisions, safety invariants, and the timeline. This README is a quick orientation.

## What this is

When a customer calls your business phone number, a voice agent answers. The agent knows who they are (if they're already a contact in SoY), what services you offer, when you're available, and how to book appointments. After the call, the transcript lands in SoY's `transcripts` table where the conversation-intelligence module automatically extracts commitments, talk patterns, and coaching notes — same pipeline as your meeting transcripts.

It's the voice channel in SoY's channel portfolio:

| Channel | Module |
|---------|--------|
| Web (form submissions) | speed-to-lead-gstack |
| Email | gmail |
| Chat (async) | telegram-bot |
| **Voice (live calls)** | **this module** |

Every channel reads from the same `contacts`, `calendar_events`, and `transcripts`. Cross-channel intelligence is automatic.

## The architectural choice: integrate, don't build

**v1 integrates with [Vapi](https://vapi.ai)** — a hosted voice agent platform that handles Twilio transport, STT, LLM, TTS, turn-taking, and interruption. We don't build any of that. We expose a webhook endpoint that Vapi calls during the conversation, the endpoint queries SoY's database, and we return JSON.

This decision is documented in detail in [`CLAUDE.md`](./CLAUDE.md). The TL;DR:

- The differentiation is the SoY data graph, not the voice pipeline
- Building Pipecat from scratch is 10-14 weeks; integrating with Vapi is 3 weeks
- A webhook endpoint is infrastructurally trivial (same pattern as the MCP server)
- Reversibility: we can swap to Pipecat later when we have real usage data

## Per-install tenancy

**One SoY install = one business.** No runtime multi-tenancy logic. Each person runs their own SoY, answers their own phone number, owns their own data. Privacy is architectural.

## Quick start (after week 1 is built)

```bash
# 1. Install on the Razer
ssh mrlovelies@100.91.234.67 "bash ~/.software-of-you/modules/voice-channel/scripts/install-razer.sh"

# 2. Run migration to create the tables
sqlite3 ~/.local/share/software-of-you/soy.db < data/migrations/058_voice_channel.sql

# 3. Configure your install
sqlite3 ~/.local/share/software-of-you/soy.db <<SQL
UPDATE voice_config
SET vapi_api_key = '...',
    vapi_agent_id = '...',
    phone_number = '+14165551234',
    business_name = 'Alex Somerville VO',
    owner_name = 'Alex',
    owner_telegram_chat_id = '...',
    owner_transfer_number = '+14165550000',
    business_hours_json = '{"mon": ["09:00", "18:00"], "tue": ["09:00", "18:00"], "wed": ["09:00", "18:00"], "thu": ["09:00", "18:00"], "fri": ["09:00", "17:00"], "sat": null, "sun": null}',
    services_json = '[{"name": "VO consultation", "duration_min": 30, "price": 0}, {"name": "Voice session", "duration_min": 60, "price": 350}]',
    enabled = 1
WHERE id = 1;

UPDATE modules SET enabled = 1 WHERE name = 'voice-channel';
SQL

# 4. Start the webhook server
ssh mrlovelies@100.91.234.67 "systemctl --user start soy-voice-channel"

# 5. Configure Vapi to point at your Tailscale Funnel webhook URL
# (See docs/vapi-setup.md for the dashboard walkthrough)

# 6. Call your number, hear the AI say hi
```

## Project structure

```
modules/voice-channel/
├── CLAUDE.md             # Canonical design doc — read this first
├── README.md             # This file
├── manifest.json         # SoY module manifest
├── requirements.txt      # Python dependencies
├── src/
│   ├── server.py         # FastAPI webhook endpoint
│   ├── tools.py          # Tool implementations (check_availability, book_appointment, etc)
│   ├── config.py         # voice_config loader
│   ├── safety.py         # Safety rails: rate limits, cost caps, hallucination prevention
│   ├── verify.py         # Post-call booking verification
│   └── vapi_client.py    # Outbound Vapi API calls (agent setup, status queries)
├── migrations/
│   └── 001_voice_channel.sql  # Symlink/reference to data/migrations/058_voice_channel.sql
├── qpacks/
│   └── voice.qpack.json  # QPack questions for the admin dashboard
├── scripts/
│   ├── install-razer.sh  # Set up venv, install deps, install systemd unit
│   └── test-call.sh      # Trigger a test call via Vapi API
├── tests/
│   ├── test_tools.py     # Tool implementations against a test SoY DB
│   ├── test_safety.py    # Safety rail enforcement
│   └── test_verify.py    # Post-call verification logic
└── docs/
    ├── vapi-setup.md     # Walkthrough of the Vapi dashboard configuration
    └── compliance.md     # AI disclosure, recording consent, PIPEDA baseline
```

## Tool surface

| Tool | Purpose |
|------|---------|
| `get_business_hours()` | Tell caller when business is open |
| `list_services()` | Tell caller what the business does |
| `lookup_caller(phone)` | Recognize returning callers, inject relationship context |
| `check_availability(date_range, service)` | Find free calendar slots |
| `book_appointment(...)` | Create the booking. **Most critical tool.** |
| `send_confirmation_sms(phone, details)` | Follow up via SMS |
| `transfer_to_human(reason)` | Escalate to owner via Telegram + transfer call |
| `log_call_outcome(outcome, notes)` | Record disposition at end |

## Safety invariants (day-1 requirements)

These are documented in detail in [`CLAUDE.md`](./CLAUDE.md). Brief list:

1. **No hallucinated bookings** — System prompt rule + structured tool responses + post-call verification
2. **Hard call duration cap** — `voice_config.max_call_duration_s`
3. **Per-caller rate limit** — Prevent abuse loops
4. **Daily cost cap** — Per-install spending limit with Telegram alerts at 50/80/100%
5. **AI disclosure** — Mandatory first-turn statement, verified per call
6. **Vapi fallback path** — When the webhook is unreachable, callers get a graceful voicemail
7. **PIPEDA-compliant data handling** — Required before first paying customer

## Cost expectations

Roughly **$0.40 per 3-minute call** with Vapi defaults (Vapi platform fee + Twilio voice + cloud LLM + STT/TTS). At 100 calls/month per tenant: ~$40/month in platform costs against a $99-249/month price point.

If we eventually switch to Legion's local LLM via Vapi's Custom LLM feature (deferred to v1.5, gated on tool call accuracy testing), the LLM portion of the cost goes to ~$0.

## Status

**v0.1 — scaffolding complete, build in progress.** See [`CLAUDE.md`](./CLAUDE.md) for the 3-week sprint plan.

| Week | Goal | Status |
|------|------|--------|
| 1 | Webhook endpoint + tool implementations + first real call | Not started |
| 2 | End-to-end booking flow with safety rails | Not started |
| 3 | Second tenant + compliance baseline + demo-ready | Not started |
