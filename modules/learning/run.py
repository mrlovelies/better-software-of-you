#!/usr/bin/env python3
"""
Learning Module — CLI entry point.

Usage:
    python3 modules/learning/run.py generate daily    # Generate today's morning digest
    python3 modules/learning/run.py generate weekly   # Generate weekly workshop
    python3 modules/learning/run.py status            # Show generation history
    python3 modules/learning/run.py profile           # Show current learning profile
"""

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"

# Load .env
env_file = PLUGIN_ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# Import sibling modules
_mod_dir = Path(__file__).resolve().parent

_generator_spec = importlib.util.spec_from_file_location("generator", _mod_dir / "generator.py")
generator = importlib.util.module_from_spec(_generator_spec)
_generator_spec.loader.exec_module(generator)

_profile_spec = importlib.util.spec_from_file_location("profile", _mod_dir / "profile.py")
profile_mod = importlib.util.module_from_spec(_profile_spec)
_profile_spec.loader.exec_module(profile_mod)


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def cmd_generate(digest_type: str):
    """Generate a digest."""
    if digest_type not in ("daily", "weekly"):
        print(f"Unknown digest type: {digest_type}")
        print("Usage: run.py generate [daily|weekly]")
        sys.exit(1)

    result = generator.generate(digest_type)
    if result:
        print(f"\nGenerated: {result['title']}")
        print(f"  Sections: {result['sections']}")
        print(f"  Duration: {result['duration_ms']}ms")
    else:
        print("No digest generated (already exists or not enough data).")


def cmd_status():
    """Show generation history."""
    db = get_db()

    # Stats
    stats = db.execute("SELECT * FROM v_learning_stats").fetchone()
    if stats:
        print("Learning Module Status")
        print("=" * 40)
        print(f"  Daily digests: {stats['daily_count']}")
        print(f"  Weekly workshops: {stats['weekly_count']}")
        print(f"  Total feedback: {stats['total_feedback']}")
        if stats['last_digest_at']:
            print(f"  Last digest: {stats['last_digest_at']}")
        print()

    # Recent digests
    rows = db.execute(
        """SELECT id, digest_type, digest_date, title, generation_duration_ms,
                  (SELECT COUNT(*) FROM learning_feedback WHERE digest_id = learning_digests.id) as feedback_count
           FROM learning_digests
           ORDER BY created_at DESC
           LIMIT 10"""
    ).fetchall()

    if not rows:
        print("No digests generated yet.")
        print("Run 'python3 modules/learning/run.py generate daily' to create your first digest.")
    else:
        print("Recent digests:")
        for r in rows:
            fb = f" ({r['feedback_count']} feedback)" if r['feedback_count'] > 0 else ""
            print(f"  [{r['digest_type']:6}] {r['digest_date']} — {r['title']}{fb}")

    db.close()


def cmd_profile():
    """Show current learning profile."""
    profile = profile_mod.get_profile()

    if not profile:
        print("No learning profile yet.")
        print("Feedback on digests builds your profile over time.")
        return

    print("Learning Profile")
    print("=" * 40)
    for category, entries in profile.items():
        print(f"\n  {category}:")
        for key, value in entries.items():
            print(f"    {key}: {value}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: run.py [generate daily|generate weekly|status|profile]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "generate":
        dtype = sys.argv[2] if len(sys.argv) > 2 else "daily"
        cmd_generate(dtype)
    elif cmd == "status":
        cmd_status()
    elif cmd == "profile":
        cmd_profile()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: run.py [generate daily|generate weekly|status|profile]")
        sys.exit(1)
