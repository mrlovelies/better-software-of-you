#!/usr/bin/env python3
"""
Pipeline Notifier — Posts harvest/triage results to Discord.

Uses Discord bot token directly via webhook-style HTTP API calls.
No discord.py dependency — just urllib.

Usage:
  python3 pipeline_notify.py create-channel     # create #signal-harvester channel
  python3 pipeline_notify.py summary             # post current pipeline summary
  python3 pipeline_notify.py signals             # post new signals awaiting review
  python3 pipeline_notify.py competitive         # post competitive intel findings
  python3 pipeline_notify.py forecasts           # post new forecasts
"""

import sys
import os
import json
import sqlite3
import argparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")
ENV_PATH = os.path.join(PLUGIN_ROOT, ".env")

# Load .env
def load_env():
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip("'\"")
                env[k.strip()] = v
    return env

ENV = load_env()
BOT_TOKEN = ENV.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = ENV.get("DISCORD_GUILD_ID", "868529770114215936")
CHANNEL_ID = ENV.get("DISCORD_HARVEST_CHANNEL", "")

DISCORD_API = "https://discord.com/api/v10"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def discord_request(method, endpoint, data=None):
    """Make an authenticated Discord API request via curl (urllib has TLS issues on some machines)."""
    import subprocess
    url = f"{DISCORD_API}{endpoint}"

    cmd = ["curl", "-s", "-X", method,
           "-H", f"Authorization: Bot {BOT_TOKEN}",
           "-H", "Content-Type: application/json",
           url]

    if data:
        cmd.extend(["-d", json.dumps(data)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.stdout.strip():
            return json.loads(result.stdout)
        return None
    except Exception as e:
        print(f"  [error] Discord API {method} {endpoint}: {e}", file=sys.stderr)
        return None


def send_message(channel_id, content=None, embeds=None):
    """Send a message to a Discord channel."""
    data = {}
    if content:
        data["content"] = content[:2000]
    if embeds:
        data["embeds"] = embeds
    return discord_request("POST", f"/channels/{channel_id}/messages", data)


def send_embed(channel_id, title, description, color=0x2d5016, fields=None, footer=None):
    """Send an embed message."""
    embed = {"title": title, "description": description[:4096], "color": color}
    if fields:
        embed["fields"] = fields[:25]
    if footer:
        embed["footer"] = {"text": footer}
    return send_message(channel_id, embeds=[embed])


def get_guild_id():
    """Find the guild the bot is in."""
    global GUILD_ID
    if GUILD_ID:
        return GUILD_ID

    guilds = discord_request("GET", "/users/@me/guilds")
    if guilds and len(guilds) > 0:
        GUILD_ID = guilds[0]["id"]
        return GUILD_ID

    print("Error: Bot is not in any guilds.", file=sys.stderr)
    return None


def cmd_create_channel(args):
    """Create #signal-harvester channel in the bot's guild."""
    if not BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN not set in .env")
        return

    guild_id = get_guild_id()
    if not guild_id:
        return

    # Check if channel already exists
    channels = discord_request("GET", f"/guilds/{guild_id}/channels")
    if channels:
        for ch in channels:
            if ch["name"] == "signal-harvester":
                print(f"Channel #signal-harvester already exists: {ch['id']}")
                save_channel_id(ch["id"])
                return

    # Find or create a "Signal Harvester" category
    category_id = None
    if channels:
        for ch in channels:
            if ch["type"] == 4 and ch["name"].lower() in ("signal harvester", "harvester", "pipeline"):
                category_id = ch["id"]
                break

    # Create category if not found
    if not category_id:
        cat = discord_request("POST", f"/guilds/{guild_id}/channels", {
            "name": "Signal Harvester",
            "type": 4,  # category
        })
        if cat:
            category_id = cat["id"]
            print(f"Created category: Signal Harvester ({category_id})")

    # Create the main channel
    channel_data = {
        "name": "signal-harvester",
        "type": 0,  # text
        "topic": "Automated demand discovery pipeline — harvest signals, triage, forecasts, competitive intel",
    }
    if category_id:
        channel_data["parent_id"] = category_id

    channel = discord_request("POST", f"/guilds/{guild_id}/channels", channel_data)
    if channel:
        print(f"Created #signal-harvester: {channel['id']}")
        save_channel_id(channel["id"])

        # Also create sub-channels under the same category
        for name, topic in [
            ("harvest-log", "Raw harvest results and new signals"),
            ("triage-review", "Signals awaiting human review — approve or reject here"),
            ("competitive-intel", "Product dissatisfaction tracking and competitive opportunities"),
            ("forecasts", "Creative product ideas generated by the pipeline"),
        ]:
            sub = discord_request("POST", f"/guilds/{guild_id}/channels", {
                "name": name,
                "type": 0,
                "topic": topic,
                "parent_id": category_id,
            })
            if sub:
                print(f"  Created #{name}: {sub['id']}")
                save_sub_channel_id(name, sub["id"])

        # Send welcome message
        send_embed(channel["id"],
            "🌿 Signal Harvester Online",
            "Automated demand discovery pipeline is now posting to this channel.\n\n"
            "**Channels:**\n"
            "• **#harvest-log** — raw harvest results\n"
            "• **#triage-review** — signals to approve/reject\n"
            "• **#competitive-intel** — product complaint tracking\n"
            "• **#forecasts** — creative product ideas\n\n"
            "Pipeline runs automatically. You'll get notified when there are signals worth reviewing.",
            color=0x2d5016)
    else:
        print("Failed to create channel.")


def save_channel_id(channel_id):
    """Save the harvest channel ID to .env and soy_meta."""
    # Save to soy_meta
    db = get_db()
    db.execute("""
        INSERT OR REPLACE INTO soy_meta (key, value, updated_at)
        VALUES ('discord_harvest_channel', ?, datetime('now'))
    """, (channel_id,))
    db.commit()
    db.close()

    # Append to .env if not already there
    env_content = ""
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            env_content = f.read()

    if "DISCORD_HARVEST_CHANNEL" not in env_content:
        with open(ENV_PATH, "a") as f:
            f.write(f"\nDISCORD_HARVEST_CHANNEL={channel_id}\n")
        print(f"  Saved to .env: DISCORD_HARVEST_CHANNEL={channel_id}")


def save_sub_channel_id(name, channel_id):
    """Save sub-channel IDs to soy_meta."""
    db = get_db()
    key = f"discord_harvest_{name.replace('-', '_')}"
    db.execute("""
        INSERT OR REPLACE INTO soy_meta (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
    """, (key, channel_id))
    db.commit()
    db.close()


def get_channel_id(name="signal-harvester"):
    """Get a harvest channel ID from soy_meta."""
    db = get_db()
    key = f"discord_harvest_{name.replace('-', '_')}" if name != "signal-harvester" else "discord_harvest_channel"
    row = db.execute("SELECT value FROM soy_meta WHERE key = ?", (key,)).fetchone()
    db.close()
    return row["value"] if row else CHANNEL_ID


def cmd_summary(args):
    """Post pipeline summary to Discord."""
    channel_id = get_channel_id()
    if not channel_id:
        print("No harvest channel configured. Run 'create-channel' first.")
        return

    db = get_db()

    total = db.execute("SELECT COUNT(*) as c FROM harvest_signals").fetchone()["c"]
    approved = db.execute("SELECT COUNT(*) as c FROM harvest_triage WHERE verdict = 'approved'").fetchone()["c"]
    built = db.execute("SELECT COUNT(*) as c FROM harvest_builds").fetchone()["c"]
    pending_review = db.execute("""
        SELECT COUNT(*) as c FROM harvest_triage
        WHERE verdict = 'pending' AND composite_score IS NOT NULL AND human_reviewed = 0
    """).fetchone()["c"]
    comp_total = db.execute("SELECT COUNT(*) as c FROM competitive_signals WHERE complaint_summary IS NOT NULL").fetchone()["c"]
    comp_targets = db.execute("SELECT COUNT(*) as c FROM competitive_targets").fetchone()["c"]
    forecasts = db.execute("SELECT COUNT(*) as c FROM harvest_forecasts WHERE status = 'idea'").fetchone()["c"]

    fields = [
        {"name": "Signals Harvested", "value": str(total), "inline": True},
        {"name": "Approved", "value": str(approved), "inline": True},
        {"name": "Built", "value": str(built), "inline": True},
        {"name": "Awaiting Review", "value": f"**{pending_review}**" if pending_review > 0 else "0", "inline": True},
        {"name": "Competitive Signals", "value": str(comp_total), "inline": True},
        {"name": "Products Tracked", "value": str(comp_targets), "inline": True},
        {"name": "Forecast Ideas", "value": str(forecasts), "inline": True},
    ]

    color = 0xff9900 if pending_review > 0 else 0x2d5016
    footer = "🔔 Signals need your review!" if pending_review > 0 else "Pipeline running smoothly"

    send_embed(channel_id, "📊 Signal Harvester — Pipeline Status", "", color=color, fields=fields, footer=footer)
    print(f"Summary posted to Discord")
    db.close()


def cmd_signals(args):
    """Post new signals awaiting review to Discord."""
    review_channel = get_channel_id("triage-review") or get_channel_id()
    if not review_channel:
        print("No channel configured.")
        return

    db = get_db()
    rows = db.execute("""
        SELECT t.*, s.raw_text, s.source_url, s.subreddit, s.upvotes, s.extracted_pain, s.industry
        FROM harvest_triage t
        JOIN harvest_signals s ON s.id = t.signal_id
        WHERE t.verdict = 'pending' AND t.composite_score IS NOT NULL AND t.human_reviewed = 0
        ORDER BY t.composite_score DESC LIMIT 5
    """).fetchall()

    if not rows:
        print("No signals awaiting review.")
        return

    for row in rows:
        pain = row["extracted_pain"] or row["raw_text"][:200]
        fields = [
            {"name": "Industry", "value": row["industry"] or "?", "inline": True},
            {"name": "Composite", "value": f"{row['composite_score']}/10", "inline": True},
            {"name": "Subreddit", "value": f"r/{row['subreddit']}", "inline": True},
            {"name": "Market", "value": f"{row['market_size_score']}/10", "inline": True},
            {"name": "Money", "value": f"{row['monetization_score']}/10", "inline": True},
            {"name": "Gap", "value": f"{row['existing_solutions_score']}/10", "inline": True},
        ]

        color = 0x2d5016 if row["composite_score"] >= 6 else 0xff9900 if row["composite_score"] >= 5 else 0x888888

        send_embed(review_channel,
            f"🔍 Signal #{row['signal_id']} — {pain[:100]}",
            f"{pain[:500]}\n\n[View on Reddit]({row['source_url']})\n\n"
            f"**Approve:** `python3 shared/signal_triage.py approve {row['signal_id']}`\n"
            f"**Reject:** `python3 shared/signal_triage.py reject {row['signal_id']}`",
            color=color, fields=fields)

    print(f"Posted {len(rows)} signals to Discord")
    db.close()


def cmd_competitive(args):
    """Post competitive intel findings to Discord."""
    comp_channel = get_channel_id("competitive-intel") or get_channel_id()
    if not comp_channel:
        print("No channel configured.")
        return

    db = get_db()
    rows = db.execute("""
        SELECT * FROM competitive_signals
        WHERE complaint_summary IS NOT NULL AND verdict = 'pending' AND human_reviewed = 0
        ORDER BY composite_score DESC LIMIT 5
    """).fetchall()

    if not rows:
        print("No competitive signals to post.")
        return

    for row in rows:
        features = json.loads(row["missing_features"]) if row["missing_features"] else []
        fields = [
            {"name": "Product", "value": row["target_product"] or "?", "inline": True},
            {"name": "Category", "value": row["target_category"] or "?", "inline": True},
            {"name": "Complaint", "value": row["complaint_type"] or "?", "inline": True},
            {"name": "Composite", "value": f"{row['composite_score']}/10", "inline": True},
            {"name": "Switchability", "value": f"{row['switchability_score']}/10", "inline": True},
            {"name": "Build Advantage", "value": f"{row['build_advantage_score']}/10", "inline": True},
        ]
        if features:
            fields.append({"name": "Missing Features", "value": ", ".join(features[:5])})

        send_embed(comp_channel,
            f"⚔️ {row['target_product'] or '?'} — {row['complaint_summary'][:100]}",
            f"{row['complaint_summary']}\n\n{row['upvotes']}↑ {row['comment_count']}💬 on r/{row['subreddit']}",
            color=0xd4380d, fields=fields)

    print(f"Posted {len(rows)} competitive signals to Discord")
    db.close()


def cmd_forecasts(args):
    """Post new forecasts to Discord."""
    forecast_channel = get_channel_id("forecasts") or get_channel_id()
    if not forecast_channel:
        print("No channel configured.")
        return

    db = get_db()
    rows = db.execute("""
        SELECT * FROM harvest_forecasts WHERE status = 'idea'
        ORDER BY composite_score DESC LIMIT 5
    """).fetchall()

    if not rows:
        print("No forecasts to post.")
        return

    for row in rows:
        fields = [
            {"name": "Origin", "value": row["origin_type"] or "?", "inline": True},
            {"name": "Composite", "value": f"{row['composite_score']}/10", "inline": True},
            {"name": "Autonomy", "value": f"{row['autonomy_score']}/10", "inline": True},
            {"name": "Revenue Model", "value": row["revenue_model"] or "?", "inline": True},
            {"name": "Build", "value": f"~{row['estimated_build_days']} days", "inline": True},
            {"name": "MRR Est.", "value": f"${row['estimated_mrr_low'] or 0:.0f}-${row['estimated_mrr_high'] or 0:.0f}", "inline": True},
        ]

        send_embed(forecast_channel,
            f"💡 {row['title']}",
            f"{row['description']}\n\n*{row['origin_reasoning'][:300]}*",
            color=0x7c3aed, fields=fields)

    print(f"Posted {len(rows)} forecasts to Discord")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="Pipeline Notifier — Discord integration")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("create-channel", help="Create #signal-harvester channel and sub-channels")
    subparsers.add_parser("summary", help="Post pipeline summary")
    subparsers.add_parser("signals", help="Post signals awaiting review")
    subparsers.add_parser("competitive", help="Post competitive intel findings")
    subparsers.add_parser("forecasts", help="Post new forecasts")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "create-channel": cmd_create_channel,
        "summary": cmd_summary,
        "signals": cmd_signals,
        "competitive": cmd_competitive,
        "forecasts": cmd_forecasts,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
