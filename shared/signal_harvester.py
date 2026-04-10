#!/usr/bin/env python3
"""
Signal Harvester — Reddit pain-point demand discovery.

Scrapes Reddit for signals where people express unmet needs:
  "I wish there was...", "why isn't there...", "someone should build...",
  "is there an app that...", "I'd pay for...", etc.

Usage:
  python3 signal_harvester.py harvest [--subreddits=...] [--limit=100]
  python3 signal_harvester.py harvest --target=<target_description>
  python3 signal_harvester.py signals [--limit=20] [--industry=...]
  python3 signal_harvester.py sources
  python3 signal_harvester.py add-source <name> <config_json>
  python3 signal_harvester.py stats
"""

import sys
import os
import json
import sqlite3
import time
import re
import argparse
from urllib.request import Request, urlopen
from urllib.parse import quote_plus
from datetime import datetime, timezone

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")

# Content sanitizer (Ruflo AIDefence extraction)
try:
    from content_sanitizer import sanitize_signal, is_safe
    HAS_SANITIZER = True
except ImportError:
    HAS_SANITIZER = False

# Semantic deduplication (Ruflo HNSW extraction)
try:
    from signal_dedup import SignalDeduplicator
    HAS_DEDUP = True
except ImportError:
    HAS_DEDUP = False

_deduplicator = None
def get_deduplicator():
    global _deduplicator
    if _deduplicator is None and HAS_DEDUP:
        _deduplicator = SignalDeduplicator(threshold=0.85)
    return _deduplicator

# Reddit public JSON endpoints — no API key needed
REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
REDDIT_SUBREDDIT_SEARCH_URL = "https://www.reddit.com/r/{subreddit}/search.json"
USER_AGENT = "SoY-SignalHarvester/1.0 (personal research tool)"

# Pain-point signal patterns — these catch people expressing unmet needs
SIGNAL_PATTERNS = [
    r"\bi wish (?:there was|there were|someone would|i had|i could)\b",
    r"\bwhy (?:isn'?t there|is there no|hasn'?t anyone|doesn'?t anyone|can'?t i)\b",
    r"\bsomeone (?:should|needs to) (?:build|make|create)\b",
    r"\bis there (?:an app|a tool|a service|a website|software|anything) (?:that|for|to)\b",
    r"\bi(?:'d| would) (?:pay|happily pay|gladly pay) (?:for|to have|money for)\b",
    r"\bi(?:'m| am) (?:so )?(?:tired|sick|frustrated) of\b",
    r"\bthere(?:'s| is) no (?:good|decent|easy|simple|reliable) (?:way|tool|app|service) to\b",
    r"\bit(?:'s| is) (?:insane|crazy|ridiculous|absurd) that (?:there'?s no|we (?:still )?(?:can'?t|don'?t have))\b",
    r"\bif only (?:there was|there were|i could|someone)\b",
    r"\ball i want is\b",
    r"\bi(?:'ve| have) been looking for\b.*(?:can'?t find|doesn'?t exist|nothing works)",
    r"\bfrustrat(?:ed|ing)\b.*\b(?:app|tool|software|service|solution)\b",
    r"\bwhat do you (?:use|recommend) for\b",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SIGNAL_PATTERNS]

# Default subreddits for general harvesting
DEFAULT_SUBREDDITS = [
    "SaaS", "startups", "Entrepreneur", "smallbusiness",
    "webdev", "programming", "sideproject",
    "ProductManagement", "freelance",
    "selfhosted", "software", "apps",
    "DigitalNomad", "WorkOnline",
]

# Search queries that surface pain points
SEARCH_QUERIES = [
    "I wish there was an app",
    "why isn't there a tool",
    "someone should build",
    "I'd pay for a service that",
    "is there an app that",
    "frustrated no good tool",
    "tired of manually doing",
    "there's no good way to",
    "looking for a tool that",
    "does anyone know of an app",
    "need a better way to",
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def classify_signal_type(text):
    """Classify what kind of pain signal this is."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["i wish", "if only", "all i want"]):
        return "wish"
    if any(w in text_lower for w in ["frustrated", "tired of", "sick of", "insane that"]):
        return "frustration"
    if any(w in text_lower for w in ["why isn't", "why is there no", "why can't"]):
        return "complaint"
    if any(w in text_lower for w in ["is there", "what do you use", "looking for", "recommend"]):
        return "question"
    if any(w in text_lower for w in ["someone should", "needs to build"]):
        return "suggestion"
    return "general"


def matches_signal_pattern(text):
    """Check if text contains any pain-point signal pattern."""
    for pattern in COMPILED_PATTERNS:
        if pattern.search(text):
            return True
    return False


def reddit_fetch(url, params=None):
    """Fetch from Reddit's public JSON API with rate limiting."""
    if params:
        query_string = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
        url = f"{url}?{query_string}"

    req = Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            time.sleep(1.5)  # respect rate limits
            return data
    except Exception as e:
        print(f"  [warn] Failed to fetch {url}: {e}", file=sys.stderr)
        return None


def harvest_subreddit_search(subreddit, query, limit=25, time_filter="month"):
    """Search a specific subreddit for pain-point signals."""
    url = REDDIT_SUBREDDIT_SEARCH_URL.format(subreddit=subreddit)
    params = {
        "q": query,
        "restrict_sr": "on",
        "sort": "relevance",
        "t": time_filter,
        "limit": min(limit, 100),
    }
    return reddit_fetch(url, params)


def harvest_global_search(query, limit=25, time_filter="month"):
    """Search all of Reddit for pain-point signals."""
    params = {
        "q": query,
        "sort": "relevance",
        "t": time_filter,
        "limit": min(limit, 100),
    }
    return reddit_fetch(REDDIT_SEARCH_URL, params)


def extract_signals_from_response(data, source_id):
    """Extract signal candidates from Reddit API response."""
    signals = []
    if not data or "data" not in data or "children" not in data["data"]:
        return signals

    for child in data["data"]["children"]:
        post = child.get("data", {})
        if post.get("removed_by_category") or post.get("is_robot_indexable") is False:
            continue

        title = post.get("title", "")
        selftext = post.get("selftext", "")
        full_text = f"{title}\n\n{selftext}".strip()

        if not matches_signal_pattern(full_text):
            continue

        # Skip very short posts (likely low signal)
        if len(full_text) < 40:
            continue

        # Content sanitization (Ruflo AIDefence)
        clean_text = full_text[:5000]
        threat_level = None
        if HAS_SANITIZER:
            clean_text, report = sanitize_signal(full_text[:5000])
            if not report["safe"] and report["max_severity"] in ("critical", "high"):
                threat_level = report["max_severity"]
                # Still store but flag — don't skip entirely, the content might
                # contain both a threat pattern AND a legitimate signal
            # PII is already stripped from clean_text

        signals.append({
            "source_id": source_id,
            "source_url": f"https://reddit.com{post.get('permalink', '')}",
            "source_author": post.get("author", "[deleted]"),
            "platform": "reddit",
            "subreddit": post.get("subreddit", ""),
            "raw_text": clean_text,
            "signal_type": classify_signal_type(full_text),
            "upvotes": post.get("ups", 0),
            "comment_count": post.get("num_comments", 0),
            "_threat_level": threat_level,  # internal flag for storage
        })

    return signals


def store_signals(db, signals):
    """Store harvested signals, deduplicating by source_url AND semantic similarity."""
    stored = 0
    skipped = 0
    semantic_dupes = 0
    dedup = get_deduplicator()

    for sig in signals:
        # Check for URL duplicate
        existing = db.execute(
            "SELECT id FROM harvest_signals WHERE source_url = ?",
            (sig["source_url"],)
        ).fetchone()

        if existing:
            skipped += 1
            continue

        # Check for semantic duplicate (HNSW)
        if dedup:
            is_dup, similar = dedup.check(sig["raw_text"])
            if is_dup:
                semantic_dupes += 1
                # Log the semantic match but still skip storage
                best_match_id, best_score = similar[0]
                print(f"    [semantic dedup] Skipping signal similar to #{best_match_id} ({best_score:.2f})")
                skipped += 1
                continue

        db.execute("""
            INSERT INTO harvest_signals
            (source_id, source_url, source_author, platform, subreddit,
             raw_text, signal_type, upvotes, comment_count, threat_level, harvested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            sig["source_id"], sig["source_url"], sig["source_author"],
            sig["platform"], sig["subreddit"], sig["raw_text"],
            sig["signal_type"], sig["upvotes"], sig["comment_count"],
            sig.get("_threat_level"),
        ))

        # Get the inserted signal's ID and add to HNSW index
        signal_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        if dedup:
            dedup.add(f"signal_{signal_id}", sig["raw_text"])

        stored += 1

    db.commit()

    # Save HNSW index
    if dedup:
        dedup.save()

    if semantic_dupes > 0:
        print(f"    ({semantic_dupes} semantic duplicates detected)")

    return stored, skipped


def ensure_reddit_source(db):
    """Ensure the default Reddit source exists."""
    row = db.execute("SELECT id FROM harvest_sources WHERE name = 'reddit'").fetchone()
    if row:
        return row["id"]

    db.execute("""
        INSERT INTO harvest_sources (name, source_type, config, enabled)
        VALUES ('reddit', 'api', ?, 1)
    """, (json.dumps({
        "subreddits": DEFAULT_SUBREDDITS,
        "queries": SEARCH_QUERIES,
        "time_filter": "month",
    }),))
    db.commit()
    return db.execute("SELECT id FROM harvest_sources WHERE name = 'reddit'").fetchone()["id"]


def cmd_harvest(args):
    """Run a harvest cycle."""
    db = get_db()
    source_id = ensure_reddit_source(db)

    # Get source config
    source = db.execute("SELECT * FROM harvest_sources WHERE id = ?", (source_id,)).fetchone()
    config = json.loads(source["config"] or "{}")

    subreddits = args.subreddits.split(",") if args.subreddits else config.get("subreddits", DEFAULT_SUBREDDITS)
    queries = config.get("queries", SEARCH_QUERIES)
    time_filter = args.time_filter or config.get("time_filter", "month")
    limit_per_query = args.limit or 25

    # If a custom target is specified, generate targeted queries
    if args.target:
        queries = [
            f"{args.target} frustrated",
            f"{args.target} I wish",
            f"{args.target} someone should build",
            f"{args.target} is there a tool",
            f"{args.target} better way to",
        ]
        print(f"Targeted harvest: \"{args.target}\"")
        print(f"Generated {len(queries)} targeted queries")

    total_stored = 0
    total_skipped = 0
    total_fetched = 0

    if args.global_only:
        # Global search only
        print(f"Harvesting globally with {len(queries)} queries...")
        for i, query in enumerate(queries):
            print(f"  [{i+1}/{len(queries)}] Searching: \"{query}\"")
            data = harvest_global_search(query, limit=limit_per_query, time_filter=time_filter)
            if data:
                signals = extract_signals_from_response(data, source_id)
                total_fetched += len(signals)
                stored, skipped = store_signals(db, signals)
                total_stored += stored
                total_skipped += skipped
                print(f"    Found {len(signals)} matches, stored {stored}, skipped {skipped} dupes")
    else:
        # Subreddit-specific search
        print(f"Harvesting {len(subreddits)} subreddits with {len(queries)} queries...")
        for sub in subreddits:
            print(f"\n  r/{sub}:")
            for query in queries[:3]:  # limit queries per sub to stay under rate limits
                print(f"    Searching: \"{query}\"")
                data = harvest_subreddit_search(sub, query, limit=limit_per_query, time_filter=time_filter)
                if data:
                    signals = extract_signals_from_response(data, source_id)
                    total_fetched += len(signals)
                    stored, skipped = store_signals(db, signals)
                    total_stored += stored
                    total_skipped += skipped
                    if signals:
                        print(f"      {len(signals)} matches, {stored} new")

    # Update last harvested timestamp
    db.execute(
        "UPDATE harvest_sources SET last_harvested_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
        (source_id,)
    )
    db.commit()

    # Log activity
    db.execute("""
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('harvest', ?, 'harvest_completed', ?, datetime('now'))
    """, (source_id, json.dumps({
        "fetched": total_fetched,
        "stored": total_stored,
        "skipped_dupes": total_skipped,
        "subreddits": len(subreddits),
        "queries": len(queries),
        "target": args.target,
    })))
    db.commit()

    print(f"\n{'='*50}")
    print(f"Harvest complete:")
    print(f"  Signals found:  {total_fetched}")
    print(f"  New stored:     {total_stored}")
    print(f"  Dupes skipped:  {total_skipped}")
    print(f"  Total in DB:    {db.execute('SELECT COUNT(*) FROM harvest_signals').fetchone()[0]}")

    db.close()


def cmd_signals(args):
    """List harvested signals."""
    db = get_db()

    query = "SELECT * FROM harvest_signals"
    params = []
    conditions = []

    if args.industry:
        conditions.append("industry = ?")
        params.append(args.industry)
    if args.signal_type:
        conditions.append("signal_type = ?")
        params.append(args.signal_type)
    if args.min_upvotes:
        conditions.append("upvotes >= ?")
        params.append(args.min_upvotes)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY upvotes DESC LIMIT ?"
    params.append(args.limit or 20)

    rows = db.execute(query, params).fetchall()

    if not rows:
        print("No signals found. Run 'harvest' first.")
        return

    for i, row in enumerate(rows):
        print(f"\n{'─'*60}")
        print(f"#{row['id']} [{row['signal_type']}] r/{row['subreddit']} | {row['upvotes']}↑ {row['comment_count']}💬")
        # Truncate display text
        text = row['raw_text'][:300]
        if len(row['raw_text']) > 300:
            text += "..."
        print(f"  {text}")
        if row['extracted_pain']:
            print(f"  PAIN: {row['extracted_pain']}")
        if row['industry']:
            print(f"  INDUSTRY: {row['industry']}")
        print(f"  {row['source_url']}")

    print(f"\n{'─'*60}")
    print(f"Showing {len(rows)} signals")
    db.close()


def cmd_sources(args):
    """List harvest sources."""
    db = get_db()
    rows = db.execute("SELECT * FROM harvest_sources ORDER BY name").fetchall()

    if not rows:
        print("No sources configured.")
        return

    for row in rows:
        status = "enabled" if row["enabled"] else "disabled"
        last = row["last_harvested_at"] or "never"
        print(f"  [{row['id']}] {row['name']} ({row['source_type']}) — {status} — last: {last}")
        if row["config"]:
            config = json.loads(row["config"])
            if "subreddits" in config:
                print(f"       subreddits: {', '.join(config['subreddits'][:8])}{'...' if len(config.get('subreddits', [])) > 8 else ''}")

    db.close()


def cmd_stats(args):
    """Show harvest statistics."""
    db = get_db()

    total = db.execute("SELECT COUNT(*) as c FROM harvest_signals").fetchone()["c"]
    by_type = db.execute(
        "SELECT signal_type, COUNT(*) as c FROM harvest_signals GROUP BY signal_type ORDER BY c DESC"
    ).fetchall()
    by_sub = db.execute(
        "SELECT subreddit, COUNT(*) as c FROM harvest_signals GROUP BY subreddit ORDER BY c DESC LIMIT 10"
    ).fetchall()
    triaged = db.execute(
        "SELECT verdict, COUNT(*) as c FROM harvest_triage GROUP BY verdict"
    ).fetchall()
    builds = db.execute(
        "SELECT status, COUNT(*) as c FROM harvest_builds GROUP BY status"
    ).fetchall()

    print(f"Signal Harvester Stats")
    print(f"{'='*40}")
    print(f"Total signals: {total}")

    if by_type:
        print(f"\nBy type:")
        for row in by_type:
            print(f"  {row['signal_type']}: {row['c']}")

    if by_sub:
        print(f"\nTop subreddits:")
        for row in by_sub:
            print(f"  r/{row['subreddit']}: {row['c']}")

    if triaged:
        print(f"\nTriage status:")
        for row in triaged:
            print(f"  {row['verdict']}: {row['c']}")

    if builds:
        print(f"\nBuilds:")
        for row in builds:
            print(f"  {row['status']}: {row['c']}")

    db.close()


def main():
    parser = argparse.ArgumentParser(description="Signal Harvester — demand discovery from Reddit")
    subparsers = parser.add_subparsers(dest="command")

    # harvest
    p_harvest = subparsers.add_parser("harvest", help="Run a harvest cycle")
    p_harvest.add_argument("--subreddits", help="Comma-separated subreddit list")
    p_harvest.add_argument("--target", help="Targeted harvest — describe what you're looking for")
    p_harvest.add_argument("--limit", type=int, default=25, help="Results per query (default 25)")
    p_harvest.add_argument("--time-filter", choices=["hour", "day", "week", "month", "year", "all"], default="month")
    p_harvest.add_argument("--global-only", action="store_true", help="Skip subreddit-specific, search globally")

    # signals
    p_signals = subparsers.add_parser("signals", help="List harvested signals")
    p_signals.add_argument("--limit", type=int, default=20)
    p_signals.add_argument("--industry", help="Filter by industry")
    p_signals.add_argument("--signal-type", help="Filter by signal type")
    p_signals.add_argument("--min-upvotes", type=int, help="Minimum upvotes")

    # sources
    subparsers.add_parser("sources", help="List harvest sources")

    # stats
    subparsers.add_parser("stats", help="Show harvest statistics")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "harvest":
        cmd_harvest(args)
    elif args.command == "signals":
        cmd_signals(args)
    elif args.command == "sources":
        cmd_sources(args)
    elif args.command == "stats":
        cmd_stats(args)


if __name__ == "__main__":
    main()
