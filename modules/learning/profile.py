#!/usr/bin/env python3
"""
Learning Module — Profile management.
Tracks the user's learning preferences based on feedback reactions.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def get_profile() -> dict:
    """Read current learning profile as a dict for prompt injection."""
    db = get_db()
    profile = {}
    try:
        rows = db.execute("SELECT category, key, value FROM learning_profile").fetchall()
        for r in rows:
            cat = r["category"]
            if cat not in profile:
                profile[cat] = {}
            profile[cat][r["key"]] = r["value"]
    except Exception:
        pass
    finally:
        db.close()
    return profile


def _upsert_profile(db, category: str, key: str, value: str):
    """Insert or update a learning profile entry."""
    db.execute(
        """INSERT INTO learning_profile (category, key, value, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(category, key) DO UPDATE
           SET value = excluded.value, updated_at = datetime('now')""",
        (category, key, value),
    )


def update_from_feedback(digest_id: int, section_id: str, reaction: str):
    """Incrementally update the learning profile based on a single feedback reaction.

    Logic:
    - too_basic → lower depth for the section's domain
    - too_advanced → raise depth for the section's domain
    - this_clicked → tag the explanation style as effective
    - got_it → neutral positive signal
    - tell_me_more → increase detail preference for domain
    """
    db = get_db()

    # Get the section's domain from the digest
    domain = None
    try:
        row = db.execute(
            "SELECT sections FROM learning_digests WHERE id = ?", (digest_id,)
        ).fetchone()
        if row and row["sections"]:
            import json
            sections = json.loads(row["sections"])
            for s in sections:
                if s.get("id") == section_id:
                    domain = s.get("domain", "general")
                    break
    except Exception:
        pass

    domain = domain or "general"

    # Read current depth for domain (default 3 on scale 1-5)
    current_depth = 3
    try:
        row = db.execute(
            "SELECT value FROM learning_profile WHERE category = 'depth' AND key = ?",
            (domain,),
        ).fetchone()
        if row:
            current_depth = int(row["value"])
    except Exception:
        pass

    if reaction == "too_basic":
        new_depth = min(5, current_depth + 1)
        _upsert_profile(db, "depth", domain, str(new_depth))
    elif reaction == "too_advanced":
        new_depth = max(1, current_depth - 1)
        _upsert_profile(db, "depth", domain, str(new_depth))
    elif reaction == "tell_me_more":
        # Increase detail preference
        new_depth = min(5, current_depth + 1)
        _upsert_profile(db, "depth", domain, str(new_depth))
        _upsert_profile(db, "style", f"{domain}_detail", "high")
    elif reaction == "this_clicked":
        # Mark explanation style as effective — store the section type
        try:
            row = db.execute(
                "SELECT sections FROM learning_digests WHERE id = ?", (digest_id,)
            ).fetchone()
            if row and row["sections"]:
                import json
                sections = json.loads(row["sections"])
                for s in sections:
                    if s.get("id") == section_id:
                        sec_type = s.get("type", "concept")
                        # Increment effective count for this type
                        count_row = db.execute(
                            "SELECT value FROM learning_profile WHERE category = 'effective_style' AND key = ?",
                            (sec_type,),
                        ).fetchone()
                        count = int(count_row["value"]) + 1 if count_row else 1
                        _upsert_profile(db, "effective_style", sec_type, str(count))
                        break
        except Exception:
            pass
    elif reaction == "got_it":
        # Neutral positive — just record engagement
        _upsert_profile(db, "pace", domain, "comfortable")

    db.commit()
    db.close()


def recalibrate():
    """Full recalculation of profile from all feedback.
    Rebuilds depth, style, and pace preferences from scratch.
    """
    db = get_db()

    # Clear existing profile
    db.execute("DELETE FROM learning_profile")

    # Get all feedback with digest context
    rows = db.execute(
        """SELECT lf.section_id, lf.reaction, ld.sections
           FROM learning_feedback lf
           JOIN learning_digests ld ON ld.id = lf.digest_id
           ORDER BY lf.created_at"""
    ).fetchall()

    import json

    # Track per-domain depth adjustments
    depth_signals = {}  # domain -> list of +1/-1
    style_counts = {}   # section_type -> count of "this_clicked"
    detail_domains = set()

    for r in rows:
        # Find the section's domain
        domain = "general"
        sec_type = "concept"
        try:
            sections = json.loads(r["sections"])
            for s in sections:
                if s.get("id") == r["section_id"]:
                    domain = s.get("domain", "general")
                    sec_type = s.get("type", "concept")
                    break
        except Exception:
            continue

        if domain not in depth_signals:
            depth_signals[domain] = []

        reaction = r["reaction"]
        if reaction == "too_basic":
            depth_signals[domain].append(1)
        elif reaction == "too_advanced":
            depth_signals[domain].append(-1)
        elif reaction == "tell_me_more":
            depth_signals[domain].append(1)
            detail_domains.add(domain)
        elif reaction == "this_clicked":
            style_counts[sec_type] = style_counts.get(sec_type, 0) + 1

    # Calculate final depth per domain
    for domain, signals in depth_signals.items():
        base = 3
        adjustment = sum(signals)
        final = max(1, min(5, base + adjustment))
        _upsert_profile(db, "depth", domain, str(final))

    # Store detail preferences
    for domain in detail_domains:
        _upsert_profile(db, "style", f"{domain}_detail", "high")

    # Store effective styles
    for style, count in style_counts.items():
        _upsert_profile(db, "effective_style", style, str(count))

    db.commit()
    db.close()

    return {"recalibrated": True, "domains": len(depth_signals), "styles": len(style_counts)}
