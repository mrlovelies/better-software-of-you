#!/usr/bin/env python3
"""Smart notification service for Seerr/Sonarr/Radarr.

Receives webhooks from Sonarr (onDownload, onGrab) and:
1. Looks up who requested the series via Seerr API
2. Sends "started landing" email on first episode download (per series+user)
3. Sends "complete" email when full request is fulfilled
4. Reorders SAB queue on grab events so earliest episodes download first

Runs as a systemd service on port 8799.

Required environment variables (set via the systemd unit's Environment= or
EnvironmentFile= directive):
    SEERR_NOTIFY_SONARR_KEY   Sonarr API key
    SEERR_NOTIFY_SAB_KEY      SABnzbd API key
    SEERR_NOTIFY_SMTP_USER    Gmail address used as the sender
    SEERR_NOTIFY_SMTP_PASS    Gmail app password (not the account password)

The Seerr API key is loaded at startup from the Seerr container's config.
"""
import json
import os
import smtplib
import re
import sys
import time
import urllib.request
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# --- Config ---
PORT = 8799
STATE_FILE = Path("/home/mrlovelies/.local/share/software-of-you/seerr_notify_state.json")
LOG_FILE = Path("/home/mrlovelies/.local/share/software-of-you/seerr_notify.log")

SEERR_URL = "http://127.0.0.1:5055"
SEERR_KEY = ""  # loaded at startup from Seerr config
SONARR_URL = "http://127.0.0.1:8989"
SONARR_KEY = os.environ.get("SEERR_NOTIFY_SONARR_KEY", "")
SAB_URL = "http://127.0.0.1:9090"
SAB_KEY = os.environ.get("SEERR_NOTIFY_SAB_KEY", "")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.environ.get("SEERR_NOTIFY_SMTP_USER", "")
SMTP_PASS = os.environ.get("SEERR_NOTIFY_SMTP_PASS", "")
SENDER_NAME = "Plex Media Server"


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def api_get(base_url, api_key, path):
    req = urllib.request.Request(
        base_url + path,
        headers={"X-Api-Key": api_key},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def send_email(to_addr, to_name, subject, body_html):
    """Send an HTML email via Gmail SMTP."""
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{SENDER_NAME} <{SMTP_USER}>"
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_addr, msg.as_string())
        log(f"email sent to {to_addr}: {subject}")
        return True
    except Exception as e:
        log(f"email FAILED to {to_addr}: {e}")
        return False


def find_seerr_requester(tvdb_id=None, tmdb_id=None, title=None):
    """Find who requested this series/movie in Seerr."""
    try:
        # Search Seerr requests
        requests = api_get(SEERR_URL, SEERR_KEY,
                          "/api/v1/request?take=100&skip=0&sort=added&filter=all")
        for r in requests.get("results", []):
            media = r.get("media", {})
            if tvdb_id and media.get("tvdbId") == tvdb_id:
                return r.get("requestedBy", {})
            if tmdb_id and media.get("tmdbId") == tmdb_id:
                return r.get("requestedBy", {})
    except Exception as e:
        log(f"seerr lookup error: {e}")
    return None


def get_series_progress(series_id):
    """Get episode file count vs total for a Sonarr series."""
    try:
        series = api_get(SONARR_URL, SONARR_KEY, f"/api/v3/series/{series_id}")
        stats = series.get("statistics", {})
        return {
            "title": series.get("title", "Unknown"),
            "have": stats.get("episodeFileCount", 0),
            "total": stats.get("episodeCount", 0),
            "tvdb_id": series.get("tvdbId"),
        }
    except Exception as e:
        log(f"sonarr series lookup error: {e}")
        return None


def reorder_sab_queue(series_title):
    """Reorder SAB queue items for a series by season+episode number."""
    try:
        url = f"{SAB_URL}/api?mode=queue&apikey={SAB_KEY}&output=json&limit=2000"
        queue = json.loads(urllib.request.urlopen(url, timeout=30).read())
        slots = queue.get("queue", {}).get("slots", [])

        # Find items matching this series (normalize title for matching)
        clean_title = re.sub(r"[^a-z0-9]", "", series_title.lower())
        series_items = []
        for s in slots:
            fn = s.get("filename", "")
            clean_fn = re.sub(r"[^a-z0-9]", "", fn.lower())
            if clean_title in clean_fn:
                # Extract SxxExx
                match = re.search(r"s(\d+)e(\d+)", fn, re.IGNORECASE)
                if match:
                    season = int(match.group(1))
                    episode = int(match.group(2))
                    series_items.append((season, episode, s["nzo_id"], fn))

        if len(series_items) < 2:
            return  # nothing to reorder

        # Sort by season, then episode
        series_items.sort(key=lambda x: (x[0], x[1]))

        # Find the position of the first item in the current queue
        slot_ids = [s["nzo_id"] for s in slots]
        positions = [slot_ids.index(nzo) for _, _, nzo, _ in series_items if nzo in slot_ids]
        if not positions:
            return
        first_pos = min(positions)

        # Move items in order starting at first_pos
        for i, (season, episode, nzo_id, fn) in enumerate(series_items):
            target_pos = first_pos + i
            switch_url = f"{SAB_URL}/api?mode=switch&value={nzo_id}&value2={target_pos}&apikey={SAB_KEY}&output=json"
            urllib.request.urlopen(switch_url, timeout=10).read()

        log(f"reordered {len(series_items)} SAB items for '{series_title}' (S{series_items[0][0]:02d}E{series_items[0][1]:02d} first)")

    except Exception as e:
        log(f"sab reorder error: {e}")


def handle_download(payload):
    """Handle Sonarr onDownload webhook — first-episode notification logic."""
    series = payload.get("series", {})
    episodes = payload.get("episodes", [])
    series_id = series.get("id")
    series_title = series.get("title", "Unknown")
    tvdb_id = series.get("tvdbId")

    if not series_id:
        return

    state = load_state()
    state_key = f"tv_{series_id}"

    # Check if we already sent "started landing"
    if state.get(state_key, {}).get("started_landing"):
        # Check if now complete
        progress = get_series_progress(series_id)
        if progress and progress["have"] >= progress["total"] and not state.get(state_key, {}).get("complete"):
            # Fully available — send complete notification
            requester = find_seerr_requester(tvdb_id=tvdb_id)
            if requester:
                email = requester.get("email")
                name = requester.get("displayName") or requester.get("plexUsername") or "there"
                if email:
                    send_email(
                        email, name,
                        f"{series_title} — Complete on Plex!",
                        f"""<div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 500px;">
                        <h2 style="color: #e5a00d;">All episodes are ready! 🎬</h2>
                        <p>Hey {name},</p>
                        <p><strong>{series_title}</strong> is now fully available on Plex. All {progress['total']} episodes are ready to watch.</p>
                        <p>Enjoy!</p>
                        </div>""",
                    )
                    state.setdefault(state_key, {})["complete"] = datetime.now().isoformat()
                    save_state(state)
        return

    # First episode downloaded for this series — send "started landing"
    progress = get_series_progress(series_id)
    requester = find_seerr_requester(tvdb_id=tvdb_id)

    if requester:
        email = requester.get("email")
        name = requester.get("displayName") or requester.get("plexUsername") or "there"
        if email and progress:
            ep_info = ""
            if episodes:
                ep = episodes[0]
                ep_info = f" (starting with S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d})"

            send_email(
                email, name,
                f"{series_title} has started to land!",
                f"""<div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 500px;">
                <h2 style="color: #e5a00d;">Your request is arriving! 📺</h2>
                <p>Hey {name},</p>
                <p><strong>{series_title}</strong> has started downloading{ep_info}. You can start watching now while the rest of the series lands.</p>
                <p>{progress['have']} of {progress['total']} episodes are on Plex so far.</p>
                <p>We'll let you know when everything is ready.</p>
                </div>""",
            )

            state.setdefault(state_key, {})["started_landing"] = datetime.now().isoformat()
            state[state_key]["requester_email"] = email

            # Check if already complete (small series, all eps grabbed at once)
            if progress["have"] >= progress["total"]:
                state[state_key]["complete"] = datetime.now().isoformat()

            save_state(state)
            return

    # No Seerr requester found — this is a background/RSS grab, skip notification
    log(f"no Seerr requester for '{series_title}' (tvdbId={tvdb_id}), skipping notification")


def handle_grab(payload):
    """Handle Sonarr onGrab webhook — reorder SAB queue by episode number."""
    series = payload.get("series", {})
    series_title = series.get("title", "")
    if series_title:
        # Small delay to let all episodes from the grab batch land in SAB
        time.sleep(5)
        reorder_sab_queue(series_title)


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        event_type = payload.get("eventType", "")

        if event_type == "Download":
            log(f"onDownload: {payload.get('series', {}).get('title', '?')} — {payload.get('episodes', [{}])[0].get('title', '?')}")
            try:
                handle_download(payload)
            except Exception as e:
                log(f"download handler error: {e}")

        elif event_type == "Grab":
            log(f"onGrab: {payload.get('series', {}).get('title', '?')}")
            try:
                handle_grab(payload)
            except Exception as e:
                log(f"grab handler error: {e}")

        elif event_type == "Test":
            log("test webhook received")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, format, *args):
        pass  # suppress default HTTP logging


def load_seerr_key():
    """Load Seerr API key from its config file."""
    global SEERR_KEY
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "exec", "seerr", "cat", "/app/config/settings.json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            config = json.loads(result.stdout)
            SEERR_KEY = config.get("main", {}).get("apiKey", "")
            log(f"loaded Seerr API key (length {len(SEERR_KEY)})")
        else:
            # Try sg docker
            result = subprocess.run(
                ["sg", "docker", "-c", "docker exec seerr cat /app/config/settings.json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                config = json.loads(result.stdout)
                SEERR_KEY = config.get("main", {}).get("apiKey", "")
                log(f"loaded Seerr API key via sg (length {len(SEERR_KEY)})")
    except Exception as e:
        log(f"failed to load Seerr key: {e}")


if __name__ == "__main__":
    log("=== seerr-notify service starting ===")

    _required = {
        "SEERR_NOTIFY_SONARR_KEY": SONARR_KEY,
        "SEERR_NOTIFY_SAB_KEY": SAB_KEY,
        "SEERR_NOTIFY_SMTP_USER": SMTP_USER,
        "SEERR_NOTIFY_SMTP_PASS": SMTP_PASS,
    }
    _missing = [k for k, v in _required.items() if not v]
    if _missing:
        log(f"FATAL: missing required env vars: {', '.join(_missing)}")
        sys.exit(1)

    load_seerr_key()

    server = HTTPServer(("127.0.0.1", PORT), WebhookHandler)
    log(f"listening on 127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
        server.server_close()
