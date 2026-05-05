-- Voice Channel — schema for the Vapi integration module
-- Module: voice-channel
-- See: modules/voice-channel/CLAUDE.md
--
-- Architecture: voice-channel is a thin integration layer over Vapi (hosted voice
-- agent platform). The webhook endpoint on the Razer receives tool calls from Vapi
-- during live conversations, queries SoY's data graph, and returns structured results.
--
-- Per-install tenancy: one SoY install = one business. voice_config is effectively
-- a singleton table (one row per install).
--
-- All statements are idempotent.

-- ═══════════════════════════════════════════════════════════════
-- voice_config: per-install settings (effectively singleton)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS voice_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Vapi integration credentials
    vapi_api_key TEXT,                  -- Encrypted at rest (TODO: integrate with service_credentials pattern)
    vapi_agent_id TEXT,                 -- The Vapi agent UUID for this install
    vapi_phone_number_id TEXT,          -- Vapi-managed phone number ID (if Vapi provisions the number)
    vapi_webhook_secret TEXT,           -- Shared secret for webhook signature validation

    -- Phone number this install answers
    phone_number TEXT,                  -- E.164 format, e.g. "+14165551234"
    phone_provider TEXT DEFAULT 'vapi', -- 'vapi' | 'twilio' (BYO Twilio number)

    -- Business context
    business_name TEXT NOT NULL DEFAULT 'My Business',
    owner_name TEXT,                    -- "Alex" — used in disclosure script and transfer messages
    owner_telegram_chat_id TEXT,        -- For owner notifications via telegram-bot
    owner_transfer_number TEXT,         -- E.164 number for transfer_to_human

    -- Hours, services, prompts
    business_hours_json TEXT,           -- JSON: {"mon": ["09:00", "17:00"], "sat": null, ...}
    services_json TEXT,                 -- JSON: [{"name": "Voice session", "duration_min": 60, "price": 250}, ...]
    timezone TEXT DEFAULT 'America/Toronto',

    -- Conversation customization
    greeting_template TEXT,             -- Override the default greeting; supports {business_name}, {owner_name}
    disclosure_script TEXT NOT NULL DEFAULT 'Hi, this is the virtual assistant for {business_name}. This call may be recorded for service quality. How can I help you today?',
    system_prompt_override TEXT,        -- Optional override for the Vapi agent's system prompt

    -- Safety rails (day-1 requirements per CLAUDE.md)
    max_call_duration_s INTEGER NOT NULL DEFAULT 600,        -- Hard cap, Vapi will hang up
    max_calls_per_caller_per_hour INTEGER NOT NULL DEFAULT 5, -- Rate limit by caller phone
    daily_cost_cap_cents INTEGER NOT NULL DEFAULT 2000,      -- $20/day default cap
    daily_cost_alert_thresholds TEXT NOT NULL DEFAULT '[50, 80, 100]', -- JSON array of % thresholds for telegram alerts

    -- LLM backend (v1 = Vapi default cloud, v1.5 = potential Legion custom LLM)
    llm_backend TEXT NOT NULL DEFAULT 'vapi_default',  -- 'vapi_default' | 'custom_legion' | 'custom_other'
    llm_custom_endpoint TEXT,                          -- For v1.5: URL of OpenAI-compatible endpoint
    llm_custom_model TEXT,                             -- For v1.5: model name to send

    -- Status
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed a single empty config row if none exists (per-install singleton)
INSERT OR IGNORE INTO voice_config (id, business_name) VALUES (1, 'My Business');


-- ═══════════════════════════════════════════════════════════════
-- voice_calls: per-call audit log
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS voice_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Vapi identifiers
    vapi_call_id TEXT UNIQUE NOT NULL,  -- Vapi's UUID for the call
    vapi_assistant_id TEXT,             -- Which agent handled it (in case of multi-agent installs)

    -- Phone numbers
    from_number TEXT NOT NULL,          -- Caller (E.164)
    to_number TEXT NOT NULL,            -- Our number that was dialed (E.164)

    -- Linked entities
    contact_id INTEGER,                 -- Matched/created contact (NULL if call disconnected before lookup)
    booked_event_id INTEGER,            -- calendar_events.id if a booking was created

    -- Call lifecycle
    started_at TEXT NOT NULL,           -- ISO timestamp
    answered_at TEXT,                   -- When the agent picked up
    ended_at TEXT,                      -- ISO timestamp
    duration_s INTEGER,                 -- Total duration in seconds

    -- Outcome
    outcome TEXT,                       -- 'booked' | 'transferred' | 'voicemail' | 'no_intent' | 'abandoned' | 'failed' | 'rate_limited' | 'cost_capped'
    outcome_details TEXT,               -- Free text explanation
    booking_verified INTEGER,           -- 1 if post-call verification confirmed the booking, 0 if not, NULL if no booking attempted
    booking_verification_at TEXT,       -- When verification ran
    hallucinated_confirmation INTEGER NOT NULL DEFAULT 0,  -- 1 if we detected the LLM claimed a booking that didn't land

    -- Cost tracking
    cost_cents INTEGER,                 -- Total cost in cents (Vapi reports this in webhook)
    cost_breakdown_json TEXT,           -- JSON: {"vapi": N, "twilio": N, "llm": N, "stt": N, "tts": N}

    -- Storage
    transcript_id INTEGER,              -- Link to transcripts table after post-call processing
    recording_url TEXT,                 -- Vapi-hosted recording URL (if recording enabled)

    -- Status flags
    transferred_to_human INTEGER NOT NULL DEFAULT 0,
    transfer_reason TEXT,
    error_message TEXT,                 -- For 'failed' outcomes

    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_voice_calls_vapi_id ON voice_calls(vapi_call_id);
CREATE INDEX IF NOT EXISTS idx_voice_calls_contact ON voice_calls(contact_id);
CREATE INDEX IF NOT EXISTS idx_voice_calls_started ON voice_calls(started_at);
CREATE INDEX IF NOT EXISTS idx_voice_calls_outcome ON voice_calls(outcome);
CREATE INDEX IF NOT EXISTS idx_voice_calls_from ON voice_calls(from_number);


-- ═══════════════════════════════════════════════════════════════
-- voice_events: per-call event stream for debugging and audit
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS voice_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,                    -- FK to voice_calls.id
    vapi_call_id TEXT,                           -- Denormalized for fast filtering
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    event_type TEXT NOT NULL,                    -- 'call_started' | 'call_answered' | 'tool_call' | 'tool_result' | 'transcript_chunk' | 'llm_response' | 'tts_started' | 'transfer' | 'hangup' | 'error' | 'safety_rail_hit'
    tool_name TEXT,                              -- For tool_call/tool_result events
    data_json TEXT,                              -- Event payload (request, response, error message, etc)
    duration_ms INTEGER,                         -- For events that have a measurable duration

    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_events_call ON voice_events(call_id);
CREATE INDEX IF NOT EXISTS idx_voice_events_type ON voice_events(event_type);
CREATE INDEX IF NOT EXISTS idx_voice_events_timestamp ON voice_events(timestamp);


-- ═══════════════════════════════════════════════════════════════
-- Computed view: v_voice_call_summary
-- For QPack questions and admin dashboards
-- ═══════════════════════════════════════════════════════════════

DROP VIEW IF EXISTS v_voice_call_summary;
CREATE VIEW IF NOT EXISTS v_voice_call_summary AS
SELECT
    vc.id,
    vc.vapi_call_id,
    vc.from_number,
    vc.to_number,
    vc.started_at,
    vc.duration_s,
    vc.outcome,
    vc.cost_cents,
    vc.booking_verified,
    vc.hallucinated_confirmation,
    vc.transferred_to_human,
    c.name AS contact_name,
    c.company AS contact_company,
    ce.title AS booked_event_title,
    ce.start_time AS booked_event_start,
    -- Categorize for filtering
    CASE
        WHEN vc.outcome = 'booked' AND vc.booking_verified = 1 THEN 'success'
        WHEN vc.outcome = 'booked' AND vc.booking_verified = 0 THEN 'verification_failed'
        WHEN vc.outcome = 'booked' AND vc.booking_verified IS NULL THEN 'verification_pending'
        WHEN vc.outcome = 'transferred' THEN 'transferred'
        WHEN vc.outcome IN ('failed', 'cost_capped', 'rate_limited') THEN 'system_failure'
        WHEN vc.outcome IN ('voicemail', 'no_intent', 'abandoned') THEN 'no_booking'
        ELSE 'other'
    END AS status_category
FROM voice_calls vc
LEFT JOIN contacts c ON c.id = vc.contact_id
LEFT JOIN calendar_events ce ON ce.id = vc.booked_event_id;


-- ═══════════════════════════════════════════════════════════════
-- Computed view: v_voice_daily_stats
-- For cost cap enforcement and dashboard
-- ═══════════════════════════════════════════════════════════════

DROP VIEW IF EXISTS v_voice_daily_stats;
CREATE VIEW IF NOT EXISTS v_voice_daily_stats AS
SELECT
    date(started_at) AS call_date,
    COUNT(*) AS total_calls,
    SUM(CASE WHEN outcome = 'booked' AND booking_verified = 1 THEN 1 ELSE 0 END) AS successful_bookings,
    SUM(CASE WHEN hallucinated_confirmation = 1 THEN 1 ELSE 0 END) AS hallucinated_count,
    SUM(CASE WHEN transferred_to_human = 1 THEN 1 ELSE 0 END) AS transferred_count,
    SUM(CASE WHEN outcome IN ('failed', 'cost_capped', 'rate_limited') THEN 1 ELSE 0 END) AS failed_count,
    COALESCE(SUM(cost_cents), 0) AS total_cost_cents,
    COALESCE(SUM(duration_s), 0) AS total_duration_s,
    AVG(duration_s) AS avg_duration_s
FROM voice_calls
GROUP BY date(started_at);


-- ═══════════════════════════════════════════════════════════════
-- Module registration
-- ═══════════════════════════════════════════════════════════════

INSERT OR REPLACE INTO modules (name, version, enabled, installed_at)
VALUES ('voice-channel', '0.1.0', 0, datetime('now'));
-- Note: enabled = 0 by default. The module is installed but disabled until
-- the webhook server is configured and a Vapi agent is set up. Enable via:
--   UPDATE modules SET enabled = 1 WHERE name = 'voice-channel';
