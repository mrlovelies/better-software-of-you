#!/usr/bin/env python3
"""
QPack Demo — Generates a self-contained interactive HTML page.

    python3 modules/qpack-generator/demo.py

All data is baked into the page at generation time. No API server needed.
Every click, search, and interaction works client-side from embedded data.
"""

import json
import sqlite3
import subprocess
import sys
import io
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
QPACK_DIR = Path(__file__).resolve().parents[2] / "qpacks"
OUTPUT_DIR = Path.home() / ".local" / "share" / "software-of-you" / "output"

# Spam/marketing patterns to filter from email response queue
EMAIL_SPAM_FILTERS = [
    "paypal", "pizza", "steam", "pinkcherry", "canfitpro", "uber",
    "1password", "linkedin", "github", "google", "slack", "newsletter",
    "noreply", "no-reply", "no_reply", "notifications", "digest",
    "automated", "mailer-daemon", "game developer", "gamesdeveloper",
    "gdcevents", "receipts", "security alert", "sign-in",
]


def _esc(s):
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _gather_data():
    """Query all data needed for the demo page."""
    # Run pipeline silently
    from pipeline import Pipeline
    from steps import ScanStep, TemplateStep, FilterStep, ValidateStep, AdaptStep, DeployStep

    buf = io.StringIO()
    with redirect_stdout(buf):
        p = Pipeline([ScanStep(), TemplateStep(), FilterStep(), ValidateStep(), AdaptStep(), DeployStep()])
        ctx = p.run()

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    # User name
    try:
        row = db.execute("SELECT value FROM user_profile WHERE category='identity' AND key='name'").fetchone()
        user_name = row["value"] if row else "there"
    except Exception:
        user_name = "there"

    data_state = ctx.get("data_state", {})
    deployed = ctx.get("deployed", [])

    # Load all QPack files and execute every question's context queries
    qpacks = {}
    question_results = {}

    for f in sorted(QPACK_DIR.glob("*.qpack.json")):
        try:
            qpack = json.loads(f.read_text())
            module = qpack.get("module", f.stem)
            qpacks[module] = qpack

            for q in qpack.get("questions", []):
                qid = q["id"]
                results = {}
                for cq in q.get("context_queries", []):
                    key = cq.get("key", "unknown")
                    sql = cq.get("sql", "")
                    try:
                        rows = db.execute(sql).fetchall()
                        results[key] = [dict(r) for r in rows]
                    except Exception as e:
                        results[key] = []

                question_results[qid] = {
                    "id": qid,
                    "label": q["label"],
                    "short_label": q.get("short_label", q["label"]),
                    "module": module,
                    "answer_format": q.get("answer_format", "data_table"),
                    "requires_llm": q.get("requires_llm", False),
                    "featured": q.get("featured", False),
                    "data": results,
                }
        except Exception:
            pass

    # Smart suggestions
    try:
        from suggestions import get_smart_suggestions
        suggestions = get_smart_suggestions()
    except Exception:
        suggestions = []

    # Email response queue — only show emails from known CRM contacts
    emails = [dict(r) for r in db.execute("""
        SELECT contact_name, from_name, from_address, subject, snippet, days_old, urgency, contact_id
        FROM v_email_response_queue
        WHERE contact_id IS NOT NULL
        ORDER BY CASE urgency WHEN 'overdue' THEN 0 WHEN 'aging' THEN 1 ELSE 2 END, days_old DESC
        LIMIT 15
    """).fetchall()]

    email_total_filtered = db.execute("""
        SELECT COUNT(*) as n FROM v_email_response_queue WHERE contact_id IS NOT NULL
    """).fetchone()["n"]

    # Contact health
    contacts = [dict(r) for r in db.execute("""
        SELECT id, name, company, email, days_silent, emails_30d, emails_inbound_30d,
               emails_outbound_30d, interactions_30d, transcripts_30d,
               your_open_commitments, their_open_commitments, active_projects,
               relationship_depth, trajectory, next_meeting, pending_follow_ups
        FROM v_contact_health ORDER BY days_silent ASC
    """).fetchall()]

    # Project health
    projects = [dict(r) for r in db.execute("""
        SELECT id, name, status, completion_pct, total_tasks, done_tasks,
               overdue_tasks, days_to_target, client_name, days_since_activity,
               next_milestone_name, next_milestone_date, open_commitments
        FROM v_project_health
        ORDER BY CASE WHEN overdue_tasks > 0 THEN 0 ELSE 1 END, completion_pct DESC
    """).fetchall()]

    # Nudge items
    nudges = [dict(r) for r in db.execute("""
        SELECT nudge_type, tier, entity_name, description, days_value, extra_context, icon,
               entity_id, contact_id, project_id
        FROM v_nudge_items
        ORDER BY CASE tier WHEN 'urgent' THEN 0 WHEN 'soon' THEN 1 ELSE 2 END, days_value DESC
    """).fetchall()]

    # Per-contact detail data (for entity panel clicks)
    contact_details = {}
    for c in contacts:
        cid = c["id"]
        detail = {"contact": c, "emails": [], "commitments": [], "interactions": []}
        try:
            detail["emails"] = [dict(r) for r in db.execute(
                "SELECT subject, snippet, direction, received_at, from_name FROM emails WHERE contact_id = ? ORDER BY received_at DESC LIMIT 10", (cid,)
            ).fetchall()]
        except Exception:
            pass
        try:
            detail["commitments"] = [dict(r) for r in db.execute(
                "SELECT description, status, is_user_commitment, deadline_date, urgency, owner_name FROM v_commitment_status WHERE owner_contact_id = ? OR involved_contact_name LIKE '%' || ? || '%'", (cid, c["name"])
            ).fetchall()]
        except Exception:
            pass
        try:
            detail["interactions"] = [dict(r) for r in db.execute(
                "SELECT type, subject, notes, occurred_at, direction FROM contact_interactions WHERE contact_id = ? ORDER BY occurred_at DESC LIMIT 10", (cid,)
            ).fetchall()]
        except Exception:
            pass
        contact_details[cid] = detail

    # Speed-to-lead template
    stl_template = Path(__file__).resolve().parent / "templates" / "speed-to-lead.json"
    stl = json.loads(stl_template.read_text()) if stl_template.exists() else None

    db.close()

    return {
        "user_name": user_name,
        "data_state": data_state,
        "deployed": deployed,
        "qpacks": qpacks,
        "question_results": question_results,
        "suggestions": suggestions,
        "emails": emails,
        "email_total": email_total_filtered,
        "contacts": contacts,
        "projects": projects,
        "nudges": nudges,
        "stl": stl,
        "contact_details": contact_details,
        "generated_at": datetime.now().isoformat(),
    }


def _build_html(data):
    """Build the self-contained HTML page."""
    name = _esc(data["user_name"])
    hour = datetime.now().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    ds = data["data_state"]
    deployed = data["deployed"]
    total_q = sum(d["questions"] for d in deployed)

    # Serialize data for JavaScript
    js_data = json.dumps({
        "questionResults": data["question_results"],
        "suggestions": data["suggestions"],
        "emails": data["emails"],
        "emailTotal": data["email_total"],
        "contacts": data["contacts"],
        "projects": data["projects"],
        "nudges": data["nudges"],
        "qpacks": {
            mod: {
                "persona": qp.get("persona", {}),
                "questions": [
                    {"id": q["id"], "label": q["label"], "featured": q.get("featured", False),
                     "requires_llm": q.get("requires_llm", False), "answer_format": q.get("answer_format", "data_table")}
                    for q in qp.get("questions", [])
                ]
            }
            for mod, qp in data["qpacks"].items()
        },
        "stl": {
            "persona": data["stl"]["persona"] if data["stl"] else {},
            "questions": [
                {"id": q["id"], "label": q["label"], "featured": q.get("featured", False),
                 "requires_llm": q.get("requires_llm", False)}
                for q in (data["stl"]["questions"] if data["stl"] else [])
            ]
        },
        "contactDetails": {str(k): v for k, v in data["contact_details"].items()},
    }, default=str, ensure_ascii=False)

    # Build sidebar modules
    sidebar_modules = ""
    for mod, qp in data["qpacks"].items():
        persona_name = _esc(qp.get("persona", {}).get("name", mod))
        qs = qp.get("questions", [])
        q_items = ""
        for q in qs:
            featured = '<span class="sq-dot"></span>' if q.get("featured") else ""
            llm_tag = '<span class="sq-tag">LLM</span>' if q.get("requires_llm") else ""
            q_items += f'<button class="sq-btn" onclick="event.stopPropagation(); showQuestion(\'{q["id"]}\')">{featured}{_esc(q["label"])}{llm_tag}</button>'
        sidebar_modules += f"""
        <div class="sm" onclick="this.classList.toggle('open')">
            <div class="sm-head">
                <span class="sm-name">{persona_name}</span>
                <span class="sm-count">{len(qs)}</span>
            </div>
            <div class="sm-qs">{q_items}</div>
        </div>"""

    # Build suggestion cards
    sug_colors = {"red": "#dc2626", "amber": "#d97706", "blue": "#2563eb", "green": "#16a34a", "zinc": "#52525b"}
    sug_html = ""
    for s in data["suggestions"][:3]:
        color = sug_colors.get(s.get("color", "zinc"), "#52525b")
        qid = s.get("qpack_id", "")
        sug_html += f"""
        <button class="sug" onclick="showQuestion('{_esc(qid)}')" style="--accent: {color}">
            <div class="sug-label">{_esc(s.get('label', ''))}</div>
            <div class="sug-id">{_esc(qid)}</div>
        </button>"""

    # Build project rows
    proj_html = ""
    for p in data["projects"][:12]:
        pct = p["completion_pct"] or 0
        done = p["done_tasks"] or 0
        total = p["total_tasks"] or 0
        overdue = p["overdue_tasks"] or 0
        stale = p["days_since_activity"] or 0
        client = f'<span class="meta">{_esc(p["client_name"])}</span>' if p["client_name"] else ""
        days_t = p["days_to_target"]

        badge = ""
        if overdue > 0:
            badge = f'<span class="pill pill-red">{overdue} overdue</span>'
        elif stale > 14:
            badge = f'<span class="pill pill-amber">stale {stale}d</span>'

        target = f'<span class="meta">due {days_t}d</span>' if days_t and days_t > 0 else ""
        bar_color = "#dc2626" if overdue > 0 else "#d97706" if stale > 14 else "#16a34a" if pct >= 50 else "#2563eb"

        proj_html += f"""
        <div class="prow">
            <div class="prow-top"><span class="prow-name">{_esc(p['name'])}</span> {client}</div>
            <div class="prow-bar">
                <div class="bar"><div class="bar-fill" style="width:{pct}%; background:{bar_color}"></div></div>
                <span class="prow-pct">{pct}%</span>
                <span class="meta">{done}/{total}</span>
                {badge} {target}
            </div>
        </div>"""

    # Build nudge items
    nudge_html = ""
    current_tier = None
    tier_colors = {"urgent": "#dc2626", "soon": "#d97706", "awareness": "#2563eb"}
    tier_labels = {"urgent": "Urgent", "soon": "Coming Up", "awareness": "Awareness"}
    for n in data["nudges"][:15]:
        tier = n["tier"]
        if tier != current_tier:
            c = tier_colors.get(tier, "#52525b")
            nudge_html += f'<div class="ntier" style="color:{c}">{tier_labels.get(tier, tier)}</div>'
            current_tier = tier
        nudge_html += f"""
        <div class="nitem">
            <div class="nitem-name">{_esc(n['entity_name'])}</div>
            <div class="nitem-desc">{_esc(n['description'])}</div>
            <div class="meta">{n['days_value'] or ''}d &middot; {_esc(n['nudge_type'])}</div>
        </div>"""

    # Build email rows
    email_html = ""
    for e in data["emails"][:8]:
        who = _esc(e["contact_name"] or e["from_name"] or e["from_address"])
        subj = _esc((e["subject"] or "")[:60])
        urgency = e["urgency"]
        uc = "#dc2626" if urgency == "overdue" else "#d97706" if urgency == "aging" else "#52525b"
        email_html += f"""
        <div class="erow">
            <div class="erow-top"><span style="color:{uc};font-weight:500">{who}</span></div>
            <div class="meta">{subj}</div>
            <div class="meta">{e['days_old']}d &middot; {urgency}</div>
        </div>"""
    remaining = data["email_total"] - min(8, len(data["emails"]))
    if remaining > 0:
        email_html += f'<div class="meta" style="padding:8px 0">+ {remaining} more</div>'

    # Build cold contacts
    cold = [c for c in data["contacts"] if (c["days_silent"] or 0) > 21]
    cold_html = ""
    if cold:
        for c in cold[:6]:
            cold_html += f"""
            <div class="crow">
                <div><span class="crow-name">{_esc(c['name'])}</span> <span class="meta">{_esc(c.get('company') or '')}</span></div>
                <div class="meta">{c['days_silent']}d silent &middot; {c['emails_30d'] or 0} emails/30d &middot; {_esc(c.get('trajectory') or '—')}</div>
            </div>"""
    else:
        cold_html = '<div class="empty-state">All relationships active</div>'

    # Build Jessica entity card
    jessica = next((c for c in data["contacts"] if "jessica" in (c["name"] or "").lower()), None)
    jessica_html = ""
    if jessica:
        depth = jessica.get("relationship_depth") or "—"
        traj = jessica.get("trajectory") or "—"
        silent = jessica.get("days_silent") or 0
        tc = "#16a34a" if traj == "improving" else "#dc2626" if traj == "declining" else "#52525b"
        sc = "#dc2626" if silent > 30 else "#d97706" if silent > 14 else "#16a34a"
        jessica_html = f"""
        <div class="entity">
            <div class="entity-name">{_esc(jessica['name'])} <span class="meta">{_esc(jessica.get('company') or '')}</span></div>
            <div class="stat-grid">
                <div class="stat"><div class="stat-val">{depth}<span class="meta">/10</span></div><div class="stat-lbl">Depth</div></div>
                <div class="stat"><div class="stat-val" style="color:{tc}">{_esc(traj)}</div><div class="stat-lbl">Trajectory</div></div>
                <div class="stat"><div class="stat-val" style="color:{sc}">{silent}d</div><div class="stat-lbl">Last Contact</div></div>
                <div class="stat"><div class="stat-val">{jessica.get('emails_30d') or 0}</div><div class="stat-lbl">Emails/30d</div></div>
                <div class="stat"><div class="stat-val">{jessica.get('active_projects') or 0}</div><div class="stat-lbl">Projects</div></div>
                <div class="stat"><div class="stat-val">{(jessica.get('your_open_commitments') or 0) + (jessica.get('their_open_commitments') or 0)}</div><div class="stat-lbl">Commitments</div></div>
            </div>
            <div class="entity-qs">
                <button class="sq-btn" onclick="showEntityDetail({jessica['id']}, 'relationship')">How is my relationship with {_esc(jessica['name'])}?</button>
                <button class="sq-btn" onclick="showEntityDetail({jessica['id']}, 'commitments')">What do I owe {_esc(jessica['name'])}?</button>
                <button class="sq-btn" onclick="showEntityDetail({jessica['id']}, 'emails')">What's {_esc(jessica['name'])} been emailing about?</button>
            </div>
        </div>"""

    # STL preview
    stl_html = ""
    if data["stl"]:
        for q in data["stl"]["questions"]:
            feat = ' <span class="pill pill-blue">featured</span>' if q.get("featured") else ""
            llm = ' <span class="sq-tag">LLM</span>' if q.get("requires_llm") else ""
            stl_html += f'<div class="stl-q">{_esc(q["label"])}{feat}{llm}</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SoY Desktop — QPack Demo</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
    --bg: #f5f5f4;
    --surface: #ffffff;
    --border: #e7e5e4;
    --text: #1c1917;
    --text-secondary: #78716c;
    --text-dim: #a8a29e;
    --accent: #4338ca;
    --accent-light: #eef2ff;
    --red: #dc2626;
    --amber: #d97706;
    --green: #16a34a;
    --blue: #2563eb;
    --radius: 10px;
    --shadow: 0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06);
    --shadow-lg: 0 4px 16px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04);
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Plus Jakarta Sans', -apple-system, sans-serif; background: var(--bg); color: var(--text); font-size: 14px; line-height: 1.5; }}
button {{ font-family: inherit; cursor: pointer; border: none; background: none; text-align: left; }}
code, .mono {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; }}

/* Layout */
.layout {{ display: flex; min-height: 100vh; }}
.sidebar {{ width: 260px; background: var(--surface); border-right: 1px solid var(--border); padding: 28px 16px; flex-shrink: 0; position: sticky; top: 0; height: 100vh; overflow-y: auto; }}
.main {{ flex: 1; padding: 40px 56px; max-width: 1080px; }}

/* Sidebar */
.sidebar-logo {{ font-size: 11px; font-weight: 700; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 28px; }}
.sm {{ margin-bottom: 2px; }}
.sm-head {{ display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; border-radius: 8px; cursor: pointer; transition: background 0.15s; }}
.sm-head:hover {{ background: #f5f5f4; }}
.sm-name {{ font-size: 13px; font-weight: 600; color: var(--text); }}
.sm-count {{ font-size: 11px; color: var(--text-dim); background: #f5f5f4; padding: 2px 7px; border-radius: 99px; }}
.sm-qs {{ max-height: 0; overflow: hidden; transition: max-height 0.3s ease; }}
.sm.open .sm-qs {{ max-height: 600px; }}
.sq-btn {{ display: block; width: 100%; font-size: 12px; color: var(--accent); padding: 7px 12px 7px 24px; border-radius: 6px; transition: background 0.1s; line-height: 1.4; }}
.sq-btn:hover {{ background: var(--accent-light); }}
.sq-dot {{ display: inline-block; width: 5px; height: 5px; border-radius: 50%; background: var(--accent); margin-right: 6px; vertical-align: middle; }}
.sq-tag {{ font-size: 9px; font-weight: 600; color: var(--text-dim); background: #f5f5f4; padding: 1px 5px; border-radius: 4px; margin-left: 6px; vertical-align: middle; letter-spacing: 0.03em; }}
.stl-sidebar {{ margin-top: 20px; padding: 12px; border: 1px dashed var(--border); border-radius: 8px; opacity: 0.6; }}
.stl-sidebar .sm-name {{ font-size: 12px; }}
.stl-sidebar .sm-count {{ font-size: 10px; }}

/* Header */
.greeting {{ font-size: 28px; font-weight: 700; color: var(--text); letter-spacing: -0.02em; }}
.stats-line {{ font-size: 13px; color: var(--text-dim); margin-top: 4px; margin-bottom: 28px; }}

/* Suggestion cards */
.suggestions {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 28px; }}
.sug {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 20px; transition: all 0.15s; border-left: 3px solid var(--accent); box-shadow: var(--shadow); }}
.sug:hover {{ box-shadow: var(--shadow-lg); transform: translateY(-1px); }}
.sug-label {{ font-size: 14px; font-weight: 600; color: var(--text); }}
.sug-id {{ font-size: 11px; color: var(--text-dim); margin-top: 4px; font-family: 'JetBrains Mono', monospace; }}

/* Search */
.search {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 18px; display: flex; align-items: center; gap: 10px; margin-bottom: 32px; box-shadow: var(--shadow); }}
.search input {{ flex: 1; border: none; outline: none; font-size: 14px; font-family: inherit; background: transparent; color: var(--text); }}
.search input::placeholder {{ color: var(--text-dim); }}
.search kbd {{ background: #f5f5f4; border: 1px solid var(--border); border-radius: 4px; padding: 2px 6px; font-size: 11px; font-family: 'JetBrains Mono', monospace; color: var(--text-dim); }}

/* Sections */
.grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 32px; }}
.section {{ margin-bottom: 32px; }}
.sec-title {{ font-size: 11px; font-weight: 700; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 12px; cursor: pointer; padding: 4px 0; }}
.sec-title:hover {{ color: var(--accent); }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; box-shadow: var(--shadow); }}

/* Common */
.meta {{ font-size: 12px; color: var(--text-dim); }}
.pill {{ font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 99px; }}
.pill-red {{ background: #fef2f2; color: var(--red); }}
.pill-amber {{ background: #fffbeb; color: var(--amber); }}
.pill-green {{ background: #f0fdf4; color: var(--green); }}
.pill-blue {{ background: #eff6ff; color: var(--blue); }}
.empty-state {{ color: var(--green); font-size: 13px; padding: 12px 0; }}

/* Projects */
.prow {{ padding: 12px 0; border-bottom: 1px solid #fafaf9; }}
.prow:last-child {{ border-bottom: none; }}
.prow-top {{ margin-bottom: 6px; }}
.prow-name {{ font-size: 14px; font-weight: 600; }}
.prow-bar {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
.bar {{ width: 100px; height: 5px; background: #f5f5f4; border-radius: 3px; overflow: hidden; flex-shrink: 0; }}
.bar-fill {{ height: 100%; border-radius: 3px; transition: width 0.5s ease; }}
.prow-pct {{ font-size: 12px; font-weight: 700; min-width: 28px; }}

/* Nudges */
.ntier {{ font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; margin-top: 16px; margin-bottom: 6px; }}
.ntier:first-child {{ margin-top: 0; }}
.nitem {{ padding: 10px 0; border-bottom: 1px solid #fafaf9; }}
.nitem:last-child {{ border-bottom: none; }}
.nitem-name {{ font-size: 13px; font-weight: 600; }}
.nitem-desc {{ font-size: 12px; color: var(--text-secondary); margin-top: 1px; }}

/* Email rows */
.erow {{ padding: 10px 0; border-bottom: 1px solid #fafaf9; }}
.erow:last-child {{ border-bottom: none; }}

/* Cold contacts */
.crow {{ padding: 10px 0; border-bottom: 1px solid #fafaf9; }}
.crow:last-child {{ border-bottom: none; }}
.crow-name {{ font-weight: 600; font-size: 14px; }}

/* Entity card */
.entity {{ margin-top: 4px; }}
.entity-name {{ font-size: 18px; font-weight: 700; margin-bottom: 16px; }}
.stat-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px; }}
.stat {{ text-align: center; padding: 12px 8px; background: #fafaf9; border-radius: 8px; }}
.stat-val {{ font-size: 20px; font-weight: 700; }}
.stat-lbl {{ font-size: 10px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.04em; margin-top: 2px; }}
.entity-qs {{ border-top: 1px solid var(--border); padding-top: 12px; }}

/* STL preview */
.stl-box {{ background: #fafaf9; border: 1px dashed var(--border); border-radius: var(--radius); padding: 20px; }}
.stl-head {{ font-size: 14px; font-weight: 700; margin-bottom: 2px; }}
.stl-sub {{ font-size: 12px; color: var(--text-dim); margin-bottom: 14px; }}
.stl-q {{ font-size: 13px; padding: 10px 14px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 6px; }}

/* Answer panel */
.overlay-bg {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.15); z-index: 90; backdrop-filter: blur(2px); }}
.overlay-bg.open {{ display: block; }}
.answer {{ display: none; position: fixed; top: 0; right: 0; width: 580px; height: 100vh; background: var(--surface); border-left: 1px solid var(--border); box-shadow: -8px 0 32px rgba(0,0,0,0.1); z-index: 100; overflow-y: auto; padding: 36px 28px; }}
.answer.open {{ display: block; }}
.answer-close {{ position: absolute; top: 16px; right: 16px; font-size: 18px; color: var(--text-dim); padding: 6px 10px; border-radius: 6px; }}
.answer-close:hover {{ background: #f5f5f4; }}
.answer-title {{ font-size: 20px; font-weight: 700; margin-bottom: 4px; padding-right: 48px; }}
.answer-meta {{ font-size: 12px; color: var(--text-dim); margin-bottom: 24px; font-family: 'JetBrains Mono', monospace; }}
.answer-table-wrap {{ overflow-x: auto; margin: 0 -4px; }}
.answer-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
.answer-table th {{ text-align: left; font-size: 10px; font-weight: 700; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.04em; padding: 8px 12px; border-bottom: 2px solid var(--border); white-space: nowrap; }}
.answer-table td {{ padding: 10px 12px; border-bottom: 1px solid #fafaf9; font-size: 13px; }}
.answer-table tr:hover {{ background: #fafaf9; }}
.answer-empty {{ color: var(--text-dim); font-style: italic; padding: 24px 0; text-align: center; }}
.answer-list-item {{ padding: 14px 0; border-bottom: 1px solid #fafaf9; }}
.answer-list-item:last-child {{ border-bottom: none; }}
.answer-list-title {{ font-weight: 600; font-size: 14px; }}
.answer-list-body {{ font-size: 12px; color: var(--text-secondary); margin-top: 2px; }}

/* Search results dropdown */
.search-results {{ position: absolute; top: 100%; left: 0; right: 0; background: var(--surface); border: 1px solid var(--border); border-radius: 0 0 var(--radius) var(--radius); box-shadow: var(--shadow-lg); display: none; max-height: 320px; overflow-y: auto; z-index: 50; }}
.search-results.open {{ display: block; }}
.sr-item {{ padding: 10px 18px; cursor: pointer; transition: background 0.1s; }}
.sr-item:hover {{ background: var(--accent-light); }}
.sr-item-label {{ font-size: 13px; font-weight: 500; }}
.sr-item-module {{ font-size: 11px; color: var(--text-dim); }}

/* Footer */
.footer {{ font-size: 12px; color: var(--text-dim); margin-top: 48px; padding-top: 20px; border-top: 1px solid var(--border); }}
.footer code {{ background: #f5f5f4; padding: 2px 6px; border-radius: 4px; }}

@media (max-width: 900px) {{
    .sidebar {{ display: none; }}
    .main {{ padding: 24px; }}
    .grid-2 {{ grid-template-columns: 1fr; }}
    .suggestions {{ grid-template-columns: 1fr; }}
    .answer {{ width: 100%; }}
}}
</style>
</head>
<body>

<div class="layout">
<div class="sidebar">
    <div class="sidebar-logo">Software of You</div>
    {sidebar_modules}
    <div class="stl-sidebar">
        <div class="sm-name">Speed-to-Lead</div>
        <div class="sm-count meta">extension — not installed</div>
    </div>
</div>

<div class="main">
    <div class="greeting">{greeting}, {name}.</div>
    <div class="stats-line">{ds.get('contacts',0)} contacts &middot; {ds.get('emails',0)} emails &middot; {ds.get('projects',0)} projects &middot; {total_q} questions across {len(deployed)} modules</div>

    <div class="suggestions">{sug_html}</div>

    <div class="search" style="position:relative">
        <input type="text" placeholder="Ask anything or search..." id="searchInput"
               oninput="handleSearchInput(this.value)" onkeydown="handleSearchKey(event)">
        <kbd>&#8984;K</kbd>
        <div class="search-results" id="searchResults"></div>
    </div>

    <div class="grid-2">
        <div class="section">
            <div class="sec-title" onclick="showQuestion('crm.cold_relationships')">Which relationships are going cold?</div>
            <div class="card">{cold_html}</div>
        </div>
        <div class="section">
            <div class="sec-title" onclick="showQuestion('nudges.attention_now')">What needs my attention?</div>
            <div class="card" style="max-height:400px;overflow-y:auto">{nudge_html if nudge_html else '<div class="empty-state">Nothing urgent. Clear.</div>'}</div>
        </div>
    </div>

    <div class="section">
        <div class="sec-title" onclick="showQuestion('projects.health_overview')">How are my projects tracking?</div>
        <div class="card">{proj_html}</div>
    </div>

    <div class="grid-2">
        <div class="section">
            <div class="sec-title" onclick="showQuestion('email.needs_reply')">What emails need my reply?</div>
            <div class="card">{email_html if email_html else '<div class="empty-state">Inbox zero.</div>'}</div>
        </div>
        <div class="section">
            <div class="sec-title">Entity: Jessica Martin</div>
            <div class="card">{jessica_html if jessica_html else '<div class="meta">No matching contact</div>'}</div>
        </div>
    </div>

    <div class="section">
        <div class="sec-title">Speed-to-Lead (extension preview)</div>
        <div class="stl-box">
            <div class="stl-head">Speed-to-Lead</div>
            <div class="stl-sub">Extension not installed — these questions activate when the stl_leads migration runs</div>
            {stl_html}
        </div>
    </div>

    <div class="footer">
        Every answer on this page came from a QPack question — pre-built SQL against computed views.
        No LLM needed for 60%+ of questions. Pipeline generated {total_q} questions across {len(deployed)} modules in &lt;1s.
        <br><br>
        Engine: <code>python3 modules/qpack-generator/run.py scan</code> &middot;
        API: <code>python3 modules/qpack-generator/serve.py</code>
    </div>
</div>
</div>

<div class="overlay-bg" id="overlayBg" onclick="closeAnswer()"></div>
<div class="answer" id="answerPanel">
    <button class="answer-close" onclick="closeAnswer()">&times;</button>
    <div id="answerContent"></div>
</div>

<script>
const DATA = {js_data};

function showQuestion(qid) {{
    const panel = document.getElementById('answerPanel');
    const bg = document.getElementById('overlayBg');
    const content = document.getElementById('answerContent');

    const result = DATA.questionResults[qid];
    if (!result) {{
        content.innerHTML = '<div class="answer-title">' + qid + '</div><div class="answer-empty">Question not found or not yet generated.</div>';
        panel.classList.add('open');
        bg.classList.add('open');
        return;
    }}

    let html = '<div class="answer-title">' + esc(result.label) + '</div>';
    html += '<div class="answer-meta">' + qid + ' &middot; ' + result.answer_format + (result.requires_llm ? ' &middot; requires LLM' : ' &middot; database') + '</div>';

    const allRows = [];
    const keys = Object.keys(result.data || {{}});
    keys.forEach(key => {{
        const rows = result.data[key];
        if (Array.isArray(rows) && rows.length > 0) {{
            allRows.push(...rows);
        }}
    }});

    if (result.answer_format === 'data_table' || (!result.requires_llm && allRows.length > 0)) {{
        if (allRows.length > 0) {{
            const cols = Object.keys(allRows[0]);
            html += '<div class="answer-table-wrap"><table class="answer-table"><thead><tr>';
            cols.forEach(c => html += '<th>' + esc(formatColName(c)) + '</th>');
            html += '</tr></thead><tbody>';
            allRows.slice(0, 30).forEach(row => {{
                html += '<tr>';
                cols.forEach(c => {{
                    let v = row[c];
                    if (v === null || v === undefined) v = '—';
                    html += '<td>' + esc(String(v)) + '</td>';
                }});
                html += '</tr>';
            }});
            html += '</tbody></table></div>';
            if (allRows.length > 30) html += '<div class="meta" style="margin-top:8px">' + (allRows.length - 30) + ' more rows</div>';
        }} else {{
            html += '<div class="answer-empty">No data for this question.</div>';
        }}
    }} else if (result.answer_format === 'prioritized_list') {{
        if (allRows.length > 0) {{
            allRows.slice(0, 15).forEach((row, i) => {{
                const name = row.entity_name || row.name || row.title || Object.values(row)[0] || '';
                const desc = row.description || row.extra_context || '';
                const tier = row.tier || '';
                const tc = tier === 'urgent' ? 'red' : tier === 'soon' ? 'amber' : 'blue';
                const badge = tier ? '<span class="pill pill-' + tc + '">' + tier + '</span> ' : '';
                html += '<div class="answer-list-item"><div class="answer-list-title">' + badge + esc(String(name)) + '</div>';
                if (desc) html += '<div class="answer-list-body">' + esc(String(desc)) + '</div>';
                html += '</div>';
            }});
        }} else {{
            html += '<div class="answer-empty">No items.</div>';
        }}
    }} else if (result.answer_format === 'metric_snapshot') {{
        if (allRows.length > 0) {{
            const row = allRows[0];
            const mainVal = Object.values(row)[0] || '—';
            const mainKey = Object.keys(row)[0] || '';
            html += '<div style="text-align:center;padding:32px 0"><div style="font-size:56px;font-weight:700">' + esc(String(mainVal)) + '</div>';
            html += '<div class="meta" style="font-size:14px">' + esc(formatColName(mainKey)) + '</div></div>';
            if (Object.keys(row).length > 1) {{
                html += '<div style="display:flex;gap:16px;justify-content:center">';
                Object.entries(row).slice(1).forEach(([k,v]) => {{
                    html += '<div style="text-align:center"><div style="font-size:22px;font-weight:700">' + esc(String(v ?? '—')) + '</div><div class="meta">' + esc(formatColName(k)) + '</div></div>';
                }});
                html += '</div>';
            }}
        }} else {{
            html += '<div class="answer-empty">No data.</div>';
        }}
    }} else if (result.requires_llm) {{
        html += '<div style="background:#fafaf9;border-radius:8px;padding:20px;margin-top:8px">';
        html += '<div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:8px">Requires LLM</div>';
        html += '<div style="font-size:13px;color:var(--text-secondary)">This question needs a local language model for synthesis. Run with Ollama connected to see the full answer.</div>';
        if (allRows.length > 0) {{
            html += '<div style="margin-top:16px;font-size:12px;font-weight:600;color:var(--text-secondary)">Raw data available (' + allRows.length + ' rows):</div>';
            const cols = Object.keys(allRows[0]);
            html += '<div class="answer-table-wrap" style="margin-top:8px"><table class="answer-table"><thead><tr>';
            cols.forEach(c => html += '<th>' + esc(formatColName(c)) + '</th>');
            html += '</tr></thead><tbody>';
            allRows.slice(0, 10).forEach(row => {{
                html += '<tr>';
                cols.forEach(c => html += '<td>' + esc(String(row[c] ?? '—')) + '</td>');
                html += '</tr>';
            }});
            html += '</tbody></table></div>';
        }}
        html += '</div>';
    }} else {{
        html += '<div class="answer-empty">No data available.</div>';
    }}

    content.innerHTML = html;
    panel.classList.add('open');
    bg.classList.add('open');
}}

function showEntityDetail(contactId, view) {{
    const panel = document.getElementById('answerPanel');
    const bg = document.getElementById('overlayBg');
    const content = document.getElementById('answerContent');

    const detail = DATA.contactDetails[String(contactId)];
    if (!detail) {{
        content.innerHTML = '<div class="answer-title">Contact not found</div>';
        panel.classList.add('open');
        bg.classList.add('open');
        return;
    }}

    const c = detail.contact;
    let html = '<div class="answer-title">' + esc(c.name) + '</div>';
    html += '<div class="answer-meta">' + esc(c.company || '') + '</div>';

    if (view === 'relationship') {{
        html += '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin:20px 0">';
        html += '<div style="text-align:center;background:#fafaf9;padding:16px;border-radius:8px"><div style="font-size:28px;font-weight:700">' + (c.relationship_depth || '—') + '</div><div class="meta">Depth /10</div></div>';
        html += '<div style="text-align:center;background:#fafaf9;padding:16px;border-radius:8px"><div style="font-size:28px;font-weight:700">' + (c.days_silent || 0) + 'd</div><div class="meta">Last Contact</div></div>';
        html += '<div style="text-align:center;background:#fafaf9;padding:16px;border-radius:8px"><div style="font-size:28px;font-weight:700">' + (c.emails_30d || 0) + '</div><div class="meta">Emails/30d</div></div>';
        html += '</div>';

        html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px">';
        html += '<div style="background:#fafaf9;padding:12px 16px;border-radius:8px"><span style="font-weight:600">' + (c.trajectory || '—') + '</span><div class="meta">Trajectory</div></div>';
        html += '<div style="background:#fafaf9;padding:12px 16px;border-radius:8px"><span style="font-weight:600">' + (c.active_projects || 0) + '</span><div class="meta">Active Projects</div></div>';
        html += '</div>';

        if (detail.interactions.length > 0) {{
            html += '<div style="font-size:11px;font-weight:700;color:var(--text-secondary);text-transform:uppercase;letter-spacing:0.04em;margin:16px 0 8px">Recent Interactions</div>';
            detail.interactions.forEach(i => {{
                html += '<div style="padding:8px 0;border-bottom:1px solid #fafaf9"><div style="font-weight:500;font-size:13px">' + esc(i.subject || i.type) + '</div><div class="meta">' + esc(i.type) + ' &middot; ' + esc(i.occurred_at || '') + '</div></div>';
            }});
        }} else {{
            html += '<div class="meta" style="margin-top:16px">No interactions logged yet.</div>';
        }}
    }}

    else if (view === 'commitments') {{
        if (detail.commitments.length > 0) {{
            detail.commitments.forEach(cm => {{
                const who = cm.is_user_commitment ? 'You owe' : 'They owe';
                const urg = cm.urgency || 'open';
                const uc = urg === 'overdue' ? 'red' : urg === 'soon' ? 'amber' : 'blue';
                html += '<div style="padding:12px 0;border-bottom:1px solid #fafaf9">';
                html += '<div style="font-weight:500;font-size:13px">' + esc(cm.description) + '</div>';
                html += '<div class="meta">' + who + ' &middot; <span class="pill pill-' + uc + '">' + urg + '</span>';
                if (cm.deadline_date) html += ' &middot; due ' + esc(cm.deadline_date);
                html += '</div></div>';
            }});
        }} else {{
            html += '<div class="answer-empty">No open commitments with ' + esc(c.name) + '.</div>';
        }}
    }}

    else if (view === 'emails') {{
        if (detail.emails.length > 0) {{
            html += '<div class="answer-table-wrap"><table class="answer-table"><thead><tr><th>Direction</th><th>Subject</th><th>Date</th></tr></thead><tbody>';
            detail.emails.forEach(e => {{
                const dir = e.direction === 'inbound' ? '← in' : '→ out';
                html += '<tr><td>' + dir + '</td><td>' + esc(e.subject || '(no subject)') + '</td><td class="meta">' + esc(e.received_at || '') + '</td></tr>';
            }});
            html += '</tbody></table></div>';
        }} else {{
            html += '<div class="answer-empty">No emails with ' + esc(c.name) + '.</div>';
        }}
    }}

    content.innerHTML = html;
    panel.classList.add('open');
    bg.classList.add('open');
}}

function closeAnswer() {{
    document.getElementById('answerPanel').classList.remove('open');
    document.getElementById('overlayBg').classList.remove('open');
}}

function handleSearchInput(val) {{
    const results = document.getElementById('searchResults');
    if (!val.trim()) {{ results.classList.remove('open'); return; }}

    const q = val.toLowerCase();
    const matches = [];
    Object.entries(DATA.qpacks).forEach(([mod, qp]) => {{
        qp.questions.forEach(question => {{
            if (question.label.toLowerCase().includes(q) || question.id.toLowerCase().includes(q)) {{
                matches.push(question);
            }}
        }});
    }});

    if (matches.length === 0) {{
        results.innerHTML = '<div class="sr-item"><div class="sr-item-label" style="color:var(--text-dim)">No matches</div></div>';
    }} else {{
        results.innerHTML = matches.slice(0, 8).map(m =>
            '<div class="sr-item" onclick="showQuestion(\\''+m.id+'\\'); document.getElementById(\\'searchResults\\').classList.remove(\\'open\\')">' +
            '<div class="sr-item-label">' + esc(m.label) + '</div>' +
            '<div class="sr-item-module">' + m.id + '</div></div>'
        ).join('');
    }}
    results.classList.add('open');
}}

function handleSearchKey(e) {{
    if (e.key === 'Enter') {{
        const val = e.target.value.trim();
        const results = document.getElementById('searchResults');
        const first = results.querySelector('.sr-item');
        if (first) first.click();
        results.classList.remove('open');
    }}
    if (e.key === 'Escape') {{
        document.getElementById('searchResults').classList.remove('open');
        e.target.blur();
    }}
}}

function formatColName(s) {{
    return s.replace(/_/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
}}

function esc(s) {{
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}}

document.addEventListener('keydown', e => {{
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {{
        e.preventDefault();
        document.getElementById('searchInput').focus();
    }}
    if (e.key === 'Escape') closeAnswer();
}});

// Auto-expand first sidebar module
document.querySelector('.sm')?.classList.add('open');
</script>

</body>
</html>"""


def run_demo():
    print("  Gathering data...")
    data = _gather_data()
    print(f"  Building page ({sum(d['questions'] for d in data['deployed'])} questions, {len(data['question_results'])} executed)...")
    html = _build_html(data)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "qpack-demo.html"
    output_path.write_text(html)
    print(f"  Written to {output_path}")
    subprocess.run(["open", str(output_path)])


if __name__ == "__main__":
    run_demo()
