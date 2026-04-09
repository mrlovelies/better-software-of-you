"""
Structured Answer Formatter — Transform raw execution results into GUI-renderable JSON.

Five output formats:
  1. data_table       — Columnar data with sort/link metadata
  2. prioritized_list — Ranked items with badges, actions, entity links
  3. summary_card     — Single-entity overview with stats and narrative
  4. insight_synthesis — Three-part card (data / insight / action)
  5. metric_snapshot  — Single KPI with trend and breakdown

Usage:
    from formatter import format_answer

    structured = format_answer(question, execution_result)
"""

import re
from datetime import datetime


# ---------------------------------------------------------------------------
# Column type inference
# ---------------------------------------------------------------------------

_DATE_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}"),           # 2026-04-07...
    re.compile(r"^\d{2}/\d{2}/\d{4}$"),           # 04/07/2026
]

_NUMBER_THRESHOLD = 0.8  # fraction of non-null values that must be numeric


def _infer_column_type(values: list) -> str:
    """Infer column type from a sample of values."""
    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_null:
        return "text"

    # Check date
    date_hits = sum(
        1 for v in non_null
        if any(p.match(str(v)) for p in _DATE_PATTERNS)
    )
    if date_hits / len(non_null) > _NUMBER_THRESHOLD:
        return "date"

    # Check number
    num_hits = 0
    for v in non_null:
        try:
            float(v)
            num_hits += 1
        except (ValueError, TypeError):
            pass
    if num_hits / len(non_null) >= _NUMBER_THRESHOLD:
        return "number"

    return "text"


def _humanize_column_key(key: str) -> str:
    """Turn snake_case column keys into human-readable labels."""
    return key.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Format: data_table
# ---------------------------------------------------------------------------

def _format_data_table(question: dict, result: dict) -> dict:
    """Build a data_table response from query results."""
    # Merge all query result sets — use the first non-empty one as primary
    rows_raw = []
    for key, query_result in result.get("queries", {}).items():
        qr = query_result.get("rows", [])
        if qr:
            rows_raw = qr
            break

    if not rows_raw:
        return {
            "format": "data_table",
            "columns": [],
            "rows": [],
            "row_count": 0,
            "entity_links": [],
            "sort_key": None,
            "sort_direction": "desc",
        }

    # Detect columns from the first row
    if isinstance(rows_raw[0], dict):
        col_keys = list(rows_raw[0].keys())
    else:
        col_keys = [f"col_{i}" for i in range(len(rows_raw[0]))]
        rows_raw = [{col_keys[i]: row[i] for i in range(len(row))} for row in rows_raw]

    # Infer column types
    columns = []
    for key in col_keys:
        sample = [row.get(key) for row in rows_raw[:50]]
        col_type = _infer_column_type(sample)
        columns.append({
            "key": key,
            "label": _humanize_column_key(key),
            "type": col_type,
        })

    # Build clean rows
    rows = []
    for row in rows_raw:
        clean = {}
        for key in col_keys:
            val = row.get(key)
            clean[key] = val
        rows.append(clean)

    # Entity link detection — look for id-like columns paired with name columns
    entity_links = []
    id_col = None
    name_col = None
    entity_type = None

    for key in col_keys:
        kl = key.lower()
        if kl in ("contact_id", "id", "entity_id", "project_id"):
            id_col = key
            if "contact" in kl:
                entity_type = "contact"
            elif "project" in kl:
                entity_type = "project"
            else:
                entity_type = "entity"
        if kl in ("name", "contact_name", "entity_name", "project_name", "title"):
            name_col = key

    if id_col and name_col:
        for i, row in enumerate(rows):
            eid = row.get(id_col)
            if eid is not None:
                entity_links.append({
                    "row": i,
                    "column": name_col,
                    "entity_type": entity_type or "entity",
                    "entity_id": eid,
                })

    # Pick a default sort key — prefer numeric columns that suggest priority
    sort_key = None
    sort_direction = "desc"
    priority_names = {"days_silent", "days_overdue", "days_value", "urgency", "relevance_score", "email_count"}
    for col in columns:
        if col["key"] in priority_names:
            sort_key = col["key"]
            break
    if not sort_key and columns:
        # Fall back to first numeric column
        for col in columns:
            if col["type"] == "number":
                sort_key = col["key"]
                break

    return {
        "format": "data_table",
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "entity_links": entity_links,
        "sort_key": sort_key,
        "sort_direction": sort_direction,
    }


# ---------------------------------------------------------------------------
# Format: prioritized_list
# ---------------------------------------------------------------------------

def _format_prioritized_list(question: dict, result: dict) -> dict:
    """Build a prioritized_list from query results or LLM output."""
    items = []

    # If the result has an LLM response, try to parse structured items from it
    llm_text = result.get("llm_response", "")
    if llm_text:
        items = _parse_llm_prioritized(llm_text)
        if items:
            return {"format": "prioritized_list", "items": items}

    # Otherwise build from query rows
    rows_raw = []
    for key, query_result in result.get("queries", {}).items():
        qr = query_result.get("rows", [])
        if qr:
            rows_raw = qr
            break

    for rank, row in enumerate(rows_raw, start=1):
        if isinstance(row, dict):
            rd = row
        else:
            rd = {"value": row}

        # Extract meaningful fields
        title = (
            rd.get("entity_name")
            or rd.get("name")
            or rd.get("title")
            or rd.get("nudge_type", "")
        )
        subtitle = rd.get("description") or rd.get("company") or rd.get("extra_context") or ""
        body = rd.get("extra_context") or rd.get("description") or ""

        # Badge from tier or severity
        badge = None
        tier = rd.get("tier") or rd.get("urgency") or rd.get("urgency_tier")
        if tier:
            color_map = {"urgent": "red", "overdue": "red", "soon": "amber", "aging": "amber", "awareness": "blue", "fresh": "green"}
            badge = {"text": str(tier), "color": color_map.get(str(tier).lower(), "gray")}

        # Entity link
        entity_link = None
        eid = rd.get("entity_id") or rd.get("contact_id") or rd.get("project_id") or rd.get("id")
        etype = rd.get("entity_type") or rd.get("nudge_type")
        if eid and etype:
            # Normalize nudge types to entity types
            type_map = {"cold_contact": "contact", "stale_project": "project", "overdue_commitment": "commitment"}
            entity_link = {"type": type_map.get(etype, etype), "id": eid}

        item = {
            "rank": rank,
            "title": str(title),
            "subtitle": str(subtitle) if subtitle != body else "",
            "body": str(body),
        }
        if badge:
            item["badge"] = badge
        if entity_link:
            item["entity_link"] = entity_link

        items.append(item)

    return {"format": "prioritized_list", "items": items}


def _parse_llm_prioritized(text: str) -> list:
    """Try to parse a numbered/bulleted list from LLM text into prioritized items."""
    items = []
    # Match lines like "1. **Name** — reason" or "- Name: reason"
    pattern = re.compile(r"^\s*(?:\d+[\.\)]\s*|\-\s*|\*\s*)(?:\*\*)?(.+?)(?:\*\*)?\s*[\—\-:]\s*(.+)$", re.MULTILINE)
    for rank, match in enumerate(pattern.finditer(text), start=1):
        title = match.group(1).strip().strip("*")
        body = match.group(2).strip()
        items.append({
            "rank": rank,
            "title": title,
            "subtitle": "",
            "body": body,
        })
    return items


# ---------------------------------------------------------------------------
# Format: summary_card
# ---------------------------------------------------------------------------

def _format_summary_card(question: dict, result: dict) -> dict:
    """Build a summary_card from query results."""
    # Use the first row of the first query as the primary entity
    primary_row = None
    for key, query_result in result.get("queries", {}).items():
        rows = query_result.get("rows", [])
        if rows:
            primary_row = rows[0] if isinstance(rows[0], dict) else {}
            break

    if not primary_row:
        return {
            "format": "summary_card",
            "title": "No data",
            "subtitle": "",
            "stats": [],
            "narrative": "",
            "entity_link": None,
        }

    title = primary_row.get("name") or primary_row.get("title") or primary_row.get("entity_name") or ""
    subtitle = primary_row.get("company") or primary_row.get("project_name") or ""

    # Build stats from numeric fields
    stats = []
    skip_keys = {"id", "contact_id", "project_id", "entity_id", "name", "title", "company", "email", "entity_name", "project_name"}
    for key, value in primary_row.items():
        if key.lower() in skip_keys or key.startswith("_"):
            continue
        if value is None:
            continue

        stat = {"label": _humanize_column_key(key), "value": value}

        # Determine format
        try:
            float(value)
            stat["format"] = "number"
        except (ValueError, TypeError):
            # Check for badge-like values
            badge_values = {"improving", "stable", "declining", "active", "stale", "cold", "hot"}
            if str(value).lower() in badge_values:
                color_map = {"improving": "green", "stable": "blue", "active": "green", "hot": "red", "declining": "red", "stale": "amber", "cold": "blue"}
                stat["format"] = "badge"
                stat["color"] = color_map.get(str(value).lower(), "gray")
            else:
                stat["format"] = "text"

        stats.append(stat)

    # Entity link
    entity_link = None
    eid = primary_row.get("id") or primary_row.get("contact_id") or primary_row.get("project_id")
    if eid:
        if "contact_id" in primary_row or "name" in primary_row:
            entity_link = {"type": "contact", "id": eid}
        elif "project_id" in primary_row or "project_name" in primary_row:
            entity_link = {"type": "project", "id": eid}

    # Narrative from LLM if present
    narrative = result.get("llm_response", "")

    return {
        "format": "summary_card",
        "title": str(title),
        "subtitle": str(subtitle),
        "stats": stats,
        "narrative": narrative,
        "entity_link": entity_link,
    }


# ---------------------------------------------------------------------------
# Format: insight_synthesis
# ---------------------------------------------------------------------------

def _format_insight_synthesis(question: dict, result: dict) -> dict:
    """Build the three-part data/insight/action card."""
    # Check for static_answer first (onboarding questions)
    static = question.get("static_answer")
    if static:
        return {
            "format": "insight_synthesis",
            "sections": [
                {"type": "data", "color": "blue", "label": "What the data shows", "content": static.get("data", "")},
                {"type": "insight", "color": "amber", "label": "What this means", "content": static.get("insight", "")},
                {"type": "action", "color": "green", "label": "What to do about it", "content": static.get("action", "")},
            ],
            "source_quality": "static",
            "confidence": 1.0,
        }

    # Try to parse LLM response into sections
    llm_text = result.get("llm_response", "")
    if llm_text:
        sections = _parse_llm_sections(llm_text)
        source = result.get("llm_tier", "local_llm")
        return {
            "format": "insight_synthesis",
            "sections": sections,
            "source_quality": source,
            "confidence": result.get("confidence", 0.7),
        }

    # Fallback: build from raw query data
    data_lines = []
    for key, query_result in result.get("queries", {}).items():
        rows = query_result.get("rows", [])
        count = len(rows)
        if count > 0:
            data_lines.append(f"{_humanize_column_key(key)}: {count} results")
        else:
            data_lines.append(f"{_humanize_column_key(key)}: no data")

    return {
        "format": "insight_synthesis",
        "sections": [
            {"type": "data", "color": "blue", "label": "What the data shows", "content": "\n".join(data_lines) or "No data available."},
            {"type": "insight", "color": "amber", "label": "What this means", "content": "Insufficient context for deeper analysis — try providing more data."},
            {"type": "action", "color": "green", "label": "What to do about it", "content": ""},
        ],
        "source_quality": "computed_view",
        "confidence": 0.5,
    }


def _parse_llm_sections(text: str) -> list:
    """Parse LLM output into data/insight/action sections.

    Looks for markers like:
      ## What the data shows / ## Data / **Data:**
      ## What this means / ## Insight / **Insight:**
      ## What to do / ## Action / **Action:**

    Falls back to putting the whole response in the data section.
    """
    section_patterns = [
        # Header-style markers
        (r"(?:^|\n)#{1,3}\s*(?:what the data shows|data)\s*\n(.*?)(?=\n#{1,3}\s|$)",
         r"(?:^|\n)#{1,3}\s*(?:what this means|insight)\s*\n(.*?)(?=\n#{1,3}\s|$)",
         r"(?:^|\n)#{1,3}\s*(?:what to do|action|next steps?|recommendation)\s*\n(.*?)(?=\n#{1,3}\s|$)"),
        # Bold-style markers
        (r"\*\*(?:Data|What the data shows)[:\*]*\*?\*?\s*(.*?)(?=\*\*(?:Insight|What this means|Action)|$)",
         r"\*\*(?:Insight|What this means)[:\*]*\*?\*?\s*(.*?)(?=\*\*(?:Action|What to do|Next)|$)",
         r"\*\*(?:Action|What to do|Next steps?|Recommendation)[:\*]*\*?\*?\s*(.*?)$"),
    ]

    for data_pat, insight_pat, action_pat in section_patterns:
        data_m = re.search(data_pat, text, re.IGNORECASE | re.DOTALL)
        insight_m = re.search(insight_pat, text, re.IGNORECASE | re.DOTALL)
        action_m = re.search(action_pat, text, re.IGNORECASE | re.DOTALL)

        if data_m or insight_m or action_m:
            return [
                {"type": "data", "color": "blue", "label": "What the data shows",
                 "content": (data_m.group(1).strip() if data_m else "").strip()},
                {"type": "insight", "color": "amber", "label": "What this means",
                 "content": (insight_m.group(1).strip() if insight_m else "").strip()},
                {"type": "action", "color": "green", "label": "What to do about it",
                 "content": (action_m.group(1).strip() if action_m else "").strip()},
            ]

    # No sections found — put the whole response in data
    return [
        {"type": "data", "color": "blue", "label": "What the data shows", "content": text.strip()},
        {"type": "insight", "color": "amber", "label": "What this means", "content": ""},
        {"type": "action", "color": "green", "label": "What to do about it", "content": ""},
    ]


# ---------------------------------------------------------------------------
# Format: metric_snapshot
# ---------------------------------------------------------------------------

def _format_metric_snapshot(question: dict, result: dict) -> dict:
    """Build a metric_snapshot from query results."""
    # Look for summary-style queries first
    summary_row = None
    detail_rows = []

    for key, query_result in result.get("queries", {}).items():
        rows = query_result.get("rows", [])
        if key in ("summary", "totals", "counts") and rows:
            summary_row = rows
        elif rows:
            detail_rows = rows

    # If we have a summary with tier/count pairs, use that as breakdown
    breakdown = []
    primary_value = None
    primary_label = question.get("short_label") or question.get("label", "")

    if summary_row:
        total = 0
        for row in summary_row:
            if isinstance(row, dict):
                label = row.get("tier") or row.get("label") or row.get("category") or ""
                value = row.get("count") or row.get("n") or row.get("value") or 0
            else:
                label = str(row[0]) if len(row) > 0 else ""
                value = row[1] if len(row) > 1 else 0
            try:
                value = int(value)
            except (ValueError, TypeError):
                value = 0
            breakdown.append({"label": str(label).title(), "value": value})
            total += value
        primary_value = str(total)
    elif detail_rows:
        # Use the count of detail rows as the primary metric
        primary_value = str(len(detail_rows))
    else:
        primary_value = "0"

    return {
        "format": "metric_snapshot",
        "primary_value": primary_value,
        "primary_label": primary_label,
        "trend": None,
        "breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_FORMAT_HANDLERS = {
    "data_table": _format_data_table,
    "prioritized_list": _format_prioritized_list,
    "summary_card": _format_summary_card,
    "insight_synthesis": _format_insight_synthesis,
    "metric_snapshot": _format_metric_snapshot,
}


def format_answer(question: dict, execution_result: dict) -> dict:
    """Format raw execution results into structured answer JSON.

    Args:
        question: The QPack question definition (with id, answer_format, etc.)
        execution_result: Raw results from the executor, containing:
            - queries: dict of {key: {"rows": [...], "columns": [...], "row_count": int}}
            - llm_response: optional string from LLM processing
            - llm_tier: optional string indicating LLM source
            - confidence: optional float

    Returns:
        Structured JSON dict matching one of the 5 answer formats.
    """
    # Normalize result shape — execute_question() returns context_data,
    # serve.py returns queries. Accept both.
    if "queries" not in execution_result and "context_data" in execution_result:
        normalized_queries = {}
        for key, val in execution_result["context_data"].items():
            if isinstance(val, list):
                cols = list(val[0].keys()) if val else []
                normalized_queries[key] = {"rows": val, "columns": cols, "row_count": len(val)}
            elif isinstance(val, dict) and "error" in val:
                normalized_queries[key] = {"rows": [], "columns": [], "row_count": 0, "error": val["error"]}
            else:
                normalized_queries[key] = {"rows": [], "columns": [], "row_count": 0}
        execution_result = dict(execution_result)
        execution_result["queries"] = normalized_queries

    answer_format = question.get("answer_format", "data_table")
    handler = _FORMAT_HANDLERS.get(answer_format, _format_data_table)

    try:
        formatted = handler(question, execution_result)
    except Exception as e:
        # Fallback: return error as insight_synthesis
        formatted = {
            "format": "insight_synthesis",
            "sections": [
                {"type": "data", "color": "blue", "label": "What the data shows", "content": f"Formatting error: {e}"},
                {"type": "insight", "color": "amber", "label": "What this means", "content": "The answer could not be formatted."},
                {"type": "action", "color": "green", "label": "What to do about it", "content": "Try running the question again or check the QPack definition."},
            ],
            "source_quality": "error",
            "confidence": 0.0,
        }

    # Attach metadata
    formatted["_meta"] = {
        "question_id": question.get("id"),
        "question_label": question.get("label"),
        "formatted_at": datetime.now().isoformat(),
        "requires_llm": question.get("requires_llm", False),
    }

    return formatted
