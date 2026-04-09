# Voice Channel — Compliance Baseline

**Status:** Draft v1, landed 2026-04-09.
**Scope:** Applies to the voice-channel module when exposed to any caller who is not the operator (Alex Somerville) themselves. Alex-as-sole-user development does not require this baseline. Everything below must be in place before Kerry becomes tenant #2, and before any paying customer is charged.
**Jurisdiction:** Canada (PIPEDA), specifically Ontario where the operator is based. TCPA notes apply to any outbound contact that crosses into the United States.

This is the operational one-pager that pairs with the code. Legal review is still outstanding — this captures what the system actually does today and what it tells the caller. Treat it as the source of truth for engineering, and the starting point for the lawyer.

## AI disclosure — immutable first turn

Every call begins with an AI disclosure. The phrase "virtual assistant" is an invariant across all four greeting variants — the same phrase lands in the transcript audit trail regardless of branch, so the disclosure is verifiable after the fact by grepping stored transcripts.

The four variants, implemented in `modules/voice-channel/src/server.py:_build_personalized_first_message`:

1. **Unknown caller (default):**
   "Hi, this is {business_name}'s virtual assistant. How can I help you today?"

2. **Known contact with a real name:**
   "Hi {first_name}, this is {business_name}'s virtual assistant. How can I help you today?"

3. **Placeholder contact (called before, no name captured yet):**
   "Hi, this is {business_name}'s virtual assistant. Good to hear from you again — can I grab your name?"

4. **Owner self-call (operator testing their own line):**
   "Hi {first_name}, this is your virtual assistant. Owner test line is up — what are we checking?"

`{business_name}` is drawn from `voice_config.business_name` at runtime. `{first_name}` is the caller's first name from the `contacts` table if known. The disclosure phrase is never templated out of the greeting, regardless of caller.

## Recording consent

Recording consent is appended to the AI disclosure on the very first turn, before any substantive exchange:

> "This call may be recorded for quality and service delivery. If you'd prefer not to be recorded, let me know and I'll stop."

The consent line runs once per call. If the caller asks to stop recording, the agent must be configured to end the call and transfer to voicemail — recording cannot be paused mid-call through the current Vapi integration, so "stop" means hang up. This behavior must be wired into the agent's system prompt before this doc can be considered compliant.

## What we collect, where it lives, how long we keep it

All voice-channel data lives in the single local SQLite database at `~/.local/share/software-of-you/soy.db` on the operator's machine. No data is sent to third-party storage except (a) Vapi's own call infrastructure during the live call, (b) Twilio for SMS confirmation delivery, and (c) Google Calendar for the booked event itself.

| Data | Table / field | Source | Retention |
|------|---------------|--------|-----------|
| Caller phone number | `contacts.phone`, `voice_calls.from_number` | Vapi webhook | Permanent (part of the CRM) |
| Caller name | `contacts.name` | Caller provides during the call | Permanent |
| Call metadata (start, end, duration, cost) | `voice_calls` | Vapi end-of-call report | **90 days**, then metadata aggregated, row deleted |
| Call transcript | `transcripts` | Vapi end-of-call report | **Permanent** (used by conversation-intelligence) |
| Call recording URL | `voice_calls.recording_url` | Vapi artifact | **30 days max** — purge job required before production |
| Tool call events | `voice_events` | Server webhook handler | **90 days**, then deleted |
| Booking (calendar event) | Google Calendar | `book_appointment` tool | Follows Google Calendar retention (operator-controlled) |
| SMS confirmation | Twilio outbound log | `book_appointment` tool | Follows Twilio retention (14 days by default) |

A retention purge job is **required before production** and is not yet implemented. It should run daily and apply the limits in the table above. Tracking as a follow-up in the voice-channel hardening roadmap.

## Data subject rights

Callers have the right to:
- **Access** — request a copy of all data the system holds about them. Satisfied today by exporting the caller's `contacts` row plus linked `voice_calls`, `voice_events`, `transcripts`, and `emails` as JSON. A CLI command to do this in one step is a follow-up.
- **Deletion** — request removal of all their data. Satisfied by deleting the caller's `contacts` row plus CASCADE on the linked tables. The transcript is harder: it feeds conversation-intelligence and relationship scoring, so deletion must propagate through derived state. Also a follow-up.
- **Portability** — receive their data in a machine-readable format. JSON export satisfies this.

Timeframe: 30 days from written request, per PIPEDA guidance.

Requests are routed to the operator's email — there is no self-serve portal today, and building one is out of scope for the first paying customer. A documented email address must be surfaced in the disclosure or in a follow-up SMS when a caller asks.

## Data Processing Agreement — Kerry's clients

When Kerry deploys voice-channel for his own clients, those clients become data controllers and Kerry becomes the processor. A DPA template is required before any third-party deployment and must cover:

- Identification of controller and processor
- Categories of personal data processed (phone numbers, names, call transcripts, recordings)
- Purpose limitation (voice booking, no secondary use, no training data)
- Subprocessor list (Vapi, Twilio, Google) with links to each vendor's DPA
- Breach notification SLA (24 hours from detection)
- Data return or deletion at contract termination
- Audit rights for the controller

A template will live at `docs/voice-channel-dpa-template.md`. Not in this PR — drafting it requires the lawyer first.

## Follow-ups before first paying customer

- [ ] Wire the recording consent line into the agent's greeting config
- [ ] Implement the retention purge job (recordings → 30d, voice_events + voice_calls metadata → 90d)
- [ ] Add a CLI command to export all data for a given caller (access right)
- [ ] Add a CLI command to delete all data for a given caller with cascade (deletion right)
- [ ] Draft the DPA template (legal review required)
- [ ] Decide where the operator's contact email is surfaced to callers who invoke their rights
- [ ] Legal review of this doc in full
