"""
Step 5: Adapt — Adjust questions based on data state.

Questions that target empty datasets get rewritten as onboarding prompts.
Featured questions get reshuffled based on what has the most data.
"""

import sys
from pathlib import Path
_mod_dir = Path(__file__).resolve().parents[1]
if str(_mod_dir) not in sys.path:
    sys.path.insert(0, str(_mod_dir))
from pipeline import PipelineStep

# Data richness tiers — determines which questions get featured
RICHNESS_TIERS = {
    "rich": {"contacts": 10, "emails": 50},       # Enough for meaningful cross-referencing
    "moderate": {"contacts": 3, "emails": 10},     # Basic insights possible
    "sparse": {"contacts": 1, "emails": 0},        # Onboarding territory
}

# Onboarding replacement questions for sparse data states
ONBOARDING_QUESTIONS = {
    "crm": {
        "id": "crm.onboard_add_contacts",
        "label": "Add your first contacts to get started",
        "short_label": "Get started",
        "keywords": ["start", "setup", "add", "contact"],
        "featured": True,
        "requires_llm": False,
        "answer_format": "insight_synthesis",
        "context_queries": [
            {"key": "state", "sql": "SELECT COUNT(*) as n FROM contacts WHERE status = 'active'"}
        ],
        "static_answer": {
            "data": "You don't have any contacts yet. SoY gets smarter the more data it has about your professional relationships.",
            "insight": "The fastest way to get value is to connect Gmail — SoY will auto-discover your most frequent contacts from email history.",
            "action": "Say \"Connect my Google account\" or \"Add a contact named [name]\" to get started."
        },
        "data_requires": {},
    },
    "gmail": {
        "id": "gmail.onboard_connect",
        "label": "Connect Gmail to see your email intelligence",
        "short_label": "Connect Gmail",
        "keywords": ["gmail", "email", "connect", "setup"],
        "featured": True,
        "requires_llm": False,
        "answer_format": "insight_synthesis",
        "context_queries": [
            {"key": "state", "sql": "SELECT COUNT(*) as n FROM emails"}
        ],
        "static_answer": {
            "data": "Gmail isn't connected yet. Email data powers relationship health, response tracking, and contact discovery.",
            "insight": "Once connected, SoY syncs your recent emails and auto-links them to your contacts. It checks every 15 minutes.",
            "action": "Say \"Connect my Google account\" to set up Gmail sync."
        },
        "data_requires": {},
    },
}


class AdaptStep(PipelineStep):
    name = "adapt"

    def __call__(self, ctx: dict) -> dict:
        log = ctx["_pipeline"].log
        templates = ctx["validated_templates"]
        data_state = ctx["data_state"]

        # Determine richness tier (check from richest to sparsest)
        tier = "empty"
        for tier_name in ("rich", "moderate", "sparse"):
            thresholds = RICHNESS_TIERS[tier_name]
            if all(data_state.get(k, 0) >= v for k, v in thresholds.items()):
                tier = tier_name
                break
        log(f"    data richness: {tier}")

        adapted = {}
        onboarding_added = 0

        for module_name, template in templates.items():
            questions = template.get("questions", [])

            # If module's data is sparse, inject onboarding question
            if module_name in ONBOARDING_QUESTIONS:
                module_data_key = {
                    "crm": "contacts",
                    "gmail": "emails",
                    "calendar": "calendar_events",
                    "project-tracker": "projects",
                    "conversation-intelligence": "transcripts",
                }.get(module_name)

                if module_data_key and data_state.get(module_data_key, 0) < 3:
                    ob_q = ONBOARDING_QUESTIONS[module_name]
                    # Prepend onboarding question, demote others from featured
                    for q in questions:
                        q["featured"] = False
                    questions = [ob_q] + questions
                    onboarding_added += 1

            # Reshuffle featured based on data availability
            # Questions with actual data get priority
            for q in questions:
                if q.get("_has_data") is False and q.get("featured"):
                    q["featured"] = False

            # Ensure at least 1 and at most 3 featured per module
            featured = [q for q in questions if q.get("featured")]
            if not featured and questions:
                questions[0]["featured"] = True
            elif len(featured) > 3:
                for q in featured[3:]:
                    q["featured"] = False

            t = dict(template)
            t["questions"] = questions
            t["_data_tier"] = tier
            adapted[module_name] = t

        if onboarding_added:
            log(f"    {onboarding_added} onboarding questions injected for sparse modules")

        ctx["adapted_templates"] = adapted
        return ctx
