"""
Keyword Router — routes natural language queries to the best matching QPack question.

Three-layer matching:
  1. Entity name match — contact or project name found in query
  2. Keyword scoring — score against module and question keyword sets
  3. Fuzzy label match — word overlap with question labels

Usage:
    python3 modules/qpack-generator/router.py "who should I focus on"
    python3 modules/qpack-generator/router.py "Jessica"
    python3 modules/qpack-generator/router.py "what's overdue"
    python3 modules/qpack-generator/router.py "what's overdue" --json
"""

import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
QPACK_DIR = Path(__file__).resolve().parents[2] / "qpacks"


# ──────────────────────────────────────────────────────────────────────
#  Module keyword sets
# ──────────────────────────────────────────────────────────────────────

MODULE_KEYWORDS = {
    "crm": [
        "contact", "relationship", "who", "person", "people", "cold",
        "silent", "company", "client", "follow up", "reconnect",
    ],
    "project-tracker": [
        "project", "task", "milestone", "deadline", "overdue",
        "progress", "blocked", "sprint",
    ],
    "gmail": [
        "email", "inbox", "reply", "draft", "thread", "sent",
        "received", "gmail",
    ],
    "calendar": [
        "calendar", "meeting", "schedule", "free time", "busy",
        "prep", "today", "tomorrow", "week",
    ],
    "notes": [
        "decision", "decided", "outcome", "revisit", "journal",
        "mood", "note",
    ],
    "core": [
        "attention", "nudge", "overdue", "urgent", "remind", "priority",
    ],
}


# ──────────────────────────────────────────────────────────────────────
#  Entity routing — maps entity types to contextual questions
# ──────────────────────────────────────────────────────────────────────

# When a query matches an entity name, route to a relevant question
ENTITY_QUESTION_MAP = {
    "contact": [
        "email.contact_threads",    # parameterized {contact}
        "crm.who_priority_week",    # general context
    ],
    "project": [
        "decisions.for_project",    # parameterized {project}
        "projects.health_overview", # general context
    ],
}


# ──────────────────────────────────────────────────────────────────────
#  QPack loader
# ──────────────────────────────────────────────────────────────────────

def _load_all_questions(qpack_dir: Path = QPACK_DIR) -> list[dict]:
    """Load all questions from all QPack files with their module info."""
    questions = []
    if not qpack_dir.exists():
        return questions

    for f in sorted(qpack_dir.glob("*.qpack.json")):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        module = data.get("module", f.stem)
        for q in data.get("questions", []):
            q["_module"] = module
            questions.append(q)
    return questions


# ──────────────────────────────────────────────────────────────────────
#  Entity name matching (Layer 1)
# ──────────────────────────────────────────────────────────────────────

def _load_entities(db_path: Path = DB_PATH) -> list[dict]:
    """Load contact and project names from the database."""
    entities = []
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        try:
            # Contacts
            try:
                rows = db.execute(
                    "SELECT id, name FROM contacts WHERE status = 'active' ORDER BY name"
                ).fetchall()
                for row in rows:
                    entities.append({"type": "contact", "id": row["id"], "name": row["name"]})
            except sqlite3.OperationalError:
                pass

            # Projects
            try:
                rows = db.execute(
                    "SELECT id, name FROM projects WHERE status IN ('active', 'planning') ORDER BY name"
                ).fetchall()
                for row in rows:
                    entities.append({"type": "project", "id": row["id"], "name": row["name"]})
            except sqlite3.OperationalError:
                pass
        finally:
            db.close()
    except Exception:
        pass

    return entities


def _match_entity(query_lower: str, entities: list[dict]) -> dict | None:
    """
    Check if the query contains any entity name.

    Matching rules:
    - Full name match (case-insensitive): "jessica martin" in query
    - First name match (if 3+ chars, case-insensitive): "jessica" in query
    - Prefers longer (more specific) matches
    - On tie, prefers contacts over projects

    Returns the best match or None.
    """
    candidates = []

    for entity in entities:
        name = entity["name"]
        name_lower = name.lower()

        # Full name match
        if name_lower in query_lower:
            candidates.append({
                "entity": entity,
                "match_length": len(name),
                "match_type": "full",
            })
            continue

        # First name match (only if 3+ chars to avoid false positives)
        first_name = name.split()[0].lower() if name.split() else ""
        if len(first_name) >= 3 and first_name in query_lower.split():
            candidates.append({
                "entity": entity,
                "match_length": len(first_name),
                "match_type": "first_name",
            })

    if not candidates:
        return None

    # Sort by: match length desc, full > first_name, contact > project
    type_priority = {"contact": 0, "project": 1}
    match_priority = {"full": 0, "first_name": 1}
    candidates.sort(key=lambda c: (
        match_priority.get(c["match_type"], 9),
        -c["match_length"],
        type_priority.get(c["entity"]["type"], 9),
    ))

    return candidates[0]["entity"]


# ──────────────────────────────────────────────────────────────────────
#  Keyword scoring (Layer 2)
# ──────────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    """Simple word tokenizer — lowercase, stripped of common punctuation."""
    cleaned = text.lower().replace("?", " ").replace("'", "'")
    return set(cleaned.split())


def _score_module(query_lower: str, module: str) -> float:
    """Score a module against the query using MODULE_KEYWORDS."""
    keywords = MODULE_KEYWORDS.get(module, [])
    if not keywords:
        return 0.0

    score = 0.0
    for kw in keywords:
        # Multi-word keywords: check if the phrase appears in the query
        if " " in kw:
            if kw in query_lower:
                score += 2.0  # multi-word matches are stronger signals
        else:
            if kw in query_lower.split():
                score += 1.0
            elif kw in query_lower:
                score += 0.5  # partial/substring match

    # Normalize by keyword count so modules with more keywords
    # don't have an inherent advantage
    return score / len(keywords) if keywords else 0.0


def _score_question(query_lower: str, query_tokens: set[str], question: dict) -> float:
    """
    Score a question against the query.

    Uses question keywords and label word overlap.
    """
    score = 0.0

    # Keyword matches (from the question's keyword list)
    q_keywords = question.get("keywords", [])
    keyword_hits = 0
    for kw in q_keywords:
        kw_lower = kw.lower()
        if " " in kw_lower:
            if kw_lower in query_lower:
                keyword_hits += 2.0
        else:
            if kw_lower in query_tokens:
                keyword_hits += 1.0
            elif kw_lower in query_lower:
                keyword_hits += 0.5
    if q_keywords:
        score += keyword_hits / len(q_keywords)

    # Label word overlap
    label = question.get("label", "")
    label_tokens = _tokenize(label)
    # Remove common stop words
    stop_words = {"what", "which", "how", "are", "is", "my", "me", "i",
                  "the", "a", "an", "to", "in", "on", "at", "for", "of",
                  "should", "do", "does", "did", "have", "has", "been"}
    label_meaningful = label_tokens - stop_words
    query_meaningful = query_tokens - stop_words

    if label_meaningful and query_meaningful:
        overlap = len(label_meaningful & query_meaningful)
        total = len(label_meaningful | query_meaningful)
        score += (overlap / total) * 0.8  # Jaccard-like, weighted lower than keywords

    # Bonus for featured questions (slight tiebreaker)
    if question.get("featured"):
        score += 0.05

    return score


# ──────────────────────────────────────────────────────────────────────
#  Router
# ──────────────────────────────────────────────────────────────────────

def route_query(
    query: str,
    db_path: Path = DB_PATH,
    qpack_dir: Path = QPACK_DIR,
) -> dict:
    """
    Route a natural language query to the best matching QPack question.

    Three layers:
    1. Entity name match — check for contact/project names in the query
    2. Keyword scoring — score against module and question keyword sets
    3. Fuzzy label match — word overlap with question labels

    Returns:
        {
            "matched_question_id": str | None,
            "matched_module": str | None,
            "confidence": float,
            "entity_match": {"type": ..., "name": ..., "id": ...} | None,
            "alternative_questions": [...],
        }
    """
    query_lower = query.lower().strip()
    query_tokens = _tokenize(query_lower)

    all_questions = _load_all_questions(qpack_dir)
    if not all_questions:
        return {
            "matched_question_id": None,
            "matched_module": None,
            "confidence": 0.0,
            "entity_match": None,
            "alternative_questions": [],
        }

    # ── Layer 1: Entity name match ──
    entities = _load_entities(db_path)
    entity_match = _match_entity(query_lower, entities)

    entity_question_id = None
    entity_confidence_bonus = 0.0

    if entity_match:
        # Find the best contextual question for this entity type
        candidate_ids = ENTITY_QUESTION_MAP.get(entity_match["type"], [])
        for cid in candidate_ids:
            for q in all_questions:
                if q["id"] == cid:
                    entity_question_id = cid
                    entity_confidence_bonus = 0.3  # Strong boost for entity match
                    break
            if entity_question_id:
                break

    # ── Layer 2 + 3: Keyword + label scoring ──
    scored = []
    for q in all_questions:
        # Skip parameterized questions unless we have an entity match
        if q.get("parameterized") and not entity_match:
            continue

        module = q.get("_module", "")
        module_score = _score_module(query_lower, module)
        question_score = _score_question(query_lower, query_tokens, q)

        # Combined score: weighted sum
        combined = (module_score * 0.4) + (question_score * 0.6)

        # Entity boost: if this question matches the entity route, boost it
        if entity_question_id and q["id"] == entity_question_id:
            combined += entity_confidence_bonus

        scored.append({
            "question_id": q["id"],
            "module": module,
            "label": q.get("label", ""),
            "score": combined,
            "module_score": module_score,
            "question_score": question_score,
        })

    # Sort by score descending
    scored.sort(key=lambda x: -x["score"])

    # Best match
    best = scored[0] if scored else None
    alternatives = scored[1:4] if len(scored) > 1 else []

    if best and best["score"] > 0:
        # Normalize confidence to 0-1 range
        # The theoretical max score is ~1.35 (perfect module + question + entity),
        # but in practice scores above 0.8 are very strong matches.
        confidence = min(best["score"] / 0.8, 1.0)

        return {
            "matched_question_id": best["question_id"],
            "matched_module": best["module"],
            "confidence": round(confidence, 2),
            "entity_match": entity_match,
            "alternative_questions": [
                {
                    "question_id": a["question_id"],
                    "module": a["module"],
                    "label": a["label"],
                    "confidence": round(min(a["score"] / 0.8, 1.0), 2),
                }
                for a in alternatives
            ],
        }

    # No match at all
    return {
        "matched_question_id": entity_question_id,
        "matched_module": None,
        "confidence": 0.3 if entity_question_id else 0.0,
        "entity_match": entity_match,
        "alternative_questions": [],
    }


# ──────────────────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 router.py \"<query>\" [--json]")
        print()
        print("Examples:")
        print("  python3 router.py \"who should I focus on\"")
        print("  python3 router.py \"Jessica\"")
        print("  python3 router.py \"what's overdue\"")
        print("  python3 router.py \"prep me for my next meeting\"")
        sys.exit(1)

    query = sys.argv[1]
    as_json = "--json" in sys.argv

    result = route_query(query)

    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"\n{'='*60}")
        print(f"  Query: \"{query}\"")
        print(f"{'='*60}\n")

        if result["matched_question_id"]:
            conf_pct = int(result["confidence"] * 100)
            print(f"  Match:      {result['matched_question_id']}")
            print(f"  Module:     {result['matched_module'] or '—'}")
            print(f"  Confidence: {conf_pct}%")
        else:
            print(f"  No match found.")

        if result.get("entity_match"):
            em = result["entity_match"]
            print(f"  Entity:     {em['name']} ({em['type']} #{em['id']})")

        if result.get("alternative_questions"):
            print(f"\n  Alternatives:")
            for alt in result["alternative_questions"]:
                conf_pct = int(alt["confidence"] * 100)
                print(f"    {alt['question_id']:40s}  {conf_pct:3d}%  {alt['label']}")

        print()
