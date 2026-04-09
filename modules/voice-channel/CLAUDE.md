# Voice Channel — Architecture & Briefing

## What this is

A voice channel for SoY. When a customer dials a business's phone number, a voice agent answers, has a natural conversation grounded in SoY's data graph, checks calendar availability, books appointments, and confirms via SMS.

Built as a SoY extension module at `modules/voice-channel/`. Deployed on the Razer hub alongside the rest of SoY's services. Intended to become the "voice" channel in SoY's channel portfolio alongside web (speed-to-lead), email (gmail module), and chat (telegram-bot).

## The core decision: integrate, don't build

**v1 integrates with Vapi (hosted voice agent platform). It does NOT build a Pipecat pipeline from scratch.**

This decision was made after running a deep Pipecat research pass AND a risk review panel. The TL;DR of why:

1. **The differentiation is the data graph, not the voice pipeline.** Every strategic argument for voice-channel — SoY as integrated data graph, context-aware tool calls, cross-channel intelligence — is delivered by **what our tool implementations do**, not by whether the voice stack is Pipecat or Vapi. A `check_availability` tool that reads `v_meeting_prep` is the same 15 lines of Python either way.

2. **Timeline reality.** Building Pipecat from scratch is 10-14 weeks realistic for a solo dev with no Pipecat priors, running parallel to QPack. Integrating with Vapi is 3 weeks. The saved 7-11 weeks go into refining QPack, talking to potential users (which is how the ICP question actually gets answered), and VO work that pays the bills.

3. **Reliability.** A webhook endpoint is infrastructurally trivial on the Razer — same pattern as the MCP server. A real-time Pipecat pipeline co-located with ambient-research, Gmail sync, and QPack on residential internet is a credibility risk for a $49-249/mo paid product. "Vapi had an outage" is actionable; "the Razer flaked mid-call" is not.

4. **Reversibility.** Integrate-first is reversible in the right direction. If Vapi tops out (cost, quality, flexibility), we swap the voice layer behind the same tool implementations. Build-first is not reversible — 10 weeks of Pipecat work is a sunk cost if we need to pivot.

5. **The learning happens in parallel.** SoY's learning module gets a "Voice Integration Protocols" curriculum. Alex builds background knowledge (Pipecat, WebRTC, STT/TTS, turn-taking) while the Vapi-based product ships and gathers real usage data. When/if we decide to build, we walk in with real priors AND real customer data about what the tool layer actually needs.

## Architectural intent

**One SoY install = one business (tenant).** No runtime multi-tenancy logic. Isolation is the default because each person's SoY is their own local database, their own contacts, their own calendar, their own config.

- **Alex's install** → Alex's Vapi agent, Alex's phone number, Alex's calendar
- **Kerry's install** → Kerry's Vapi agent, Kerry's phone number, Kerry's calendar
- **Future client install** → their Vapi agent, their phone number, their calendar

Each install has its own Vapi API key and agent ID in `voice_config`. The webhook endpoint on the Razer knows which tenant it's serving because it's serving exactly one tenant.

**Voice-channel is a thin integration layer, not a pipeline.** It exposes a FastAPI webhook endpoint that Vapi calls when the voice agent needs to invoke a tool. The endpoint queries SoY's database, formats the response, and returns. That's the whole product.

**Voice-channel is a consumer of SoY data, not a provider.** It reads from and writes to existing SoY tables — `contacts`, `calendar_events`, `transcripts` — rather than introducing a parallel data world. The three new tables (`voice_calls`, `voice_config`, `voice_events`) are specific to voice channel operations, not business entities.

## The architecture

```
┌────────────────────────────────────────────────────────────┐
│                      TWILIO PSTN                           │
│              (caller dials Alex's number)                  │
└───────────────┬────────────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────────────┐
│                         VAPI                               │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Voice agent: Twilio transport, STT, LLM, TTS,       │  │
│  │  turn-taking, interruption handling, barge-in        │  │
│  │  System prompt + tool definitions + voice config     │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                 │
│                          │ Tool call                       │
│                          │ HTTPS POST                      │
│                          ▼                                 │
└────────────────────────────────────────────────────────────┘
                           │
                           │ Webhook
                           ▼
┌────────────────────────────────────────────────────────────┐
│          RAZER (soy-1) — Ubuntu 24.04                      │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  voice-channel FastAPI webhook                       │  │
│  │   ├── /webhook/tool      — tool call dispatch        │  │
│  │   ├── /webhook/call      — call lifecycle events     │  │
│  │   ├── /webhook/transcript — post-call transcript    │  │
│  │   └── /webhook/status    — health check             │  │
│  │                                                      │  │
│  │  Tool implementations:                               │  │
│  │   ├── get_business_hours()                           │  │
│  │   ├── list_services()                                │  │
│  │   ├── lookup_caller(phone)                           │  │
│  │   ├── check_availability(date_range)                 │  │
│  │   ├── book_appointment(...)                          │  │
│  │   ├── send_confirmation_sms(...)                     │  │
│  │   └── transfer_to_human(reason)                      │  │
│  │                                                      │  │
│  │  Exposed publicly via existing Tailscale Funnel      │  │
│  │  (no WebSocket needed — webhooks are HTTP POST)      │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │                                 │
│                          ▼                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  SoY Database (SQLite)                               │  │
│  │   ├── contacts, calendar_events, transcripts         │  │
│  │   ├── voice_calls, voice_config, voice_events        │  │
│  │   └── conversation-intelligence auto-processes       │  │
│  │       voice transcripts for commitments + coaching   │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
                          │
       ┌──────────────────┼──────────────────┐
       ▼                                     ▼
  ┌──────────────┐                   ┌──────────────────┐
  │  Telegram    │                   │  speed-to-lead-  │
  │  (owner      │                   │  gstack (SMS     │
  │  notif)      │                   │  confirmations)  │
  └──────────────┘                   └──────────────────┘
```

**Key observation: the Razer is hosting HTTP webhooks, not real-time audio.** This eliminates the entire category of risk that building Pipecat would have introduced — no WebSocket query-param stripping, no residential internet stability concerns for the voice stream, no audio framing bugs, no resource contention with other SoY modules during live calls, no Tailscale Funnel trap. A webhook endpoint is the same infrastructure pattern the MCP server and soy_server already use.

### Legion's role: deferred Custom LLM optimization

**v1 uses Vapi's built-in cloud LLM (GPT-4o-mini default). Legion is NOT used in v1.**

But Vapi supports a "Custom LLM" feature where the agent can be pointed at any OpenAI-compatible HTTPS endpoint. This is a real v1.5 optimization opportunity:

- Expose Legion's Ollama (`http://100.69.255.78:11434/v1`) publicly via Tailscale Funnel with API key auth
- Configure Vapi's agent to use Legion as its conversation LLM instead of OpenAI
- Each conversation turn hits Legion's gemma4:e4b or qwen3:30b-a3b instead of OpenAI's API

**Potential benefits if it works:**
- Per-call LLM cost drops from ~$0.01-0.03 to $0 (just electricity)
- Privacy story improves — LLM inference stays on Alex's hardware (Vapi still sees the call audio for STT/TTS, but the LLM isn't a third-party API)
- Hardware utilization (RTX 5080 paying for itself instead of idle 90% of the time)
- Lower latency on TTFT in the best case (Tailscale path may beat OpenAI's edge for this region)

**The risk that gates this:** small-model multi-turn tool call accuracy. The risk panel's most concrete concern about local LLMs for voice booking. GPT-4o-mini hits ~60-70% on multi-turn function calling benchmarks. Qwen3-30B-A3B is probably ~40-55%. **For a booking bot where the entire product value is "call the right tool with the right arguments and don't hallucinate the confirmation," that gap is a real product risk.**

**Decision criteria for v1.5 switchover:**
1. v1 is shipping and stable (cloud LLM baseline working, real calls accumulating)
2. Parallel A/B test: same call scripts, same tools, same prompts, swap only the LLM endpoint
3. Measure on real bookings: tool call success rate, hallucinated confirmation rate, failed bookings caught by post-call verification
4. Switch over only if Legion's local LLM hits 90%+ tool call success on the test set
5. Even after switch, keep cloud LLM as instant fallback if local quality degrades

This pairs naturally with the learning module curriculum — "Voice + Custom LLM Backends" becomes one of the topics, building Alex's priors while the v1 cloud version ships and gathers the data needed to evaluate the switch.

**Verdict for v1: cloud LLM via Vapi defaults. Legion deferred until v1.5 with explicit accuracy gating.**

## The tool surface

Defined in `src/tools.py`. Each tool is a Python function exposed via the FastAPI webhook. Vapi's LLM calls these via HTTPS POST during a live conversation.

| Tool | Reads/Writes | Purpose |
|------|-------------|---------|
| `get_business_hours()` | `voice_config` | Tell caller when business is open |
| `list_services()` | `voice_config` | Tell caller what the business does |
| `lookup_caller(phone)` | `v_contact_health`, `contacts` | Recognize returning callers, inject relationship context into the LLM's response ("Good to hear from you again, Jessica") |
| `check_availability(date_range, service)` | `v_meeting_prep`, `calendar_events` | Find free slots, check business hours, respect blackout windows |
| `book_appointment(date, time, caller_name, phone, service, notes)` | `calendar_events`, `contacts`, `contact_interactions`, `voice_calls` | **Create the booking. Match or create contact. Log interaction. Return structured success/fail.** |
| `send_confirmation_sms(phone, details)` | Via speed-to-lead-gstack | Follow up with booking details |
| `transfer_to_human(reason)` | Triggers telegram-bot + returns transfer TwiML hint to Vapi | Escalate to owner when AI can't help |
| `log_call_outcome(outcome, notes)` | `voice_calls` | Record call disposition at end |

### Safety invariant: no hallucinated bookings

The single most dangerous failure mode is the LLM saying "Great, you're booked for Tuesday at 2pm!" when the `book_appointment` tool actually failed. This is a day-1 requirement, not a polish item:

1. **System prompt rule (immutable):** "NEVER confirm a booking that has not been explicitly verified by a successful `book_appointment` tool response. If the tool returns an error or unknown status, say 'I'm having trouble reaching the calendar right now, let me have [owner name] call you back' and trigger `transfer_to_human`."

2. **Tool response schema:** Every tool returns `{ "status": "success" | "error" | "pending", "data": {...}, "message": "..." }`. The LLM is prompted to read status explicitly.

3. **Post-call verification:** After every call that claims to have booked, a background job re-queries the calendar to confirm the event exists. If it doesn't, immediate Telegram alert to Alex: "VOICE CHANNEL ALERT: booking for [caller] at [time] claimed success but not found in calendar."

4. **Voice_calls audit trail:** Every call's tool invocations logged to `voice_events` with full request/response. If we can't reconstruct what happened, we can't trust it.

## Integrations with existing SoY

**Reads from:**
- `v_contact_health` — look up caller by phone number, surface relationship context
- `v_meeting_prep` — check upcoming appointments, avoid double-booking
- `calendar_events` — availability checking
- `user_profile` — owner name, tenant defaults
- `contacts` — match caller phone numbers to existing contacts

**Writes to:**
- `calendar_events` — new appointments land here, Google Calendar sync pushes them upstream automatically
- `contacts` — new callers become contacts (first call = lead discovery)
- `transcripts` — full conversation transcript stored at call end, conversation-intelligence module auto-analyzes (commitments, talk ratios, coaching notes — all free because that module already exists)
- `contact_interactions` — each call logged as an interaction
- `activity_log` — call events for the timeline

**New tables (migration 058):**

| Table | Purpose |
|-------|---------|
| `voice_calls` | Per-call log: vapi_call_id, from_number, to_number, contact_id, started_at, ended_at, duration_s, outcome, booked_event_id, cost_cents, recording_url |
| `voice_config` | Per-install settings: vapi_api_key (encrypted), vapi_agent_id, phone_number, business_hours JSON, services JSON, greeting_template, owner_name, owner_telegram, owner_transfer_number, disclosure_script, max_call_duration_s, daily_cost_cap_cents |
| `voice_events` | Per-call event stream: call_id, timestamp, event_type (tool_call, tool_result, transcript_chunk, llm_response, error), data JSON |

**Sends via existing modules:**
- SMS confirmations → reuse speed-to-lead-gstack's Twilio + Resend setup (already has 10DLC registered)
- Owner notifications → telegram-bot ("Call from +1-416-555-... booked Tue 2pm" + "ALERT: booking verification failed")
- Admin dashboard → QPack questions in `qpacks/voice.qpack.json`

## Running location & infrastructure

**Razer only.** The webhook server runs on the Razer alongside SoY's other services:

- New Python venv at `~/voice-channel-env/` isolated from ambient-research and signal-harvester
- FastAPI process via systemd (Restart=always) on a dedicated port (TBD, avoiding conflicts)
- Exposed publicly via existing Tailscale Funnel (HTTPS webhooks, not WebSockets — no query-param stripping issue)
- Logs to `~/.local/share/software-of-you/voice-channel.log`
- Health check endpoint monitored by a watchdog

**What makes this safe that the Pipecat plan wasn't:**
- HTTP webhook endpoints don't need WebSocket query-param support → Tailscale Funnel works fine
- Not hosting real-time audio → no concern about residential internet stability affecting calls (Vapi holds the call, our server is just answering tool queries)
- Stateless request/response → no concern about Razer reboots killing in-flight calls (Vapi retries webhook on 5xx)
- Low resource footprint → no concern about resource contention with ambient-research or Gmail sync
- Same infra pattern as the MCP server → proven reliability

## Safety rails (day 1 requirements)

These are not polish items. They're required before any real call is routed:

1. **Hallucinated booking prevention** — described above in the tool surface section
2. **Post-call booking verification** — background job re-queries calendar after every claimed booking
3. **Hard call duration cap** — configurable in `voice_config.max_call_duration_s`, Vapi hangs up at cap
4. **Per-number rate limit** — max N calls/hour from the same caller phone number (prevent abuse loops)
5. **Daily cost cap** — `voice_config.daily_cost_cap_cents`, alerts at 50/80/100%, webhook starts returning "try later" responses when exceeded
6. **AI disclosure** — immutable first turn: "Hi, this is [business name]'s virtual assistant — how can I help you today?" Baked into the agent's greeting config, verified on every call's transcript
7. **Vapi fallback plan** — if the webhook is unreachable, Vapi's configured fallback is "I'm having trouble reaching our systems, please leave a voicemail and we'll call you back" + transcript logged
8. **Recording consent** — appended to the disclosure script, per PIPEDA requirements

## Compliance baseline (required before first paying customer)

_One-page document to be written before we charge anyone, including Kerry as tenant #2. Draft location: `docs/voice-channel-compliance.md`_

Must cover:
- AI disclosure script (exact wording)
- Recording consent language
- PIPEDA-compliant data handling policy (what we collect, where it lives, how long we keep it)
- Data processing agreement template (when Kerry's clients deploy this for THEIR customers)
- Retention policy (transcripts = permanent, recordings = 30 days max, voice_events = 90 days)
- Data subject rights (access, deletion, portability)

Not required for the Alex-as-sole-user phase. Required before Kerry becomes tenant #2.

## The channel portfolio picture

```
SoY local database
    │
    ├── Web channel      → speed-to-lead-gstack (forms, SMS response)
    ├── Email channel    → gmail module (sync, response queue)
    ├── Chat channel     → telegram-bot (async AFK access)
    └── Voice channel    → THIS MODULE (Vapi-powered live phone calls)
```

Every channel reads from the same `contacts`, `calendar_events`, and `transcripts`. Cross-channel intelligence is automatic — a phone caller becomes a contact who can be tracked by email sync, shows up in QPack questions, feeds into relationship health scores. No integration code needed. That's the SoY data graph differentiation, and it's independent of which voice stack we use.

## What NOT to do

- **Do not build a Pipecat pipeline.** v1 integrates with Vapi. Pipecat is deferred until (a) we have real usage data showing Vapi's limits, and (b) Alex has worked through the learning module voice integration curriculum.
- **Do not switch Vapi to Legion's local LLM in v1.** Custom LLM via Funnel is a v1.5 optimization gated on parallel A/B testing of tool call accuracy on real bookings. Cloud LLM is the baseline. Switching is data-driven, not aspirational.
- **Do not build multi-tenant isolation logic.** One install, one tenant. If you find yourself writing `WHERE tenant_id = ?`, stop.
- **Do not run voice-channel on Legion.** Stays on Razer. Legion's role is unchanged (ambient-research LLM inference, visual_qa vision evaluation).
- **Do not skip the safety rails.** Hallucinated booking prevention, hard caps, post-call verification are day-1 requirements. If they're not in the first working version, the first working version isn't done.
- **Do not expose voice-channel to paying customers before the compliance baseline exists.** Alex-as-user is fine. Kerry as tenant #2 needs the one-pager written first.
- **Do not couple voice-channel failure to other SoY services.** Separate venv, separate systemd unit, independent health check, dedicated logs. voice-channel going down should not affect ambient-research, Gmail sync, QPack, or anything else.
- **Do not hardcode Vapi.** Even though we're committing to Vapi for v1, the tool implementations should be decoupled from Vapi's specific request/response format. Vapi adapter is a thin layer; tool logic is framework-agnostic. This is the "reversibility" property that makes integrate-first safe.
- **Do not store Vapi API keys in plain text.** Use the same encrypted credential pattern as `service_credentials` (or whatever SoY's current credential storage is).

## Timeline: 3-week parallel sprint

### Week 1 — Infrastructure + first real call
- Vapi account, buy phone number, configure basic agent
- Scaffold `modules/voice-channel/` (done in this branch)
- Migration 058 with `voice_calls`, `voice_config`, `voice_events`
- FastAPI webhook endpoint on Razer, exposed via Tailscale Funnel
- Tool implementations (all 8), read-only first pass
- Hardcoded system prompt for Alex's use case
- **Checkpoint:** Call the Vapi number, hear AI greeting, ask "what services do you offer?", get the answer from SoY `voice_config`

### Week 2 — End-to-end booking flow
- `book_appointment` tool writes `calendar_events`, creates contact if new
- Google Calendar sync picks up the event automatically (existing behavior)
- SMS confirmation via speed-to-lead-gstack pattern
- Telegram notification to Alex on successful booking
- Call transcript logged to `transcripts`, conversation-intelligence auto-processes
- All safety rails active: hallucinated booking prevention, post-call verification, hard caps, rate limits
- **Checkpoint:** Real end-to-end booking lands in Alex's actual Google Calendar, SMS arrives, transcript in SoY, post-call verification passes

### Week 3 — Second tenant + compliance + demo-ready
- Second install for Kerry: different phone number, different `voice_config`, different calendar
- Compliance one-pager written
- QPack questions for admin surface: "Who called today?", "What did the AI book?", "Which calls did I need to handle myself?", "What's my voice channel cost this month?"
- Second-tenant test call to prove the "1 install = 1 business" pattern works
- Demo video of full flow for Kerry to review
- **Checkpoint:** Alex and Kerry both running voice-channel on their own SoY installs, accumulating real usage data

## Open questions before Week 1 starts

These need answers or decisions before the first commit of real code:

1. **Vapi account setup** — Alex creates account, we configure first agent in the dashboard, get API key
2. **Phone number** — Alex provisions a test number through Vapi (or brings his own Twilio number, Vapi supports both)
3. **Which port does the FastAPI webhook live on?** — need to check Razer's current port allocation and pick an unused one for the Funnel mapping
4. **Credentials storage** — does SoY already have an encrypted credentials pattern we should use? If so, use it. If not, simplest is `.env` file with `chmod 600` for v1
5. **Learning module curriculum entry** — write a "Voice Integration Protocols" curriculum for the learning module so Alex is building Pipecat priors in parallel while the Vapi version ships

## References

### Primary docs
- **Vapi docs:** [docs.vapi.ai](https://docs.vapi.ai)
- **Vapi webhook reference:** [docs.vapi.ai/server-url](https://docs.vapi.ai/server-url)
- **Vapi function/tool calling:** [docs.vapi.ai/tools](https://docs.vapi.ai/tools)
- **Vapi pricing:** [vapi.ai/pricing](https://vapi.ai/pricing) — ~$0.05/min base + model costs

### For the learning module (deferred Pipecat path)
- **Pipecat research report:** the full report generated in this session, saved to `docs/pipecat-research-2026-04-09.md` (TODO)
- **Pipecat GitHub:** [github.com/pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat)
- **Pipecat examples:** [github.com/pipecat-ai/pipecat-examples](https://github.com/pipecat-ai/pipecat-examples)
- **The "build it yourself" reference bot:** `pipecat-examples/twilio-chatbot/inbound/bot.py`

### Compliance references (to be added when the one-pager is written)
- PIPEDA text and guidance
- CRTC AI voice guidance (as of 2025)
- Twilio Voice AUP
- Vapi compliance docs
