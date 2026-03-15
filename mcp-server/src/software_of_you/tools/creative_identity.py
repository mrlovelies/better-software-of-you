"""Creative identity tool — manage writing style baseline, narrative principles, and project creative context."""

import re
from mcp.server.fastmcp import FastMCP
from software_of_you.db import execute, execute_many, execute_write, rows_to_dicts


VALID_PRINCIPLE_CATEGORIES = (
    "structure", "pacing", "character", "theme", "pov", "tone", "dialogue", "general",
)
VALID_CONTEXT_TYPES = (
    "character", "structure", "theme", "scene", "decision",
    "thread", "canon", "lore", "relationship", "note",
)
VALID_CONTEXT_STATUSES = ("active", "resolved", "deprecated", "draft", "complete")
VALID_SOURCE_TYPES = ("user_written", "ai_approved", "ai_rejected", "reference")
VALID_MODES = ("learned", "exploratory", "raw")
VALID_ANNOTATION_TYPES = ("correction", "question", "idea", "observation")
VALID_ANNOTATION_STATUSES = ("pending", "verified", "revised", "noted", "dismissed")
VALID_THREAD_TYPES = ("question", "provocation", "observation", "idea")
VALID_THREAD_STATUSES = ("open", "answered", "discussed", "archived")


def register(server: FastMCP) -> None:
    @server.tool()
    def creative_identity(
        action: str,
        record_id: int = 0,
        # Writing samples
        title: str = "",
        content: str = "",
        source_type: str = "",
        # Narrative principles
        category: str = "",
        principle: str = "",
        weight: float = 0.0,
        evidence: str = "",
        source_session: str = "",
        # Creative context
        project_id: int = 0,
        project_name: str = "",
        context_type: str = "",
        status: str = "",
        tags: str = "",
        # Sessions
        session_date: str = "",
        observations: str = "",
        decisions_made: str = "",
        open_questions: str = "",
        scenes_worked: str = "",
        # Annotations
        highlighted_text: str = "",
        annotation_type: str = "",
        author: str = "",
        research_response: str = "",
        revision_made: str = "",
        # Threads
        thread_type: str = "",
        prompt: str = "",
        response: str = "",
        # General
        mode: str = "",
        notes: str = "",
        text: str = "",
    ) -> dict:
        """Manage your persistent creative writing identity.

        Actions — Style Mode:
          set_mode       — Set style mode: learned, exploratory, or raw (mode required)
          get_mode       — Get current style mode

        Actions — Writing Samples (Layer 1 — Mechanical Baseline):
          add_sample     — Ingest a writing sample (title, content required; source_type optional)
          list_samples   — List samples (optional: source_type, project_id filters)
          get_sample     — Get a sample with full metrics (record_id required)
          delete_sample  — Delete a sample (record_id required)
          get_baseline   — Get aggregate mechanical baseline from all approved/written samples

        Actions — Narrative Principles (Layer 2 — Creative DNA):
          add_principle     — Add a principle (category, principle required)
          list_principles   — List principles (optional: category filter)
          update_principle  — Update a principle (record_id required)
          disable_principle — Soft-disable a principle (record_id required)

        Actions — Creative Context (Layer 3 — Project Lore):
          add_context     — Add context entry (context_type, title, content required; project_id/project_name optional)
          list_context    — List context entries (optional: project_id, context_type, status filters)
          get_context     — Get a context entry (record_id required)
          update_context  — Update a context entry (record_id required)

        Actions — Sessions:
          log_session     — Log a creative session (project_id optional, observations/decisions/open_questions)
          list_sessions   — List sessions (optional: project_id filter)

        Actions — Annotations (Lore Review Loop):
          add_annotation     — Add an annotation to a context entry (record_id=context_id, highlighted_text, notes required)
          list_annotations   — List annotations (optional: record_id=context_id, status filter)
          review_annotation  — Review an annotation: update status, add research_response, revision_made (record_id required)

        Actions — Creative Threads (Ongoing Thoughts):
          add_thread         — Start a new creative thread (prompt required; thread_type, project_id optional)
          list_threads       — List threads (optional: project_id, status filter)
          reply_thread       — Reply to a thread (record_id required, response required)

        Actions — Profile:
          get_profile        — Get full creative identity for prompt injection
          get_project_profile — Get project-specific creative profile (project_id required)
          check_drift        — Compare text against baseline (text required)
        """
        if action == "set_mode":
            return _set_mode(mode)
        elif action == "get_mode":
            return _get_mode()
        elif action == "add_sample":
            return _add_sample(title, content, source_type, project_id, project_name, notes)
        elif action == "list_samples":
            return _list_samples(source_type, project_id)
        elif action == "get_sample":
            return _get_sample(record_id)
        elif action == "delete_sample":
            return _delete_sample(record_id)
        elif action == "get_baseline":
            return _get_baseline()
        elif action == "add_principle":
            return _add_principle(category, principle, weight, evidence, source_session)
        elif action == "list_principles":
            return _list_principles(category)
        elif action == "update_principle":
            return _update_principle(record_id, category, principle, weight, evidence, source_session)
        elif action == "disable_principle":
            return _disable_principle(record_id)
        elif action == "add_context":
            return _add_context(project_id, project_name, context_type, title, content, status, tags)
        elif action == "list_context":
            return _list_context(project_id, context_type, status)
        elif action == "get_context":
            return _get_context(record_id)
        elif action == "update_context":
            return _update_context(record_id, context_type, title, content, status, tags)
        elif action == "log_session":
            return _log_session(project_id, project_name, session_date, observations, decisions_made,
                                open_questions, scenes_worked, mode, notes)
        elif action == "list_sessions":
            return _list_sessions(project_id)
        elif action == "get_profile":
            return _get_profile()
        elif action == "get_project_profile":
            return _get_project_profile(project_id, project_name)
        elif action == "check_drift":
            return _check_drift(text)
        elif action == "add_annotation":
            return _add_annotation(record_id, highlighted_text, notes, annotation_type, author)
        elif action == "list_annotations":
            return _list_annotations(record_id, project_id, status)
        elif action == "review_annotation":
            return _review_annotation(record_id, status, research_response, revision_made)
        elif action == "add_thread":
            return _add_thread(project_id, project_name, prompt, thread_type, author, tags)
        elif action == "list_threads":
            return _list_threads(project_id, status)
        elif action == "reply_thread":
            return _reply_thread(record_id, response)
        else:
            return {"error": f"Unknown action: {action}. See docstring for available actions."}


# ─── Helpers ────────────────────────────────────────────────────────────────

def _resolve_project(project_id, project_name):
    """Resolve a project by ID or fuzzy name match."""
    if project_id:
        return project_id
    if project_name:
        rows = execute("SELECT id FROM projects WHERE name LIKE ?", (f"%{project_name}%",))
        if len(rows) == 1:
            return rows[0]["id"]
    return None


def _compute_metrics(text):
    """Compute mechanical writing metrics from raw text."""
    if not text or not text.strip():
        return {}

    words = text.split()
    word_count = len(words)

    # Sentence detection: split on .!? followed by space or end
    sentences = [s.strip() for s in re.split(r'[.!?]+(?:\s|$)', text) if s.strip()]
    sentence_count = len(sentences) if sentences else 1
    avg_sentence_length = round(word_count / sentence_count, 1) if sentence_count else None

    # Dialogue detection: content between quotation marks (straight or curly)
    dialogue_matches = re.findall(r'["\u201c](.*?)["\u201d]', text, re.DOTALL)
    dialogue_words = sum(len(m.split()) for m in dialogue_matches)
    dialogue_ratio = round(dialogue_words / word_count, 3) if word_count else None

    # Paragraph detection: split on double newlines
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    paragraph_count = len(paragraphs) if paragraphs else 1
    avg_paragraph_length = round(sentence_count / paragraph_count, 1) if paragraph_count else None

    # Punctuation counts
    question_count = text.count('?')
    exclamation_count = text.count('!')

    # Italics: markdown *word* or _word_ (not ** bold)
    italics_count = len(re.findall(r'(?<!\*)\*(?!\*).+?(?<!\*)\*(?!\*)', text))
    italics_count += len(re.findall(r'(?<!_)_(?!_).+?(?<!_)_(?!_)', text))

    return {
        "word_count": word_count,
        "sentence_count": sentence_count,
        "avg_sentence_length": avg_sentence_length,
        "dialogue_word_count": dialogue_words,
        "dialogue_ratio": dialogue_ratio,
        "paragraph_count": paragraph_count,
        "avg_paragraph_length": avg_paragraph_length,
        "question_count": question_count,
        "exclamation_count": exclamation_count,
        "italics_count": italics_count,
    }


# ─── Style Mode ─────────────────────────────────────────────────────────────

def _set_mode(mode):
    if not mode:
        return {"error": "mode is required. Use: learned, exploratory, raw"}
    if mode not in VALID_MODES:
        return {"error": f"Invalid mode '{mode}'. Use: {', '.join(VALID_MODES)}"}

    execute_write(
        "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) VALUES ('creative_style_mode', ?, datetime('now'))",
        (mode,),
    )
    descriptions = {
        "learned": "Full profile applied — mechanical baseline + narrative principles + project context",
        "exploratory": "Narrative principles + project context, mechanical baseline ignored (experiment with form)",
        "raw": "Blank slate — no style learning applied",
    }
    return {
        "result": {"mode": mode, "description": descriptions[mode]},
        "_context": {"presentation": f"Style mode set to **{mode}**."},
    }


def _get_mode():
    rows = execute("SELECT value FROM soy_meta WHERE key = 'creative_style_mode'", ())
    mode = rows[0]["value"] if rows else "raw"
    return {"result": {"mode": mode}}


# ─── Writing Samples ────────────────────────────────────────────────────────

def _add_sample(title, content, source_type, project_id, project_name, notes):
    if not title:
        return {"error": "title is required."}
    if not content:
        return {"error": "content is required."}
    if source_type and source_type not in VALID_SOURCE_TYPES:
        return {"error": f"Invalid source_type '{source_type}'. Use: {', '.join(VALID_SOURCE_TYPES)}"}

    st = source_type or "user_written"
    pid = _resolve_project(project_id, project_name)
    metrics = _compute_metrics(content)

    rid = execute_write(
        """INSERT INTO writing_samples
           (title, source_type, content, project_id,
            word_count, sentence_count, avg_sentence_length,
            dialogue_word_count, dialogue_ratio,
            paragraph_count, avg_paragraph_length,
            question_count, exclamation_count, italics_count, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            title, st, content, pid,
            metrics.get("word_count"), metrics.get("sentence_count"),
            metrics.get("avg_sentence_length"),
            metrics.get("dialogue_word_count"), metrics.get("dialogue_ratio"),
            metrics.get("paragraph_count"), metrics.get("avg_paragraph_length"),
            metrics.get("question_count"), metrics.get("exclamation_count"),
            metrics.get("italics_count"), notes or None,
        ),
    )
    execute_write(
        """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
           VALUES ('writing_sample', ?, 'sample_added', ?, datetime('now'))""",
        (rid, f"{title} ({st}, {metrics.get('word_count', 0)} words)"),
    )

    return {
        "result": {"sample_id": rid, "title": title, "source_type": st, "metrics": metrics},
        "_context": {
            "suggestions": [
                "Use get_baseline to see how this sample shifts your aggregate metrics",
                "Add more samples to build a stronger baseline",
            ],
            "presentation": f"Sample ingested: **{title}** — {metrics.get('word_count', 0)} words, "
                            f"{metrics.get('sentence_count', 0)} sentences, "
                            f"{metrics.get('dialogue_ratio', 0):.0%} dialogue.",
        },
    }


def _list_samples(source_type, project_id):
    conditions = []
    params = []
    if source_type:
        conditions.append("ws.source_type = ?")
        params.append(source_type)
    if project_id:
        conditions.append("ws.project_id = ?")
        params.append(project_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = execute(
        f"""SELECT ws.id, ws.title, ws.source_type, ws.word_count, ws.avg_sentence_length,
                   ws.dialogue_ratio, ws.paragraph_count, ws.created_at,
                   p.name as project_name
            FROM writing_samples ws
            LEFT JOIN projects p ON ws.project_id = p.id
            {where}
            ORDER BY ws.created_at DESC""",
        tuple(params),
    )
    return {
        "result": rows_to_dicts(rows),
        "count": len(rows),
        "_context": {
            "suggestions": ["Use get_sample with record_id for full metrics and content"],
            "presentation": "Table: title, source type, word count, avg sentence length, dialogue ratio, date.",
        },
    }


def _get_sample(record_id):
    if not record_id:
        return {"error": "record_id is required."}
    rows = execute(
        """SELECT ws.*, p.name as project_name
           FROM writing_samples ws
           LEFT JOIN projects p ON ws.project_id = p.id
           WHERE ws.id = ?""",
        (record_id,),
    )
    if not rows:
        return {"error": f"No writing sample with id {record_id}."}
    return {
        "result": rows_to_dicts(rows)[0],
        "_context": {"presentation": "Show all metrics. Include content preview (first 500 chars)."},
    }


def _delete_sample(record_id):
    if not record_id:
        return {"error": "record_id is required."}
    existing = execute("SELECT title FROM writing_samples WHERE id = ?", (record_id,))
    if not existing:
        return {"error": f"No writing sample with id {record_id}."}

    execute_many([
        ("DELETE FROM writing_samples WHERE id = ?", (record_id,)),
        (
            """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
               VALUES ('writing_sample', ?, 'sample_deleted', ?, datetime('now'))""",
            (record_id, f"Deleted: {existing[0]['title']}"),
        ),
    ])
    return {"result": {"record_id": record_id, "deleted": True}}


def _get_baseline():
    """Compute aggregate mechanical baseline from user_written and ai_approved samples."""
    rows = execute(
        """SELECT COUNT(*) as sample_count,
                  ROUND(AVG(word_count), 1) as avg_word_count,
                  ROUND(AVG(sentence_count), 1) as avg_sentence_count,
                  ROUND(AVG(avg_sentence_length), 1) as avg_sentence_length,
                  ROUND(AVG(dialogue_ratio), 3) as avg_dialogue_ratio,
                  ROUND(AVG(avg_paragraph_length), 1) as avg_paragraph_length,
                  ROUND(AVG(CAST(question_count AS REAL) / NULLIF(sentence_count, 0)), 3) as question_density,
                  ROUND(AVG(CAST(exclamation_count AS REAL) / NULLIF(sentence_count, 0)), 3) as exclamation_density,
                  ROUND(AVG(CAST(italics_count AS REAL) / NULLIF(paragraph_count, 0)), 3) as italics_density,
                  MIN(avg_sentence_length) as min_sentence_length,
                  MAX(avg_sentence_length) as max_sentence_length,
                  MIN(dialogue_ratio) as min_dialogue_ratio,
                  MAX(dialogue_ratio) as max_dialogue_ratio
           FROM writing_samples
           WHERE source_type IN ('user_written', 'ai_approved')""",
        (),
    )
    baseline = rows_to_dicts(rows)[0] if rows else {}

    if not baseline.get("sample_count"):
        return {
            "result": {"sample_count": 0},
            "_context": {
                "presentation": "No samples yet. Add writing samples to build a baseline.",
                "suggestions": ["Use add_sample to ingest your first writing sample"],
            },
        }

    return {
        "result": baseline,
        "_context": {
            "suggestions": [
                "Use check_drift with new text to compare against this baseline",
                "Add more samples for a more reliable baseline",
            ],
            "presentation": "Show baseline metrics as a clean summary. Note sample count for confidence.",
        },
    }


# ─── Narrative Principles ───────────────────────────────────────────────────

def _add_principle(category, principle, weight, evidence, source_session):
    if not category:
        return {"error": "category is required."}
    if category not in VALID_PRINCIPLE_CATEGORIES:
        return {"error": f"Invalid category '{category}'. Use: {', '.join(VALID_PRINCIPLE_CATEGORIES)}"}
    if not principle:
        return {"error": "principle is required."}

    w = weight if weight > 0 else 0.7  # default weight

    rid = execute_write(
        """INSERT INTO narrative_principles (category, principle, weight, evidence, source_session)
           VALUES (?, ?, ?, ?, ?)""",
        (category, principle, w, evidence or None, source_session or None),
    )
    execute_write(
        """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
           VALUES ('narrative_principle', ?, 'principle_added', ?, datetime('now'))""",
        (rid, f"[{category}] {principle[:80]}"),
    )

    return {
        "result": {"principle_id": rid, "category": category, "weight": w},
        "_context": {
            "suggestions": ["Use list_principles to see all active principles"],
            "presentation": f"Principle added to **{category}** (weight: {w}).",
        },
    }


def _list_principles(category):
    conditions = ["active = 1"]
    params = []
    if category:
        conditions.append("category = ?")
        params.append(category)

    where = " AND ".join(conditions)
    rows = execute(
        f"""SELECT id, category, principle, weight, evidence, source_session, created_at
            FROM narrative_principles
            WHERE {where}
            ORDER BY category, weight DESC""",
        tuple(params),
    )
    # Group by category for display
    grouped = {}
    for r in rows_to_dicts(rows):
        cat = r["category"]
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(r)

    return {
        "result": grouped,
        "count": len(rows),
        "_context": {
            "suggestions": ["Use update_principle to adjust weights based on new feedback"],
            "presentation": "Group by category. Show principle text and weight. Higher weight = stronger influence.",
        },
    }


def _update_principle(record_id, category, principle, weight, evidence, source_session):
    if not record_id:
        return {"error": "record_id is required."}
    existing = execute("SELECT * FROM narrative_principles WHERE id = ?", (record_id,))
    if not existing:
        return {"error": f"No principle with id {record_id}."}

    updates = []
    params = []
    for field, value in [
        ("category", category), ("principle", principle),
        ("evidence", evidence), ("source_session", source_session),
    ]:
        if value:
            updates.append(f"{field} = ?")
            params.append(value)
    if weight > 0:
        updates.append("weight = ?")
        params.append(weight)

    if not updates:
        return {"error": "No fields to update."}

    updates.append("updated_at = datetime('now')")
    params.append(record_id)

    execute_write(
        f"UPDATE narrative_principles SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    return {
        "result": {"principle_id": record_id, "updated": True},
        "_context": {"presentation": "Principle updated."},
    }


def _disable_principle(record_id):
    if not record_id:
        return {"error": "record_id is required."}
    existing = execute("SELECT principle FROM narrative_principles WHERE id = ?", (record_id,))
    if not existing:
        return {"error": f"No principle with id {record_id}."}

    execute_write(
        "UPDATE narrative_principles SET active = 0, updated_at = datetime('now') WHERE id = ?",
        (record_id,),
    )
    return {
        "result": {"principle_id": record_id, "active": False},
        "_context": {"presentation": f"Principle disabled: {existing[0]['principle'][:60]}..."},
    }


# ─── Creative Context ───────────────────────────────────────────────────────

def _add_context(project_id, project_name, context_type, title, content, status, tags):
    if not context_type:
        return {"error": "context_type is required."}
    if context_type not in VALID_CONTEXT_TYPES:
        return {"error": f"Invalid context_type '{context_type}'. Use: {', '.join(VALID_CONTEXT_TYPES)}"}
    if not title:
        return {"error": "title is required."}
    if not content:
        return {"error": "content is required."}

    pid = _resolve_project(project_id, project_name)
    st = status if status and status in VALID_CONTEXT_STATUSES else "active"

    rid = execute_write(
        """INSERT INTO creative_context (project_id, context_type, title, content, status, tags)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (pid, context_type, title, content, st, tags or None),
    )
    execute_write(
        """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
           VALUES ('creative_context', ?, 'context_added', ?, datetime('now'))""",
        (rid, f"[{context_type}] {title}"),
    )

    return {
        "result": {"context_id": rid, "context_type": context_type, "title": title, "project_id": pid},
        "_context": {
            "suggestions": [
                "Add more context entries to build a richer project profile",
                "Use get_project_profile to see the full creative context for this project",
            ],
            "presentation": f"Context entry added: **{title}** ({context_type}).",
        },
    }


def _list_context(project_id, context_type, status):
    conditions = []
    params = []
    if project_id:
        conditions.append("cc.project_id = ?")
        params.append(project_id)
    if context_type:
        conditions.append("cc.context_type = ?")
        params.append(context_type)
    if status:
        conditions.append("cc.status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = execute(
        f"""SELECT cc.id, cc.context_type, cc.title, cc.status, cc.tags, cc.created_at, cc.updated_at,
                   p.name as project_name
            FROM creative_context cc
            LEFT JOIN projects p ON cc.project_id = p.id
            {where}
            ORDER BY cc.project_id, cc.context_type, cc.created_at DESC""",
        tuple(params),
    )
    return {
        "result": rows_to_dicts(rows),
        "count": len(rows),
        "_context": {
            "suggestions": ["Use get_context with record_id for full content"],
            "presentation": "Table: type, title, status, project, date. Group by project if multiple.",
        },
    }


def _get_context(record_id):
    if not record_id:
        return {"error": "record_id is required."}
    rows = execute(
        """SELECT cc.*, p.name as project_name
           FROM creative_context cc
           LEFT JOIN projects p ON cc.project_id = p.id
           WHERE cc.id = ?""",
        (record_id,),
    )
    if not rows:
        return {"error": f"No context entry with id {record_id}."}
    return {"result": rows_to_dicts(rows)[0]}


def _update_context(record_id, context_type, title, content, status, tags):
    if not record_id:
        return {"error": "record_id is required."}
    existing = execute("SELECT * FROM creative_context WHERE id = ?", (record_id,))
    if not existing:
        return {"error": f"No context entry with id {record_id}."}

    updates = []
    params = []
    if context_type and context_type in VALID_CONTEXT_TYPES:
        updates.append("context_type = ?")
        params.append(context_type)
    if title:
        updates.append("title = ?")
        params.append(title)
    if content:
        updates.append("content = ?")
        params.append(content)
    if status and status in VALID_CONTEXT_STATUSES:
        updates.append("status = ?")
        params.append(status)
    if tags:
        updates.append("tags = ?")
        params.append(tags)

    if not updates:
        return {"error": "No fields to update."}

    updates.append("updated_at = datetime('now')")
    params.append(record_id)

    execute_write(
        f"UPDATE creative_context SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    return {
        "result": {"context_id": record_id, "updated": True},
        "_context": {"presentation": "Context entry updated."},
    }


# ─── Sessions ───────────────────────────────────────────────────────────────

def _log_session(project_id, project_name, session_date, observations,
                 decisions_made, open_questions, scenes_worked, mode, notes):
    pid = _resolve_project(project_id, project_name)

    # Get current mode if not specified
    if not mode:
        rows = execute("SELECT value FROM soy_meta WHERE key = 'creative_style_mode'", ())
        mode = rows[0]["value"] if rows else "raw"

    rid = execute_write(
        """INSERT INTO creative_sessions
           (project_id, session_date, observations, decisions_made,
            open_questions, scenes_worked, mode_used, notes)
           VALUES (?, COALESCE(?, date('now')), ?, ?, ?, ?, ?, ?)""",
        (pid, session_date or None, observations or None, decisions_made or None,
         open_questions or None, scenes_worked or None, mode, notes or None),
    )
    execute_write(
        """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
           VALUES ('creative_session', ?, 'session_logged', ?, datetime('now'))""",
        (rid, f"Creative session logged (mode: {mode})"),
    )

    return {
        "result": {"session_id": rid, "mode_used": mode, "project_id": pid},
        "_context": {
            "suggestions": [
                "Capture new narrative principles from this session with add_principle",
                "Update creative context entries based on decisions made",
            ],
            "presentation": "Session logged. Summarize what was captured.",
        },
    }


def _list_sessions(project_id):
    conditions = []
    params = []
    if project_id:
        conditions.append("cs.project_id = ?")
        params.append(project_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = execute(
        f"""SELECT cs.*, p.name as project_name
            FROM creative_sessions cs
            LEFT JOIN projects p ON cs.project_id = p.id
            {where}
            ORDER BY cs.session_date DESC""",
        tuple(params),
    )
    return {
        "result": rows_to_dicts(rows),
        "count": len(rows),
        "_context": {"presentation": "Table: date, project, mode, summary of observations."},
    }


# ─── Profile & Drift ────────────────────────────────────────────────────────

def _get_profile():
    """Assemble the full creative identity for prompt injection."""
    # Current mode
    mode_rows = execute("SELECT value FROM soy_meta WHERE key = 'creative_style_mode'", ())
    mode = mode_rows[0]["value"] if mode_rows else "raw"

    result = {"mode": mode}

    # Narrative principles (always included unless mode is raw)
    if mode != "raw":
        principles = execute(
            """SELECT category, principle, weight
               FROM narrative_principles
               WHERE active = 1
               ORDER BY category, weight DESC""",
            (),
        )
        grouped = {}
        for r in rows_to_dicts(principles):
            cat = r["category"]
            if cat not in grouped:
                grouped[cat] = []
            grouped[cat].append({"principle": r["principle"], "weight": r["weight"]})
        result["narrative_principles"] = grouped

    # Mechanical baseline (only in learned mode)
    if mode == "learned":
        baseline_rows = execute(
            """SELECT ROUND(AVG(avg_sentence_length), 1) as avg_sentence_length,
                      ROUND(AVG(dialogue_ratio), 3) as avg_dialogue_ratio,
                      ROUND(AVG(avg_paragraph_length), 1) as avg_paragraph_length,
                      COUNT(*) as sample_count
               FROM writing_samples
               WHERE source_type IN ('user_written', 'ai_approved')""",
            (),
        )
        if baseline_rows:
            result["mechanical_baseline"] = rows_to_dicts(baseline_rows)[0]

    return {
        "result": result,
        "_context": {
            "presentation": "Format as injectable creative profile. Group principles by category.",
            "usage": "Inject this into system prompts when doing creative work. "
                     "In learned mode, flag deviations from mechanical baseline. "
                     "In exploratory mode, apply principles but don't constrain form.",
        },
    }


def _get_project_profile(project_id, project_name):
    """Assemble project-specific creative context."""
    pid = _resolve_project(project_id, project_name)
    if not pid:
        return {"error": "project_id or project_name is required."}

    # Project info
    proj_rows = execute("SELECT name, description FROM projects WHERE id = ?", (pid,))
    project_info = rows_to_dicts(proj_rows)[0] if proj_rows else {}

    # All active context entries
    context_rows = execute(
        """SELECT context_type, title, content, status, tags
           FROM creative_context
           WHERE project_id = ? AND status IN ('active', 'draft')
           ORDER BY context_type, created_at""",
        (pid,),
    )
    grouped = {}
    for r in rows_to_dicts(context_rows):
        ct = r["context_type"]
        if ct not in grouped:
            grouped[ct] = []
        grouped[ct].append(r)

    # Recent sessions
    session_rows = execute(
        """SELECT session_date, observations, decisions_made, open_questions, scenes_worked
           FROM creative_sessions
           WHERE project_id = ?
           ORDER BY session_date DESC
           LIMIT 5""",
        (pid,),
    )

    return {
        "result": {
            "project": project_info,
            "context": grouped,
            "recent_sessions": rows_to_dicts(session_rows),
            "context_entry_count": len(context_rows),
        },
        "_context": {
            "presentation": "Format as project creative brief. Group context by type. "
                            "Highlight open questions and recent decisions.",
            "usage": "Inject this when working on this specific creative project.",
        },
    }


def _check_drift(text):
    """Compare text metrics against the mechanical baseline."""
    if not text:
        return {"error": "text is required."}

    # Compute metrics for the input text
    sample_metrics = _compute_metrics(text)

    # Get baseline
    baseline_rows = execute(
        """SELECT ROUND(AVG(avg_sentence_length), 1) as avg_sentence_length,
                  ROUND(AVG(dialogue_ratio), 3) as avg_dialogue_ratio,
                  ROUND(AVG(avg_paragraph_length), 1) as avg_paragraph_length,
                  COUNT(*) as sample_count
           FROM writing_samples
           WHERE source_type IN ('user_written', 'ai_approved')""",
        (),
    )
    baseline = rows_to_dicts(baseline_rows)[0] if baseline_rows else {}

    if not baseline.get("sample_count"):
        return {
            "result": {"sample_metrics": sample_metrics, "baseline": None},
            "_context": {
                "presentation": "No baseline yet — showing metrics for this text only.",
                "suggestions": ["Add writing samples to build a baseline for drift detection"],
            },
        }

    # Compute drift flags
    flags = []
    b_sl = baseline.get("avg_sentence_length")
    s_sl = sample_metrics.get("avg_sentence_length")
    if b_sl and s_sl:
        pct = abs(s_sl - b_sl) / b_sl * 100
        if pct > 30:
            direction = "longer" if s_sl > b_sl else "shorter"
            flags.append(f"Sentence length is {pct:.0f}% {direction} than baseline ({s_sl} vs {b_sl} words/sentence)")

    b_dr = baseline.get("avg_dialogue_ratio")
    s_dr = sample_metrics.get("dialogue_ratio")
    if b_dr and s_dr and b_dr > 0:
        pct = abs(s_dr - b_dr) / b_dr * 100
        if pct > 40:
            direction = "more" if s_dr > b_dr else "less"
            flags.append(f"Dialogue ratio is {pct:.0f}% {direction} than baseline ({s_dr:.1%} vs {b_dr:.1%})")

    b_pl = baseline.get("avg_paragraph_length")
    s_pl = sample_metrics.get("avg_paragraph_length")
    if b_pl and s_pl:
        pct = abs(s_pl - b_pl) / b_pl * 100
        if pct > 30:
            direction = "denser" if s_pl > b_pl else "sparser"
            flags.append(f"Paragraphs are {pct:.0f}% {direction} than baseline ({s_pl} vs {b_pl} sentences/para)")

    return {
        "result": {
            "sample_metrics": sample_metrics,
            "baseline": baseline,
            "drift_flags": flags,
            "within_baseline": len(flags) == 0,
        },
        "_context": {
            "presentation": "Show drift flags prominently if any. Show side-by-side metrics.",
            "note": f"Based on {baseline.get('sample_count', 0)} samples. "
                    "More samples = more reliable drift detection.",
        },
    }


# ─── Annotations ─────────────────────────────────────────────────────────

def _add_annotation(context_id, highlighted_text, note, annotation_type, author):
    if not context_id:
        return {"error": "record_id (context_id) is required."}
    if not highlighted_text:
        return {"error": "highlighted_text is required."}
    if not note:
        return {"error": "notes is required."}

    at = annotation_type if annotation_type and annotation_type in VALID_ANNOTATION_TYPES else "observation"
    au = author if author and author in ("user", "ai") else "ai"

    # Verify context exists
    existing = execute("SELECT id, title FROM creative_context WHERE id = ?", (context_id,))
    if not existing:
        return {"error": f"No context entry with id {context_id}."}

    rid = execute_write(
        """INSERT INTO lore_annotations (context_id, highlighted_text, note, annotation_type, author)
           VALUES (?, ?, ?, ?, ?)""",
        (context_id, highlighted_text, note, at, au),
    )
    execute_write(
        """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
           VALUES ('lore_annotation', ?, 'annotation_added', ?, datetime('now'))""",
        (rid, f"[{at}] on '{existing[0]['title']}': {note[:80]}"),
    )

    return {
        "result": {"annotation_id": rid, "context_id": context_id, "type": at, "author": au},
        "_context": {
            "suggestions": ["Use list_annotations to see all pending annotations",
                            "Use review_annotation to research and resolve"],
            "presentation": f"Annotation added to **{existing[0]['title']}** ({at}).",
        },
    }


def _list_annotations(context_id, project_id, status):
    conditions = []
    params = []
    if context_id:
        conditions.append("la.context_id = ?")
        params.append(context_id)
    if project_id:
        conditions.append("cc.project_id = ?")
        params.append(project_id)
    if status:
        conditions.append("la.status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = execute(
        f"""SELECT la.*, cc.title as context_title, cc.context_type
            FROM lore_annotations la
            JOIN creative_context cc ON la.context_id = cc.id
            {where}
            ORDER BY la.created_at DESC""",
        tuple(params),
    )

    results = rows_to_dicts(rows)

    # Group by context entry for presentation
    by_entry = {}
    for r in results:
        key = r["context_title"]
        if key not in by_entry:
            by_entry[key] = []
        by_entry[key].append(r)

    # Summary
    type_counts = {}
    for r in results:
        t = r["annotation_type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    return {
        "result": results,
        "count": len(results),
        "by_entry": {k: len(v) for k, v in by_entry.items()},
        "type_counts": type_counts,
        "_context": {
            "suggestions": ["Use review_annotation with record_id to research and resolve each annotation"],
            "presentation": "Group by context entry. Show highlighted text, note, type, and status.",
            "review_workflow": "For each pending annotation: research the claim, compare against FFX canon, "
                               "then use review_annotation to mark as verified/revised with your findings.",
        },
    }


def _review_annotation(record_id, status, research_response, revision_made):
    if not record_id:
        return {"error": "record_id is required."}

    existing = execute(
        """SELECT la.*, cc.title as context_title, cc.id as ctx_id
           FROM lore_annotations la
           JOIN creative_context cc ON la.context_id = cc.id
           WHERE la.id = ?""",
        (record_id,),
    )
    if not existing:
        return {"error": f"No annotation with id {record_id}."}

    ann = rows_to_dicts(existing)[0]

    if status and status not in VALID_ANNOTATION_STATUSES:
        return {"error": f"Invalid status. Use: {', '.join(VALID_ANNOTATION_STATUSES)}"}

    updates = []
    params = []
    if status:
        updates.append("status = ?")
        params.append(status)
    if research_response:
        updates.append("research_response = ?")
        params.append(research_response)
    if revision_made:
        updates.append("revision_made = ?")
        params.append(revision_made)

    if not updates:
        return {"error": "Provide status, research_response, or revision_made."}

    updates.append("reviewed_at = datetime('now')")
    updates.append("updated_at = datetime('now')")
    params.append(record_id)

    execute_write(
        f"UPDATE lore_annotations SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )

    return {
        "result": {
            "annotation_id": record_id,
            "context_title": ann["context_title"],
            "status": status or ann["status"],
            "reviewed": True,
        },
        "_context": {
            "suggestions": [
                "If status is 'revised', update the source context entry with update_context",
                "Use list_annotations to see remaining pending annotations",
            ],
        },
    }


# ─── Creative Threads ────────────────────────────────────────────────────

def _add_thread(project_id, project_name, prompt, thread_type, author, tags):
    if not prompt:
        return {"error": "prompt is required."}

    pid = _resolve_project(project_id, project_name)
    tt = thread_type if thread_type and thread_type in VALID_THREAD_TYPES else "question"
    au = author if author and author in ("user", "ai") else "ai"

    rid = execute_write(
        """INSERT INTO creative_threads (project_id, author, thread_type, prompt, tags)
           VALUES (?, ?, ?, ?, ?)""",
        (pid, au, tt, prompt, tags or None),
    )
    execute_write(
        """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
           VALUES ('creative_thread', ?, 'thread_created', ?, datetime('now'))""",
        (rid, f"[{tt}] {prompt[:80]}"),
    )

    return {
        "result": {"thread_id": rid, "type": tt, "author": au},
        "_context": {
            "presentation": f"Creative thread started ({tt}).",
            "suggestions": ["This will appear in the Ongoing Thoughts section of the dashboard"],
        },
    }


def _list_threads(project_id, status):
    conditions = []
    params = []
    if project_id:
        conditions.append("ct.project_id = ?")
        params.append(project_id)
    if status:
        conditions.append("ct.status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = execute(
        f"""SELECT ct.*, p.name as project_name
            FROM creative_threads ct
            LEFT JOIN projects p ON ct.project_id = p.id
            {where}
            ORDER BY
                CASE ct.status WHEN 'open' THEN 0 WHEN 'answered' THEN 1 ELSE 2 END,
                ct.created_at DESC""",
        tuple(params),
    )

    return {
        "result": rows_to_dicts(rows),
        "count": len(rows),
        "_context": {
            "suggestions": ["Reply to open threads with reply_thread",
                            "Start new threads with add_thread to provoke creative thinking"],
            "presentation": "Show open threads prominently. Group by status.",
        },
    }


def _reply_thread(record_id, response):
    if not record_id:
        return {"error": "record_id is required."}
    if not response:
        return {"error": "response is required."}

    existing = execute("SELECT * FROM creative_threads WHERE id = ?", (record_id,))
    if not existing:
        return {"error": f"No thread with id {record_id}."}

    execute_write(
        """UPDATE creative_threads
           SET response = ?, status = 'answered', updated_at = datetime('now')
           WHERE id = ?""",
        (response, record_id),
    )

    return {
        "result": {"thread_id": record_id, "status": "answered"},
        "_context": {"presentation": "Thread answered."},
    }
