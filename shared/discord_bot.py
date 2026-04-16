#!/usr/bin/env python3
"""Local Discord bot for Software of You.

Mirrors the Telegram bot (telegram_bot.py) but for Discord using discord.py.
Runs locally as a persistent service. Uses `claude -p` (Claude Code CLI) for AI
responses and `claude -p --dangerously-skip-permissions` for autonomous dev
sessions. Reads/writes the local SoY SQLite database directly.

Prerequisites:
    - Python 3.8+
    - discord.py (`pip install discord.py`)
    - Claude Code CLI (`claude`) in PATH with active subscription
    - git (for dev sessions)
    - vercel CLI (optional, for preview deploys)
    - DISCORD_BOT_TOKEN and DISCORD_OWNER_ID in .env

Usage:
    python3 shared/discord_bot.py
"""

import asyncio
import contextlib
import glob
import json
import os
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid

try:
    import discord
    from discord import app_commands
except ImportError:
    print("Error: discord.py not installed. Run: pip install discord.py")
    sys.exit(1)

class SessionReviewView(discord.ui.View):
    """Interactive buttons for dev session approve/reject/view."""

    def __init__(self, session_id: str, bot_instance):
        super().__init__(timeout=86400)  # 24 hours
        self.session_id = session_id
        self.bot_instance = bot_instance

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, emoji="\u2705")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        result = self.bot_instance._handle_approve_sync(self.session_id)
        await interaction.response.send_message(result, ephemeral=False)
        self.disable_all()
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.red, emoji="\u274c")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        result = self.bot_instance._handle_reject_sync(self.session_id)
        await interaction.response.send_message(result, ephemeral=False)
        self.disable_all()
        await interaction.message.edit(view=self)

    @discord.ui.button(label="View Output", style=discord.ButtonStyle.grey, emoji="\U0001f4cb")
    async def view_output(self, interaction: discord.Interaction, button: discord.ui.Button):
        result = self.bot_instance._get_session_output_sync(self.session_id)
        if len(result) > 1900:
            result = result[:1900] + "\n..."
        await interaction.response.send_message(f"```\n{result}\n```", ephemeral=True)

    def disable_all(self):
        for item in self.children:
            item.disabled = True


PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")

# Load .env if present
ENV_PATH = os.path.join(PLUGIN_ROOT, ".env")
if os.path.exists(ENV_PATH):
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                    v = v[1:-1]
                os.environ.setdefault(k.strip(), v)

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
OWNER_ID = os.environ.get("DISCORD_OWNER_ID", "")
NUDGES_CHANNEL_NAME = os.environ.get("DISCORD_NUDGES_CHANNEL", "nudges")

# ── Constants ──

# Discord
MAX_MESSAGE_LEN = 2000

# Claude
DEFAULT_MODEL = "sonnet"
ALLOWED_MODELS = {"sonnet", "opus", "haiku"}
CLAUDE_TIMEOUT = 120

# Sessions & timeouts
SESSION_TIMEOUT_HOURS = 4
DEV_SESSION_TIMEOUT = 600  # 10 minutes
NEW_PROJECT_TIMEOUT = 1200  # 20 minutes
DEV_SESSION_MAX_ACTIVE = 3
DEPLOY_TIMEOUT = 180  # 3 minutes
CONFIRMATION_TIMEOUT = 120  # seconds

# Workspace & files
DEV_BRANCH_PREFIX = "dev/"
WORKSPACE_BASE = os.environ.get("SOY_WORKSPACE_ROOT", "~/wkspaces")
INBOX_PROJECT_NAME = "Discord Inbox"
ERROR_LOG_MAX = 50
TEMP_FILE_MAX_AGE = 86400  # 24 hours
TASK_DISPLAY_LIMIT = 30

# Nudges
NUDGE_INTERVAL = 3600  # 1 hour between nudge checks

# Icons (same as Telegram bot)
STATUS_ICONS = {
    "running": "\U0001f504", "completed": "\u2705", "failed": "\u274c",
    "timeout": "\u23f0", "killed": "\U0001f480",
}
DEPLOY_ICONS = {
    "deploying": "\U0001f680", "deployed": "\U0001f310", "deploy_failed": "\u26a0\ufe0f",
}
REVIEW_ICONS = {
    "pending": "\u23f3", "approved": "\u2705", "rejected": "\U0001f5d1",
}
PRIORITY_ICONS = {
    "urgent": "\U0001f534", "high": "\U0001f7e0", "medium": "\U0001f7e1", "low": "\u26aa",
}
PRIORITY_COLORS = {
    "urgent": 0xED4245,  # red
    "high": 0xE67E22,    # orange
    "medium": 0xF1C40F,  # yellow
    "low": 0x95A5A6,     # grey
}
STATUS_COLORS = {
    "running": 0x3498DB,    # blue
    "completed": 0x2ECC71,  # green
    "failed": 0xED4245,     # red
    "timeout": 0xE67E22,    # orange
    "killed": 0x95A5A6,     # grey
    "deployed": 0x2ECC71,
    "deploy_failed": 0xE67E22,
}
PROJECT_COLOR = 0x5865F2  # discord blurple
NUDGE_COLOR = 0xFEE75C    # yellow


class SoYBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)
        self.start_time_ts = None
        self.message_count = 0
        self.last_claude_call = None
        self.current_session_id = None
        self.active_dev_sessions = {}
        self.active_deploys = {}
        self.workspace_locks = set()
        self.pending_confirmations = {}  # channel_id -> {action, data, expires_at}
        self._claude_available = True
        self._nudges_channel = None
        self._last_nudge_time = 0
        self._last_digest_date = None
        self._setup_slash_commands()

    # ── Slash Command Registration ──

    def _setup_slash_commands(self):
        """Register Discord slash commands."""

        @self.tree.command(name="status", description="Project overview and task counts")
        async def cmd_status(interaction: discord.Interaction):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            embeds = self._build_status_embeds()
            await interaction.followup.send(embeds=embeds)

        @self.tree.command(name="tasks", description="Open tasks (optionally filter by project)")
        @app_commands.describe(project="Project name to filter by")
        async def cmd_tasks(interaction: discord.Interaction, project: str = ""):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            embed = self._build_tasks_embed(project)
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="notes", description="Recent notes")
        @app_commands.describe(search="Search term")
        async def cmd_notes(interaction: discord.Interaction, search: str = ""):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            text = self._get_notes_text(search)
            await self._send_long(interaction.followup, text)

        @self.tree.command(name="dev", description="Spawn a remote dev session")
        @app_commands.describe(
            project="Project name or ID (auto-detected from channel if linked)",
            instruction="What to build/fix",
            model="LLM model (sonnet/opus/haiku)",
        )
        async def cmd_dev(interaction: discord.Interaction, instruction: str = "", project: str = "", model: str = DEFAULT_MODEL):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            # Auto-detect project from channel if not specified
            if not project:
                _, channel_project = self._get_channel_project(str(interaction.channel_id))
                if channel_project:
                    project = channel_project
            if not project or not instruction:
                await interaction.followup.send(
                    "**Usage:** `/dev instruction:<what to do>` (in a project channel)\n"
                    "**Or:** `/dev project:<name> instruction:<what to do>`\n\n"
                    f"Options: model (default: {DEFAULT_MODEL})")
                return
            await self._handle_dev(interaction.channel, f"{project} {instruction}", model)

        @self.tree.command(name="new", description="Create a new project from scratch")
        @app_commands.describe(
            slug="Project slug (lowercase, hyphens)",
            instruction="What to build",
            model="LLM model (sonnet/opus/haiku)",
        )
        async def cmd_new(interaction: discord.Interaction, slug: str = "", instruction: str = "", model: str = DEFAULT_MODEL):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            if not slug or not instruction:
                await interaction.followup.send(
                    "**Usage:** `/new slug:<project-slug> instruction:<what to build>`\n\n"
                    f"Example: `/new slug:acme-landing instruction:Build a modern landing page`\n\n"
                    f"Options: model (default: {DEFAULT_MODEL})")
                return
            await self._handle_new_project(interaction.channel, slug, instruction, model)

        @self.tree.command(name="sessions", description="List recent dev sessions")
        async def cmd_sessions(interaction: discord.Interaction):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            embeds = self._build_sessions_embeds()
            await interaction.followup.send(embeds=embeds)

        @self.tree.command(name="session", description="View session output")
        @app_commands.describe(session_id="Session ID or prefix")
        async def cmd_session(interaction: discord.Interaction, session_id: str):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            result = self._build_session_embed(session_id)
            if isinstance(result, str):
                await interaction.followup.send(result)
            else:
                await interaction.followup.send(embed=result)

        @self.tree.command(name="approve", description="Merge a session's branch to main")
        @app_commands.describe(session_id="Session ID or prefix")
        async def cmd_approve(interaction: discord.Interaction, session_id: str):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            await self._handle_approve(interaction.channel, session_id)

        @self.tree.command(name="reject", description="Discard a session's branch")
        @app_commands.describe(session_id="Session ID or prefix")
        async def cmd_reject(interaction: discord.Interaction, session_id: str):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            await self._handle_reject(interaction.channel, session_id)

        @self.tree.command(name="kill", description="Kill a running dev session")
        @app_commands.describe(session_id="Session ID or prefix")
        async def cmd_kill(interaction: discord.Interaction, session_id: str):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            text = self._handle_kill(session_id)
            await interaction.followup.send(text)

        @self.tree.command(name="debug", description="Bot diagnostics")
        async def cmd_debug(interaction: discord.Interaction):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            embed = self._build_debug_embed(str(interaction.channel_id))
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="errors", description="Recent error log")
        async def cmd_errors(interaction: discord.Interaction):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            embed = self._build_errors_embed()
            await interaction.followup.send(embed=embed)

        @self.tree.command(name="sync", description="Create/sync Discord channels for all active projects")
        async def cmd_sync(interaction: discord.Interaction):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            text = await self._sync_project_channels(interaction.guild)
            await self._send_long(interaction.followup, text)

        @self.tree.command(name="link", description="Link this channel to a project")
        @app_commands.describe(project="Project name or ID")
        async def cmd_link(interaction: discord.Interaction, project: str):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            text = self._link_channel(str(interaction.channel_id), project)
            await interaction.followup.send(text)

        @self.tree.command(name="unlink", description="Unlink this channel from its project")
        async def cmd_unlink(interaction: discord.Interaction):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            text = self._unlink_channel(str(interaction.channel_id))
            await interaction.followup.send(text)

        @self.tree.command(name="set-model", description="Set preferred AI model for this channel")
        @app_commands.describe(model="Model to use: sonnet (default), opus (smarter/slower), haiku (fast/cheap), or reset")
        async def cmd_set_model(interaction: discord.Interaction, model: str):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            text = self._set_channel_model(str(interaction.channel_id), model)
            await interaction.followup.send(text)

        @self.tree.command(name="clear", description="Clear conversation history for this channel")
        @app_commands.describe(scope="channel (default) or all to clear every channel")
        async def cmd_clear(interaction: discord.Interaction, scope: str = "channel"):
            if not self._is_owner(interaction):
                await interaction.response.send_message("Unauthorized.", ephemeral=True)
                return
            await interaction.response.defer()
            channel_id = str(interaction.channel_id)
            with self._db() as conn:
                if scope == "all":
                    result = conn.execute("DELETE FROM discord_conversations")
                    conn.execute("DELETE FROM discord_bot_sessions")
                    conn.commit()
                    count = result.rowcount
                    await interaction.followup.send(
                        f"\u2705 Cleared {count} messages across all channels. Sessions reset."
                    )
                else:
                    # Get active session for this channel
                    session_id = self._get_or_create_session(channel_id)
                    result = conn.execute(
                        "DELETE FROM discord_conversations WHERE channel_id = ?",
                        (channel_id,),
                    )
                    conn.execute(
                        "DELETE FROM discord_bot_sessions WHERE channel_id = ?",
                        (channel_id,),
                    )
                    conn.commit()
                    count = result.rowcount
                    await interaction.followup.send(
                        f"\u2705 Cleared {count} messages for this channel. Session reset."
                    )

    # ── Discord Events ──

    async def on_ready(self):
        """Called when bot connects to Discord."""
        self.start_time_ts = time.time()

        # Apply migrations
        self._run_migrations()

        # Recover orphaned sessions
        self._recover_orphaned_sessions()
        self._cleanup_old_temp_files()

        # Check Claude CLI
        if not shutil.which("claude"):
            print("Warning: `claude` CLI not found. AI chat unavailable.")
            self._claude_available = False

        # Sync slash commands
        await self.tree.sync()

        # Auto-create project channels and #nudges
        for guild in self.guilds:
            result = await self._sync_project_channels(guild)
            print(result.replace("**", "").replace("\u2705", "+").replace("\u2014", "-"))

        # Find nudges channel (may have just been created)
        for guild in self.guilds:
            for channel in guild.text_channels:
                if channel.name == NUDGES_CHANNEL_NAME:
                    self._nudges_channel = channel
                    break

        masked_id = f"{OWNER_ID[:3]}...{OWNER_ID[-2:]}" if len(OWNER_ID) > 5 else "***"
        print(f"SoY Discord bot started — {self.user} (local mode, claude -p)")
        print(f"Owner ID: {masked_id}")
        print(f"Database: {DB_PATH}")
        if self._nudges_channel:
            print(f"Nudges channel: #{self._nudges_channel.name}")
        print("Press Ctrl-C to stop.\n")

        # Start background polling for dev sessions
        self.loop.create_task(self._poll_sessions())
        self.loop.create_task(self._rotate_presence())

        # Emit heartbeat event
        try:
            sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared"))
            from soy_logging import emit_event
            emit_event("agent_started", "discord-bot", f"Bot started as {self.user}")
        except Exception:
            pass

    async def on_message(self, message):
        """Process incoming messages."""
        # Ignore own messages
        if message.author == self.user:
            return

        # Owner check
        if str(message.author.id) != str(OWNER_ID):
            return

        channel = message.channel
        channel_id = str(channel.id)
        is_thread = hasattr(channel, 'parent') and channel.parent is not None

        # Build message text — include attachment descriptions for context
        text = message.content.strip()
        attachment_descriptions = []
        for att in message.attachments:
            ext = os.path.splitext(att.filename)[1].lower()
            if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                attachment_descriptions.append(f"[User attached image: {att.filename}]")
            elif ext == ".pdf":
                attachment_descriptions.append(f"[User attached PDF: {att.filename}]")
            else:
                attachment_descriptions.append(f"[User attached file: {att.filename}]")

        # Handle file-only drops (no text) in non-thread channels
        if message.attachments and not text and not is_thread:
            await self._handle_file_drop(message)
            session_id = self._get_or_create_session(channel_id)
            file_context = " ".join(attachment_descriptions)
            self._save_message(session_id, "user", file_context, str(message.id), channel_id)
            return

        if not text and not attachment_descriptions:
            return

        # Combine text with attachment descriptions
        full_text = text
        if attachment_descriptions:
            full_text = " ".join(attachment_descriptions) + "\n" + text if text else " ".join(attachment_descriptions)

        self.message_count += 1
        timestamp = time.strftime("%H:%M:%S")
        channel_label = getattr(channel, 'name', 'DM')
        if is_thread:
            channel_label = f"{getattr(channel.parent, 'name', '?')}/{channel_label}"
        print(f"[{timestamp}] #{channel_label} Message #{self.message_count}: {full_text[:80]}{'...' if len(full_text) > 80 else ''}")

        try:
            # Check for pending confirmation
            if channel_id in self.pending_confirmations:
                conf = self.pending_confirmations[channel_id]
                if time.time() > conf["expires_at"]:
                    del self.pending_confirmations[channel_id]
                else:
                    await self._handle_pending_confirmation(text, channel)
                    return

            # Intercept natural language dev requests (not in threads — threads are conversations)
            if not is_thread and self._is_dev_request(text):
                await self._suggest_dev_command(text, channel)
                return

            # Quick bypass: handle simple DB queries without Claude
            bypass_response = await self._try_quick_bypass(full_text, channel_id)
            if bypass_response:
                session_id = self._get_or_create_session(channel_id)
                self._save_message(session_id, "user", full_text, str(message.id), channel_id)
                self._save_message(session_id, "assistant", bypass_response, None, channel_id)
                await self._send_split(channel, bypass_response)
                return

            # Natural language → claude -p
            if not self._claude_available:
                await channel.send("AI chat is offline (claude CLI not installed). Slash commands still work.")
                return

            # Handle file attachments alongside the AI call
            if message.attachments:
                await self._handle_file_drop(message)

            async with channel.typing():
                if is_thread:
                    # Thread mode: fetch history from Discord API for context
                    thread_history = await self._fetch_thread_history(channel)
                    parent_channel_id = str(channel.parent.id)
                    # Also save to DB so thread conversations are discoverable
                    thread_session_id = self._get_or_create_session(channel_id)
                    self._save_message(thread_session_id, "user", full_text, str(message.id), channel_id)
                    response = await asyncio.get_event_loop().run_in_executor(
                        None, self._call_claude_with_history, thread_history, parent_channel_id
                    )
                else:
                    # Channel mode: use DB-backed session
                    session_id = self._get_or_create_session(channel_id)
                    self._save_message(session_id, "user", full_text, str(message.id), channel_id)
                    response = await asyncio.get_event_loop().run_in_executor(
                        None, self._call_claude, full_text, session_id, channel_id
                    )

                # Parse task/note markers
                tasks, notes, cleaned_response = self._parse_markers(response)

                # Capture items to local DB
                captured = []
                if tasks or notes:
                    captured = self._capture_items(tasks, notes)

                final = cleaned_response
                if captured:
                    final += "\n\n*Captured:*\n" + "\n".join(f"- {c}" for c in captured)

                if not final.strip():
                    final = "Done."

                # Save assistant reply to DB
                save_session = thread_session_id if is_thread else session_id
                self._save_message(save_session, "assistant", final, channel_id=channel_id)

                await self._send_long_channel(channel, final)

        except Exception as e:
            self._log_error(str(e), channel_id=channel_id, user_msg=text)
            await channel.send("Something went wrong. Check `/errors` for details.")

    # ── File Drops ──

    async def _handle_file_drop(self, message):
        """Handle file attachments in project channels."""
        channel = message.channel
        channel_id = str(channel.id)
        project_id, project_name = self._get_channel_project(channel_id)

        for attachment in message.attachments:
            filename = attachment.filename.lower()
            ext = os.path.splitext(filename)[1]

            # Receipt/expense photos
            if ext in (".jpg", ".jpeg", ".png", ".webp", ".pdf"):
                # Save to receipts incoming folder
                photo_dir = os.path.join(
                    os.path.expanduser("~"), "Documents", "taxes",
                    str(time.strftime("%Y")), "receipts", "incoming",
                )
                os.makedirs(photo_dir, exist_ok=True)
                date_prefix = time.strftime("%Y-%m-%d_%H%M%S")
                safe_name = re.sub(r"[^\w.\-]", "_", attachment.filename).lower()
                dest = os.path.join(photo_dir, f"{date_prefix}_{safe_name}")

                await attachment.save(dest)

                try:
                    with self._db() as conn:
                        conn.execute(
                            "INSERT INTO activity_log (entity_type, entity_id, action, details, created_at) "
                            "VALUES ('receipt', 0, 'photo_received', ?, datetime('now'))",
                            (f"File saved: {dest}. Channel: #{getattr(channel, 'name', 'DM')}. Project: {project_name or 'none'}",),
                        )
                        conn.commit()
                except Exception:
                    pass

                caption = message.content.strip() if message.content else ""
                if caption:
                    await channel.send(f"\U0001f4f8 Receipt saved and logged: `{attachment.filename}`")
                else:
                    await channel.send(
                        f"\U0001f4f8 Saved `{attachment.filename}` to receipts.\n"
                        f"Reply with a description to log as an expense (e.g. \"lunch with client $45\").")
            else:
                # Generic file — log it
                proj_note = f" for **{project_name}**" if project_name else ""
                await channel.send(f"\U0001f4ce Received `{attachment.filename}`{proj_note}. (File storage coming soon.)")

    # ── Reaction Shortcuts ──

    async def on_raw_reaction_add(self, payload):
        """Handle emoji reactions as quick actions."""
        # Only respond to owner
        if str(payload.user_id) != str(OWNER_ID):
            return

        emoji = str(payload.emoji)
        channel = self.get_channel(payload.channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        # Only react to bot's own messages
        if message.author != self.user:
            return

        # Check mark = mark task done (from nudge embeds)
        if emoji == "\u2705" and message.embeds:
            embed = message.embeds[0]
            if "Overdue" in (embed.title or "") or "Due Soon" in (embed.title or ""):
                await channel.send("\u2705 To mark tasks done, use the SoY `/tasks` command for now.")

        # Eyes = acknowledge / "I'll review later"
        elif emoji == "\U0001f440" and message.embeds:
            embed = message.embeds[0]
            if "Session" in (embed.title or "") or "Preview" in (embed.title or ""):
                await channel.send("\U0001f440 Noted \u2014 session flagged for review.")

    # ── Background Tasks ──

    async def _rotate_presence(self):
        """Rotate bot presence between useful status lines every 60 seconds."""
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                status_lines = []
                with self._db() as conn:
                    # Overdue tasks
                    try:
                        overdue = conn.execute(
                            "SELECT COUNT(*) as c FROM tasks WHERE status = 'open' AND due_date < date('now')"
                        ).fetchone()
                        if overdue and overdue["c"] > 0:
                            status_lines.append(f"{overdue['c']} tasks overdue")
                    except Exception:
                        pass

                    # Running dev sessions
                    active_count = len(self.active_dev_sessions)
                    if active_count > 0:
                        status_lines.append(f"{active_count} dev session{'s' if active_count > 1 else ''} running")

                    # Next calendar event
                    try:
                        next_event = conn.execute(
                            "SELECT title, start_time FROM calendar_events "
                            "WHERE start_time > datetime('now') ORDER BY start_time LIMIT 1"
                        ).fetchone()
                        if next_event:
                            title = next_event["title"][:30]
                            start = next_event["start_time"][11:16] if next_event["start_time"] else ""
                            status_lines.append(f"Next: {title} @ {start}")
                    except Exception:
                        pass

                    # Open tasks count
                    try:
                        open_count = conn.execute(
                            "SELECT COUNT(*) as c FROM tasks WHERE status = 'open'"
                        ).fetchone()
                        if open_count:
                            status_lines.append(f"{open_count['c']} open tasks")
                    except Exception:
                        pass

                if not status_lines:
                    status_lines = ["Software of You"]

                # Pick the next one in rotation
                idx = int(time.time() / 60) % len(status_lines)
                text = status_lines[idx]
                await self.change_presence(activity=discord.Activity(
                    type=discord.ActivityType.watching, name=text
                ))
            except Exception:
                pass
            await asyncio.sleep(60)

    async def _poll_sessions(self):
        """Periodically check dev sessions, deploys, and nudges."""
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                if self.active_dev_sessions:
                    await self._check_dev_sessions()
                if self.active_deploys:
                    await self._check_deploys()
                # Nudge check every NUDGE_INTERVAL
                if self._nudges_channel and (time.time() - self._last_nudge_time) > NUDGE_INTERVAL:
                    await self._post_nudges()
                    self._last_nudge_time = time.time()
                # Daily morning digest (post once per day after 8 AM)
                if self._nudges_channel:
                    today = time.strftime("%Y-%m-%d")
                    hour = int(time.strftime("%H"))
                    if hour >= 8 and self._last_digest_date != today:
                        await self._post_daily_digest()
                        self._last_digest_date = today
            except Exception as e:
                print(f"[poll] Error: {e}")
            await asyncio.sleep(3)

    # ── Message Helpers ──

    async def _send_long_channel(self, channel, text):
        """Send a message, splitting at paragraph boundaries if too long."""
        chunks = self._chunk_text(text)
        for chunk in chunks:
            await channel.send(chunk)

    async def _send_long(self, followup, text):
        """Send via interaction followup, splitting if needed."""
        chunks = self._chunk_text(text)
        for i, chunk in enumerate(chunks):
            if i == 0:
                await followup.send(chunk)
            else:
                await followup.send(chunk)

    @staticmethod
    def _chunk_text(text):
        """Split text at paragraph boundaries for Discord's 2000 char limit."""
        if len(text) <= MAX_MESSAGE_LEN:
            return [text]
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= MAX_MESSAGE_LEN:
                chunks.append(remaining)
                break
            split_idx = remaining.rfind("\n\n", 0, MAX_MESSAGE_LEN)
            if split_idx < MAX_MESSAGE_LEN // 2:
                split_idx = remaining.rfind("\n", 0, MAX_MESSAGE_LEN)
            if split_idx < MAX_MESSAGE_LEN // 2:
                split_idx = MAX_MESSAGE_LEN
            chunks.append(remaining[:split_idx])
            remaining = remaining[split_idx:].lstrip()
        return chunks

    def _is_owner(self, interaction):
        """Check if interaction is from the bot owner."""
        return str(interaction.user.id) == str(OWNER_ID)

    # ── Database ──

    def _run_migrations(self):
        """Apply all SQL migrations on startup."""
        migrations_dir = os.path.join(PLUGIN_ROOT, "data", "migrations")
        if not os.path.isdir(migrations_dir):
            return
        sql_files = sorted(glob.glob(os.path.join(migrations_dir, "*.sql")))
        if not sql_files:
            return
        conn = sqlite3.connect(DB_PATH)
        try:
            for sql_file in sql_files:
                with open(sql_file) as f:
                    sql = f.read()
                try:
                    conn.executescript(sql)
                except sqlite3.Error as e:
                    print(f"Migration warning ({os.path.basename(sql_file)}): {e}")
            conn.commit()
        finally:
            conn.close()
        print(f"Migrations: applied {len(sql_files)} files")

    @contextlib.contextmanager
    def _db(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Session Management ──

    def _get_or_create_session(self, channel_id=None):
        """Get active session or create new one."""
        with self._db() as conn:
            row = conn.execute(
                "SELECT id, message_count FROM discord_bot_sessions "
                "WHERE last_message_at > datetime('now', ? || ' hours') "
                "AND (channel_id = ? OR ? IS NULL) "
                "ORDER BY last_message_at DESC LIMIT 1",
                (f"-{SESSION_TIMEOUT_HOURS}", channel_id, channel_id),
            ).fetchone()

            if row:
                session_id = row["id"]
                conn.execute(
                    "UPDATE discord_bot_sessions SET last_message_at = datetime('now'), "
                    "message_count = message_count + 1 WHERE id = ?",
                    (session_id,),
                )
            else:
                # Previous session expired — write a close summary to session_handoffs
                expired = conn.execute(
                    "SELECT id, channel_id, message_count, started_at FROM discord_bot_sessions "
                    "WHERE (channel_id = ? OR ? IS NULL) "
                    "ORDER BY last_message_at DESC LIMIT 1",
                    (channel_id, channel_id),
                ).fetchone()
                if expired and expired["message_count"] and expired["message_count"] >= 3:
                    self._write_session_close_summary(conn, expired["id"], expired["channel_id"])

                session_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO discord_bot_sessions (id, channel_id) VALUES (?, ?)",
                    (session_id, channel_id),
                )
            conn.commit()
        self.current_session_id = session_id
        return session_id

    def _save_message(self, session_id, role, content, discord_msg_id=None, channel_id=None):
        """Save message to conversation history."""
        with self._db() as conn:
            conn.execute(
                "INSERT INTO discord_conversations (session_id, role, content, discord_message_id, channel_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, discord_msg_id, channel_id),
            )
            conn.commit()

    _HISTORY_RECENT_COUNT = 8  # Keep last N messages in full
    _HISTORY_SUMMARIZE_THRESHOLD = 15  # Summarize when total exceeds this

    def _get_history(self, session_id, limit=30):
        """Get conversation history with sliding window.
        When > 15 messages, older messages are compressed into a summary
        to reduce token usage while preserving context."""
        with self._db() as conn:
            rows = conn.execute(
                "SELECT role, content FROM discord_conversations "
                "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        messages = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

        if len(messages) <= self._HISTORY_SUMMARIZE_THRESHOLD:
            return messages

        # Split into older (to summarize) and recent (keep full)
        older = messages[:-self._HISTORY_RECENT_COUNT]
        recent = messages[-self._HISTORY_RECENT_COUNT:]

        # Build a compact summary of older messages
        summary_parts = []
        for msg in older:
            role = "User" if msg["role"] == "user" else "Bot"
            # Truncate each message to key content
            text = msg["content"][:100]
            if len(msg["content"]) > 100:
                text += "..."
            summary_parts.append(f"  {role}: {text}")

        summary = {
            "role": "system",
            "content": f"[Earlier conversation summary ({len(older)} messages):\n"
                       + "\n".join(summary_parts) + "\n]"
        }

        return [summary] + recent

    async def _try_quick_bypass(self, text: str, channel_id: str) -> str | None:
        """Try to answer simple queries directly from DB without Claude. Returns response or None."""
        lower = text.lower().strip()

        # Task queries
        if lower in ("tasks", "my tasks", "task list", "what are my tasks", "open tasks"):
            with self._db() as conn:
                rows = conn.execute(
                    "SELECT t.title, t.priority, p.name as project FROM tasks t "
                    "LEFT JOIN projects p ON p.id = t.project_id "
                    "WHERE t.status = 'open' ORDER BY "
                    "CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END "
                    "LIMIT 15"
                ).fetchall()
            if not rows:
                return "No open tasks."
            lines = ["**Open Tasks:**"]
            for r in rows:
                pri = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(r["priority"], "⚪")
                proj = f" ({r['project']})" if r["project"] else ""
                lines.append(f"{pri} {r['title']}{proj}")
            return "\n".join(lines)

        # Calendar/schedule queries
        if lower in ("calendar", "schedule", "what's today", "whats today", "what's on my calendar",
                      "what's on today", "today's schedule", "meetings today", "meetings"):
            with self._db() as conn:
                rows = conn.execute(
                    "SELECT title, start_time, end_time, location FROM calendar_events "
                    "WHERE date(start_time) = date('now') ORDER BY start_time"
                ).fetchall()
            if not rows:
                return "Nothing on the calendar today."
            lines = ["**Today's Schedule:**"]
            for r in rows:
                start = r["start_time"][11:16] if r["start_time"] and len(r["start_time"]) > 11 else "?"
                lines.append(f"• **{start}** — {r['title']}")
            return "\n".join(lines)

        # Status query
        if lower in ("status", "project status", "projects"):
            with self._db() as conn:
                rows = conn.execute(
                    "SELECT p.name, p.status, "
                    "(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'open') as open_tasks "
                    "FROM projects p WHERE p.status IN ('active', 'planning') ORDER BY p.name"
                ).fetchall()
            if not rows:
                return "No active projects."
            lines = ["**Active Projects:**"]
            for r in rows:
                tasks_info = f" ({r['open_tasks']} open tasks)" if r["open_tasks"] else ""
                lines.append(f"• **{r['name']}**{tasks_info} — {r['status']}")
            return "\n".join(lines)

        # Nudges
        if lower in ("nudges", "what needs attention", "attention", "what's overdue"):
            with self._db() as conn:
                try:
                    rows = conn.execute(
                        "SELECT tier, item_type, summary FROM v_nudge_items ORDER BY "
                        "CASE tier WHEN 'urgent' THEN 0 WHEN 'soon' THEN 1 ELSE 2 END LIMIT 10"
                    ).fetchall()
                except Exception:
                    return None  # View might not exist, fall through to Claude
            if not rows:
                return "Nothing needs attention right now."
            lines = ["**Needs Attention:**"]
            for r in rows:
                tier = {"urgent": "🔴", "soon": "🟠", "awareness": "🟡"}.get(r["tier"], "⚪")
                lines.append(f"{tier} {r['summary']}")
            return "\n".join(lines)

        return None  # No bypass — proceed to Claude

    def _write_session_close_summary(self, conn, session_id, channel_id):
        """Write a summary of an expired Discord session to session_handoffs."""
        try:
            msgs = conn.execute(
                "SELECT role, content FROM discord_conversations "
                "WHERE session_id = ? ORDER BY created_at LIMIT 50",
                (session_id,),
            ).fetchall()
            if not msgs:
                return

            # Build a compact summary from user messages
            user_msgs = [m["content"][:150] for m in msgs if m["role"] == "user"]
            if not user_msgs:
                return

            # Check for project context
            project_name = None
            proj = conn.execute(
                "SELECT project_name FROM discord_channel_projects WHERE channel_id = ?",
                (str(channel_id),),
            ).fetchone()
            if proj:
                project_name = proj["project_name"]

            summary_parts = ["## Discord Session Summary\n"]
            if project_name:
                summary_parts.append(f"**Project:** {project_name}\n")
            summary_parts.append(f"**Messages:** {len(msgs)}\n")
            summary_parts.append("\n### Topics discussed\n")
            for msg in user_msgs[:10]:
                summary_parts.append(f"- {msg}\n")

            summary = "".join(summary_parts)

            conn.execute(
                "INSERT INTO session_handoffs (summary, source, status) VALUES (?, ?, ?)",
                (summary, "discord", "consumed"),
            )
        except Exception:
            pass  # Don't let summary generation break session creation

    # ── Channel-Project Routing ──

    def _get_channel_project(self, channel_id):
        """Get project linked to a channel, if any."""
        with self._db() as conn:
            row = conn.execute(
                "SELECT project_id, project_name FROM discord_channel_projects WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
        if row:
            return row["project_id"], row["project_name"]
        return None, None

    def _get_channel_model(self, channel_id):
        """Get preferred model for a channel. Falls back to DEFAULT_MODEL."""
        if not channel_id:
            return DEFAULT_MODEL
        with self._db() as conn:
            row = conn.execute(
                "SELECT preferred_model FROM discord_channel_projects WHERE channel_id = ?",
                (str(channel_id),),
            ).fetchone()
        if row and row["preferred_model"] and row["preferred_model"] in ALLOWED_MODELS:
            return row["preferred_model"]
        return DEFAULT_MODEL

    def _set_channel_model(self, channel_id, model):
        """Set or reset the preferred model for a channel."""
        model = model.strip().lower()
        if model == "reset" or model == "default":
            with self._db() as conn:
                # Only update if the row exists
                row = conn.execute(
                    "SELECT channel_id FROM discord_channel_projects WHERE channel_id = ?",
                    (channel_id,),
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE discord_channel_projects SET preferred_model = NULL WHERE channel_id = ?",
                        (channel_id,),
                    )
                    conn.commit()
            return f"✅ Model reset to default (**{DEFAULT_MODEL}**) for this channel."

        if model not in ALLOWED_MODELS:
            return (
                f"❌ Unknown model `{model}`. Valid options: `sonnet`, `opus`, `haiku`, or `reset`.\n\n"
                f"**sonnet** — default, fast and capable\n"
                f"**opus** — best reasoning, slower and more expensive\n"
                f"**haiku** — fastest, lightest tasks"
            )

        with self._db() as conn:
            row = conn.execute(
                "SELECT channel_id FROM discord_channel_projects WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE discord_channel_projects SET preferred_model = ? WHERE channel_id = ?",
                    (model, channel_id),
                )
            else:
                # Channel not yet linked to a project — create a model-only entry
                conn.execute(
                    "INSERT INTO discord_channel_projects (channel_id, project_name, preferred_model) "
                    "VALUES (?, '', ?)",
                    (channel_id, model),
                )
            conn.commit()

        model_notes = {
            "opus": "🧠 Opus is on — best for complex reasoning, architecture, and writing. Slower and pricier.",
            "haiku": "⚡ Haiku is on — fastest model, great for quick lookups and simple tasks.",
            "sonnet": "✅ Sonnet is on — the default. Fast, capable, well-rounded.",
        }
        return f"✅ This channel will now use **{model}**.\n{model_notes.get(model, '')}"

    async def _sync_project_channels(self, guild):
        """Create Discord channels for all active projects and link them. Also creates #nudges."""
        with self._db() as conn:
            projects = conn.execute(
                "SELECT id, name, workspace_path FROM projects "
                "WHERE status IN ('active', 'planning') ORDER BY name"
            ).fetchall()

        existing_channels = {ch.name: ch for ch in guild.text_channels}
        created = []
        linked = []
        already = []

        # Find or create a "SoY Projects" category
        soy_category = None
        for cat in guild.categories:
            if cat.name.lower() in ("soy projects", "soy", "projects"):
                soy_category = cat
                break
        if not soy_category:
            soy_category = await guild.create_category("SoY Projects")
            created.append("SoY Projects (category)")

        # Create #nudges if missing
        if NUDGES_CHANNEL_NAME not in existing_channels:
            nudges_ch = await guild.create_text_channel(NUDGES_CHANNEL_NAME, category=soy_category,
                topic="Proactive alerts, reminders, and nudges from SoY")
            created.append(f"#{NUDGES_CHANNEL_NAME}")
            existing_channels[NUDGES_CHANNEL_NAME] = nudges_ch

        # Create a channel for each project
        for p in projects:
            # Generate channel name: lowercase, hyphens, no special chars
            channel_name = re.sub(r'[^a-z0-9-]', '-', p["name"].lower()).strip('-')
            channel_name = re.sub(r'-+', '-', channel_name)

            if channel_name in existing_channels:
                ch = existing_channels[channel_name]
            else:
                workspace = p["workspace_path"] or ""
                topic = f"SoY Project: {p['name']}"
                if workspace:
                    topic += f" | {workspace}"
                ch = await guild.create_text_channel(channel_name, category=soy_category, topic=topic)
                created.append(f"#{channel_name}")

            # Auto-link channel to project
            channel_id = str(ch.id)
            with self._db() as conn:
                existing_link = conn.execute(
                    "SELECT project_id FROM discord_channel_projects WHERE channel_id = ?",
                    (channel_id,),
                ).fetchone()
                if existing_link:
                    already.append(f"#{channel_name} \u2192 {p['name']}")
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO discord_channel_projects (channel_id, project_id, project_name) "
                        "VALUES (?, ?, ?)",
                        (channel_id, p["id"], p["name"]),
                    )
                    conn.commit()
                    self._register_v2_channel(channel_id)
                    linked.append(f"#{channel_name} \u2192 {p['name']}")

        # Build report
        lines = ["**Channel Sync Complete**\n"]
        if created:
            lines.append("**Created:**")
            for c in created:
                lines.append(f"- {c}")
        if linked:
            lines.append("**Linked:**")
            for l in linked:
                lines.append(f"- {l}")
        if already:
            lines.append("**Already linked:**")
            for a in already:
                lines.append(f"- {a}")
        if not created and not linked:
            lines.append("Everything already in sync.")

        return "\n".join(lines)

    def _link_channel(self, channel_id, project_arg):
        """Link a Discord channel to a SoY project."""
        with self._db() as conn:
            project_id, project_name = self._fuzzy_match_project(conn, project_arg)
            if not project_id and project_arg.isdigit():
                row = conn.execute(
                    "SELECT id, name FROM projects WHERE id = ?", (int(project_arg),)
                ).fetchone()
                if row:
                    project_id, project_name = row["id"], row["name"]

            if not project_id:
                projects = conn.execute(
                    "SELECT id, name FROM projects WHERE status IN ('active', 'planning') ORDER BY name"
                ).fetchall()
                listing = "\n".join(f"- `{p['id']}` \u2014 {p['name']}" for p in projects)
                return f"No matching project: {project_arg}\n\n**Projects:**\n{listing}"

            conn.execute(
                "INSERT OR REPLACE INTO discord_channel_projects (channel_id, project_id, project_name) "
                "VALUES (?, ?, ?)",
                (channel_id, project_id, project_name),
            )
            conn.commit()
        return f"\u2705 Channel linked to **{project_name}**. Messages here will have project context."

    def _unlink_channel(self, channel_id):
        """Unlink a Discord channel from its project."""
        with self._db() as conn:
            row = conn.execute(
                "SELECT project_name FROM discord_channel_projects WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if not row:
                return "This channel isn't linked to any project."
            conn.execute("DELETE FROM discord_channel_projects WHERE channel_id = ?", (channel_id,))
            conn.commit()
        return f"Unlinked from **{row['project_name']}**."

    def _register_v2_channel(self, channel_id):
        """Register a channel in v2 bot's access.json so it works when v2 is active."""
        access_path = os.path.expanduser("~/.claude/channels/discord/access.json")
        try:
            with open(access_path) as f:
                data = json.load(f)
            if channel_id not in data.get("groups", {}):
                data.setdefault("groups", {})[channel_id] = {
                    "requireMention": False,
                    "allowFrom": [OWNER_ID],
                }
                with open(access_path, "w") as f:
                    json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not register channel in v2 access.json: {e}")

    # ── Context Building ──

    _system_prompt_cache = None
    _system_prompt_cache_time = 0
    _SYSTEM_PROMPT_TTL = 300  # 5 minutes

    def _build_system_prompt(self, channel_id=None):
        """Build system prompt with live SoY data. Cached for 5 minutes."""
        now = time.time()
        if (self._system_prompt_cache is not None
                and now - self._system_prompt_cache_time < self._SYSTEM_PROMPT_TTL):
            base, project_names = self._system_prompt_cache
            # Only recompute the channel-specific section
            channel_section = self._build_channel_section(channel_id, project_names) if channel_id else ""
            handoff_section = self._build_handoff_section()
            return base + channel_section + handoff_section

        prompt, project_names = self._build_system_prompt_uncached(channel_id)
        self._system_prompt_cache = (self._extract_base_prompt(prompt), project_names)
        self._system_prompt_cache_time = now
        return prompt

    def _build_channel_section(self, channel_id, project_names):
        if not channel_id:
            return ""
        with self._db() as conn:
            cp_row = conn.execute(
                "SELECT project_name FROM discord_channel_projects WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
        if not cp_row:
            return ""
        channel_project = cp_row["project_name"]
        workspace_hint = ""
        ws = self._get_workspace_for_channel(channel_id)
        if ws:
            workspace_hint = (
                f"\nYou are running from this project's workspace directory ({ws}). "
                "You have full access to the codebase — you can read files, understand the code structure, "
                "and answer questions about the implementation. The project's CLAUDE.md is loaded automatically."
            )
        return f"""
## Channel Context
This message is in a channel linked to **{channel_project}**. Default to this project for tasks, notes, and dev work unless the user specifies otherwise.{workspace_hint}
"""

    def _build_handoff_section(self):
        try:
            with self._db() as conn:
                handoff_row = conn.execute(
                    "SELECT summary, source, branch, status, created_at FROM session_handoffs "
                    "WHERE status IN ('active', 'picked_up') "
                    "AND created_at > datetime('now', '-24 hours') "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            if not handoff_row:
                return ""
            picked_up = handoff_row["status"] == "picked_up"
            status_note = (
                "The user already resumed this session on their machine but may reference it here too."
                if picked_up else
                "This session hasn't been picked up yet — the user may be continuing from their phone."
            )
            return f"""
## Recent Session Context ({handoff_row['created_at']}, from {handoff_row['source']})
{status_note}
{handoff_row['summary']}

When the user engages with this context, mark it consumed:
[HANDOFF_PICKED_UP]
"""
        except Exception:
            return ""

    def _extract_base_prompt(self, full_prompt):
        """Extract the base prompt without channel/handoff sections for caching."""
        # Cut before channel/handoff sections if present
        for marker in ["## Channel Context", "## Recent Session Context"]:
            idx = full_prompt.find(marker)
            if idx > 0:
                return full_prompt[:idx]
        return full_prompt

    def _build_system_prompt_uncached(self, channel_id=None):
        """Build system prompt with live SoY data (no cache)."""
        with self._db() as conn:
            owner_row = conn.execute(
                "SELECT value FROM user_profile WHERE category = 'identity' AND key = 'name'"
            ).fetchone()
            owner_name = owner_row["value"] if owner_row else "there"

            projects = conn.execute(
                "SELECT p.name, p.status, c.name as client, "
                "(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status != 'done') as open_tasks, "
                "(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'done') as done_tasks "
                "FROM projects p LEFT JOIN contacts c ON c.id = p.client_id "
                "WHERE p.status IN ('active', 'planning') ORDER BY p.name"
            ).fetchall()

            tasks = conn.execute(
                "SELECT t.title, t.priority, t.status, t.due_date, p.name as project_name "
                "FROM tasks t LEFT JOIN projects p ON p.id = t.project_id "
                "WHERE t.status != 'done' "
                "ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
                f"WHEN 'medium' THEN 2 ELSE 3 END LIMIT {TASK_DISPLAY_LIMIT}"
            ).fetchall()

            contacts = conn.execute(
                "SELECT name, company, role FROM contacts WHERE status = 'active' ORDER BY name LIMIT 20"
            ).fetchall()

            # Channel-project context
            channel_project = None
            if channel_id:
                cp_row = conn.execute(
                    "SELECT project_name FROM discord_channel_projects WHERE channel_id = ?",
                    (channel_id,),
                ).fetchone()
                if cp_row:
                    channel_project = cp_row["project_name"]

            # Recent handoff
            handoff_row = None
            try:
                handoff_row = conn.execute(
                    "SELECT summary, source, branch, status, created_at FROM session_handoffs "
                    "WHERE status IN ('active', 'picked_up') "
                    "AND created_at > datetime('now', '-24 hours') "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            except Exception:
                pass

        if projects:
            projects_ctx = "\n".join(
                f"- {p['name']} ({p['status']}) \u2014 {p['open_tasks']} open, {p['done_tasks']} done"
                + (f", client: {p['client']}" if p["client"] else "")
                for p in projects
            )
        else:
            projects_ctx = "No active projects."

        if tasks:
            tasks_ctx = "\n".join(
                f"- [{t['priority'] or 'medium'}] {t['title']}"
                + (f" ({t['project_name']})" if t["project_name"] else "")
                + (f" \u2014 due {t['due_date']}" if t["due_date"] else "")
                for t in tasks
            )
        else:
            tasks_ctx = "No open tasks."

        if contacts:
            contacts_ctx = "\n".join(
                f"- {c['name']}"
                + (f" \u2014 {c['company']}" if c["company"] else "")
                + (f" ({c['role']})" if c["role"] else "")
                for c in contacts
            )
        else:
            contacts_ctx = "No contacts."

        project_names = [p["name"] for p in projects]

        channel_section = ""
        if channel_project:
            # Check if workspace exists for codebase context hint
            workspace_hint = ""
            if channel_id:
                ws = self._get_workspace_for_channel(channel_id)
                if ws:
                    workspace_hint = (
                        f"\nYou are running from this project's workspace directory ({ws}). "
                        "You have full access to the codebase — you can read files, understand the code structure, "
                        "and answer questions about the implementation. The project's CLAUDE.md is loaded automatically."
                    )
            channel_section = f"""
## Channel Context
This message is in a channel linked to **{channel_project}**. Default to this project for tasks, notes, and dev work unless the user specifies otherwise.{workspace_hint}
"""

        handoff_section = ""
        if handoff_row:
            picked_up = handoff_row["status"] == "picked_up"
            status_note = (
                "The user already resumed this session on their machine but may reference it here too."
                if picked_up else
                "This session hasn't been picked up yet \u2014 the user may be continuing from their phone."
            )
            handoff_section = f"""
## Recent Session Context ({handoff_row['created_at']}, from {handoff_row['source']})
{status_note}
{handoff_row['summary']}

When the user engages with this context, mark it consumed:
[HANDOFF_PICKED_UP]
"""

        channel_section = self._build_channel_section(channel_id, project_names) if channel_id else ""
        handoff_section = self._build_handoff_section()

        prompt = f"""You are the Discord interface for Software of You \u2014 {owner_name}'s personal data platform.

You're running locally on {owner_name}'s machine, with direct access to all SoY data.

## {owner_name}'s Projects
{projects_ctx}

## Open Tasks
{tasks_ctx}

## Key Contacts
{contacts_ctx}

## Known Project Names (for matching)
{json.dumps(project_names)}
{channel_section}{handoff_section}
## Behavior
- Keep responses concise but you have more room than Telegram.
- When the user mentions a task or TODO, capture it:
  [TASK: title | project_name | priority]
- When the user shares a note/idea, capture it:
  [NOTE: title | content | project_name]
- project_name must match one of the known project names above (or be empty if unclear).
- priority must be one of: low, medium, high, urgent. Default to medium.
- After markers, write your conversational response. Markers are stripped before sending.
- Never fabricate data.
- Use **bold** for emphasis, bullet points for lists. Discord markdown is supported.
- **Dev commands**: Use /dev for code changes. Use /new for new projects."""
        return prompt, project_names

    # ── Claude Integration ──

    def _get_workspace_for_channel(self, channel_id):
        """Get workspace path for a linked channel's project."""
        if not channel_id:
            return None
        with self._db() as conn:
            row = conn.execute(
                "SELECT p.workspace_path FROM discord_channel_projects dcp "
                "JOIN projects p ON p.id = dcp.project_id "
                "WHERE dcp.channel_id = ?",
                (channel_id,),
            ).fetchone()
        if row and row["workspace_path"]:
            workspace = os.path.expanduser(row["workspace_path"])
            if os.path.isdir(workspace):
                return workspace
        return None

    def _call_claude(self, user_text, session_id, channel_id=None, model=None):
        """Call claude -p with conversation context. Runs in thread pool.
        When in a linked project channel, runs from the project workspace
        so Claude has full codebase context via CLAUDE.md and file access."""
        if model is None:
            model = self._get_channel_model(channel_id)
        system_prompt = self._build_system_prompt(channel_id)
        # Get history — the current user message was already saved before this call,
        # so it's included in the history. We don't append it again.
        history = self._get_history(session_id, limit=30)

        prompt_parts = []
        if history:
            prompt_parts.append("## Conversation history (most recent messages):")
            for msg in history:
                role_label = "User" if msg["role"] == "user" else "You (Assistant)"
                prompt_parts.append(f"{role_label}: {msg['content']}")
        else:
            prompt_parts.append(f"User: {user_text}")

        prompt = "\n".join(prompt_parts)

        # If channel is linked to a project, run from that workspace
        # so Claude picks up the project's CLAUDE.md and can read code
        workspace = self._get_workspace_for_channel(channel_id)
        cwd = workspace or PLUGIN_ROOT

        try:
            clean_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            result = subprocess.run(
                [
                    "claude", "-p",
                    "--system-prompt", system_prompt,
                    "--model", model,
                    "--no-session-persistence",
                    prompt,
                ],
                capture_output=True,
                text=True,
                timeout=CLAUDE_TIMEOUT,
                cwd=cwd,
                env=clean_env,
            )
            self.last_claude_call = time.time()

            if result.returncode != 0:
                stderr = result.stderr.strip()
                if stderr:
                    self._log_error(f"claude -p error: {stderr}", user_msg=user_text)
                if result.stdout.strip():
                    return result.stdout.strip()
                if stderr:
                    return f"Error: {stderr[:200]}"
                return "Something went wrong."

            return result.stdout.strip() or "I processed that but had nothing to say."

        except subprocess.TimeoutExpired:
            self._log_error("claude -p timed out", user_msg=user_text)
            return "That took too long \u2014 try a simpler question or break it up."
        except FileNotFoundError:
            self._log_error("claude CLI not found in PATH", user_msg=user_text)
            return "Error: `claude` CLI not found."
        except Exception as e:
            self._log_error(str(e), user_msg=user_text)
            return "Something went wrong processing that."

    async def _fetch_thread_history(self, thread, limit=50):
        """Fetch full conversation history from a Discord thread.
        Returns a list of {"role": "user"|"assistant", "content": str} in chronological order.
        The thread acts as a complete session — every message is context."""
        history = []
        messages = []

        # Fetch messages from Discord API (newest first)
        async for msg in thread.history(limit=limit, oldest_first=True):
            content = msg.content.strip()

            # Build attachment descriptions
            att_descs = []
            for att in msg.attachments:
                ext = os.path.splitext(att.filename)[1].lower()
                if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                    att_descs.append(f"[Image: {att.filename}]")
                elif ext == ".pdf":
                    att_descs.append(f"[PDF: {att.filename}]")
                else:
                    att_descs.append(f"[File: {att.filename}]")

            # Combine content + attachments
            full_content = content
            if att_descs:
                att_text = " ".join(att_descs)
                full_content = f"{att_text}\n{content}" if content else att_text

            if not full_content:
                # Skip empty messages (e.g. embeds-only from bot)
                # But include embed descriptions if present
                if msg.embeds:
                    embed_descs = []
                    for embed in msg.embeds:
                        parts = []
                        if embed.title:
                            parts.append(embed.title)
                        if embed.description:
                            parts.append(embed.description)
                        for field in embed.fields:
                            parts.append(f"{field.name}: {field.value}")
                        if parts:
                            embed_descs.append(" | ".join(parts))
                    if embed_descs:
                        full_content = "[Bot embed: " + "; ".join(embed_descs) + "]"

            if not full_content:
                continue

            role = "assistant" if msg.author == self.user else "user"
            history.append({"role": role, "content": full_content})

        return history

    def _call_claude_with_history(self, history, channel_id=None, model=None):
        """Call claude -p with pre-built conversation history (for thread mode).
        The full thread history is passed as context — no DB session needed."""
        if model is None:
            model = self._get_channel_model(channel_id)
        system_prompt = self._build_system_prompt(channel_id)

        prompt_parts = []
        if history:
            prompt_parts.append("## Full conversation (this is a Discord thread — you have complete context):")
            for msg in history:
                role_label = "User" if msg["role"] == "user" else "You (Assistant)"
                prompt_parts.append(f"{role_label}: {msg['content']}")

        if not prompt_parts:
            return "I don't see any messages in this thread."

        prompt = "\n".join(prompt_parts)

        workspace = self._get_workspace_for_channel(channel_id)
        cwd = workspace or PLUGIN_ROOT

        try:
            clean_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            result = subprocess.run(
                [
                    "claude", "-p",
                    "--system-prompt", system_prompt,
                    "--model", model,
                    "--no-session-persistence",
                    prompt,
                ],
                capture_output=True,
                text=True,
                timeout=CLAUDE_TIMEOUT,
                cwd=cwd,
                env=clean_env,
            )
            self.last_claude_call = time.time()

            if result.returncode != 0:
                stderr = result.stderr.strip()
                if stderr:
                    self._log_error(f"claude -p error: {stderr}")
                if result.stdout.strip():
                    return result.stdout.strip()
                if stderr:
                    return f"Error: {stderr[:200]}"
                return "Something went wrong."

            return result.stdout.strip() or "I processed that but had nothing to say."

        except subprocess.TimeoutExpired:
            self._log_error("claude -p timed out (thread mode)")
            return "That took too long \u2014 try a simpler question or break it up."
        except FileNotFoundError:
            self._log_error("claude CLI not found in PATH")
            return "Error: `claude` CLI not found."
        except Exception as e:
            self._log_error(str(e))
            return "Something went wrong processing that."

    # ── Task/Note Capture ──

    def _parse_markers(self, text):
        """Parse [TASK:] and [NOTE:] markers from Claude's response."""
        tasks = []
        notes = []

        for match in re.finditer(r'\[TASK:\s*([^]]+)\]', text):
            parts = [p.strip() for p in match.group(1).split("|")]
            title = parts[0] if parts else None
            project = parts[1] if len(parts) > 1 and parts[1] else None
            priority = parts[2] if len(parts) > 2 and parts[2] else "medium"
            if title:
                tasks.append({"title": title, "project": project, "priority": priority})

        for match in re.finditer(r'\[NOTE:\s*([^]]+)\]', text):
            parts = [p.strip() for p in match.group(1).split("|")]
            title = parts[0] if parts else None
            content = parts[1] if len(parts) > 1 and parts[1] else None
            project = parts[2] if len(parts) > 2 and parts[2] else None
            if title:
                notes.append({"title": title, "content": content, "project": project})

        handoff_picked_up = '[HANDOFF_PICKED_UP]' in text

        cleaned = re.sub(r'\[TASK:\s*[^]]+\]\s*\n?', '', text)
        cleaned = re.sub(r'\[NOTE:\s*[^]]+\]\s*\n?', '', cleaned)
        cleaned = re.sub(r'\[EXPENSE:\s*[^]]+\]\s*\n?', '', cleaned)
        cleaned = re.sub(r'\[HANDOFF_PICKED_UP\]\s*\n?', '', cleaned)
        cleaned = cleaned.strip()

        if handoff_picked_up:
            try:
                with self._db() as conn:
                    conn.execute(
                        "UPDATE session_handoffs SET status = 'consumed', "
                        "picked_up_at = COALESCE(picked_up_at, datetime('now')), "
                        "picked_up_by = COALESCE(picked_up_by || '+discord', 'discord') "
                        "WHERE status IN ('active', 'picked_up')"
                    )
            except Exception:
                pass

        return tasks, notes, cleaned

    def _fuzzy_match_project(self, conn, project_name):
        """Find a project by fuzzy name match."""
        if not project_name:
            return None, None

        row = conn.execute(
            "SELECT id, name FROM projects WHERE LOWER(name) = LOWER(?)", (project_name,)
        ).fetchone()
        if row:
            return row["id"], row["name"]

        row = conn.execute(
            "SELECT id, name FROM projects WHERE LOWER(name) LIKE LOWER(?)",
            (f"%{project_name}%",),
        ).fetchone()
        if row:
            return row["id"], row["name"]

        if "-" in project_name:
            deslug = project_name.replace("-", " ")
            row = conn.execute(
                "SELECT id, name FROM projects WHERE LOWER(name) = LOWER(?)", (deslug,)
            ).fetchone()
            if row:
                return row["id"], row["name"]
            row = conn.execute(
                "SELECT id, name FROM projects WHERE LOWER(name) LIKE LOWER(?)",
                (f"%{deslug}%",),
            ).fetchone()
            if row:
                return row["id"], row["name"]

        row = conn.execute(
            "SELECT id, name FROM projects WHERE workspace_path LIKE ?",
            (f"%/{project_name}",),
        ).fetchone()
        if row:
            return row["id"], row["name"]

        return None, None

    def _capture_items(self, tasks, notes):
        """Insert captured tasks/notes into local SoY database."""
        with self._db() as conn:
            captured = []

            for task in tasks:
                project_id, project_name = self._fuzzy_match_project(conn, task.get("project"))
                if not project_id and task.get("project"):
                    project_id, project_name = self._get_or_create_inbox(conn)

                priority = task.get("priority", "medium")
                if priority not in ("low", "medium", "high", "urgent"):
                    priority = "medium"

                conn.execute(
                    "INSERT INTO tasks (project_id, title, status, priority, created_at, updated_at) "
                    "VALUES (?, ?, 'todo', ?, datetime('now'), datetime('now'))",
                    (project_id, task["title"], priority),
                )
                local_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
                conn.execute(
                    "INSERT INTO activity_log (entity_type, entity_id, action, details, created_at) "
                    "VALUES ('task', ?, 'created', ?, datetime('now'))",
                    (local_id, json.dumps({"source": "discord", "title": task["title"]})),
                )
                proj_label = f" ({project_name})" if project_name else ""
                captured.append(f"Task: {task['title']}{proj_label}")

            for note in notes:
                project_id, project_name = self._fuzzy_match_project(conn, note.get("project"))
                conn.execute(
                    "INSERT INTO standalone_notes (title, content, linked_projects, created_at, updated_at) "
                    "VALUES (?, ?, ?, datetime('now'), datetime('now'))",
                    (note["title"], note.get("content"), project_name),
                )
                local_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
                conn.execute(
                    "INSERT INTO activity_log (entity_type, entity_id, action, details, created_at) "
                    "VALUES ('standalone_note', ?, 'created', ?, datetime('now'))",
                    (local_id, json.dumps({"source": "discord", "title": note["title"]})),
                )
                captured.append(f"Note: {note['title']}")

            conn.commit()
        return captured

    def _get_or_create_inbox(self, conn):
        """Get or create inbox project for unmatched items."""
        row = conn.execute(
            "SELECT id, name FROM projects WHERE name = ?", (INBOX_PROJECT_NAME,)
        ).fetchone()
        if row:
            return row["id"], row["name"]
        conn.execute(
            "INSERT INTO projects (name, status, description, created_at, updated_at) "
            "VALUES (?, 'active', 'Items captured via Discord bot', datetime('now'), datetime('now'))",
            (INBOX_PROJECT_NAME,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, name FROM projects WHERE name = ?", (INBOX_PROJECT_NAME,)
        ).fetchone()
        return row["id"], row["name"]

    # ── Embed Builders ──

    def _build_status_embeds(self):
        """Build rich embeds for /status."""
        with self._db() as conn:
            projects = conn.execute(
                "SELECT p.id, p.name, p.status, c.name as client, "
                "(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status != 'done') as open_tasks, "
                "(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'done') as done_tasks, "
                "(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status != 'done' "
                "AND t.due_date < date('now')) as overdue "
                "FROM projects p LEFT JOIN contacts c ON c.id = p.client_id "
                "WHERE p.status IN ('active', 'planning') ORDER BY p.name"
            ).fetchall()

        if not projects:
            embed = discord.Embed(title="SoY Status", description="No active projects.\nCreate one with `/new`.", color=PROJECT_COLOR)
            return [embed]

        embeds = []
        for p in projects:
            total = p["open_tasks"] + p["done_tasks"]
            pct = int(p["done_tasks"] / total * 100) if total else 0
            bar_filled = pct // 10
            progress_bar = "\u2588" * bar_filled + "\u2591" * (10 - bar_filled)

            color = 0xED4245 if p["overdue"] else PROJECT_COLOR
            embed = discord.Embed(title=f"{p['name']}", color=color)
            embed.add_field(name="Status", value=p["status"].title(), inline=True)
            embed.add_field(name="Tasks", value=f"{p['open_tasks']} open / {p['done_tasks']} done", inline=True)
            if p["client"]:
                embed.add_field(name="Client", value=p["client"], inline=True)
            if p["overdue"]:
                embed.add_field(name="Overdue", value=f"\U0001f534 {p['overdue']} tasks", inline=True)
            embed.add_field(name="Progress", value=f"`{progress_bar}` {pct}%", inline=False)
            embed.set_footer(text=f"ID: {p['id']}")
            embeds.append(embed)

        return embeds[:10]  # Discord max 10 embeds per message

    def _build_tasks_embed(self, filter_text=""):
        """Build rich embed for /tasks."""
        with self._db() as conn:
            if filter_text:
                tasks = conn.execute(
                    "SELECT t.title, t.priority, t.status, t.due_date, p.name as project_name "
                    "FROM tasks t LEFT JOIN projects p ON p.id = t.project_id "
                    "WHERE t.status != 'done' AND LOWER(p.name) LIKE LOWER(?) "
                    "ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
                    f"WHEN 'medium' THEN 2 ELSE 3 END LIMIT {TASK_DISPLAY_LIMIT}",
                    (f"%{filter_text}%",),
                ).fetchall()
            else:
                tasks = conn.execute(
                    "SELECT t.title, t.priority, t.status, t.due_date, p.name as project_name "
                    "FROM tasks t LEFT JOIN projects p ON p.id = t.project_id "
                    "WHERE t.status != 'done' "
                    "ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
                    f"WHEN 'medium' THEN 2 ELSE 3 END LIMIT {TASK_DISPLAY_LIMIT}"
                ).fetchall()

        title = f"Tasks \u2014 {filter_text}" if filter_text else "Open Tasks"
        if not tasks:
            return discord.Embed(title=title, description="No open tasks.", color=0x2ECC71)

        lines = []
        for t in tasks:
            pri_icon = PRIORITY_ICONS.get(t["priority"] or "medium", "\U0001f7e1")
            proj = f" *{t['project_name']}*" if t["project_name"] else ""
            due = f" \u2014 due {t['due_date']}" if t["due_date"] else ""
            lines.append(f"{pri_icon} {t['title']}{proj}{due}")

        # Pick color from highest priority task
        top_pri = tasks[0]["priority"] or "medium"
        color = PRIORITY_COLORS.get(top_pri, 0xF1C40F)

        embed = discord.Embed(title=title, description="\n".join(lines), color=color)
        embed.set_footer(text=f"{len(tasks)} tasks")
        return embed

    def _get_notes_text(self, search=""):
        """Build notes text."""
        with self._db() as conn:
            if search:
                notes = conn.execute(
                    "SELECT title, SUBSTR(content, 1, 100) as content, linked_projects, created_at "
                    "FROM standalone_notes WHERE LOWER(title) LIKE LOWER(?) OR LOWER(content) LIKE LOWER(?) "
                    "ORDER BY created_at DESC LIMIT 20",
                    (f"%{search}%", f"%{search}%"),
                ).fetchall()
                header = f"**Notes \u2014 \"{search}\"**\n\n"
            else:
                notes = conn.execute(
                    "SELECT title, SUBSTR(content, 1, 100) as content, linked_projects, created_at "
                    "FROM standalone_notes ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
                header = "**Recent Notes**\n\n"

        if not notes:
            return "No notes found."

        text = header
        for n in notes:
            proj = f" *{n['linked_projects']}*" if n["linked_projects"] else ""
            preview = f"\n  {n['content']}..." if n["content"] else ""
            text += f"- **{n['title']}**{proj}{preview}\n\n"
        return text.strip()

    def _build_sessions_embeds(self):
        """Build rich embeds for /sessions."""
        with self._db() as conn:
            sessions = conn.execute(
                "SELECT session_id, project_name, status, instruction, "
                "duration_seconds, started_at, deploy_status, review_status "
                "FROM telegram_dev_sessions ORDER BY started_at DESC LIMIT 10"
            ).fetchall()

        if not sessions:
            return [discord.Embed(title="Dev Sessions", description="No dev sessions yet.", color=PROJECT_COLOR)]

        embeds = []
        for s in sessions:
            icon = STATUS_ICONS.get(s["status"], "\u2753")
            color = STATUS_COLORS.get(s["status"], 0x95A5A6)
            duration = self._format_duration(s["duration_seconds"]) if s["duration_seconds"] else ""

            embed = discord.Embed(
                title=f"{icon} {s['session_id']} \u2014 {s['project_name']}",
                description=s["instruction"][:200],
                color=color,
            )
            embed.add_field(name="Status", value=s["status"].title(), inline=True)
            if duration:
                embed.add_field(name="Duration", value=duration, inline=True)
            if s["deploy_status"]:
                deploy_icon = DEPLOY_ICONS.get(s["deploy_status"], "")
                embed.add_field(name="Deploy", value=f"{deploy_icon} {s['deploy_status']}", inline=True)
            if s["review_status"]:
                review_icon = REVIEW_ICONS.get(s["review_status"], "")
                embed.add_field(name="Review", value=f"{review_icon} {s['review_status']}", inline=True)
            if s["started_at"]:
                embed.set_footer(text=s["started_at"])
            embeds.append(embed)

        return embeds[:10]

    def _build_session_embed(self, session_arg):
        """Build rich embed for /session detail."""
        with self._db() as conn:
            row, err = self._find_session_by_prefix(conn, session_arg)

        if not row:
            return err or f"No session found matching `{session_arg}`."

        color = STATUS_COLORS.get(row["status"], 0x95A5A6)
        icon = STATUS_ICONS.get(row["status"], "\u2753")

        embed = discord.Embed(
            title=f"{icon} Session {row['session_id']}",
            color=color,
        )
        embed.add_field(name="Project", value=row["project_name"], inline=True)
        embed.add_field(name="Status", value=row["status"].title(), inline=True)
        embed.add_field(name="Model", value=row["model"], inline=True)
        embed.add_field(name="Instruction", value=row["instruction"][:1024], inline=False)

        if row["duration_seconds"]:
            embed.add_field(name="Duration", value=self._format_duration(row["duration_seconds"]), inline=True)
        if row["branch_name"]:
            branch_text = f"`{row['branch_name']}`"
            github_url = self._get_github_url(row["workspace_path"])
            if github_url:
                branch_text += f"\n[Review code]({github_url}/tree/{row['branch_name']})"
            embed.add_field(name="Branch", value=branch_text, inline=True)
        if row["review_status"]:
            review_label = {"pending": "\u23f3 Pending", "approved": "\u2705 Approved", "rejected": "\U0001f5d1 Rejected"}
            embed.add_field(name="Review", value=review_label.get(row["review_status"], row["review_status"]), inline=True)
        if row["preview_url"]:
            embed.add_field(name="Preview", value=row["preview_url"], inline=False)
        elif row["deploy_status"]:
            deploy_label = {"deploying": "\U0001f680 Deploying...", "deployed": "\U0001f310 Deployed", "deploy_failed": "\u26a0\ufe0f Failed"}
            embed.add_field(name="Deploy", value=deploy_label.get(row["deploy_status"], row["deploy_status"]), inline=True)
        if row["git_diff_stat"]:
            stat_text = row["git_diff_stat"][:1024]
            embed.add_field(name="Changes", value=f"```\n{stat_text}\n```", inline=False)
        if row["output_summary"]:
            summary = row["output_summary"][:1024]
            embed.add_field(name="Output", value=summary, inline=False)
        elif row["status"] == "running":
            embed.add_field(name="Output", value="*Still running...*", inline=False)

        return embed

    def _build_debug_embed(self, channel_id=None):
        """Build rich embed for /debug."""
        uptime = ""
        if self.start_time_ts:
            elapsed = int(time.time() - self.start_time_ts)
            hours, remainder = divmod(elapsed, 3600)
            minutes, secs = divmod(remainder, 60)
            uptime = f"{hours}h {minutes}m {secs}s"

        last_claude = "never"
        if self.last_claude_call:
            ago = int(time.time() - self.last_claude_call)
            last_claude = f"{ago}s ago"

        channel_model = self._get_channel_model(channel_id) if channel_id else DEFAULT_MODEL
        model_display = channel_model if channel_model == DEFAULT_MODEL else f"{channel_model} ⭐"

        embed = discord.Embed(title="SoY Discord Debug", color=PROJECT_COLOR)
        embed.add_field(name="Mode", value="local (claude -p)", inline=True)
        embed.add_field(name="Model", value=model_display, inline=True)
        embed.add_field(name="Python", value=sys.version.split()[0], inline=True)
        embed.add_field(name="Uptime", value=uptime or "unknown", inline=True)
        embed.add_field(name="Messages", value=str(self.message_count), inline=True)
        embed.add_field(name="Last Claude Call", value=last_claude, inline=True)
        embed.add_field(name="Active Sessions", value=str(len(self.active_dev_sessions)), inline=True)
        embed.add_field(name="Owner ID", value=f"`{OWNER_ID}`", inline=True)
        embed.add_field(name="Database", value=f"`{DB_PATH}`", inline=False)
        return embed

    def _build_errors_embed(self):
        """Build rich embed for /errors."""
        with self._db() as conn:
            errors = conn.execute(
                "SELECT error_message, user_message_preview, created_at "
                "FROM discord_bot_errors ORDER BY created_at DESC LIMIT 5"
            ).fetchall()

        if not errors:
            return discord.Embed(title="Recent Errors", description="No errors recorded.", color=0x2ECC71)

        embed = discord.Embed(title=f"Recent Errors ({len(errors)})", color=0xED4245)
        for i, e in enumerate(errors, 1):
            value = f"`{e['error_message'][:200]}`"
            if e["user_message_preview"]:
                value += f"\n*{e['user_message_preview']}*"
            embed.add_field(name=e["created_at"], value=value, inline=False)
        return embed

    # ── Dev Sessions ──

    def _resolve_project_for_dev(self, text):
        """Resolve a project name from /dev args."""
        with self._db() as conn:
            projects = conn.execute(
                "SELECT id, name, workspace_path FROM projects WHERE status IN ('active', 'planning')"
            ).fetchall()

        if not projects:
            raise ValueError("No active projects found.")

        words = text.split()

        # Numeric ID
        if words[0].isdigit():
            pid = int(words[0])
            matched = next((p for p in projects if p["id"] == pid), None)
            if matched:
                instruction = " ".join(words[1:]).strip()
                if not instruction:
                    raise ValueError("No instruction provided.")
                return self._validate_project_workspace(matched, instruction)
            raise ValueError(f"No active project with ID {pid}.")

        # Progressive prefix matching
        matched = None
        instruction_start = 0

        for i in range(1, len(words) + 1):
            prefix = " ".join(words[:i]).lower()
            deslugged = prefix.replace("-", " ")
            candidates = self._match_projects(projects, prefix, deslugged)
            if len(candidates) == 1:
                matched = candidates[0]
                instruction_start = i
            elif len(candidates) == 0:
                break

        if matched:
            instruction = " ".join(words[instruction_start:]).strip()
            if not instruction:
                raise ValueError("No instruction provided.")
            return self._validate_project_workspace(matched, instruction)

        # Fallback: first word
        first = words[0].lower()
        deslugged = first.replace("-", " ")
        candidates = self._match_projects(projects, first, deslugged)
        if len(candidates) == 1:
            instruction = " ".join(words[1:]).strip()
            if not instruction:
                raise ValueError("No instruction provided.")
            return self._validate_project_workspace(candidates[0], instruction)

        if len(candidates) > 1:
            listing = "\n".join(f"- `{p['id']}` \u2014 {p['name']}" for p in candidates)
            raise ValueError(f"Multiple matches:\n{listing}\n\nUse the project ID.")

        listing = "\n".join(f"- `{p['id']}` \u2014 {p['name']}" for p in projects)
        raise ValueError(f"No matching project.\n\n{listing}")

    @staticmethod
    def _match_projects(projects, prefix, deslugged=None):
        """Match projects by name prefix, substring, or workspace directory name."""
        if deslugged is None:
            deslugged = prefix
        seen = set()
        result = []
        for p in projects:
            name = p["name"].lower()
            dirname = os.path.basename(p["workspace_path"] or "").lower()
            if (name.startswith(prefix) or prefix in name
                    or name.startswith(deslugged) or deslugged in name
                    or dirname == prefix or dirname == prefix.replace(" ", "-")):
                if p["id"] not in seen:
                    seen.add(p["id"])
                    result.append(p)
        return result

    @staticmethod
    def _validate_project_workspace(matched, instruction):
        """Validate workspace exists."""
        workspace = matched["workspace_path"]
        if not workspace:
            raise ValueError(f"No workspace path set for {matched['name']}.")
        workspace = os.path.expanduser(workspace)
        if not os.path.isdir(workspace):
            raise ValueError(f"Workspace not found: {workspace}")
        return matched["id"], matched["name"], workspace, instruction

    async def _handle_dev(self, channel, args, model=DEFAULT_MODEL):
        """Handle /dev command."""
        if model not in ALLOWED_MODELS:
            await channel.send(f"\u274c Unknown model: {model}. Use: {', '.join(sorted(ALLOWED_MODELS))}")
            return

        if not args:
            await channel.send(
                "**Usage:** `/dev project:<name> instruction:<what to do>`\n\n"
                f"Options: model (default: {DEFAULT_MODEL})")
            return

        active_count = len(self.active_dev_sessions)
        if active_count >= DEV_SESSION_MAX_ACTIVE:
            await channel.send(
                f"\u274c Max {DEV_SESSION_MAX_ACTIVE} concurrent sessions. "
                f"Wait for one to finish or `/kill` one.")
            return

        try:
            project_id, project_name, workspace, instruction = self._resolve_project_for_dev(args)
        except ValueError as e:
            await channel.send(f"\u274c {e}")
            return

        if not self._ensure_git_remote(workspace, project_name):
            await channel.send(
                f"\u274c No git remote for **{project_name}**. "
                f"Add one manually or run `gh repo create` in the workspace.")
            return

        # Check for dirty workspace
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "-uno"],
            capture_output=True, text=True, cwd=workspace, timeout=5,
        ).stdout.strip()
        if dirty:
            channel_id = str(channel.id)
            await channel.send(
                f"\u26a0\ufe0f **{project_name}** has uncommitted changes:\n"
                f"```\n{dirty[:500]}\n```\n\n"
                "Reply **commit** to commit them first, **stash** to stash, or **cancel**.")
            self.pending_confirmations[channel_id] = {
                "action": "dirty_workspace",
                "project_id": project_id,
                "project_name": project_name,
                "workspace": workspace,
                "instruction": instruction,
                "model": model,
                "expires_at": time.time() + CONFIRMATION_TIMEOUT,
            }
            return

        await self._launch_dev_session(project_id, project_name, workspace, instruction, channel, model)

    async def _handle_new_project(self, channel, slug, instruction, model=DEFAULT_MODEL):
        """Handle /new command."""
        if model not in ALLOWED_MODELS:
            await channel.send(f"\u274c Unknown model: {model}. Use: {', '.join(sorted(ALLOWED_MODELS))}")
            return

        if not re.match(r'^[a-z][a-z0-9-]*$', slug):
            await channel.send(
                "\u274c Slug must start with a letter and contain only lowercase letters, numbers, and hyphens.")
            return

        display_name = slug.replace("-", " ").title()
        workspace = os.path.expanduser(f"{WORKSPACE_BASE}/{slug}")

        if os.path.exists(workspace):
            await channel.send(f"\u274c Directory already exists: `{workspace}`")
            return

        with self._db() as conn:
            existing = conn.execute(
                "SELECT id FROM projects WHERE LOWER(name) = LOWER(?)", (display_name,)
            ).fetchone()
        if existing:
            await channel.send(f"\u274c Project '{display_name}' already exists.")
            return

        active_count = len(self.active_dev_sessions)
        if active_count >= DEV_SESSION_MAX_ACTIVE:
            await channel.send(f"\u274c Max {DEV_SESSION_MAX_ACTIVE} concurrent sessions.")
            return

        if not shutil.which("gh"):
            await channel.send("\u274c `gh` CLI not found. Install it: https://cli.github.com")
            return

        await channel.send(f"\u2699\ufe0f Creating **{display_name}**...\nWorkspace: `{workspace}`")

        project_id = None
        try:
            with self._db() as conn:
                cursor = conn.execute(
                    "INSERT INTO projects (name, status, workspace_path, created_at, updated_at) "
                    "VALUES (?, 'active', ?, datetime('now'), datetime('now'))",
                    (display_name, workspace),
                )
                project_id = cursor.lastrowid
                conn.execute(
                    "INSERT INTO activity_log (entity_type, entity_id, action, details, created_at) "
                    "VALUES ('project', ?, 'created', ?, datetime('now'))",
                    (project_id, "Created via /new from Discord"),
                )
                conn.commit()

            os.makedirs(workspace, exist_ok=True)
            self._scaffold_project(slug, display_name, workspace)

            subprocess.run(["git", "init"], capture_output=True, text=True, cwd=workspace, timeout=10, check=True)
            subprocess.run(["git", "branch", "-M", "main"], capture_output=True, text=True, cwd=workspace, timeout=5, check=True)
            subprocess.run(["git", "add", "-A"], capture_output=True, text=True, cwd=workspace, timeout=10, check=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], capture_output=True, text=True, cwd=workspace, timeout=10, check=True)

            # Create GitHub repo
            repo_name = re.sub(r'[^a-zA-Z0-9-]', '-', display_name).strip('-').lower()
            gh_result = subprocess.run(
                ["gh", "repo", "create", repo_name, "--private", "--source=.", "--push"],
                capture_output=True, text=True, cwd=workspace, timeout=30,
            )
            if gh_result.returncode != 0:
                error = gh_result.stderr.strip() or gh_result.stdout.strip()
                await channel.send(f"\u26a0\ufe0f GitHub repo creation failed: {error[:300]}")

            session_id, branch_name = await asyncio.get_event_loop().run_in_executor(
                None, self._spawn_dev_session,
                project_id, display_name, workspace, instruction, str(channel.id), model,
                NEW_PROJECT_TIMEOUT,
            )

            # Auto-create a Discord channel for the new project
            project_channel = None
            try:
                guild = channel.guild if hasattr(channel, "guild") else None
                if guild:
                    channel_name = re.sub(r'[^a-z0-9-]', '-', display_name.lower()).strip('-')
                    channel_name = re.sub(r'-+', '-', channel_name)
                    soy_category = None
                    for cat in guild.categories:
                        if cat.name.lower() in ("soy projects", "soy", "projects"):
                            soy_category = cat
                            break
                    if not soy_category:
                        soy_category = await guild.create_category("SoY Projects")
                    topic = f"SoY Project: {display_name} | {workspace}"
                    project_channel = await guild.create_text_channel(
                        channel_name, category=soy_category, topic=topic
                    )
                    with self._db() as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO discord_channel_projects "
                            "(channel_id, project_id, project_name) VALUES (?, ?, ?)",
                            (str(project_channel.id), project_id, display_name),
                        )
                        conn.commit()
                    self._register_v2_channel(str(project_channel.id))
            except Exception as ch_err:
                await channel.send(f"\u26a0\ufe0f Channel auto-creation failed: {ch_err}")

            ch_mention = f" | Channel: {project_channel.mention}" if project_channel else ""
            await channel.send(
                f"\U0001f680 **{display_name}** is live!\n\n"
                f"**Project ID:** {project_id}\n"
                f"**Session:** `{session_id}`\n"
                f"**Branch:** `{branch_name}`\n"
                f"**Model:** {model}\n"
                f"**Instruction:** {instruction}{ch_mention}\n\n"
                "I'll message you when the session completes.")

        except Exception as e:
            if project_id:
                self._cleanup_failed_project(project_id)
            await channel.send(f"\u274c Failed to create project: {e}")

    def _scaffold_project(self, slug, display_name, workspace):
        """Create minimal project files."""
        with open(os.path.join(workspace, ".gitignore"), "w") as f:
            f.write("node_modules/\n.vercel/\n.env\n.DS_Store\n")
        with open(os.path.join(workspace, "index.html"), "w") as f:
            f.write(
                f"<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
                f"  <meta charset=\"UTF-8\">\n"
                f"  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
                f"  <title>{display_name}</title>\n"
                f"</head>\n<body>\n  <h1>{display_name}</h1>\n</body>\n</html>\n"
            )
        with open(os.path.join(workspace, "package.json"), "w") as f:
            f.write(json.dumps({"name": slug, "version": "0.0.1", "private": True}, indent=2) + "\n")
        with open(os.path.join(workspace, "CLAUDE.md"), "w") as f:
            f.write(
                f"# {display_name}\n\n"
                "IMPORTANT: Never push to main without explicit instruction. Commit on your branch only.\n"
            )

    def _cleanup_failed_project(self, project_id):
        """Remove DB records for a failed project."""
        with self._db() as conn:
            conn.execute("DELETE FROM activity_log WHERE entity_type = 'project' AND entity_id = ?", (project_id,))
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.commit()

    async def _launch_dev_session(self, project_id, project_name, workspace, instruction, channel, model=DEFAULT_MODEL):
        """Validate, spawn, and notify for a dev session."""
        try:
            session_id, branch_name = await asyncio.get_event_loop().run_in_executor(
                None, self._spawn_dev_session,
                project_id, project_name, workspace, instruction, str(channel.id), model, None,
            )
        except ValueError as e:
            await channel.send(f"\u274c {e}")
            return

        # Create a thread for this dev session
        embed = discord.Embed(
            title=f"\U0001f527 Dev Session `{session_id}`",
            description=instruction,
            color=STATUS_COLORS["running"],
        )
        embed.add_field(name="Project", value=project_name, inline=True)
        embed.add_field(name="Branch", value=f"`{branch_name}`", inline=True)
        embed.add_field(name="Model", value=model, inline=True)
        start_msg = await channel.send(embed=embed)

        try:
            thread = await start_msg.create_thread(
                name=f"dev-{session_id}: {instruction[:80]}",
                auto_archive_duration=1440,  # 24 hours
            )
            await thread.send(f"\U0001f504 Session running... I'll post updates here.")
            # Store thread reference for later updates
            self.active_dev_sessions[session_id]["thread_id"] = thread.id
        except Exception as e:
            print(f"[thread] Could not create thread: {e}")

    def _spawn_dev_session(self, project_id, project_name, workspace, instruction, channel_id, model=DEFAULT_MODEL, timeout=None):
        """Spawn a background claude -p session for dev work on an isolated branch."""
        session_id = uuid.uuid4().hex[:8]
        branch_name = f"{DEV_BRANCH_PREFIX}{session_id}"

        if workspace in self.workspace_locks:
            raise ValueError("This workspace is busy. Wait for it to finish or `/kill` the session first.")
        self.workspace_locks.add(workspace)

        try:
            porcelain = subprocess.run(
                ["git", "status", "--porcelain", "-uno"],
                capture_output=True, text=True, cwd=workspace, timeout=5,
            )
            if porcelain.stdout.strip():
                raise ValueError("Workspace has uncommitted changes.")

            subprocess.run(
                ["git", "checkout", "main"],
                capture_output=True, text=True, cwd=workspace, timeout=10, check=True,
            )
            subprocess.run(
                ["git", "pull", "--ff-only"],
                capture_output=True, text=True, cwd=workspace, timeout=15,
            )
            git_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=workspace, timeout=5,
            ).stdout.strip()
            subprocess.run(
                ["git", "checkout", "-b", branch_name],
                capture_output=True, text=True, cwd=workspace, timeout=10, check=True,
            )
        except ValueError:
            self.workspace_locks.discard(workspace)
            raise
        except subprocess.CalledProcessError as e:
            self.workspace_locks.discard(workspace)
            raise ValueError(f"Git setup failed: {e.stderr.strip() or e}")
        except Exception as e:
            self.workspace_locks.discard(workspace)
            raise ValueError(f"Git setup failed: {e}")

        stdout_file = tempfile.NamedTemporaryFile(
            prefix=f"soy_dev_{session_id}_", suffix=".log", delete=False, mode="w",
        )
        os.chmod(stdout_file.name, stat.S_IRUSR | stat.S_IWUSR)

        # Previous session context
        prev_context = ""
        with self._db() as conn:
            prev = conn.execute(
                "SELECT instruction, status, output_summary FROM telegram_dev_sessions "
                "WHERE project_name = ? AND status IN ('completed', 'failed') "
                "ORDER BY started_at DESC LIMIT 1",
                (project_name,),
            ).fetchone()
        if prev and prev["output_summary"]:
            prev_context = (
                f"\n\n## Previous session context\n"
                f"Instruction: {prev['instruction']}\n"
                f"Status: {prev['status']}\n"
                f"Output:\n{prev['output_summary'][:1500]}"
            )

        dev_instructions = (
            "You are running autonomously as a remote dev session triggered from Discord. "
            f"You are on branch `{branch_name}`. "
            "Work independently \u2014 no interactive questions. Make the requested changes, "
            "commit your work with a clear commit message, and end your output with a "
            "'## Summary' section describing what you did and any issues encountered. "
            "IMPORTANT: Never push to main. Never merge to main. Commit on this branch only. "
            "The user reviews and approves merges separately via /approve."
            + prev_context
        )

        clean_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        process = subprocess.Popen(
            [
                "claude", "-p",
                "--model", model,
                "--no-session-persistence",
                "--dangerously-skip-permissions",
                "--append-system-prompt", dev_instructions,
                instruction,
            ],
            stdout=stdout_file,
            stderr=subprocess.STDOUT,
            cwd=workspace,
            env=clean_env,
        )

        # Record in DB (uses telegram_dev_sessions table — shared infrastructure)
        with self._db() as conn:
            conn.execute(
                "INSERT INTO telegram_dev_sessions "
                "(session_id, project_id, project_name, workspace_path, instruction, "
                "status, model, pid, stdout_path, git_before_sha, telegram_chat_id, "
                "branch_name, review_status) "
                "VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, 'pending')",
                (session_id, project_id, project_name, workspace, instruction,
                 model, process.pid, stdout_file.name, git_sha, f"discord:{channel_id}",
                 branch_name),
            )
            conn.commit()

        self.active_dev_sessions[session_id] = {
            "process": process,
            "stdout_file": stdout_file,
            "started_at": time.time(),
            "channel_id": channel_id,
            "workspace": workspace,
            "project_name": project_name,
            "project_id": project_id,
            "git_before_sha": git_sha,
            "branch_name": branch_name,
            "timeout": timeout or DEV_SESSION_TIMEOUT,
        }

        return session_id, branch_name

    async def _check_dev_sessions(self):
        """Poll active dev sessions for completion."""
        finished = []
        for sid, info in self.active_dev_sessions.items():
            proc = info["process"]
            elapsed = time.time() - info["started_at"]

            ret = proc.poll()
            if ret is not None:
                finished.append((sid, ret, elapsed))
            elif elapsed > info.get("timeout", DEV_SESSION_TIMEOUT):
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
                finished.append((sid, "timeout", elapsed))

        for sid, result, elapsed in finished:
            await self._finalize_dev_session(sid, result, elapsed)

    async def _finalize_dev_session(self, session_id, exit_result, elapsed):
        """Finalize a completed/timed-out dev session."""
        info = self.active_dev_sessions.pop(session_id, None)
        if not info:
            return

        self.workspace_locks.discard(info.get("workspace"))
        try:
            info["stdout_file"].close()
        except Exception:
            pass

        stdout_path = info["stdout_file"].name
        channel_id = info["channel_id"]
        workspace = info["workspace"]
        branch_name = info.get("branch_name")
        duration = int(elapsed)

        if exit_result == "timeout":
            status = "timeout"
            exit_code = -1
        elif exit_result == 0:
            status = "completed"
            exit_code = 0
        else:
            status = "failed"
            exit_code = exit_result if isinstance(exit_result, int) else -1

        output_text = ""
        try:
            with open(stdout_path, "r") as f:
                output_text = f.read()
        except Exception:
            pass

        summary = None
        summary_match = re.search(r'## Summary\s*\n(.*)', output_text, re.DOTALL)
        if summary_match:
            summary = summary_match.group(1).strip()[:2000]
        elif output_text:
            summary = output_text[-500:].strip()

        diff_stat = None
        try:
            diff_result = subprocess.run(
                ["git", "diff", "--stat", info["git_before_sha"] or "HEAD~1", "HEAD"],
                capture_output=True, text=True, cwd=workspace, timeout=10,
            )
            if diff_result.returncode == 0 and diff_result.stdout.strip():
                diff_stat = diff_result.stdout.strip()
        except Exception:
            pass

        with self._db() as conn:
            conn.execute(
                "UPDATE telegram_dev_sessions SET "
                "status = ?, exit_code = ?, output_summary = ?, git_diff_stat = ?, "
                "completed_at = datetime('now'), duration_seconds = ? "
                "WHERE session_id = ?",
                (status, exit_code, summary, diff_stat, duration, session_id),
            )
            conn.commit()

        # Get the channel (or thread) to send to
        channel = self.get_channel(int(channel_id)) if channel_id and channel_id.isdigit() else None
        if not channel:
            return

        # Try to post in the dev session thread if it exists
        thread = None
        thread_id = info.get("thread_id")
        if thread_id:
            thread = self.get_channel(thread_id)

        target = thread or channel

        status_icon = STATUS_ICONS.get(status, "\u2753")
        duration_str = self._format_duration(duration)
        color = STATUS_COLORS.get(status, 0x95A5A6)

        # Build result embed
        embed = discord.Embed(
            title=f"{status_icon} Session `{session_id}` \u2014 {status}",
            color=color,
        )
        embed.add_field(name="Project", value=info["project_name"], inline=True)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        if branch_name:
            embed.add_field(name="Branch", value=f"`{branch_name}`", inline=True)

        if diff_stat:
            embed.add_field(name="Changes", value=f"```\n{diff_stat[:1024]}\n```", inline=False)

        if summary:
            if len(summary) > 1024:
                summary = summary[:1024] + "..."
            embed.add_field(name="Summary", value=summary, inline=False)
        elif status == "timeout":
            embed.add_field(name="Summary", value="Session timed out.", inline=False)

        # Push branch and deploy for completed sessions
        commit_url = None
        if status == "completed" and branch_name:
            try:
                push_result = subprocess.run(
                    ["git", "push", "-u", "origin", branch_name],
                    capture_output=True, text=True, cwd=workspace, timeout=30,
                )
                if push_result.returncode == 0:
                    sha_result = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        capture_output=True, text=True, cwd=workspace, timeout=5,
                    )
                    sha = sha_result.stdout.strip()
                    github_url = self._get_github_url(workspace)
                    if github_url and sha:
                        commit_url = f"{github_url}/commit/{sha}"
            except Exception:
                pass

        if status == "completed" and branch_name:
            if commit_url:
                embed.add_field(name="Code Review", value=f"[View changes]({commit_url})", inline=True)
            await target.send(embed=embed)
            await target.send("\U0001f680 Deploying preview...")
            self._start_preview_deploy(session_id, workspace, channel_id, info["project_name"], thread_id=thread_id)
        else:
            if status == "completed":
                view = SessionReviewView(session_id, self)
                await target.send(embed=embed, view=view)
            else:
                actions = f"`/session {session_id}` for full output"
                embed.add_field(name="Actions", value=actions, inline=False)
                await target.send(embed=embed)

    # ── Preview Deploys ──

    def _start_preview_deploy(self, session_id, workspace, channel_id, project_name, thread_id=None):
        """Spawn vercel deploy in background."""
        stdout_file = tempfile.NamedTemporaryFile(
            prefix=f"soy_deploy_{session_id}_", suffix=".log", delete=False, mode="w",
        )
        os.chmod(stdout_file.name, stat.S_IRUSR | stat.S_IWUSR)
        try:
            process = subprocess.Popen(
                ["vercel", "deploy", "--yes"],
                stdout=stdout_file, stderr=subprocess.STDOUT, cwd=workspace,
            )
        except FileNotFoundError:
            stdout_file.close()
            os.unlink(stdout_file.name)
            return
        except Exception:
            stdout_file.close()
            os.unlink(stdout_file.name)
            return

        with self._db() as conn:
            conn.execute(
                "UPDATE telegram_dev_sessions SET deploy_status = 'deploying', "
                "deploy_pid = ?, deploy_stdout_path = ? WHERE session_id = ?",
                (process.pid, stdout_file.name, session_id),
            )
            conn.commit()

        self.active_deploys[session_id] = {
            "process": process,
            "stdout_file": stdout_file,
            "started_at": time.time(),
            "channel_id": channel_id,
            "workspace": workspace,
            "project_name": project_name,
            "thread_id": thread_id,
        }

    async def _check_deploys(self):
        """Poll active deploys for completion."""
        finished = []
        for sid, info in self.active_deploys.items():
            proc = info["process"]
            elapsed = time.time() - info["started_at"]

            ret = proc.poll()
            if ret is not None:
                finished.append((sid, ret, elapsed))
            elif elapsed > DEPLOY_TIMEOUT:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
                finished.append((sid, "timeout", elapsed))

        for sid, result, elapsed in finished:
            await self._finalize_deploy(sid, result, elapsed)

    async def _finalize_deploy(self, session_id, exit_result, elapsed):
        """Finalize a completed deploy."""
        info = self.active_deploys.pop(session_id, None)
        if not info:
            return

        try:
            info["stdout_file"].close()
        except Exception:
            pass

        stdout_path = info["stdout_file"].name
        channel_id = info["channel_id"]

        output_text = ""
        try:
            with open(stdout_path, "r") as f:
                output_text = f.read()
        except Exception:
            pass

        preview_url = None
        url_match = re.search(r'Preview:\s+(https://\S+\.vercel\.app)', output_text)
        if url_match:
            preview_url = url_match.group(1)
        else:
            url_match = re.search(r'(https://\S+\.vercel\.app)\s', output_text)
            if url_match:
                preview_url = url_match.group(1)

        if exit_result == "timeout":
            deploy_status = "deploy_failed"
        elif exit_result == 0 and preview_url:
            deploy_status = "deployed"
        else:
            deploy_status = "deploy_failed"

        with self._db() as conn:
            conn.execute(
                "UPDATE telegram_dev_sessions SET deploy_status = ?, preview_url = ?, "
                "deploy_pid = NULL WHERE session_id = ?",
                (deploy_status, preview_url, session_id),
            )
            conn.commit()

        channel = self.get_channel(int(channel_id)) if channel_id and channel_id.isdigit() else None
        if not channel:
            return

        # Post to thread if available
        thread = None
        thread_id = info.get("thread_id")
        if thread_id:
            thread = self.get_channel(thread_id)
        target = thread or channel

        actions = f"`/approve {session_id}` to merge | `/reject {session_id}` to discard"

        if deploy_status == "deployed":
            embed = discord.Embed(
                title=f"\U0001f310 Preview Ready",
                description=f"[{preview_url}]({preview_url})",
                color=STATUS_COLORS["deployed"],
            )
            embed.add_field(name="Actions", value=actions, inline=False)
            embed.set_footer(text=f"Session: {session_id}")
            await target.send(embed=embed)
        elif exit_result == "timeout":
            embed = discord.Embed(
                title=f"\u26a0\ufe0f Deploy Timed Out",
                description=f"Exceeded {DEPLOY_TIMEOUT}s",
                color=STATUS_COLORS["deploy_failed"],
            )
            embed.add_field(name="Actions", value=actions, inline=False)
            await target.send(embed=embed)
        else:
            error_preview = output_text[-500:].strip() if output_text else "No output"
            embed = discord.Embed(
                title=f"\u26a0\ufe0f Deploy Failed",
                description=f"```\n{error_preview[:1024]}\n```",
                color=STATUS_COLORS["deploy_failed"],
            )
            embed.add_field(name="Actions", value=actions, inline=False)
            await target.send(embed=embed)

    # ── Approve/Reject ──

    async def _handle_approve(self, channel, session_arg):
        """Merge a session's branch to main."""
        if not session_arg:
            await channel.send("**Usage:** `/approve session_id:<id>`")
            return

        with self._db() as conn:
            row, err = self._find_session_by_prefix(conn, session_arg)

            if not row:
                await channel.send(err or f"No session found matching `{session_arg}`.")
                return
            if not row["branch_name"]:
                await channel.send(f"Session `{row['session_id']}` has no branch to merge.")
                return
            if row["review_status"] == "approved":
                await channel.send(f"Session `{row['session_id']}` was already approved.")
                return
            if row["review_status"] == "rejected":
                await channel.send(f"Session `{row['session_id']}` was already rejected.")
                return

            workspace = row["workspace_path"]
            branch = row["branch_name"]
            sid = row["session_id"]
            project_name = row["project_name"]

        if workspace in self.workspace_locks:
            await channel.send("Workspace is busy. Wait or `/kill` the session first.")
            return
        self.workspace_locks.add(workspace)

        try:
            subprocess.run(
                ["git", "checkout", "main"],
                capture_output=True, text=True, cwd=workspace, timeout=10, check=True,
            )
            merge_result = subprocess.run(
                ["git", "merge", branch, "--no-edit"],
                capture_output=True, text=True, cwd=workspace, timeout=30,
            )
            if merge_result.returncode != 0:
                error = merge_result.stderr.strip() or merge_result.stdout.strip()
                await channel.send(
                    f"\u274c Merge conflict on `{sid}`\n\n```\n{error[:500]}\n```\n\nResolve manually.")
                subprocess.run(
                    ["git", "merge", "--abort"],
                    capture_output=True, text=True, cwd=workspace, timeout=10,
                )
                return

            subprocess.run(["git", "branch", "-d", branch], capture_output=True, text=True, cwd=workspace, timeout=10)
            subprocess.run(["git", "push", "origin", "--delete", branch], capture_output=True, text=True, cwd=workspace, timeout=15)

            push_result = subprocess.run(
                ["git", "push"], capture_output=True, text=True, cwd=workspace, timeout=30,
            )
            pushed = push_result.returncode == 0
        except subprocess.CalledProcessError as e:
            await channel.send(f"\u274c Git error: {e.stderr.strip() or e}")
            return
        except Exception as e:
            await channel.send(f"\u274c Error: {e}")
            return
        finally:
            self.workspace_locks.discard(workspace)

        with self._db() as conn:
            conn.execute(
                "UPDATE telegram_dev_sessions SET review_status = 'approved' WHERE session_id = ?", (sid,),
            )
            conn.commit()

        msg = (
            f"\u2705 **Approved** \u2014 `{sid}` merged to main\n"
            f"**Project:** {project_name}\n"
            f"Branch `{branch}` deleted."
        )
        if pushed:
            prod_url = self._get_vercel_production_url(workspace)
            if prod_url:
                msg += f"\n\n\U0001f680 Production: {prod_url}"
            else:
                msg += "\n\nPushed to remote."
        else:
            msg += "\n\n\u26a0\ufe0f Push failed \u2014 run `git push` manually."
        await channel.send(msg)

    async def _handle_reject(self, channel, session_arg):
        """Discard a session's branch."""
        if not session_arg:
            await channel.send("**Usage:** `/reject session_id:<id>`")
            return

        with self._db() as conn:
            row, err = self._find_session_by_prefix(conn, session_arg)

            if not row:
                await channel.send(err or f"No session found matching `{session_arg}`.")
                return
            if not row["branch_name"]:
                await channel.send(f"Session `{row['session_id']}` has no branch to reject.")
                return
            if row["review_status"] == "approved":
                await channel.send(f"Session `{row['session_id']}` was already approved.")
                return
            if row["review_status"] == "rejected":
                await channel.send(f"Session `{row['session_id']}` was already rejected.")
                return

            workspace = row["workspace_path"]
            branch = row["branch_name"]
            sid = row["session_id"]
            project_name = row["project_name"]

        if workspace in self.workspace_locks:
            await channel.send("Workspace is busy.")
            return
        self.workspace_locks.add(workspace)

        try:
            subprocess.run(["git", "checkout", "main"], capture_output=True, text=True, cwd=workspace, timeout=10, check=True)
            subprocess.run(["git", "branch", "-D", branch], capture_output=True, text=True, cwd=workspace, timeout=10, check=True)
            subprocess.run(["git", "push", "origin", "--delete", branch], capture_output=True, text=True, cwd=workspace, timeout=15)
        except subprocess.CalledProcessError as e:
            await channel.send(f"\u274c Git error: {e.stderr.strip() or e}")
            return
        except Exception as e:
            await channel.send(f"\u274c Error: {e}")
            return
        finally:
            self.workspace_locks.discard(workspace)

        with self._db() as conn:
            conn.execute(
                "UPDATE telegram_dev_sessions SET review_status = 'rejected' WHERE session_id = ?", (sid,),
            )
            conn.commit()

        await channel.send(
            f"\U0001f5d1 **Rejected** \u2014 `{sid}` discarded\n"
            f"**Project:** {project_name}\n"
            f"Branch `{branch}` deleted. Main unchanged.")

    def _handle_approve_sync(self, session_id: str) -> str:
        """Synchronous approve for button interactions. Returns status message."""
        with self._db() as conn:
            row, err = self._find_session_by_prefix(conn, session_id)
            if not row:
                return err or f"Session `{session_id}` not found."
            if row["review_status"] == "approved":
                return f"Already approved."
            workspace = row["workspace_path"]
            branch = row["branch_name"]
            sid = row["session_id"]
            project_name = row["project_name"]

        if not branch:
            return f"Session has no branch to merge."

        try:
            subprocess.run(["git", "checkout", "main"], capture_output=True, cwd=workspace, timeout=10)
            subprocess.run(["git", "merge", branch], capture_output=True, cwd=workspace, timeout=30, check=True)
            subprocess.run(["git", "branch", "-d", branch], capture_output=True, cwd=workspace, timeout=10)
            subprocess.run(["git", "push"], capture_output=True, cwd=workspace, timeout=30)
        except Exception as e:
            return f"Merge failed: {e}"

        with self._db() as conn:
            conn.execute("UPDATE telegram_dev_sessions SET review_status = 'approved' WHERE session_id = ?", (sid,))
            conn.commit()

        return f"\u2705 **Approved** — `{sid}` merged to main\n**Project:** {project_name}\nBranch `{branch}` deleted."

    def _handle_reject_sync(self, session_id: str) -> str:
        """Synchronous reject for button interactions. Returns status message."""
        with self._db() as conn:
            row, err = self._find_session_by_prefix(conn, session_id)
            if not row:
                return err or f"Session `{session_id}` not found."
            if row["review_status"] == "rejected":
                return "Already rejected."
            workspace = row["workspace_path"]
            branch = row["branch_name"]
            sid = row["session_id"]
            project_name = row["project_name"]

        if not branch:
            return "Session has no branch."

        try:
            subprocess.run(["git", "checkout", "main"], capture_output=True, cwd=workspace, timeout=10)
            subprocess.run(["git", "branch", "-D", branch], capture_output=True, cwd=workspace, timeout=10)
        except Exception as e:
            return f"Reject failed: {e}"

        with self._db() as conn:
            conn.execute("UPDATE telegram_dev_sessions SET review_status = 'rejected' WHERE session_id = ?", (sid,))
            conn.commit()

        return f"\U0001f5d1 **Rejected** — `{sid}` discarded\n**Project:** {project_name}\nBranch `{branch}` deleted."

    def _get_session_output_sync(self, session_id: str) -> str:
        """Get session output for the View Output button."""
        with self._db() as conn:
            row = conn.execute(
                "SELECT output_summary FROM telegram_dev_sessions WHERE session_id LIKE ?",
                (f"{session_id}%",),
            ).fetchone()
        if row and row["output_summary"]:
            return row["output_summary"]
        return "No output available."

    def _handle_kill(self, session_arg):
        """Kill a running session. Returns status message."""
        if not session_arg:
            return "**Usage:** `/kill session_id:<id>`"

        matches = [sid for sid in self.active_dev_sessions if sid.startswith(session_arg)]
        if len(matches) > 1:
            ids = ", ".join(f"`{s}`" for s in matches)
            return f"Ambiguous prefix \u2014 matches: {ids}"
        matched_sid = matches[0] if matches else None

        if not matched_sid:
            with self._db() as conn:
                row, err = self._find_session_by_prefix(conn, session_arg)
            if row:
                return f"Session `{row['session_id']}` already {row['status']}."
            return err or f"No session found matching `{session_arg}`."

        info = self.active_dev_sessions.pop(matched_sid)
        self.workspace_locks.discard(info.get("workspace"))
        try:
            info["process"].kill()
            info["process"].wait(timeout=5)
        except Exception:
            pass
        try:
            info["stdout_file"].close()
        except Exception:
            pass

        if matched_sid in self.active_deploys:
            deploy_info = self.active_deploys.pop(matched_sid)
            try:
                deploy_info["process"].kill()
                deploy_info["process"].wait(timeout=5)
            except Exception:
                pass
            try:
                deploy_info["stdout_file"].close()
            except Exception:
                pass

        elapsed = int(time.time() - info["started_at"])
        with self._db() as conn:
            conn.execute(
                "UPDATE telegram_dev_sessions SET status = 'killed', "
                "completed_at = datetime('now'), duration_seconds = ?, "
                "deploy_status = CASE WHEN deploy_status = 'deploying' THEN 'deploy_failed' ELSE deploy_status END "
                "WHERE session_id = ?",
                (elapsed, matched_sid),
            )
            conn.commit()

        return f"\U0001f480 Killed session `{matched_sid}` ({info['project_name']})."

    # ── Pending Confirmations ──

    async def _handle_pending_confirmation(self, text, channel):
        """Route pending confirmation responses."""
        channel_id = str(channel.id)
        conf = self.pending_confirmations.pop(channel_id)
        action = conf["action"]

        if text.strip().lower() == "cancel":
            await channel.send("Cancelled.")
            return

        if action == "dirty_workspace":
            choice = text.strip().lower()
            workspace = conf["workspace"]
            if choice == "commit":
                subprocess.run(["git", "add", "-A"], capture_output=True, text=True, cwd=workspace, timeout=10)
                result = subprocess.run(
                    ["git", "commit", "-m", "WIP: commit uncommitted changes via Discord"],
                    capture_output=True, text=True, cwd=workspace, timeout=10,
                )
                if result.returncode != 0:
                    await channel.send(f"\u274c Commit failed: {result.stderr.strip()[:300]}")
                    return
                await channel.send("\u2705 Changes committed.")
            elif choice == "stash":
                result = subprocess.run(
                    ["git", "stash", "push", "-m", "soy-bot: stashed before dev session"],
                    capture_output=True, text=True, cwd=workspace, timeout=10,
                )
                if result.returncode != 0:
                    await channel.send(f"\u274c Stash failed: {result.stderr.strip()[:300]}")
                    return
                await channel.send("\U0001f4e6 Changes stashed.")
            else:
                await channel.send("Cancelled.")
                return

            await self._launch_dev_session(
                conf["project_id"], conf["project_name"], workspace,
                conf["instruction"], channel, conf["model"])

    # ── Dev Request Detection ──

    _DEV_PATTERNS = re.compile(
        r'\b(?:'
        r'spin up.*(?:dev|claude|instance|session)'
        r'|(?:start|launch|run|kick off|fire up).*(?:dev session|claude|code session)'
        r'|(?:fix|build|implement|add|update|refactor|change|create|remove|delete|write)\b.*\b(?:bug|feature|component|page|code|function|test|style|css|html|api|endpoint|route|spinner|modal|button|form|layout|nav|header|footer|sidebar|dashboard|view|screen)'
        r'|make (?:a |the )?(?:code |)change'
        r'|dev (?:session|instance|work)'
        r')\b',
        re.IGNORECASE,
    )

    def _is_dev_request(self, text):
        """Detect natural language dev requests."""
        return bool(self._DEV_PATTERNS.search(text))

    async def _suggest_dev_command(self, text, channel):
        """Guide user to /dev command."""
        with self._db() as conn:
            projects = conn.execute(
                "SELECT id, name, workspace_path FROM projects "
                "WHERE status IN ('active', 'planning') AND workspace_path IS NOT NULL "
                "ORDER BY name"
            ).fetchall()

        if not projects:
            await channel.send("No projects with workspaces set up.")
            return

        lines = [f"- `{p['id']}` \u2014 {p['name']}" for p in projects]
        await channel.send(
            "Use `/dev` to start a dev session:\n\n"
            f"**Projects:**\n" + "\n".join(lines))

    # ── Utility Methods ──

    @staticmethod
    def _format_duration(seconds):
        """Format seconds as 'Xm Ys' or 'Ys'."""
        if not seconds:
            return "0s"
        mins, secs = divmod(int(seconds), 60)
        return f"{mins}m {secs}s" if mins else f"{secs}s"

    @staticmethod
    def _find_session_by_prefix(conn, prefix):
        """Find a dev session by ID prefix."""
        rows = conn.execute(
            "SELECT * FROM telegram_dev_sessions WHERE SUBSTR(session_id, 1, ?) = ?",
            (len(prefix), prefix),
        ).fetchall()
        if len(rows) == 1:
            return rows[0], None
        if len(rows) > 1:
            ids = ", ".join(f"`{r['session_id']}`" for r in rows)
            return None, f"Ambiguous prefix \u2014 matches: {ids}"
        return None, None

    def _log_error(self, error_msg, stack=None, channel_id=None, user_msg=None):
        """Log error to discord_bot_errors."""
        try:
            with self._db() as conn:
                conn.execute(
                    "INSERT INTO discord_bot_errors (error_message, error_stack, user_message_preview, channel_id) "
                    "VALUES (?, ?, ?, ?)",
                    (str(error_msg)[:500], (stack or "")[:500], (user_msg or "")[:100], channel_id),
                )
                conn.execute(
                    "DELETE FROM discord_bot_errors WHERE id NOT IN "
                    f"(SELECT id FROM discord_bot_errors ORDER BY created_at DESC LIMIT {ERROR_LOG_MAX})"
                )
                conn.commit()
        except Exception:
            pass

    @staticmethod
    def _ensure_git_remote(workspace, project_name):
        """Check workspace has git remote. Returns True if ready."""
        git_dir = os.path.join(workspace, ".git")
        if not os.path.isdir(git_dir):
            return False
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=workspace, timeout=5,
        )
        return result.returncode == 0 and bool(result.stdout.strip())

    @staticmethod
    def _get_github_url(workspace):
        """Extract GitHub URL from git remote."""
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, cwd=workspace, timeout=5,
            )
            remote = result.stdout.strip()
            if not remote:
                return None
            match = re.match(r'git@github\.com:(.+?)(?:\.git)?$', remote)
            if match:
                return f"https://github.com/{match.group(1)}"
            match = re.match(r'https://github\.com/(.+?)(?:\.git)?$', remote)
            if match:
                return f"https://github.com/{match.group(1)}"
            return None
        except Exception:
            return None

    @staticmethod
    def _get_vercel_production_url(workspace):
        """Get Vercel production URL."""
        try:
            config_path = os.path.join(workspace, ".vercel", "project.json")
            with open(config_path) as f:
                config = json.load(f)
            project_name = config.get("projectName")
            if project_name:
                return f"https://{project_name}.vercel.app"
        except Exception:
            pass
        return None

    def _recover_orphaned_sessions(self):
        """Clean up stale sessions from previous runs."""
        with self._db() as conn:
            stale = conn.execute(
                "SELECT session_id, pid, telegram_chat_id, project_name "
                "FROM telegram_dev_sessions WHERE status = 'running'"
            ).fetchall()

            for row in stale:
                pid = row["pid"]
                if pid:
                    try:
                        result = subprocess.run(
                            ["ps", "-p", str(pid), "-o", "comm="],
                            capture_output=True, text=True, timeout=5,
                        )
                        if "claude" in result.stdout.lower():
                            os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, Exception):
                        pass

                conn.execute(
                    "UPDATE telegram_dev_sessions SET status = 'killed', "
                    "completed_at = datetime('now') WHERE session_id = ?",
                    (row["session_id"],),
                )

            stale_deploys = conn.execute(
                "SELECT session_id, deploy_pid FROM telegram_dev_sessions WHERE deploy_status = 'deploying'"
            ).fetchall()

            for row in stale_deploys:
                pid = row["deploy_pid"]
                if pid:
                    try:
                        result = subprocess.run(
                            ["ps", "-p", str(pid), "-o", "comm="],
                            capture_output=True, text=True, timeout=5,
                        )
                        if "vercel" in result.stdout.lower():
                            os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, Exception):
                        pass

                conn.execute(
                    "UPDATE telegram_dev_sessions SET deploy_status = 'deploy_failed', "
                    "deploy_pid = NULL WHERE session_id = ?",
                    (row["session_id"],),
                )

            if stale or stale_deploys:
                conn.commit()

    def _cleanup_old_temp_files(self):
        """Remove old dev session temp files."""
        try:
            tmp_dir = tempfile.gettempdir()
            cutoff = time.time() - TEMP_FILE_MAX_AGE
            for fname in os.listdir(tmp_dir):
                if (fname.startswith("soy_dev_") or fname.startswith("soy_deploy_")) and fname.endswith(".log"):
                    fpath = os.path.join(tmp_dir, fname)
                    if os.path.getmtime(fpath) < cutoff:
                        os.unlink(fpath)
        except Exception:
            pass

    # ── Nudges ──

    async def send_nudge(self, text):
        """Post a message to the #nudges channel (for proactive alerts)."""
        if self._nudges_channel:
            await self._send_long_channel(self._nudges_channel, text)

    async def send_nudge_embed(self, embed):
        """Post an embed to the #nudges channel."""
        if self._nudges_channel:
            await self._nudges_channel.send(embed=embed)

    async def _post_daily_digest(self):
        """Post a morning digest to #nudges. Runs once per day after 8 AM."""
        if not self._nudges_channel:
            return

        with self._db() as conn:
            # Today's calendar
            events = []
            try:
                events = conn.execute(
                    "SELECT title, start_time, end_time, location "
                    "FROM calendar_events WHERE date(start_time) = date('now') "
                    "ORDER BY start_time ASC LIMIT 10"
                ).fetchall()
            except Exception:
                pass

            # Priority tasks
            priority_tasks = conn.execute(
                "SELECT t.title, t.priority, t.due_date, p.name as project_name "
                "FROM tasks t LEFT JOIN projects p ON p.id = t.project_id "
                "WHERE t.status != 'done' AND t.priority IN ('urgent', 'high') "
                "ORDER BY CASE t.priority WHEN 'urgent' THEN 0 ELSE 1 END "
                "LIMIT 10"
            ).fetchall()

            # Sessions awaiting review
            pending_review = conn.execute(
                "SELECT session_id, project_name, instruction "
                "FROM telegram_dev_sessions WHERE review_status = 'pending' "
                "AND status = 'completed' ORDER BY completed_at DESC LIMIT 5"
            ).fetchall()

            # Project health summary
            projects = conn.execute(
                "SELECT p.name, "
                "(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status != 'done') as open_tasks, "
                "(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status != 'done' "
                "AND t.due_date < date('now')) as overdue "
                "FROM projects p WHERE p.status = 'active' ORDER BY p.name"
            ).fetchall()

        # Build digest
        header = discord.Embed(
            title="\u2615 Good Morning \u2014 Daily Digest",
            description=time.strftime("%A, %B %d %Y"),
            color=PROJECT_COLOR,
        )
        await self._nudges_channel.send(embed=header)

        # Calendar
        if events:
            lines = []
            for ev in events:
                start = ev["start_time"]
                # Extract just the time portion
                time_part = start.split("T")[-1][:5] if "T" in start else start[-8:-3]
                loc = f" @ {ev['location']}" if ev["location"] else ""
                lines.append(f"\U0001f4c5 **{time_part}** \u2014 {ev['title']}{loc}")
            embed = discord.Embed(
                title="Today's Schedule",
                description="\n".join(lines),
                color=0x3498DB,
            )
            await self._nudges_channel.send(embed=embed)

        # Priority tasks
        if priority_tasks:
            lines = []
            for t in priority_tasks:
                icon = PRIORITY_ICONS.get(t["priority"], "\U0001f7e1")
                proj = f" *{t['project_name']}*" if t["project_name"] else ""
                due = f" \u2014 due {t['due_date']}" if t["due_date"] else ""
                lines.append(f"{icon} {t['title']}{proj}{due}")
            embed = discord.Embed(
                title="Priority Tasks",
                description="\n".join(lines),
                color=0xED4245,
            )
            await self._nudges_channel.send(embed=embed)

        # Sessions awaiting review
        if pending_review:
            lines = []
            for s in pending_review:
                instr = s["instruction"][:60] + ("..." if len(s["instruction"]) > 60 else "")
                lines.append(f"\u23f3 `{s['session_id']}` \u2014 {s['project_name']}: {instr}")
            embed = discord.Embed(
                title="Awaiting Review",
                description="\n".join(lines),
                color=0xE67E22,
            )
            await self._nudges_channel.send(embed=embed)

        # Project health
        if projects:
            lines = []
            for p in projects:
                status = "\U0001f534" if p["overdue"] else "\U0001f7e2"
                overdue = f" ({p['overdue']} overdue)" if p["overdue"] else ""
                lines.append(f"{status} **{p['name']}** \u2014 {p['open_tasks']} open{overdue}")
            embed = discord.Embed(
                title="Project Health",
                description="\n".join(lines),
                color=PROJECT_COLOR,
            )
            await self._nudges_channel.send(embed=embed)

        if not events and not priority_tasks and not pending_review:
            await self._nudges_channel.send(embed=discord.Embed(
                description="\U0001f7e2 Clear day \u2014 no urgent items.",
                color=0x2ECC71,
            ))

    async def _post_nudges(self):
        """Check for actionable items and post to #nudges. Runs hourly."""
        if not self._nudges_channel:
            return

        embeds = []

        with self._db() as conn:
            # Overdue tasks
            overdue = conn.execute(
                "SELECT t.title, t.priority, t.due_date, p.name as project_name "
                "FROM tasks t LEFT JOIN projects p ON p.id = t.project_id "
                "WHERE t.status != 'done' AND t.due_date < date('now') "
                "ORDER BY t.due_date ASC LIMIT 10"
            ).fetchall()

            if overdue:
                lines = []
                for t in overdue:
                    pri_icon = PRIORITY_ICONS.get(t["priority"] or "medium", "\U0001f7e1")
                    proj = f" *{t['project_name']}*" if t["project_name"] else ""
                    lines.append(f"{pri_icon} {t['title']}{proj} \u2014 due {t['due_date']}")
                embed = discord.Embed(
                    title="\U0001f534 Overdue Tasks",
                    description="\n".join(lines),
                    color=0xED4245,
                )
                embeds.append(embed)

            # Upcoming tasks (due in next 3 days)
            upcoming = conn.execute(
                "SELECT t.title, t.priority, t.due_date, p.name as project_name "
                "FROM tasks t LEFT JOIN projects p ON p.id = t.project_id "
                "WHERE t.status != 'done' AND t.due_date >= date('now') "
                "AND t.due_date <= date('now', '+3 days') "
                "ORDER BY t.due_date ASC LIMIT 10"
            ).fetchall()

            if upcoming:
                lines = []
                for t in upcoming:
                    pri_icon = PRIORITY_ICONS.get(t["priority"] or "medium", "\U0001f7e1")
                    proj = f" *{t['project_name']}*" if t["project_name"] else ""
                    lines.append(f"{pri_icon} {t['title']}{proj} \u2014 due {t['due_date']}")
                embed = discord.Embed(
                    title="\U0001f7e0 Due Soon (3 days)",
                    description="\n".join(lines),
                    color=0xE67E22,
                )
                embeds.append(embed)

            # Open commitments (from conversation intelligence)
            try:
                commitments = conn.execute(
                    "SELECT description, owner, due_date, contact_name "
                    "FROM commitments WHERE status = 'open' "
                    "AND (due_date IS NULL OR due_date <= date('now', '+3 days')) "
                    "ORDER BY due_date ASC LIMIT 10"
                ).fetchall()

                if commitments:
                    lines = []
                    for c in commitments:
                        owner = c["owner"] or "you"
                        contact = f" (with {c['contact_name']})" if c["contact_name"] else ""
                        due = f" \u2014 due {c['due_date']}" if c["due_date"] else " \u2014 no date"
                        lines.append(f"\u2022 {c['description']}{contact}{due} [{owner}]")
                    embed = discord.Embed(
                        title="\U0001f4cb Open Commitments",
                        description="\n".join(lines),
                        color=NUDGE_COLOR,
                    )
                    embeds.append(embed)
            except Exception:
                pass  # commitments table may not exist

            # Cold contacts (no interaction in 30+ days)
            try:
                cold = conn.execute(
                    "SELECT name, company, "
                    "(SELECT MAX(date) FROM activity_log WHERE entity_type = 'contact' AND entity_id = contacts.id) as last_activity "
                    "FROM contacts WHERE status = 'active' "
                    "AND id IN (SELECT entity_id FROM activity_log WHERE entity_type = 'contact' "
                    "GROUP BY entity_id HAVING MAX(date) < date('now', '-30 days')) "
                    "LIMIT 5"
                ).fetchall()

                if cold:
                    lines = []
                    for c in cold:
                        company = f" ({c['company']})" if c["company"] else ""
                        lines.append(f"\u2022 {c['name']}{company}")
                    embed = discord.Embed(
                        title="\U0001f9ca Cold Contacts (30+ days)",
                        description="\n".join(lines),
                        color=0x3498DB,
                    )
                    embeds.append(embed)
            except Exception:
                pass  # view may not exist

            # Upcoming calendar events (next 24 hours)
            try:
                events = conn.execute(
                    "SELECT title, start_time, end_time, location "
                    "FROM calendar_events WHERE start_time >= datetime('now') "
                    "AND start_time <= datetime('now', '+24 hours') "
                    "ORDER BY start_time ASC LIMIT 5"
                ).fetchall()

                if events:
                    lines = []
                    for ev in events:
                        loc = f" @ {ev['location']}" if ev["location"] else ""
                        lines.append(f"\U0001f4c5 {ev['title']} \u2014 {ev['start_time']}{loc}")
                    embed = discord.Embed(
                        title="\U0001f4c5 Upcoming (24h)",
                        description="\n".join(lines),
                        color=0x3498DB,
                    )
                    embeds.append(embed)
            except Exception:
                pass  # calendar table may not exist

        # Only post if there's something to nudge about
        if embeds:
            # Add a header
            header = discord.Embed(
                title="\U0001f514 SoY Nudge",
                description=time.strftime("%A, %B %d %Y \u2014 %I:%M %p"),
                color=NUDGE_COLOR,
            )
            await self._nudges_channel.send(embed=header)
            for embed in embeds[:10]:
                await self._nudges_channel.send(embed=embed)


def main():
    if not BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN not set in .env")
        sys.exit(1)
    if not OWNER_ID:
        print("Error: DISCORD_OWNER_ID not set in .env")
        sys.exit(1)
    if not shutil.which("git"):
        print("Error: git not found in PATH.")
        sys.exit(1)

    bot = SoYBot()
    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
