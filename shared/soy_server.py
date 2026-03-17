#!/usr/bin/env python3
"""SoY Hub — unified local server for Software of You.

Serves the hub home page, all generated HTML views, and the audition board API.
Replaces the standalone audition_server.py.

Usage:
    python3 soy_server.py          # Start on port 8787
    python3 soy_server.py 9090     # Start on custom port
"""

import json
import mimetypes
import os
import re
import signal
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")
OUTPUT_DIR = os.path.join(PLUGIN_ROOT, "output")
HUB_DIR = os.path.join(PLUGIN_ROOT, "hub", "dist")
DEFAULT_PORT = 8787
SHARED_DIR = os.path.join(PLUGIN_ROOT, "shared")

# ── Dark mode overlay for iframe-embedded pages ─────────────
_dark_mode_css = None
_theme_bridge_js = None

def _get_dark_mode_css():
    global _dark_mode_css
    if _dark_mode_css is None:
        css_path = os.path.join(SHARED_DIR, "page-dark-mode.css")
        try:
            with open(css_path) as f:
                _dark_mode_css = f.read()
        except FileNotFoundError:
            _dark_mode_css = ""
    return _dark_mode_css

def _get_theme_bridge_js():
    global _theme_bridge_js
    if _theme_bridge_js is None:
        js_path = os.path.join(SHARED_DIR, "page-theme-bridge.js")
        try:
            with open(js_path) as f:
                _theme_bridge_js = f.read()
        except FileNotFoundError:
            _theme_bridge_js = ""
    return _theme_bridge_js

def _prepare_for_hub(html):
    """Strip baked-in sidebar and inject dark mode support for hub iframe."""
    # ── Strip sidebar HTML elements ──
    # <aside id="sidebar" ...>...</aside>
    html = re.sub(r'<aside\s+id=["\']sidebar["\'][^>]*>.*?</aside>', '', html, flags=re.DOTALL)
    # <button id="sidebar-toggle" ...>...</button>
    html = re.sub(r'<button\s+id=["\']sidebar-toggle["\'][^>]*>.*?</button>', '', html, flags=re.DOTALL)
    # <div id="sidebar-backdrop" ...>...</div>
    html = re.sub(r'<div\s+id=["\']sidebar-backdrop["\'][^>]*>.*?</div>', '', html, flags=re.DOTALL)

    # ── Strip sidebar CSS from <style> blocks ──
    # Remove all .sidebar* rules and @media sidebar rules
    html = re.sub(r'/\*\s*──\s*SIDEBAR\s*──\s*\*/.*?(?=/\*\s*──|</style>)', '', html, flags=re.DOTALL)
    # Individual sidebar rules that might not be in a marked block
    html = re.sub(r'^\s*\.sidebar[^{]*\{[^}]*\}\s*$', '', html, flags=re.MULTILINE)
    html = re.sub(r'@media[^{]*\{\s*\.sidebar[^}]*\{[^}]*\}\s*\}', '', html, flags=re.DOTALL)

    # ── Strip sidebar JS ──
    # Remove marked SIDEBAR block (matches "SIDEBAR", "SIDEBAR JS", etc.)
    html = re.sub(
        r'//\s*──\s*SIDEBAR[^─]*──.*?(?=//\s*──|</script>)',
        '', html, flags=re.DOTALL
    )
    # Remove individual sidebar functions (for pages without comment markers)
    html = re.sub(r'function\s+toggleSection\s*\([^)]*\)\s*\{[^}]*\}', '', html)
    html = re.sub(r'function\s+showAllEntities\s*\([^)]*\)\s*\{[^}]*\}', '', html)
    html = re.sub(r'function\s+toggleSidebar\s*\(\)\s*\{.*?\n\s*\}', '', html, flags=re.DOTALL)
    # Remove keydown Escape listener that references sidebar (multi-line)
    html = re.sub(
        r"document\.addEventListener\(['\"]keydown['\"].*?sidebar.*?\}\s*\);",
        '', html, flags=re.DOTALL
    )
    # Remove stray mobile toggle buttons
    html = re.sub(
        r'<button\s+class=["\']sidebar-mobile-toggle["\'][^>]*>.*?</button>',
        '', html, flags=re.DOTALL
    )

    # ── Remove lg:ml-60 from main ──
    html = re.sub(r'(<main\s+[^>]*?)class="([^"]*?)lg:ml-60([^"]*?)"', r'\1class="\2\3"', html)

    # ── Inject dark mode CSS + theme bridge JS ──
    css = _get_dark_mode_css()
    js = _get_theme_bridge_js()
    inject = ""
    if css:
        inject += f"<style>{css}</style>"
    if js:
        inject += f"<script>{js}</script>"
    if inject:
        if "<head>" in html:
            html = html.replace("<head>", f"<head>{inject}", 1)
        elif "<HEAD>" in html:
            html = html.replace("<HEAD>", f"<HEAD>{inject}", 1)
        else:
            html = inject + html
    return html

# ── Sidebar CSS ──────────────────────────────────────────────
SIDEBAR_CSS = """
/* ── SIDEBAR ── */
.sidebar {
  position: fixed;
  top: 0;
  left: 0;
  height: 100vh;
  width: 15rem;
  background: white;
  border-right: 1px solid #e4e4e7;
  display: flex;
  flex-direction: column;
  z-index: 40;
  transform: translateX(-100%);
  transition: transform 0.2s ease;
}
@media (min-width: 1024px) {
  .sidebar { transform: translateX(0); }
}
.sidebar.open { transform: translateX(0); }

.sidebar-header {
  padding: 1rem 1rem;
  border-bottom: 1px solid #f4f4f5;
  flex-shrink: 0;
}
.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  font-weight: 600;
  color: #18181b;
  text-decoration: none;
}
.sidebar-logo:hover { color: #3b82f6; }

.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 0.5rem;
}

.sidebar-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.75rem;
  border-radius: 0.375rem;
  font-size: 0.8125rem;
  color: #71717a;
  text-decoration: none;
  transition: all 0.15s;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sidebar-item:hover {
  background: #f4f4f5;
  color: #18181b;
}
.sidebar-item.active {
  background: #eff6ff;
  color: #1d4ed8;
  font-weight: 600;
}
.sidebar-item-disabled {
  opacity: 0.4;
  cursor: default;
  pointer-events: none;
}

.sidebar-badge {
  margin-left: auto;
  font-size: 0.6875rem;
  background: #e4e4e7;
  color: #52525b;
  padding: 0.0625rem 0.375rem;
  border-radius: 9999px;
  font-weight: 500;
  flex-shrink: 0;
}
.sidebar-item.active .sidebar-badge {
  background: #3b82f6;
  color: white;
}
.sidebar-badge-alert {
  background: #fecaca;
  color: #dc2626;
}
.sidebar-item.active .sidebar-badge-alert {
  background: #dc2626;
  color: white;
}

.sidebar-section {
  margin-top: 0.5rem;
}
.sidebar-section-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 0.375rem 0.75rem;
  font-size: 0.6875rem;
  font-weight: 600;
  color: #71717a;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  cursor: pointer;
  background: none;
  border: none;
  font-family: inherit;
  transition: color 0.15s;
}
.sidebar-section-label:hover {
  color: #52525b;
}
.sidebar-chevron {
  transition: transform 0.15s;
  flex-shrink: 0;
}
.sidebar-section.open > .sidebar-section-label .sidebar-chevron {
  transform: rotate(90deg);
}
.sidebar-section-content {
  display: none;
  padding-top: 0.125rem;
}
.sidebar-section.open > .sidebar-section-content {
  display: block;
}

.sidebar-entity {
  display: block;
  padding: 0.25rem 0.75rem 0.25rem 1.75rem;
  font-size: 0.8125rem;
  color: #71717a;
  text-decoration: none;
  transition: all 0.15s;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sidebar-entity:hover {
  background: #f4f4f5;
  color: #18181b;
  border-radius: 0.375rem;
}
.sidebar-entity.active {
  background: #eff6ff;
  color: #1d4ed8;
  font-weight: 600;
  border-radius: 0.375rem;
}
.sidebar-entity-disabled {
  opacity: 0.4;
  cursor: default;
  pointer-events: none;
}

.sidebar-divider {
  height: 1px;
  background: #f4f4f5;
  margin: 0.375rem 0.75rem;
}

.sidebar-subitem {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.2rem 0.75rem 0.2rem 2.25rem;
  font-size: 0.6875rem;
  color: #71717a;
  text-decoration: none;
  border-radius: 0.375rem;
  transition: all 0.15s;
}
.sidebar-subitem svg,
.sidebar-subitem i {
  width: 0.75rem !important;
  height: 0.75rem !important;
  flex-shrink: 0;
}
.sidebar-subitem:hover { background: #f4f4f5; color: #3f3f46; }
.sidebar-subitem.active { background: #eff6ff; color: #2563eb; }

.sidebar-entity-nolink {
  cursor: default;
  opacity: 0.8;
}

.sidebar-show-all {
  display: block;
  width: 100%;
  padding: 0.25rem 0.75rem 0.25rem 1.75rem;
  font-size: 0.75rem;
  color: #3b82f6;
  text-align: left;
  cursor: pointer;
  background: none;
  border: none;
  font-family: inherit;
  transition: color 0.15s;
}
.sidebar-show-all:hover { color: #1d4ed8; }

.sidebar-entity-overflow {
  display: none;
}
.sidebar-section.show-all .sidebar-entity-overflow {
  display: block;
}
.sidebar-section.show-all .sidebar-show-all {
  display: none;
}

.sidebar-tip-zone {
  padding: 0.75rem;
  border-top: 1px solid #f4f4f5;
  flex-shrink: 0;
}
.sidebar-tip {
  background: #fafafa;
  border-radius: 0.5rem;
  padding: 0.75rem;
}
.sidebar-tip-label {
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #a1a1aa;
  font-weight: 600;
  margin-bottom: 0.25rem;
}
.sidebar-tip-text {
  font-size: 0.75rem;
  color: #71717a;
  line-height: 1.4;
}

.sidebar-mobile-toggle {
  position: fixed;
  top: 1rem;
  left: 1rem;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  background: white;
  border: 1px solid #e4e4e7;
  border-radius: 0.5rem;
  padding: 0.5rem;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
  cursor: pointer;
  color: #52525b;
}
@media (min-width: 1024px) {
  .sidebar-mobile-toggle { display: none; }
}

.sidebar-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.3);
  z-index: 30;
  display: none;
}
.sidebar-backdrop.visible {
  display: block;
}
@media (min-width: 1024px) {
  .sidebar-backdrop { display: none !important; }
}

/* === Delight Layer CSS === */
@keyframes delightFadeUp {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: translateY(0); }
}
.delight-card {
    opacity: 0;
    animation: delightFadeUp 0.4s ease-out forwards;
}
.delight-card:nth-child(1) { animation-delay: 0ms; }
.delight-card:nth-child(2) { animation-delay: 50ms; }
.delight-card:nth-child(3) { animation-delay: 100ms; }
.delight-card:nth-child(4) { animation-delay: 150ms; }
.delight-card:nth-child(5) { animation-delay: 200ms; }
.delight-card:nth-child(6) { animation-delay: 250ms; }

.delight-hover {
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.delight-hover:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}

@keyframes delightSoftPulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}
.sidebar-active-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #3b82f6;
    display: inline-block;
    flex-shrink: 0;
    animation: delightSoftPulse 2.5s ease-in-out infinite;
}

.sidebar-logo-icon {
    transition: transform 0.5s ease;
}
.sidebar-logo-icon:hover {
    transform: rotate(360deg);
}

@media (prefers-reduced-motion: reduce) {
    .delight-card {
        animation: none !important;
        opacity: 1 !important;
    }
    .delight-hover:hover {
        transform: none !important;
    }
    .sidebar-active-dot {
        animation: none !important;
    }
    .sidebar-logo-icon:hover {
        transform: none !important;
    }
}
"""

# ── Sidebar JS ───────────────────────────────────────────────
SIDEBAR_JS = """
function toggleSection(id) {
  var section = document.getElementById(id);
  if (section) section.classList.toggle('open');
}
function showAllEntities(sectionId) {
  var section = document.getElementById(sectionId);
  if (section) section.classList.add('show-all');
}
function toggleSidebar() {
  var sidebar = document.getElementById('sidebar');
  var backdrop = document.getElementById('sidebar-backdrop');
  var isOpen = sidebar.classList.contains('open');
  if (isOpen) {
    sidebar.classList.remove('open');
    backdrop.classList.remove('visible');
  } else {
    sidebar.classList.add('open');
    backdrop.classList.add('visible');
  }
}
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    var sidebar = document.getElementById('sidebar');
    var backdrop = document.getElementById('sidebar-backdrop');
    if (sidebar) sidebar.classList.remove('open');
    if (backdrop) backdrop.classList.remove('visible');
  }
});
"""


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _row_to_dict(row):
    return dict(row) if row else None


def _time_ago(iso_str):
    """Convert ISO datetime string to human-readable '2 days ago' format."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        days = delta.days
        if days == 0:
            hours = delta.seconds // 3600
            if hours == 0:
                return "just now"
            return f"{hours}h ago"
        if days == 1:
            return "yesterday"
        if days < 30:
            return f"{days}d ago"
        months = days // 30
        return f"{months}mo ago"
    except Exception:
        return iso_str


def _esc(s):
    """HTML-escape a string."""
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_sidebar(active_page="hub"):
    """Build the sidebar HTML from current database state.

    active_page: 'hub', 'dashboard', or a filename like 'contact-daniel-byrne.html'
    """
    conn = _get_db()

    # Installed modules
    modules = [r["name"] for r in conn.execute(
        "SELECT name FROM modules WHERE enabled = 1"
    ).fetchall()]

    # Badge counts
    badge_counts = {}
    for row in conn.execute("""
        SELECT 'contacts' as section, COUNT(*) as count FROM contacts WHERE status = 'active'
        UNION ALL SELECT 'emails', COUNT(*) FROM emails
        UNION ALL SELECT 'calendar', COUNT(*) FROM calendar_events WHERE start_time > datetime('now', '-30 days')
        UNION ALL SELECT 'transcripts', COUNT(*) FROM transcripts
        UNION ALL SELECT 'decisions', COUNT(*) FROM decisions
        UNION ALL SELECT 'journal', COUNT(*) FROM journal_entries
        UNION ALL SELECT 'notes', COUNT(*) FROM standalone_notes
    """).fetchall():
        badge_counts[row["section"]] = row["count"]

    # Generated module views (filename → entity_name)
    gen_module_views = {}
    for r in conn.execute(
        "SELECT entity_name, filename FROM generated_views WHERE view_type = 'module_view'"
    ).fetchall():
        gen_module_views[r["filename"]] = r["entity_name"]

    # All generated view filenames for link-or-disable check
    gen_filenames = set()
    for r in conn.execute("SELECT filename FROM generated_views").fetchall():
        gen_filenames.add(r["filename"])

    # Contact entity pages
    contact_pages = conn.execute("""
        SELECT entity_id, entity_name, filename FROM generated_views
        WHERE view_type = 'entity_page' AND entity_type = 'contact'
        ORDER BY entity_name ASC
    """).fetchall()

    # Project entity pages
    project_pages = conn.execute("""
        SELECT entity_id, entity_name, filename FROM generated_views
        WHERE view_type = 'entity_page' AND entity_type = 'project'
        ORDER BY entity_name ASC
    """).fetchall()

    # All project-linked sub-views (PM reports, prep docs, etc.)
    project_sub_views = conn.execute("""
        SELECT entity_id, entity_name, filename, view_type FROM generated_views
        WHERE entity_type = 'project' AND view_type NOT IN ('entity_page')
        ORDER BY entity_name ASC
    """).fetchall()

    # Also fetch project names for sub-views that don't have an entity page yet
    all_projects = conn.execute("""
        SELECT id, name FROM projects ORDER BY name ASC
    """).fetchall()

    # Urgent nudge count
    urgent_count = 0
    try:
        row = conn.execute("""
            SELECT
              (SELECT COUNT(*) FROM follow_ups WHERE status = 'pending' AND due_date < date('now'))
              + (SELECT COUNT(*) FROM commitments WHERE status IN ('open','overdue') AND deadline_date < date('now'))
              + (SELECT COUNT(*) FROM tasks t JOIN projects p ON p.id = t.project_id WHERE t.status NOT IN ('done') AND t.due_date < date('now'))
              as urgent_count
        """).fetchone()
        if row:
            urgent_count = row["urgent_count"] or 0
    except Exception:
        pass

    conn.close()

    # Helper: render a sidebar item (link or disabled span)
    def _sidebar_item(filename, icon, label, badge=None, badge_alert=False):
        active_cls = " active" if active_page == filename else ""
        badge_html = ""
        if badge and badge > 0:
            alert_cls = " sidebar-badge-alert" if badge_alert else ""
            badge_html = f'<span class="sidebar-badge{alert_cls}">{badge}</span>'
        if filename in gen_filenames:
            return f'''<a href="/pages/{_esc(filename)}" class="sidebar-item{active_cls}">
              <i data-lucide="{icon}" class="w-4 h-4"></i>
              {_esc(label)}
              {badge_html}
            </a>'''
        else:
            return f'''<span class="sidebar-item sidebar-item-disabled" title="Run the command to generate this view">
              <i data-lucide="{icon}" class="w-4 h-4"></i>
              {_esc(label)}
            </span>'''

    # Helper: auditions sidebar item (uses /auditions route, not /pages/)
    def _sidebar_auditions():
        active_cls = " active" if active_page == "audition-board.html" else ""
        board_exists = "audition-board.html" in gen_filenames or os.path.isfile(os.path.join(OUTPUT_DIR, "audition-board.html"))
        if board_exists:
            return f'''<a href="/auditions" class="sidebar-item{active_cls}">
              <i data-lucide="mic" class="w-4 h-4"></i>
              Auditions
            </a>'''
        else:
            return '''<span class="sidebar-item sidebar-item-disabled" title="Run /audition-board to generate">
              <i data-lucide="mic" class="w-4 h-4"></i>
              Auditions
            </span>'''

    # Helper: render entity page links
    def _entity_links(pages, cap=10):
        html = ""
        for i, p in enumerate(pages):
            overflow = " sidebar-entity-overflow" if i >= cap else ""
            active_cls = " active" if active_page == p["filename"] else ""
            html += f'<a href="/pages/{_esc(p["filename"])}" class="sidebar-entity{active_cls}{overflow}">{_esc(p["entity_name"])}</a>\n'
        if len(pages) > cap:
            html += f'<button class="sidebar-show-all" onclick="showAllEntities(\'section-people\')">Show all ({len(pages)})</button>'
        return html

    # Determine which section should be open
    open_section = None
    if active_page == "hub":
        open_section = None  # No section auto-expanded on hub
    elif active_page == "dashboard.html":
        open_section = None
    elif any(active_page == p["filename"] for p in contact_pages):
        open_section = "section-people"
    elif active_page in ("contacts.html", "network-map.html"):
        open_section = "section-people"
    elif any(active_page == p["filename"] for p in project_pages) or \
         any(active_page == sv["filename"] for sv in project_sub_views):
        open_section = "section-projects"
    elif active_page in ("email-hub.html", "week-view.html"):
        open_section = "section-comms"
    elif active_page in ("conversations.html", "decision-journal.html", "journal.html", "notes.html"):
        open_section = "section-intelligence"
    elif active_page in ("weekly-review.html", "nudges.html", "timeline.html", "search.html", "audition-board.html"):
        open_section = "section-tools"

    def _section_open(section_id):
        return " open" if open_section == section_id else ""

    # Module names are stored lowercase/hyphenated in the DB
    mod_set = set(m.lower() for m in modules)
    has_crm = "crm" in mod_set
    has_projects = "project-tracker" in mod_set
    has_gmail = "gmail" in mod_set
    has_calendar = "calendar" in mod_set
    has_comms = has_gmail or has_calendar
    has_conversations = "conversation-intelligence" in mod_set
    has_decisions = "decision-log" in mod_set
    has_journal = "journal" in mod_set
    has_notes = "notes" in mod_set
    has_auditions = "auditions" in mod_set
    has_intel = has_conversations or has_decisions or has_journal or has_notes

    # Build sections
    people_section = ""
    if has_crm:
        contact_links = ""
        if contact_pages:
            contact_links = '<div class="sidebar-divider"></div>\n' + _entity_links(contact_pages)
        people_section = f'''
    <div class="sidebar-section{_section_open('section-people')}" id="section-people">
      <button class="sidebar-section-label" onclick="toggleSection('section-people')">
        <span>People</span>
        <i data-lucide="chevron-right" class="w-3.5 h-3.5 sidebar-chevron"></i>
      </button>
      <div class="sidebar-section-content">
        {_sidebar_item("contacts.html", "users", "Contacts", badge_counts.get("contacts"))}
        {_sidebar_item("network-map.html", "share-2", "Network Map")}
        {contact_links}
      </div>
    </div>'''

    projects_section = ""
    if has_projects:
        # Build a map of project_id -> {name, entity_page, sub_views}
        project_map = {}
        # Seed from entity pages
        for p in project_pages:
            pid = p["entity_id"]
            project_map[pid] = {
                "name": p["entity_name"],
                "entity_filename": p["filename"],
                "sub_views": [],
            }
        # Add sub-views (PM reports, prep docs, etc.)
        for sv in project_sub_views:
            pid = sv["entity_id"]
            if pid not in project_map:
                # Project has sub-views but no entity page — find name from projects table
                proj_name = sv["entity_name"]
                for proj in all_projects:
                    if proj["id"] == pid:
                        proj_name = proj["name"]
                        break
                project_map[pid] = {
                    "name": proj_name,
                    "entity_filename": None,
                    "sub_views": [],
                }
            project_map[pid]["sub_views"].append(sv)

        # Also add projects that have no views at all but exist in the DB
        # (skip — only show projects that have at least one generated view)

        # Render each project group
        view_type_labels = {
            "pm_report": "PM Report",
            "prep_page": "Prep Doc",
            "project_brief": "Brief",
            "project_analysis": "Analysis",
        }
        view_type_icons = {
            "pm_report": "brain",
            "prep_page": "clipboard-check",
            "project_brief": "file-text",
            "project_analysis": "scan-search",
        }

        project_links = ""
        for pid in sorted(project_map, key=lambda k: project_map[k]["name"]):
            pm = project_map[pid]
            # Main project link (entity page) or just a label
            if pm["entity_filename"]:
                active_cls = " active" if active_page == pm["entity_filename"] else ""
                project_links += f'<a href="/pages/{_esc(pm["entity_filename"])}" class="sidebar-entity{active_cls}">{_esc(pm["name"])}</a>\n'
            else:
                project_links += f'<span class="sidebar-entity sidebar-entity-nolink">{_esc(pm["name"])}</span>\n'
            # Sub-view links indented beneath
            for sv in pm["sub_views"]:
                active_cls = " active" if active_page == sv["filename"] else ""
                label = sv["entity_name"] or view_type_labels.get(sv["view_type"], sv["view_type"])
                icon = view_type_icons.get(sv["view_type"], "file")
                project_links += (
                    f'<a href="/pages/{_esc(sv["filename"])}" class="sidebar-subitem{active_cls}">'
                    f'<i data-lucide="{icon}" class="w-3 h-3"></i> {_esc(label)}</a>\n'
                )

        projects_section = f'''
    <div class="sidebar-section{_section_open('section-projects')}" id="section-projects">
      <button class="sidebar-section-label" onclick="toggleSection('section-projects')">
        <span>Projects</span>
        <i data-lucide="chevron-right" class="w-3.5 h-3.5 sidebar-chevron"></i>
      </button>
      <div class="sidebar-section-content">
        {project_links}
      </div>
    </div>'''

    comms_section = ""
    if has_comms:
        email_item = _sidebar_item("email-hub.html", "mail", "Email", badge_counts.get("emails")) if has_gmail else ""
        cal_item = _sidebar_item("week-view.html", "calendar", "Calendar", badge_counts.get("calendar")) if has_calendar else ""
        comms_section = f'''
    <div class="sidebar-section{_section_open('section-comms')}" id="section-comms">
      <button class="sidebar-section-label" onclick="toggleSection('section-comms')">
        <span>Comms</span>
        <i data-lucide="chevron-right" class="w-3.5 h-3.5 sidebar-chevron"></i>
      </button>
      <div class="sidebar-section-content">
        {email_item}
        {cal_item}
      </div>
    </div>'''

    intel_section = ""
    if has_intel:
        items = ""
        if has_conversations:
            items += _sidebar_item("conversations.html", "message-square", "Conversations")
        if has_decisions:
            items += _sidebar_item("decision-journal.html", "git-branch", "Decisions")
        if has_journal:
            items += _sidebar_item("journal.html", "book-open", "Journal")
        if has_notes:
            items += _sidebar_item("notes.html", "sticky-note", "Notes")
        intel_section = f'''
    <div class="sidebar-section{_section_open('section-intelligence')}" id="section-intelligence">
      <button class="sidebar-section-label" onclick="toggleSection('section-intelligence')">
        <span>Intelligence</span>
        <i data-lucide="chevron-right" class="w-3.5 h-3.5 sidebar-chevron"></i>
      </button>
      <div class="sidebar-section-content">
        {items}
      </div>
    </div>'''

    # Tools section (always present)
    nudge_badge = urgent_count if urgent_count > 0 else None
    tools_section = f'''
    <div class="sidebar-section{_section_open('section-tools')}" id="section-tools">
      <button class="sidebar-section-label" onclick="toggleSection('section-tools')">
        <span>Tools</span>
        <i data-lucide="chevron-right" class="w-3.5 h-3.5 sidebar-chevron"></i>
      </button>
      <div class="sidebar-section-content">
        {_sidebar_item("weekly-review.html", "clipboard-list", "Weekly Review")}
        {_sidebar_item("nudges.html", "bell", "Nudges", nudge_badge, badge_alert=True)}
        {_sidebar_item("timeline.html", "clock", "Timeline")}
        {_sidebar_item("search.html", "search", "Search")}
        {_sidebar_auditions() if has_auditions else ""}
      </div>
    </div>'''

    # Hub active state
    hub_active = " active" if active_page == "hub" else ""
    dash_active = " active" if active_page == "dashboard.html" else ""

    # Dashboard link
    dash_link = ""
    if "dashboard.html" in gen_filenames:
        dash_link = f'''<a href="/pages/dashboard.html" class="sidebar-item{dash_active}">
      <i data-lucide="layout-dashboard" class="w-4 h-4"></i>
      Dashboard
    </a>'''
    else:
        dash_link = '''<span class="sidebar-item sidebar-item-disabled" title="Run /dashboard to generate">
      <i data-lucide="layout-dashboard" class="w-4 h-4"></i>
      Dashboard
    </span>'''

    return f'''<aside id="sidebar" class="sidebar">
  <div class="sidebar-header">
    <a href="/" class="sidebar-logo">
      <i data-lucide="hexagon" class="w-4 h-4 sidebar-logo-icon"></i>
      <span>Software of You</span>
    </a>
  </div>
  <nav class="sidebar-nav">
    <a href="/" class="sidebar-item{hub_active}">
      <i data-lucide="home" class="w-4 h-4"></i>
      Hub
    </a>
    {dash_link}
    {people_section}
    {projects_section}
    {comms_section}
    {intel_section}
    {tools_section}
  </nav>
  <div style="padding:0.25rem 0.5rem;">
    <button class="dark-toggle" onclick="toggleDarkMode()" title="Toggle dark mode">
      <svg id="dark-icon-sun" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
      <svg id="dark-icon-moon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:none"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
      <span id="dark-toggle-label">Dark mode</span>
    </button>
  </div>
  <div class="sidebar-tip-zone">
    <div class="sidebar-tip">
      <p class="sidebar-tip-label">Tip</p>
      <p class="sidebar-tip-text">Use /help-soy to see all available commands.</p>
    </div>
  </div>
</aside>

<button id="sidebar-toggle" class="sidebar-mobile-toggle" onclick="toggleSidebar()">
  <i data-lucide="menu" class="w-5 h-5"></i>
</button>
<div id="sidebar-backdrop" class="sidebar-backdrop" onclick="toggleSidebar()"></div>'''


SUBNAV_CSS = """
.section-subnav {
  position: sticky;
  top: 0;
  z-index: 20;
  background: rgba(250,250,250,0.95);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-bottom: 1px solid #e4e4e7;
  padding: 0.5rem 0;
  margin-bottom: 1.5rem;
}
.section-pill {
  display: inline-flex;
  align-items: center;
  padding: 0.375rem 0.75rem;
  border-radius: 9999px;
  font-size: 0.75rem;
  font-weight: 500;
  color: #71717a;
  text-decoration: none;
  white-space: nowrap;
  transition: all 0.15s;
}
.section-pill:hover {
  color: #7c3aed;
  background: rgba(139, 92, 246, 0.08);
}
.section-pill.active {
  color: #7c3aed;
  background: rgba(139, 92, 246, 0.1);
}
.no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
.no-scrollbar::-webkit-scrollbar { display: none; }
"""

SUBNAV_JS = """
// Scrollspy for section pills
(function() {
  var pills = document.querySelectorAll('.section-pill');
  if (!pills.length) return;
  var pillSections = [];
  pills.forEach(function(pill) {
    var id = pill.getAttribute('href');
    if (!id || id.charAt(0) !== '#') return;
    var el = document.getElementById(id.substring(1));
    if (el) pillSections.push({ el: el, pill: pill });
  });
  function updatePills() {
    var scrollY = window.scrollY + 140;
    var current = pillSections[0];
    for (var i = 0; i < pillSections.length; i++) {
      if (pillSections[i].el.offsetTop <= scrollY) current = pillSections[i];
    }
    pills.forEach(function(p) { p.classList.remove('active'); });
    if (current) current.pill.classList.add('active');
  }
  window.addEventListener('scroll', updatePills);
  updatePills();
})();
"""

# ── Dark Mode CSS ─────────────────────────────────────────────
DARKMODE_CSS = """
/* ── DARK MODE OVERRIDES ── */
/* Surfaces */
html.dark body { background-color: #18181b !important; color: #fafafa !important; }
html.dark .bg-zinc-50, html.dark [class*="bg-zinc-50\\/"] { background-color: #18181b !important; }
html.dark .bg-white, html.dark [class*="bg-white\\/"] { background-color: #27272a !important; }
html.dark .bg-zinc-100 { background-color: #3f3f46 !important; }
html.dark .bg-zinc-200 { background-color: #52525b !important; }
html.dark .bg-zinc-900:not(pre):not(code) { background-color: #fafafa !important; }
html.dark pre.bg-zinc-900, html.dark pre[class*="bg-zinc-9"] { background-color: #0f0f14 !important; color: #e4e4e7 !important; }

/* Text */
html.dark .text-zinc-900 { color: #fafafa !important; }
html.dark .text-zinc-800 { color: #e4e4e7 !important; }
html.dark .text-zinc-700 { color: #d4d4d8 !important; }
html.dark .text-zinc-600 { color: #a1a1aa !important; }
html.dark .text-zinc-500 { color: #a1a1aa !important; }
html.dark .text-zinc-400 { color: #a1a1aa !important; }
html.dark .text-white { color: #18181b !important; }

/* Borders */
html.dark .border-zinc-200 { border-color: #3f3f46 !important; }
html.dark .border-zinc-100 { border-color: #3f3f46 !important; }
html.dark .border-zinc-300 { border-color: #52525b !important; }
html.dark .divide-zinc-200 > :not([hidden]) ~ :not([hidden]) { border-color: #3f3f46 !important; }
html.dark .divide-zinc-100 > :not([hidden]) ~ :not([hidden]) { border-color: #3f3f46 !important; }

/* Shadows */
html.dark [class*="shadow"] { --tw-shadow-color: rgba(0,0,0,0.3) !important; }

/* Accent tint backgrounds */
html.dark .bg-blue-50 { background-color: rgba(59,130,246,0.12) !important; }
html.dark .bg-green-50 { background-color: rgba(34,197,94,0.12) !important; }
html.dark .bg-amber-50 { background-color: rgba(245,158,11,0.12) !important; }
html.dark .bg-red-50 { background-color: rgba(239,68,68,0.12) !important; }
html.dark .bg-purple-50 { background-color: rgba(168,85,247,0.12) !important; }
html.dark .bg-violet-50 { background-color: rgba(139,92,246,0.12) !important; }
html.dark .bg-indigo-50 { background-color: rgba(99,102,241,0.12) !important; }
html.dark .bg-orange-50 { background-color: rgba(249,115,22,0.12) !important; }
html.dark .bg-yellow-50 { background-color: rgba(234,179,8,0.12) !important; }
html.dark .bg-emerald-50 { background-color: rgba(16,185,129,0.12) !important; }
html.dark .bg-teal-50 { background-color: rgba(20,184,166,0.12) !important; }
html.dark .bg-cyan-50 { background-color: rgba(6,182,212,0.12) !important; }
html.dark .bg-sky-50 { background-color: rgba(14,165,233,0.12) !important; }
html.dark .bg-rose-50 { background-color: rgba(244,63,94,0.12) !important; }
html.dark .bg-pink-50 { background-color: rgba(236,72,153,0.12) !important; }
html.dark .bg-fuchsia-50 { background-color: rgba(192,38,211,0.12) !important; }

/* Accent -100 backgrounds */
html.dark .bg-blue-100 { background-color: rgba(59,130,246,0.2) !important; }
html.dark .bg-green-100 { background-color: rgba(34,197,94,0.2) !important; }
html.dark .bg-emerald-100 { background-color: rgba(16,185,129,0.2) !important; }
html.dark .bg-violet-100 { background-color: rgba(139,92,246,0.2) !important; }
html.dark .bg-purple-100 { background-color: rgba(168,85,247,0.2) !important; }
html.dark .bg-amber-100 { background-color: rgba(245,158,11,0.2) !important; }
html.dark .bg-red-100 { background-color: rgba(239,68,68,0.2) !important; }
html.dark .bg-indigo-100 { background-color: rgba(99,102,241,0.2) !important; }
html.dark .bg-orange-100 { background-color: rgba(249,115,22,0.2) !important; }
html.dark .bg-yellow-100 { background-color: rgba(234,179,8,0.2) !important; }
html.dark .bg-teal-100 { background-color: rgba(20,184,166,0.2) !important; }
html.dark .bg-cyan-100 { background-color: rgba(6,182,212,0.2) !important; }
html.dark .bg-sky-100 { background-color: rgba(14,165,233,0.2) !important; }
html.dark .bg-rose-100 { background-color: rgba(244,63,94,0.2) !important; }
html.dark .bg-pink-100 { background-color: rgba(236,72,153,0.2) !important; }
html.dark .bg-fuchsia-100 { background-color: rgba(192,38,211,0.2) !important; }

/* Accent -200 backgrounds */
html.dark .bg-blue-200 { background-color: rgba(59,130,246,0.25) !important; }
html.dark .bg-green-200 { background-color: rgba(34,197,94,0.25) !important; }
html.dark .bg-violet-200 { background-color: rgba(139,92,246,0.25) !important; }

/* Gradient overrides — must override background-image directly since
   Tailwind CDN bakes the gradient inline and CSS var !important won't propagate */
html.dark .bg-gradient-to-b.from-blue-50.to-white { background-image: linear-gradient(to bottom, rgba(59,130,246,0.12), #27272a) !important; }
html.dark .bg-gradient-to-b.from-green-50.to-white { background-image: linear-gradient(to bottom, rgba(34,197,94,0.12), #27272a) !important; }
html.dark .bg-gradient-to-b.from-amber-50.to-white { background-image: linear-gradient(to bottom, rgba(245,158,11,0.12), #27272a) !important; }
html.dark .bg-gradient-to-b.from-violet-50.to-white { background-image: linear-gradient(to bottom, rgba(139,92,246,0.12), #27272a) !important; }
html.dark .bg-gradient-to-b.from-purple-50.to-white { background-image: linear-gradient(to bottom, rgba(168,85,247,0.12), #27272a) !important; }
html.dark .bg-gradient-to-b.from-emerald-50.to-white { background-image: linear-gradient(to bottom, rgba(16,185,129,0.12), #27272a) !important; }
html.dark .bg-gradient-to-b.from-red-50.to-white { background-image: linear-gradient(to bottom, rgba(239,68,68,0.12), #27272a) !important; }
html.dark .bg-gradient-to-r.from-blue-50.to-white { background-image: linear-gradient(to right, rgba(59,130,246,0.12), #27272a) !important; }
html.dark .bg-gradient-to-r.from-green-50.to-white { background-image: linear-gradient(to right, rgba(34,197,94,0.12), #27272a) !important; }
html.dark .bg-gradient-to-r.from-violet-50.to-white { background-image: linear-gradient(to right, rgba(139,92,246,0.12), #27272a) !important; }
/* Catch-all: any gradient ending in to-white on a bg-white-overridden surface */
html.dark .bg-gradient-to-b.to-white { --tw-gradient-to: #27272a !important; }
html.dark .bg-gradient-to-r.to-white { --tw-gradient-to: #27272a !important; }
html.dark .bg-gradient-to-b.to-zinc-50 { --tw-gradient-to: #18181b !important; }
html.dark .bg-gradient-to-br.from-blue-50.to-white { background-image: linear-gradient(to bottom right, rgba(59,130,246,0.12), #27272a) !important; }
html.dark .bg-gradient-to-br.from-violet-50.to-white { background-image: linear-gradient(to bottom right, rgba(139,92,246,0.12), #27272a) !important; }

/* Accent border colors */
html.dark .border-blue-100 { border-color: rgba(59,130,246,0.25) !important; }
html.dark .border-blue-200 { border-color: rgba(59,130,246,0.3) !important; }
html.dark .border-green-100 { border-color: rgba(34,197,94,0.25) !important; }
html.dark .border-green-200 { border-color: rgba(34,197,94,0.3) !important; }
html.dark .border-emerald-100 { border-color: rgba(16,185,129,0.25) !important; }
html.dark .border-emerald-200 { border-color: rgba(16,185,129,0.3) !important; }
html.dark .border-violet-100 { border-color: rgba(139,92,246,0.25) !important; }
html.dark .border-violet-200 { border-color: rgba(139,92,246,0.3) !important; }
html.dark .border-amber-100 { border-color: rgba(245,158,11,0.25) !important; }
html.dark .border-amber-200 { border-color: rgba(245,158,11,0.3) !important; }
html.dark .border-red-100 { border-color: rgba(239,68,68,0.25) !important; }
html.dark .border-red-200 { border-color: rgba(239,68,68,0.3) !important; }

/* Accent text: lighten -700 and -800 variants for dark bg contrast */
html.dark .text-blue-700 { color: #93c5fd !important; }
html.dark .text-blue-800 { color: #93c5fd !important; }
html.dark .text-blue-600 { color: #60a5fa !important; }
html.dark .text-green-700 { color: #86efac !important; }
html.dark .text-green-600 { color: #4ade80 !important; }
html.dark .text-emerald-700 { color: #6ee7b7 !important; }
html.dark .text-emerald-600 { color: #34d399 !important; }
html.dark .text-emerald-500 { color: #34d399 !important; }
html.dark .text-violet-700 { color: #c4b5fd !important; }
html.dark .text-violet-600 { color: #a78bfa !important; }
html.dark .text-purple-700 { color: #d8b4fe !important; }
html.dark .text-purple-600 { color: #c084fc !important; }
html.dark .text-amber-800 { color: #fcd34d !important; }
html.dark .text-amber-700 { color: #fcd34d !important; }
html.dark .text-amber-600 { color: #fbbf24 !important; }
html.dark .text-red-800 { color: #fca5a5 !important; }
html.dark .text-red-700 { color: #fca5a5 !important; }
html.dark .text-red-600 { color: #f87171 !important; }
html.dark .text-green-800 { color: #86efac !important; }
html.dark .text-green-700 { color: #86efac !important; }
html.dark .text-orange-700 { color: #fdba74 !important; }
html.dark .text-orange-600 { color: #fb923c !important; }

/* Callout cards (inline <style> backgrounds that Tailwind selectors can't catch) */
html.dark .callout-blue { background: rgba(59,130,246,0.1) !important; border-left-color: #60a5fa !important; }
html.dark .callout-amber { background: rgba(245,158,11,0.1) !important; border-left-color: #fbbf24 !important; }
html.dark .callout-red { background: rgba(239,68,68,0.1) !important; border-left-color: #f87171 !important; }
html.dark .callout-green { background: rgba(34,197,94,0.1) !important; border-left-color: #4ade80 !important; }
html.dark .callout-blue, html.dark .callout-blue p, html.dark .callout-blue li { color: #bfdbfe !important; }
html.dark .callout-amber, html.dark .callout-amber p, html.dark .callout-amber li { color: #fde68a !important; }
html.dark .callout-red, html.dark .callout-red p, html.dark .callout-red li { color: #fecaca !important; }
html.dark .callout-green, html.dark .callout-green p, html.dark .callout-green li { color: #bbf7d0 !important; }
html.dark .callout-blue strong, html.dark .callout-amber strong, html.dark .callout-red strong, html.dark .callout-green strong { color: #fafafa !important; }

/* Opacity modifier backgrounds (Tailwind bg-*/50 syntax) */
html.dark [class*="bg-blue-50\\/"] { background-color: rgba(59,130,246,0.08) !important; }
html.dark [class*="bg-green-50\\/"] { background-color: rgba(34,197,94,0.08) !important; }
html.dark [class*="bg-emerald-50\\/"] { background-color: rgba(16,185,129,0.08) !important; }

/* Border-l accent colors */
html.dark .border-l-blue-200, html.dark [class*="border-blue-200"] { border-color: rgba(59,130,246,0.3) !important; }

/* Opacity-modifier accent backgrounds (Tailwind bg-color-shade/opacity) */
html.dark [class*="bg-rose-50\\/"] { background-color: rgba(244,63,94,0.08) !important; }
html.dark [class*="bg-violet-50\\/"] { background-color: rgba(139,92,246,0.08) !important; }
html.dark [class*="bg-purple-50\\/"] { background-color: rgba(168,85,247,0.08) !important; }
html.dark [class*="bg-amber-50\\/"] { background-color: rgba(245,158,11,0.08) !important; }
html.dark [class*="bg-indigo-50\\/"] { background-color: rgba(99,102,241,0.08) !important; }

/* Rose accent borders */
html.dark .border-rose-100 { border-color: rgba(244,63,94,0.25) !important; }
html.dark .border-rose-200 { border-color: rgba(244,63,94,0.3) !important; }
html.dark .border-rose-300 { border-color: rgba(244,63,94,0.4) !important; }
html.dark .border-pink-100 { border-color: rgba(236,72,153,0.25) !important; }
html.dark .border-pink-200 { border-color: rgba(236,72,153,0.3) !important; }
html.dark .border-purple-100 { border-color: rgba(168,85,247,0.25) !important; }
html.dark .border-purple-200 { border-color: rgba(168,85,247,0.3) !important; }
html.dark .border-indigo-100 { border-color: rgba(99,102,241,0.25) !important; }
html.dark .border-indigo-200 { border-color: rgba(99,102,241,0.3) !important; }

/* Rose/pink accent text */
html.dark .text-rose-500 { color: #fb7185 !important; }
html.dark .text-rose-600 { color: #fb7185 !important; }
html.dark .text-rose-700 { color: #fda4af !important; }
html.dark .text-pink-500 { color: #f472b6 !important; }
html.dark .text-pink-600 { color: #f472b6 !important; }
html.dark .text-indigo-600 { color: #818cf8 !important; }
html.dark .text-indigo-700 { color: #a5b4fc !important; }
html.dark .text-violet-500 { color: #a78bfa !important; }

/* PM Report outcome badges (hardcoded in page <style>, need html.dark override) */
html.dark .outcome-completed { background: rgba(16,185,129,0.15) !important; color: #6ee7b7 !important; }
html.dark .outcome-partial { background: rgba(245,158,11,0.15) !important; color: #fcd34d !important; }
html.dark .outcome-blocked { background: rgba(239,68,68,0.15) !important; color: #fca5a5 !important; }
html.dark .outcome-none { background: rgba(63,63,70,0.5) !important; color: #a1a1aa !important; }

/* PM Report sidebar-link (inline styles in page) */
html.dark .sidebar-link { color: #a1a1aa !important; }
html.dark .sidebar-link:hover { background: rgba(139,92,246,0.15) !important; color: #a78bfa !important; }
html.dark .sidebar-link.active { background: rgba(139,92,246,0.15) !important; color: #a78bfa !important; }

/* Delight layer dark overrides */
html.dark .delight-row:hover { background-color: rgba(63,63,70,0.5) !important; }
html.dark .delight-hover:hover { box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3) !important; }

/* Sidebar */
html.dark .sidebar { background: #1f1f23 !important; border-right-color: #3f3f46 !important; }
html.dark .sidebar-header { border-bottom-color: #3f3f46 !important; }
html.dark .sidebar-logo { color: #e4e4e7 !important; }
html.dark .sidebar-item { color: #a1a1aa !important; }
html.dark .sidebar-item:hover { background: #3f3f46 !important; color: #fafafa !important; }
html.dark .sidebar-item.active { background: rgba(59,130,246,0.15) !important; color: #60a5fa !important; }
html.dark .sidebar-entity { color: #a1a1aa !important; }
html.dark .sidebar-entity:hover { background: #3f3f46 !important; color: #fafafa !important; }
html.dark .sidebar-entity.active { background: rgba(59,130,246,0.15) !important; color: #60a5fa !important; }
html.dark .sidebar-subitem { color: #a1a1aa !important; }
html.dark .sidebar-subitem:hover { background: #3f3f46 !important; color: #d4d4d8 !important; }
html.dark .sidebar-subitem.active { background: rgba(59,130,246,0.15) !important; color: #60a5fa !important; }
html.dark .sidebar-badge { background: #3f3f46 !important; color: #a1a1aa !important; }
html.dark .sidebar-badge-alert { background: rgba(239,68,68,0.2) !important; color: #f87171 !important; }
html.dark .sidebar-divider { background: #3f3f46 !important; }
html.dark .sidebar-section-label { color: #a1a1aa !important; }
html.dark .sidebar-section-label:hover { color: #d4d4d8 !important; }
html.dark .sidebar-tip-zone { border-top-color: #3f3f46 !important; }
html.dark .sidebar-tip { background: #27272a !important; }
html.dark .sidebar-tip-label { color: #a1a1aa !important; }
html.dark .sidebar-tip-text { color: #a1a1aa !important; }
html.dark .sidebar-mobile-toggle { background: #27272a !important; border-color: #3f3f46 !important; color: #a1a1aa !important; }
html.dark .sidebar-backdrop { background: rgba(0,0,0,0.6) !important; }

/* Subnav */
html.dark .section-subnav { background: rgba(24,24,27,0.95) !important; border-bottom-color: #3f3f46 !important; }
html.dark .section-pill { color: #a1a1aa !important; }
html.dark .section-pill:hover { color: #a78bfa !important; background: rgba(139,92,246,0.15) !important; }
html.dark .section-pill.active { color: #a78bfa !important; background: rgba(139,92,246,0.15) !important; }

/* Hover states */
html.dark .hover\\:bg-zinc-50:hover { background-color: #3f3f46 !important; }
html.dark .hover\\:bg-zinc-100:hover { background-color: #52525b !important; }
html.dark .hover\\:bg-white:hover { background-color: #3f3f46 !important; }
html.dark .hover\\:border-zinc-300:hover { border-color: #52525b !important; }
html.dark .hover\\:border-blue-300:hover { border-color: rgba(59,130,246,0.4) !important; }
html.dark .hover\\:border-violet-300:hover { border-color: rgba(139,92,246,0.4) !important; }
html.dark .hover\\:text-zinc-700:hover { color: #d4d4d8 !important; }

/* Inputs and code blocks */
html.dark input, html.dark textarea, html.dark select { background-color: #27272a !important; border-color: #3f3f46 !important; color: #fafafa !important; }
html.dark code { background-color: #3f3f46 !important; color: #e4e4e7 !important; }
html.dark pre { background-color: #27272a !important; color: #e4e4e7 !important; }

/* Tables */
html.dark th { background-color: #27272a !important; color: #d4d4d8 !important; border-color: #3f3f46 !important; }
html.dark td { border-color: #3f3f46 !important; }
html.dark tr:hover td { background-color: rgba(63,63,70,0.5) !important; }

/* Ring / focus */
html.dark .ring-zinc-200 { --tw-ring-color: #3f3f46 !important; }
html.dark .ring-zinc-100 { --tw-ring-color: #3f3f46 !important; }

/* Dark toggle button */
.dark-toggle {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.75rem;
  border-radius: 0.375rem;
  font-size: 0.8125rem;
  color: #71717a;
  cursor: pointer;
  background: none;
  border: none;
  font-family: inherit;
  width: 100%;
  transition: all 0.15s;
}
.dark-toggle:hover { background: #f4f4f5; color: #18181b; }
html.dark .dark-toggle { color: #a1a1aa !important; }
html.dark .dark-toggle:hover { background: #3f3f46 !important; color: #fafafa !important; }
.dark-toggle svg { width: 1rem; height: 1rem; flex-shrink: 0; }
"""

# ── Dark Mode JS ──────────────────────────────────────────────
DARKMODE_JS = """
// Dark mode toggle
(function() {
  function updateDarkToggleIcon() {
    var sunIcon = document.getElementById('dark-icon-sun');
    var moonIcon = document.getElementById('dark-icon-moon');
    var label = document.getElementById('dark-toggle-label');
    if (!sunIcon || !moonIcon) return;
    var isDark = document.documentElement.classList.contains('dark');
    sunIcon.style.display = isDark ? 'none' : 'block';
    moonIcon.style.display = isDark ? 'block' : 'none';
    if (label) label.textContent = isDark ? 'Light mode' : 'Dark mode';
  }
  window.toggleDarkMode = function() {
    var isDark = document.documentElement.classList.toggle('dark');
    localStorage.setItem('soy-dark-mode', isDark ? 'dark' : 'light');
    updateDarkToggleIcon();
  };
  // System preference listener (only applies when user hasn't explicitly chosen)
  try {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
      if (!localStorage.getItem('soy-dark-mode')) {
        if (e.matches) {
          document.documentElement.classList.add('dark');
        } else {
          document.documentElement.classList.remove('dark');
        }
        updateDarkToggleIcon();
      }
    });
  } catch(e) {}
  // Update icon on load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', updateDarkToggleIcon);
  } else {
    updateDarkToggleIcon();
  }
})();
"""

# ── Dark Mode Init (synchronous, prevents FOUC) ──────────────
DARKMODE_INIT = """<script>(function(){var s=localStorage.getItem('soy-dark-mode');if(s==='dark'||(s!=='light'&&window.matchMedia('(prefers-color-scheme:dark)').matches)){document.documentElement.classList.add('dark')}})()</script>"""


def _inject_sidebar_into_page(html, filename):
    """Inject the global SoY sidebar into a served page.

    - Builds a fresh sidebar from the DB with the correct active state
    - Detects existing in-page sidebars (PM reports) and converts them
      to a horizontal sticky sub-nav within the content area
    - Adds sidebar CSS/JS if not present
    - Adjusts <main> margin for sidebar offset
    """
    sidebar_html = _build_sidebar(active_page=filename)

    # ── Strip any existing hardcoded sidebar ───────────────────
    # Pages generated before server-side injection have stale sidebars
    # that are missing new pages, Hub link, etc. Always replace them.
    if 'id="sidebar"' in html:
        html = re.sub(
            r'<aside\s+id="sidebar"[^>]*>.*?</aside>',
            '', html, count=1, flags=re.DOTALL,
        )

    # ── Handle existing in-page sidebar (PM reports) ─────────
    # These are section-navigation asides (not id="sidebar"), like
    # the fixed left nav in PM reports with anchor links.
    in_page_nav = ""
    aside_match = re.search(
        r'<aside\s[^>]*class="fixed[^"]*"[^>]*>.*?</aside>',
        html, re.DOTALL,
    )
    if aside_match:
        aside_html = aside_match.group()
        # Extract anchor links: <a href="#id" ...><i ...></i> Label</a>
        nav_links = re.findall(
            r'<a\s+href="#([^"]+)"[^>]*>\s*(?:<i[^>]*></i>\s*)?([^<]+)',
            aside_html,
        )
        if nav_links:
            pills = []
            for href_id, label in nav_links:
                pills.append(
                    f'<a href="#{_esc(href_id.strip())}" '
                    f'class="section-pill">{_esc(label.strip())}</a>'
                )
            in_page_nav = (
                '<div class="section-subnav">'
                '<div class="flex items-center gap-1 overflow-x-auto no-scrollbar">'
                + "".join(pills)
                + "</div></div>"
            )
        # Remove the old aside
        html = html[: aside_match.start()] + html[aside_match.end() :]

    # ── Inject dark mode init script in <head> (prevents FOUC) ─
    if "soy-dark-mode" not in html:
        html = html.replace("<head>", "<head>\n" + DARKMODE_INIT, 1)

    # ── Inject sidebar CSS ────────────────────────────────────
    # Always strip any stale sidebar CSS and inject the current version.
    # Pages with hardcoded sidebars have old CSS missing new rules.
    if ".sidebar-subitem" not in html:
        # Old sidebar CSS exists but is incomplete — replace it
        if ".sidebar {" in html or ".sidebar{" in html:
            html = re.sub(
                r'(/\*\s*sidebar\s*\*/\s*)?\.sidebar\s*\{[^}]*\}.*?(?=\n\s*</style>|\n\s*/\*(?!.*sidebar))',
                '', html, count=1, flags=re.DOTALL | re.IGNORECASE,
            )
        css_block = f"<style>{SIDEBAR_CSS}\n{SUBNAV_CSS}\n{DARKMODE_CSS}</style>"
        html = html.replace("</head>", css_block + "\n</head>", 1)
    elif ".section-subnav" not in html and in_page_nav:
        html = html.replace("</style>", SUBNAV_CSS + "\n</style>", 1)

    # Inject dark mode CSS if not already present (sidebar CSS was already there)
    if "DARK MODE OVERRIDES" not in html:
        # Find last </style> and inject before it
        last_style = html.rfind("</style>")
        if last_style > 0:
            html = html[:last_style] + "\n" + DARKMODE_CSS + "\n" + html[last_style:]

    # ── Inject sidebar HTML after <body> ──────────────────────
    body_match = re.search(r"<body[^>]*>", html)
    if body_match:
        pos = body_match.end()
        html = html[:pos] + "\n" + sidebar_html + "\n" + html[pos:]

    # ── Adjust <main> margin ──────────────────────────────────
    # Replace any existing ml-56 (PM reports) with ml-60
    html = html.replace("lg:ml-56", "lg:ml-60")
    # If page has no <main> tag at all, wrap first child div after <body> in <main>
    if not re.search(r"<main[\s>]", html):
        body_m = re.search(r"(<body[^>]*>)\s*", html)
        if body_m:
            pos = body_m.end()
            html = html[:pos] + '\n<main class="lg:ml-60">\n' + html[pos:]
            # Close </main> before </body>
            html = html.replace("</body>", "</main>\n</body>", 1)
    # Add lg:ml-60 if main doesn't have it
    elif "lg:ml-60" not in html:
        # main with existing class
        html, n = re.subn(
            r'<main\s+class="([^"]*)"',
            lambda m: f'<main class="lg:ml-60 {m.group(1)}"',
            html, count=1,
        )
        if n == 0:
            html = re.sub(
                r"<main(?=[\s>])", '<main class="lg:ml-60"', html, count=1
            )

    # ── Inject in-page sub-nav at top of main content ─────────
    if in_page_nav:
        # Insert after the first <div> inside <main>
        main_div_match = re.search(
            r"(<main[^>]*>\s*<div[^>]*>)", html
        )
        if main_div_match:
            pos = main_div_match.end()
            html = html[:pos] + "\n" + in_page_nav + "\n" + html[pos:]

    # ── Inject sidebar + subnav + dark mode JS if not present ──
    if "toggleSidebar" not in html:
        js_block = f"<script>\n{SIDEBAR_JS}\n{SUBNAV_JS}\n{DARKMODE_JS}\n</script>"
        html = html.replace("</body>", js_block + "\n</body>", 1)
    elif ".section-pill" not in html and in_page_nav:
        # Sidebar JS exists but subnav JS doesn't — add subnav JS
        last_script = html.rfind("</script>")
        if last_script > 0:
            html = html[:last_script] + "\n" + SUBNAV_JS + "\n" + html[last_script:]

    # Inject dark mode JS if not already present
    if "toggleDarkMode" not in html:
        last_script = html.rfind("</script>")
        if last_script > 0:
            html = html[:last_script] + "\n" + DARKMODE_JS + "\n" + html[last_script:]

    return html


def _render_hub():
    """Render the hub home page HTML from current generated_views state."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM generated_views ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()

    views = [_row_to_dict(r) for r in rows]

    # Categorize views — split entity pages by entity_type
    prep_docs = [v for v in views if v["view_type"] == "prep_page"]
    contact_pages = [v for v in views if v["view_type"] == "entity_page" and v.get("entity_type") == "contact"]
    project_entity_pages = [v for v in views if v["view_type"] == "entity_page" and v.get("entity_type") == "project"]
    project_briefs = [v for v in views if v["view_type"] == "project_brief"]
    dashboards = [v for v in views if v["view_type"] == "dashboard"]
    tool_pages = [v for v in views if v["view_type"] == "tool_page"]
    pm_reports = [v for v in views if v["view_type"] == "pm_report"]
    project_analyses = [v for v in views if v["view_type"] == "project_analysis"]
    other = [
        v
        for v in views
        if v["view_type"]
        not in ("prep_page", "entity_page", "project_brief", "dashboard", "tool_page", "pm_report", "project_analysis")
    ]

    def _view_card(v, icon="file-text", color="zinc", sub_items=None):
        """Render a single view card, optionally with sub-item links."""
        name = _esc(v["entity_name"] or v["filename"])
        time_ago = _time_ago(v["updated_at"])
        vtype = v["view_type"].replace("_", " ").title()
        if v["filename"] == "audition-board.html":
            href = "/auditions"
        else:
            href = f"/pages/{_esc(v['filename'])}"
        sub_html = ""
        if sub_items:
            sub_links = ""
            for s in sub_items:
                s_name = s["view_type"].replace("_", " ").title()
                s_href = f"/pages/{_esc(s['filename'])}"
                s_time = _time_ago(s["updated_at"])
                sub_links += f'''
                <a href="{s_href}" class="flex items-center gap-1.5 text-xs text-purple-600 hover:text-purple-800 hover:underline">
                    <i data-lucide="brain" class="w-3 h-3"></i>
                    {s_name}
                    <span class="text-zinc-400 ml-auto">{s_time}</span>
                </a>'''
            sub_html = f'<div class="mt-2 pt-2 border-t border-zinc-100 space-y-1">{sub_links}</div>'
        return f"""
        <a href="{href}" class="group bg-white rounded-xl border border-zinc-200 p-4 hover:shadow-md hover:border-zinc-300 transition-all block delight-card delight-hover">
            <div class="flex items-start justify-between mb-2">
                <div class="w-9 h-9 rounded-lg bg-{color}-50 flex items-center justify-center">
                    <i data-lucide="{icon}" class="w-4.5 h-4.5 text-{color}-600"></i>
                </div>
                <span class="text-xs text-zinc-400">{time_ago}</span>
            </div>
            <p class="text-sm font-semibold text-zinc-900 group-hover:text-zinc-700">{name}</p>
            <p class="text-xs text-zinc-400 mt-0.5">{vtype}</p>
        </a>"""

    _sub_icon_map = {
        "pm_report": ("brain", "purple"),
        "project_brief": ("file-text", "zinc"),
        "prep_page": ("clipboard-check", "amber"),
        "project_analysis": ("scan-search", "indigo"),
    }

    def _project_card(v, sub_items=None):
        """Render a project card with optional sub-view links."""
        name = _esc(v["entity_name"] or v["filename"])
        time_ago = _time_ago(v["updated_at"])
        vtype = v["view_type"].replace("_", " ").title()
        href = f"/pages/{_esc(v['filename'])}"
        sub_html = ""
        if sub_items:
            sub_links = ""
            for s in sub_items:
                s_label = s["view_type"].replace("_", " ").title()
                s_href = f"/pages/{_esc(s['filename'])}"
                s_time = _time_ago(s["updated_at"])
                s_icon, s_color = _sub_icon_map.get(s["view_type"], ("file", "purple"))
                sub_links += f'''
                <a href="{s_href}" class="flex items-center gap-1.5 text-xs text-{s_color}-600 hover:text-{s_color}-800 hover:underline py-0.5">
                    <i data-lucide="{s_icon}" class="w-3 h-3 flex-shrink-0"></i>
                    <span>{s_label}</span>
                    <span class="text-zinc-400 ml-auto">{s_time}</span>
                </a>'''
            sub_html = f'<div class="mt-2 pt-2 border-t border-zinc-100 space-y-1">{sub_links}</div>'
        return f"""
        <div class="bg-white rounded-xl border border-zinc-200 p-4 hover:shadow-md hover:border-zinc-300 transition-all delight-card delight-hover">
            <a href="{href}" class="block group">
                <div class="flex items-start justify-between mb-2">
                    <div class="w-9 h-9 rounded-lg bg-green-50 flex items-center justify-center">
                        <i data-lucide="folder-open" class="w-4.5 h-4.5 text-green-600"></i>
                    </div>
                    <span class="text-xs text-zinc-400">{time_ago}</span>
                </div>
                <p class="text-sm font-semibold text-zinc-900 group-hover:text-zinc-700">{name}</p>
                <p class="text-xs text-zinc-400 mt-0.5">{vtype}</p>
            </a>
            {sub_html}
        </div>"""

    def _section(title, icon, cards_html):
        if not cards_html:
            return ""
        return f"""
        <section class="mb-8">
            <div class="flex items-center gap-2 mb-3">
                <i data-lucide="{icon}" class="w-4 h-4 text-zinc-400"></i>
                <h2 class="text-sm font-semibold text-zinc-500 uppercase tracking-wide">{title}</h2>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {cards_html}
            </div>
        </section>"""

    # Quick Actions — always shown
    quick_actions = """
        <a href="/auditions" class="group bg-white rounded-xl border border-zinc-200 p-4 hover:shadow-md hover:border-violet-300 transition-all block delight-card delight-hover">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-lg bg-violet-50 flex items-center justify-center">
                    <i data-lucide="clapperboard" class="w-5 h-5 text-violet-600"></i>
                </div>
                <div>
                    <p class="text-sm font-semibold text-zinc-900">Audition Board</p>
                    <p class="text-xs text-zinc-400">Kanban pipeline</p>
                </div>
            </div>
        </a>"""

    for d in dashboards:
        quick_actions += f"""
        <a href="/pages/{_esc(d['filename'])}" class="group bg-white rounded-xl border border-zinc-200 p-4 hover:shadow-md hover:border-blue-300 transition-all block delight-card delight-hover">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center">
                    <i data-lucide="layout-dashboard" class="w-5 h-5 text-blue-600"></i>
                </div>
                <div>
                    <p class="text-sm font-semibold text-zinc-900">Dashboard</p>
                    <p class="text-xs text-zinc-400">Overview &amp; metrics</p>
                </div>
            </div>
        </a>"""

    # Build Prep Docs section
    prep_cards = "\n".join(_view_card(v, "file-text", "amber") for v in prep_docs)

    # Build People section (contacts only)
    people_cards = "\n".join(_view_card(v, "user", "blue") for v in contact_pages)

    # Build Projects section — group entity pages, briefs, and PM reports by entity_id
    # Collect all project-related views by entity_id
    project_groups = {}  # entity_id → {"main": view, "subs": [pm_reports]}
    for v in project_entity_pages:
        eid = v.get("entity_id")
        if eid not in project_groups:
            project_groups[eid] = {"main": v, "subs": []}
        else:
            project_groups[eid]["main"] = v
    for v in project_briefs:
        eid = v.get("entity_id")
        if eid and eid not in project_groups:
            project_groups[eid] = {"main": v, "subs": []}
        elif eid:
            # Only set main if no entity page exists yet
            if not project_groups[eid]["main"]:
                project_groups[eid]["main"] = v
    for v in pm_reports + project_analyses:
        eid = v.get("entity_id")
        if eid and eid in project_groups:
            project_groups[eid]["subs"].append(v)
        elif eid:
            project_groups[eid] = {"main": None, "subs": [v]}
        else:
            # Orphan sub-view (no entity_id) — will be rendered standalone
            if None not in project_groups:
                project_groups[None] = {"main": None, "subs": []}
            project_groups[None]["subs"].append(v)

    project_cards = ""
    for eid, group in project_groups.items():
        if group["main"]:
            project_cards += _project_card(group["main"], group["subs"])
        else:
            # Orphan sub-items without a main project page — render as standalone cards
            for s in group["subs"]:
                project_cards += _view_card(s, "brain", "purple")

    # Build Other section
    other_cards = "\n".join(_view_card(v, "file", "zinc") for v in other)

    # Assemble sections
    sections_html = ""
    sections_html += _section("Prep Docs", "briefcase", prep_cards)
    sections_html += _section("People", "users", people_cards)
    sections_html += _section("Projects", "folder", project_cards)
    sections_html += _section("Other Views", "layers", other_cards)

    has_content = any([prep_docs, contact_pages, project_entity_pages, project_briefs, pm_reports, project_analyses, other])
    empty_state = ""
    if not has_content:
        empty_state = """
        <div class="text-center py-16">
            <div class="w-16 h-16 rounded-2xl bg-zinc-100 flex items-center justify-center mx-auto mb-4">
                <i data-lucide="compass" class="w-8 h-8 text-zinc-400"></i>
            </div>
            <p class="text-zinc-500 mb-1">Your hub will fill up as you use SoY.</p>
            <p class="text-sm text-zinc-400">Try <code class="bg-zinc-100 px-1.5 py-0.5 rounded text-xs">/dashboard</code>, <code class="bg-zinc-100 px-1.5 py-0.5 rounded text-xs">/prep</code>, or <code class="bg-zinc-100 px-1.5 py-0.5 rounded text-xs">/entity-page</code></p>
        </div>"""

    sidebar_html = _build_sidebar(active_page="hub")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <script>(function(){{var s=localStorage.getItem('soy-dark-mode');if(s==='dark'||(s!=='light'&&window.matchMedia('(prefers-color-scheme:dark)').matches)){{document.documentElement.classList.add('dark')}}}})();</script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SoY Hub — Software of You</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {{
            theme: {{ extend: {{ fontFamily: {{ sans: ['Inter', 'system-ui', 'sans-serif'] }} }} }}
        }}
    </script>
    <style>
        {SIDEBAR_CSS}
        {DARKMODE_CSS}
    </style>
</head>
<body class="bg-zinc-50 text-zinc-900 font-sans antialiased">
    {sidebar_html}

    <main class="lg:ml-60">
      <div class="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

        <!-- Header -->
        <div class="flex items-center gap-3 mb-8">
            <div class="w-11 h-11 rounded-xl bg-zinc-900 flex items-center justify-center">
                <i data-lucide="hexagon" class="w-5.5 h-5.5 text-white"></i>
            </div>
            <div>
                <h1 class="text-xl font-bold">Software of You</h1>
                <p class="text-sm text-zinc-400">Your hub</p>
            </div>
        </div>

        <!-- Quick Actions -->
        <section class="mb-8">
            <div class="flex items-center gap-2 mb-3">
                <i data-lucide="zap" class="w-4 h-4 text-zinc-400"></i>
                <h2 class="text-sm font-semibold text-zinc-500 uppercase tracking-wide">Quick Actions</h2>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {quick_actions}
            </div>
        </section>

        {sections_html}
        {empty_state}

      </div>

      <footer class="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 mt-8 pb-8">
          <div class="pt-4 border-t border-zinc-100 text-center">
              <p class="text-xs text-zinc-400">Software of You &middot; Hub &middot; localhost:{DEFAULT_PORT}</p>
          </div>
      </footer>
    </main>

    <script>
        {SIDEBAR_JS}
        {DARKMODE_JS}
        lucide.createIcons();
    </script>
</body>
</html>"""


class SoYHandler(BaseHTTPRequestHandler):
    """Handle hub, page serving, and audition API requests."""

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        body = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode())

    def _send_static_file(self, filepath):
        """Serve a static file with correct MIME type."""
        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return False
        content_type, _ = mimetypes.guess_type(filepath)
        if content_type is None:
            content_type = "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET routes ──────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Health check (lightweight probe for open_page.sh)
        if path == "/health":
            self._send_json({"status": "ok"})
            return

        # Hub home page — serve React app if built, else legacy Python hub
        if path == "/":
            index_path = os.path.join(HUB_DIR, "index.html")
            if os.path.isfile(index_path):
                self._send_static_file(index_path)
            else:
                self._send_html(_render_hub())
            return

        # Audition board (legacy — only if React hub not built)
        if (path == "/auditions" or path == "/board") and not os.path.isdir(HUB_DIR):
            board_path = os.path.join(OUTPUT_DIR, "audition-board.html")
            try:
                with open(board_path, "r") as f:
                    self._send_html(_inject_sidebar_into_page(f.read(), "audition-board.html"))
            except FileNotFoundError:
                self._send_json(
                    {"error": "Board HTML not found. Run /audition-board first."}, 404
                )
            return

        # SPA client routes — let React handle these when hub is built
        spa_routes = ("/auditions", "/income", "/contacts/", "/projects/",
                      "/nudges", "/emails", "/calendar", "/decisions", "/journal", "/notes",
                      "/writing", "/learning", "/health")
        if os.path.isdir(HUB_DIR) and any(path == r or path.startswith(r) for r in spa_routes):
            index_path = os.path.join(HUB_DIR, "index.html")
            if os.path.isfile(index_path):
                self._send_static_file(index_path)
                return

        # Serve shared (client-safe) pages — no sidebar/dark mode injection
        if path.startswith("/share/"):
            filename = path[7:]  # strip "/share/"
            if (
                ".." in filename
                or "/" in filename
                or not filename.endswith(".html")
                or not re.match(r"^[a-zA-Z0-9_\-]+\.html$", filename)
            ):
                self._send_json({"error": "Invalid filename"}, 400)
                return
            filepath = os.path.join(OUTPUT_DIR, "share", filename)
            try:
                with open(filepath, "r") as f:
                    self._send_html(f.read())
            except FileNotFoundError:
                self._send_json({"error": "Shared page not found. Run /share first."}, 404)
            return

        # Serve pages from output/
        if path.startswith("/pages/"):
            filename = path[7:]  # strip "/pages/"
            # Security: no path traversal, must end in .html
            if (
                ".." in filename
                or "/" in filename
                or not filename.endswith(".html")
                or not re.match(r"^[a-zA-Z0-9_\-]+\.html$", filename)
            ):
                self._send_json({"error": "Invalid filename"}, 400)
                return
            filepath = os.path.join(OUTPUT_DIR, filename)
            qs = parse_qs(parsed.query)
            raw = "1" in qs.get("raw", [])
            try:
                with open(filepath, "r") as f:
                    html = f.read()
                if raw:
                    self._send_html(_prepare_for_hub(html))
                else:
                    self._send_html(_inject_sidebar_into_page(html, filename))
            except FileNotFoundError:
                self._send_json({"error": "Page not found"}, 404)
            return

        # API: list registered pages
        if path == "/api/pages":
            conn = _get_db()
            rows = conn.execute(
                "SELECT * FROM generated_views ORDER BY updated_at DESC"
            ).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        # API: list auditions
        if path == "/api/auditions":
            conn = _get_db()
            rows = conn.execute(
                """SELECT id, project_name, role_name, role_type, production_type,
                          casting_director, casting_company, source, status,
                          received_at, deadline, submitted_at, callback_date,
                          notes, self_tape_specs, sides_url, source_url,
                          urgency, days_until_deadline, days_since_received, agent_name
                   FROM v_audition_pipeline
                   ORDER BY COALESCE(deadline, '9999-12-31') ASC"""
            ).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        # API: single audition (skip /api/auditions/summary — handled later)
        if path.startswith("/api/auditions/") and path != "/api/auditions/summary":
            try:
                aid = int(path.split("/")[-1])
            except ValueError:
                self._send_json({"error": "Invalid ID"}, 400)
                return
            conn = _get_db()
            row = conn.execute(
                "SELECT * FROM v_audition_pipeline WHERE id = ?", (aid,)
            ).fetchone()
            conn.close()
            if not row:
                self._send_json({"error": "Not found"}, 404)
                return
            self._send_json(_row_to_dict(row))
            return

        # API: list analysis items (optionally filtered by project_id)
        if path == "/api/analysis-items":
            qs = parse_qs(parsed.query)
            conn = _get_db()
            if "project_id" in qs:
                rows = conn.execute(
                    """SELECT ai.*, pa.summary as analysis_summary, pa.created_at as analysis_date
                       FROM project_analysis_items ai
                       JOIN project_analyses pa ON pa.id = ai.analysis_id
                       WHERE ai.project_id = ?
                       ORDER BY ai.category, ai.priority DESC, ai.id""",
                    (qs["project_id"][0],),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT ai.*, pa.summary as analysis_summary, pa.created_at as analysis_date
                       FROM project_analysis_items ai
                       JOIN project_analyses pa ON pa.id = ai.analysis_id
                       ORDER BY ai.created_at DESC, ai.id"""
                ).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        # API: project data for live-updating project pages
        m = re.match(r"^/api/projects/(\d+)$", path)
        if m:
            pid = int(m.group(1))
            conn = _get_db()
            project = conn.execute(
                """SELECT id, name, status, priority, description, target_date,
                          client_id, created_at, updated_at
                   FROM projects WHERE id = ?""", (pid,)
            ).fetchone()
            if not project:
                conn.close()
                self._send_json({"error": "Not found"}, 404)
                return
            proj_dict = _row_to_dict(project)

            # Client info
            client = None
            if proj_dict.get("client_id"):
                try:
                    client_row = conn.execute(
                        "SELECT id, name, email, company, role FROM contacts WHERE id = ?",
                        (proj_dict["client_id"],),
                    ).fetchone()
                    client = _row_to_dict(client_row)
                except Exception:
                    pass
            proj_dict["client"] = client

            tasks = conn.execute(
                """SELECT id, title, description, status, priority, due_date, completed_at
                   FROM tasks WHERE project_id = ?
                   ORDER BY CASE status
                     WHEN 'in_progress' THEN 1 WHEN 'todo' THEN 2
                     WHEN 'blocked' THEN 3 WHEN 'done' THEN 4 END,
                   due_date ASC NULLS LAST""",
                (pid,),
            ).fetchall()

            # Health from computed view
            health = None
            try:
                health = conn.execute(
                    "SELECT * FROM v_project_health WHERE id = ?", (pid,)
                ).fetchone()
            except Exception:
                pass
            task_stats = {
                "total": health["total_tasks"] if health else 0,
                "todo": health["todo_tasks"] if health else 0,
                "in_progress": health["active_tasks"] if health else 0,
                "done": health["done_tasks"] if health else 0,
                "blocked": health["blocked_tasks"] if health else 0,
                "completion_pct": health["completion_pct"] if health else 0,
            }

            # Milestones
            milestones = []
            try:
                for r in conn.execute(
                    """SELECT id, name AS title, target_date, status, completed_date AS completed_at
                       FROM milestones WHERE project_id = ?
                       ORDER BY target_date ASC NULLS LAST""",
                    (pid,),
                ).fetchall():
                    milestones.append(_row_to_dict(r))
            except Exception:
                pass

            decisions = conn.execute(
                """SELECT id, title, decision, status, decided_at
                   FROM decisions WHERE project_id = ?
                   ORDER BY decided_at DESC""",
                (pid,),
            ).fetchall()
            activity = conn.execute(
                """SELECT action, details, created_at FROM activity_log
                   WHERE entity_type = 'project' AND entity_id = ?
                   ORDER BY created_at DESC LIMIT 20""",
                (pid,),
            ).fetchall()

            # Page filename
            page_filename = None
            try:
                pf_row = conn.execute(
                    """SELECT filename FROM generated_views
                       WHERE entity_type = 'project' AND entity_id = ?
                       AND view_type = 'entity_page'""",
                    (pid,),
                ).fetchone()
                if pf_row:
                    page_filename = pf_row["filename"]
            except Exception:
                pass

            # Sub-views (pm_reports, analyses, etc.)
            sub_views = []
            try:
                for r in conn.execute(
                    """SELECT id, view_type, entity_name, filename
                       FROM generated_views
                       WHERE entity_type = 'project' AND entity_id = ?
                       AND view_type != 'entity_page'
                       ORDER BY entity_name""",
                    (pid,),
                ).fetchall():
                    sub_views.append(_row_to_dict(r))
            except Exception:
                pass

            conn.close()
            self._send_json({
                "project": proj_dict,
                "tasks": [_row_to_dict(t) for t in tasks],
                "task_stats": task_stats,
                "health": _row_to_dict(health) if health else None,
                "milestones": milestones,
                "decisions": [_row_to_dict(d) for d in decisions],
                "activity": [_row_to_dict(a) for a in activity],
                "page_filename": page_filename,
                "sub_views": sub_views,
            })
            return

        # API: home page data for React hub
        if path == "/api/home":
            conn = _get_db()
            data = {}

            # User name
            try:
                row = conn.execute(
                    "SELECT value FROM user_profile WHERE category = 'identity' AND key = 'name'"
                ).fetchone()
                data["user_name"] = row["value"] if row else None
            except Exception:
                data["user_name"] = None

            # Badge counts (same as navigation)
            badges = {}
            for row in conn.execute("""
                SELECT 'contacts' as section, COUNT(*) as count FROM contacts WHERE status = 'active'
                UNION ALL SELECT 'emails', COUNT(*) FROM emails
                UNION ALL SELECT 'calendar', COUNT(*) FROM calendar_events WHERE start_time > datetime('now', '-30 days')
                UNION ALL SELECT 'transcripts', COUNT(*) FROM transcripts
                UNION ALL SELECT 'decisions', COUNT(*) FROM decisions
                UNION ALL SELECT 'journal', COUNT(*) FROM journal_entries
                UNION ALL SELECT 'notes', COUNT(*) FROM standalone_notes
            """).fetchall():
                badges[row["section"]] = row["count"]
            data["badges"] = badges

            # Project health
            projects = []
            try:
                for row in conn.execute("""
                    SELECT id, name, status, completion_pct, total_tasks, done_tasks,
                           overdue_tasks, days_to_target
                    FROM v_project_health
                    WHERE status IN ('active', 'idea')
                    ORDER BY CASE status WHEN 'active' THEN 1 ELSE 2 END, name
                """).fetchall():
                    projects.append(_row_to_dict(row))
            except Exception:
                pass
            data["projects"] = projects

            # Upcoming events (next 7 days)
            events = []
            try:
                for row in conn.execute("""
                    SELECT title, start_time, end_time, location, all_day
                    FROM calendar_events
                    WHERE start_time > datetime('now')
                      AND start_time < datetime('now', '+7 days')
                    ORDER BY start_time LIMIT 8
                """).fetchall():
                    events.append(_row_to_dict(row))
            except Exception:
                pass
            data["upcoming_events"] = events

            # Nudge summary
            nudges = {"urgent": 0, "soon": 0, "awareness": 0}
            try:
                for row in conn.execute(
                    "SELECT tier, COUNT(*) as count FROM v_nudge_summary GROUP BY tier"
                ).fetchall():
                    nudges[row["tier"]] = row["count"]
            except Exception:
                pass
            data["nudges"] = nudges

            # Recent activity
            activity = []
            try:
                for row in conn.execute("""
                    SELECT entity_type, action, details, created_at
                    FROM activity_log
                    ORDER BY created_at DESC LIMIT 10
                """).fetchall():
                    activity.append(_row_to_dict(row))
            except Exception:
                pass
            data["recent_activity"] = activity

            conn.close()
            self._send_json(data)
            return

        # API: navigation data for React hub sidebar
        if path == "/api/navigation":
            conn = _get_db()

            # Installed modules
            modules = [r["name"] for r in conn.execute(
                "SELECT name FROM modules WHERE enabled = 1"
            ).fetchall()]

            # Badge counts
            badges = {}
            for row in conn.execute("""
                SELECT 'contacts' as section, COUNT(*) as count FROM contacts WHERE status = 'active'
                UNION ALL SELECT 'emails', COUNT(*) FROM emails
                UNION ALL SELECT 'calendar', COUNT(*) FROM calendar_events WHERE start_time > datetime('now', '-30 days')
                UNION ALL SELECT 'transcripts', COUNT(*) FROM transcripts
                UNION ALL SELECT 'decisions', COUNT(*) FROM decisions
                UNION ALL SELECT 'journal', COUNT(*) FROM journal_entries
                UNION ALL SELECT 'notes', COUNT(*) FROM standalone_notes
            """).fetchall():
                badges[row["section"]] = row["count"]

            # All generated views with hierarchy
            raw_views = conn.execute("""
                SELECT gv.id, gv.view_type, gv.entity_type, gv.entity_id, gv.entity_name,
                       gv.filename, gv.parent_page_id,
                       parent.filename as parent_filename
                FROM generated_views gv
                LEFT JOIN generated_views parent ON gv.parent_page_id = parent.id
                ORDER BY gv.entity_name
            """).fetchall()

            # Urgent nudge count
            urgent_count = 0
            try:
                row = conn.execute("""
                    SELECT
                      (SELECT COUNT(*) FROM follow_ups WHERE status = 'pending' AND due_date < date('now'))
                      + (SELECT COUNT(*) FROM commitments WHERE status IN ('open','overdue') AND deadline_date < date('now'))
                      + (SELECT COUNT(*) FROM tasks t JOIN projects p ON p.id = t.project_id WHERE t.status NOT IN ('done') AND t.due_date < date('now'))
                      as urgent_count
                """).fetchone()
                if row:
                    urgent_count = row["urgent_count"] or 0
            except Exception:
                pass

            # All contacts with page filenames
            nav_contacts = []
            try:
                for r in conn.execute("""
                    SELECT c.id, c.name, c.company, c.role,
                           gv.filename as page_filename
                    FROM contacts c
                    LEFT JOIN generated_views gv
                      ON gv.entity_type = 'contact' AND gv.entity_id = c.id
                      AND gv.view_type = 'entity_page'
                    WHERE c.status = 'active'
                    ORDER BY c.name
                """).fetchall():
                    nav_contacts.append(_row_to_dict(r))
            except Exception:
                pass

            # All projects with page filenames and health
            nav_projects = []
            try:
                for r in conn.execute("""
                    SELECT p.id, p.name, p.status, p.client_id,
                           cl.name as client_name,
                           COALESCE(vh.completion_pct, 0) as completion_pct,
                           COALESCE(vh.total_tasks, 0) as total_tasks,
                           COALESCE(vh.done_tasks, 0) as done_tasks,
                           gv.filename as page_filename
                    FROM projects p
                    LEFT JOIN contacts cl ON cl.id = p.client_id
                    LEFT JOIN v_project_health vh ON vh.id = p.id
                    LEFT JOIN generated_views gv
                      ON gv.entity_type = 'project' AND gv.entity_id = p.id
                      AND gv.view_type = 'entity_page'
                    ORDER BY CASE p.status
                      WHEN 'active' THEN 1 WHEN 'idea' THEN 2 ELSE 3 END,
                      p.name
                """).fetchall():
                    nav_projects.append(_row_to_dict(r))
            except Exception:
                pass

            # Project sub-views (non-entity_page generated views)
            project_sub_views = {}
            try:
                for r in conn.execute("""
                    SELECT gv.id, gv.view_type, gv.entity_id, gv.entity_name, gv.filename
                    FROM generated_views gv
                    WHERE gv.entity_type = 'project' AND gv.view_type != 'entity_page'
                    ORDER BY gv.entity_name
                """).fetchall():
                    sv = _row_to_dict(r)
                    eid = sv["entity_id"]
                    if eid not in project_sub_views:
                        project_sub_views[eid] = []
                    project_sub_views[eid].append(sv)
            except Exception:
                pass

            # Attach children to projects (generated views + module routes)
            # Build set of projects with writing/creative module data
            writing_project_ids = set()
            try:
                for tbl in ["writing_samples", "writing_drafts", "creative_context"]:
                    try:
                        for r in conn.execute(f"SELECT DISTINCT project_id FROM {tbl} WHERE project_id IS NOT NULL").fetchall():
                            writing_project_ids.add(r["project_id"])
                    except Exception:
                        pass
            except Exception:
                pass

            for proj in nav_projects:
                children = list(project_sub_views.get(proj["id"], []))
                # Add Writing module link for projects with writing data
                if proj["id"] in writing_project_ids:
                    children.append({
                        "id": -proj["id"],  # virtual ID
                        "view_type": "module_route",
                        "entity_id": proj["id"],
                        "entity_name": "Writing",
                        "filename": "",
                        "route": "writing",
                    })
                proj["children"] = children

            # Learning badge: digests with no feedback in last 7 days
            try:
                row = conn.execute("""
                    SELECT COUNT(*) as n FROM learning_digests ld
                    WHERE ld.created_at > datetime('now', '-7 days')
                      AND NOT EXISTS (
                        SELECT 1 FROM learning_feedback lf WHERE lf.digest_id = ld.id
                      )
                """).fetchone()
                if row and row["n"] > 0:
                    badges["learning"] = row["n"]
            except Exception:
                pass

            # Health badge: active errors/warnings
            try:
                row = conn.execute("""
                    SELECT COUNT(*) as n FROM v_health_summary
                    WHERE status IN ('error', 'warning')
                """).fetchone()
                if row and row["n"] > 0:
                    badges["health"] = row["n"]
            except Exception:
                pass

            conn.close()

            # Group children under parents
            view_map = {}
            children = []
            for r in raw_views:
                v = _row_to_dict(r)
                v["children"] = []
                if v["parent_page_id"] is not None:
                    children.append(v)
                else:
                    view_map[v["id"]] = v

            for c in children:
                pid = c["parent_page_id"]
                if pid in view_map:
                    view_map[pid]["children"].append(c)
                else:
                    # Orphan — treat as top-level
                    view_map[c["id"]] = c

            self._send_json({
                "modules": modules,
                "badges": badges,
                "views": list(view_map.values()),
                "urgent_count": urgent_count,
                "contacts": nav_contacts,
                "projects": nav_projects,
            })
            return

        # API: single contact detail
        m = re.match(r"^/api/contacts/(\d+)$", path)
        if m:
            cid = int(m.group(1))
            conn = _get_db()
            try:
                contact = conn.execute(
                    """SELECT id, name, email, phone, company, role, type, status,
                              notes, created_at, updated_at
                       FROM contacts WHERE id = ?""",
                    (cid,),
                ).fetchone()
            except Exception:
                contact = None
            if not contact:
                conn.close()
                self._send_json({"error": "Not found"}, 404)
                return
            contact_dict = _row_to_dict(contact)

            # Projects where this contact is client
            projects = []
            try:
                for r in conn.execute(
                    """SELECT p.id, p.name, p.status,
                              COALESCE(vh.completion_pct, 0) as completion_pct
                       FROM projects p
                       LEFT JOIN v_project_health vh ON vh.id = p.id
                       WHERE p.client_id = ?""",
                    (cid,),
                ).fetchall():
                    projects.append(_row_to_dict(r))
            except Exception:
                pass

            # Recent interactions
            interactions = []
            try:
                for r in conn.execute(
                    """SELECT id, interaction_type, summary, occurred_at, sentiment
                       FROM contact_interactions WHERE contact_id = ?
                       ORDER BY occurred_at DESC LIMIT 20""",
                    (cid,),
                ).fetchall():
                    interactions.append(_row_to_dict(r))
            except Exception:
                pass

            # Recent emails
            emails = []
            try:
                for r in conn.execute(
                    """SELECT id, subject, snippet, direction, received_at
                       FROM emails WHERE contact_id = ?
                       ORDER BY received_at DESC LIMIT 20""",
                    (cid,),
                ).fetchall():
                    emails.append(_row_to_dict(r))
            except Exception:
                pass

            # Page filename
            page_filename = None
            try:
                pf_row = conn.execute(
                    """SELECT filename FROM generated_views
                       WHERE entity_type = 'contact' AND entity_id = ?
                       AND view_type = 'entity_page'""",
                    (cid,),
                ).fetchone()
                if pf_row:
                    page_filename = pf_row["filename"]
            except Exception:
                pass

            conn.close()
            contact_dict["projects"] = projects
            contact_dict["interactions"] = interactions
            contact_dict["emails"] = emails
            contact_dict["page_filename"] = page_filename
            self._send_json(contact_dict)
            return

        # API: income summary
        if path == "/api/income/summary":
            conn = _get_db()
            data = {}

            # Summary by currency
            summary = []
            try:
                for r in conn.execute("""
                    SELECT COUNT(*) as total_records,
                           SUM(amount) as total_gross,
                           SUM(net_amount) as total_net,
                           SUM(agent_fee_amount) as total_agent_fees,
                           currency
                    FROM income_records
                    GROUP BY currency
                """).fetchall():
                    summary.append(_row_to_dict(r))
            except Exception:
                pass
            data["summary"] = summary

            # Individual records
            records = []
            try:
                for r in conn.execute("""
                    SELECT ir.*, c.name as contact_name, p.name as project_name
                    FROM income_records ir
                    LEFT JOIN contacts c ON c.id = ir.contact_id
                    LEFT JOIN projects p ON p.id = ir.project_id
                    ORDER BY ir.received_date DESC
                """).fetchall():
                    records.append(_row_to_dict(r))
            except Exception:
                pass
            data["records"] = records

            # Page filename
            page_filename = None
            try:
                pf_row = conn.execute(
                    "SELECT filename FROM generated_views WHERE filename = 'income.html'"
                ).fetchone()
                if pf_row:
                    page_filename = pf_row["filename"]
            except Exception:
                pass
            data["page_filename"] = page_filename

            conn.close()
            self._send_json(data)
            return

        # API: auditions summary
        if path == "/api/auditions/summary":
            conn = _get_db()
            data = {}

            # Status counts
            status_counts = []
            try:
                for r in conn.execute(
                    "SELECT status, COUNT(*) as count FROM auditions GROUP BY status"
                ).fetchall():
                    status_counts.append(_row_to_dict(r))
            except Exception:
                pass
            data["status_counts"] = status_counts

            # Page filename
            page_filename = None
            try:
                pf_row = conn.execute(
                    "SELECT filename FROM generated_views WHERE filename = 'audition-board.html'"
                ).fetchone()
                if pf_row:
                    page_filename = pf_row["filename"]
            except Exception:
                pass
            data["page_filename"] = page_filename

            conn.close()
            self._send_json(data)
            return


        # ── Writing Module API ──────────────────────────────────────
        if path == "/api/writing/drafts":
            qs = parse_qs(parsed.query)
            project_id = qs.get("project_id", [None])[0]
            conn = _get_db()
            if project_id:
                rows = conn.execute(
                    """SELECT d.*, p.name as project_name,
                              (SELECT COUNT(*) FROM draft_feedback df WHERE df.draft_id = d.id AND df.status = 'open') as open_feedback,
                              (SELECT GROUP_CONCAT(dc.character_name, ', ')
                               FROM draft_characters dc WHERE dc.draft_id = d.id
                               ORDER BY CASE dc.role WHEN 'pov' THEN 0 WHEN 'featured' THEN 1 ELSE 2 END) as characters
                       FROM writing_drafts d
                       LEFT JOIN projects p ON p.id = d.project_id
                       WHERE d.project_id = ?
                       ORDER BY d.sort_order, d.id""",
                    (int(project_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT d.*, p.name as project_name,
                              (SELECT COUNT(*) FROM draft_feedback df WHERE df.draft_id = d.id AND df.status = 'open') as open_feedback,
                              (SELECT GROUP_CONCAT(dc.character_name, ', ')
                               FROM draft_characters dc WHERE dc.draft_id = d.id
                               ORDER BY CASE dc.role WHEN 'pov' THEN 0 WHEN 'featured' THEN 1 ELSE 2 END) as characters
                       FROM writing_drafts d
                       LEFT JOIN projects p ON p.id = d.project_id
                       ORDER BY d.project_id, d.sort_order, d.id"""
                ).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        if re.match(r"^/api/writing/drafts/\d+$", path):
            draft_id = int(path.split("/")[-1])
            conn = _get_db()
            draft = conn.execute(
                """SELECT d.*, p.name as project_name
                   FROM writing_drafts d
                   LEFT JOIN projects p ON p.id = d.project_id
                   WHERE d.id = ?""",
                (draft_id,),
            ).fetchone()
            if not draft:
                conn.close()
                self._send_json({"error": "Draft not found"}, 404)
                return
            data = _row_to_dict(draft)

            ver = conn.execute(
                """SELECT * FROM draft_versions
                   WHERE draft_id = ? AND version_number = ?""",
                (draft_id, data["current_version"]),
            ).fetchone()
            data["content"] = _row_to_dict(ver) if ver else None

            versions = conn.execute(
                """SELECT id, version_number, word_count, change_summary, created_at
                   FROM draft_versions WHERE draft_id = ?
                   ORDER BY version_number DESC""",
                (draft_id,),
            ).fetchall()
            data["versions"] = [_row_to_dict(v) for v in versions]

            feedback = conn.execute(
                """SELECT * FROM draft_feedback WHERE draft_id = ?
                   ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, created_at DESC""",
                (draft_id,),
            ).fetchall()
            data["feedback"] = [_row_to_dict(f) for f in feedback]

            characters = conn.execute(
                """SELECT * FROM draft_characters WHERE draft_id = ?
                   ORDER BY CASE role WHEN 'pov' THEN 0 WHEN 'featured' THEN 1 WHEN 'mentioned' THEN 2 ELSE 3 END""",
                (draft_id,),
            ).fetchall()
            data["characters"] = [_row_to_dict(c) for c in characters]

            lore = conn.execute(
                """SELECT dll.*, cc.title as context_title, cc.context_type
                   FROM draft_lore_links dll
                   JOIN creative_context cc ON cc.id = dll.context_id
                   WHERE dll.draft_id = ?
                   ORDER BY dll.link_type""",
                (draft_id,),
            ).fetchall()
            data["lore_links"] = [_row_to_dict(l) for l in lore]

            conn.close()
            self._send_json(data)
            return

        # GET all feedback for a project's drafts
        if path == "/api/writing/feedback":
            qs = parse_qs(parsed.query)
            project_id = qs.get("project_id", [None])[0]
            conn = _get_db()
            if project_id:
                rows = conn.execute(
                    """SELECT df.*, d.title as draft_title
                       FROM draft_feedback df
                       JOIN writing_drafts d ON d.id = df.draft_id
                       WHERE d.project_id = ?
                       ORDER BY df.draft_id, df.created_at""",
                    (int(project_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT df.*, d.title as draft_title
                       FROM draft_feedback df
                       JOIN writing_drafts d ON d.id = df.draft_id
                       ORDER BY df.draft_id, df.created_at"""
                ).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        if path == "/api/writing/progress":
            qs = parse_qs(parsed.query)
            project_id = qs.get("project_id", [None])[0]
            conn = _get_db()
            if project_id:
                rows = conn.execute("SELECT * FROM v_writing_progress WHERE project_id = ?", (int(project_id),)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM v_writing_progress").fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        # ── Annotations API ─────────────────────────────────────────
        if path == "/api/annotations":
            qs = parse_qs(parsed.query)
            context_id = qs.get("context_id", [None])[0]
            status_filter = qs.get("status", [None])[0]
            conn = _get_db()
            conditions = []
            params = []
            if context_id:
                conditions.append("la.context_id = ?")
                params.append(int(context_id))
            if status_filter:
                conditions.append("la.status = ?")
                params.append(status_filter)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = conn.execute(
                f"""SELECT la.*, cc.title as context_title, cc.context_type
                    FROM lore_annotations la
                    JOIN creative_context cc ON la.context_id = cc.id
                    {where}
                    ORDER BY la.created_at DESC""",
                tuple(params),
            ).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        # ── Creative Threads API ──────────────────────────────────
        if path == "/api/threads":
            qs = parse_qs(parsed.query)
            project_id = qs.get("project_id", [None])[0]
            status_filter = qs.get("status", [None])[0]
            conn = _get_db()
            conditions = []
            params = []
            if project_id:
                conditions.append("ct.project_id = ?")
                params.append(int(project_id))
            if status_filter:
                conditions.append("ct.status = ?")
                params.append(status_filter)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = conn.execute(
                f"""SELECT ct.*, p.name as project_name
                    FROM creative_threads ct
                    LEFT JOIN projects p ON ct.project_id = p.id
                    {where}
                    ORDER BY ct.created_at DESC""",
                tuple(params),
            ).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        # ── Phase 2 API endpoints: native React views ──────────────

        if path == "/api/nudges":
            conn = _get_db()
            try:
                rows = conn.execute("""
                    SELECT nudge_type, entity_id, tier, entity_name,
                           contact_id, project_id, description,
                           relevant_date, days_value, extra_context, icon
                    FROM v_nudge_items
                    ORDER BY CASE tier
                        WHEN 'urgent' THEN 1 WHEN 'soon' THEN 2 ELSE 3
                    END, days_value DESC
                """).fetchall()
                conn.close()
                self._send_json([_row_to_dict(r) for r in rows])
            except Exception as e:
                conn.close()
                self._send_json([])
            return

        if path == "/api/emails":
            conn = _get_db()
            qs = parse_qs(parsed.query)
            limit = int(qs.get("limit", ["100"])[0])
            rows = conn.execute("""
                SELECT e.id, e.subject, e.snippet, e.from_name, e.from_address,
                       e.to_addresses, e.direction, e.is_read, e.is_starred,
                       e.received_at, e.thread_id, e.labels,
                       e.contact_id, c.name as contact_name
                FROM emails e
                LEFT JOIN contacts c ON c.id = e.contact_id
                ORDER BY e.received_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        if path == "/api/calendar":
            conn = _get_db()
            rows = conn.execute("""
                SELECT ce.id, ce.title, ce.description, ce.location,
                       ce.start_time, ce.end_time, ce.all_day, ce.status,
                       ce.attendees, ce.project_id,
                       p.name as project_name
                FROM calendar_events ce
                LEFT JOIN projects p ON p.id = ce.project_id
                WHERE ce.start_time > datetime('now', '-7 days')
                  AND ce.start_time < datetime('now', '+30 days')
                  AND ce.status != 'cancelled'
                ORDER BY ce.start_time ASC
            """).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        if path == "/api/decisions":
            conn = _get_db()
            rows = conn.execute("""
                SELECT d.id, d.title, d.context, d.decision, d.rationale,
                       d.outcome, d.outcome_date, d.status, d.confidence_level,
                       d.project_id, d.contact_id, d.decided_at,
                       p.name as project_name, c.name as contact_name
                FROM decisions d
                LEFT JOIN projects p ON p.id = d.project_id
                LEFT JOIN contacts c ON c.id = d.contact_id
                ORDER BY d.decided_at DESC
            """).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        if path == "/api/journal":
            conn = _get_db()
            rows = conn.execute("""
                SELECT id, content, mood, energy, highlights, entry_date,
                       linked_contacts, linked_projects, created_at
                FROM journal_entries
                ORDER BY entry_date DESC
            """).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        if path == "/api/notes":
            conn = _get_db()
            qs = parse_qs(parsed.query)
            q = qs.get("q", [""])[0].strip()
            if q:
                like = f"%{q}%"
                rows = conn.execute("""
                    SELECT id, title, content, tags, pinned,
                           linked_contacts, linked_projects,
                           created_at, updated_at
                    FROM standalone_notes
                    WHERE title LIKE ? OR content LIKE ?
                    ORDER BY pinned DESC, updated_at DESC
                """, (like, like)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, title, content, tags, pinned,
                           linked_contacts, linked_projects,
                           created_at, updated_at
                    FROM standalone_notes
                    ORDER BY pinned DESC, updated_at DESC
                """).fetchall()
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        # ── Platform Health API ──────────────────────────────────

        if path == "/api/health/status":
            conn = _get_db()
            try:
                # Latest sweep
                sweep = conn.execute(
                    "SELECT * FROM health_sweeps ORDER BY created_at DESC LIMIT 1"
                ).fetchone()

                # Per-check status from view
                checks = []
                for r in conn.execute(
                    "SELECT * FROM v_health_summary ORDER BY machine, check_type"
                ).fetchall():
                    checks.append(_row_to_dict(r))
            except Exception:
                sweep = None
                checks = []
            conn.close()
            self._send_json({
                "latest_sweep": _row_to_dict(sweep) if sweep else None,
                "checks": checks,
            })
            return

        if path == "/api/health/history":
            params = parse_qs(parsed.query)
            days = int(params.get("days", ["7"])[0])
            conn = _get_db()
            try:
                rows = conn.execute(
                    """SELECT * FROM health_sweeps
                       WHERE created_at > datetime('now', ?)
                       ORDER BY created_at DESC""",
                    (f"-{days} days",),
                ).fetchall()
            except Exception:
                rows = []
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        # ── Learning API ─────────────────────────────────────────

        if path == "/api/learning/digests":
            params = parse_qs(parsed.query)
            dtype = params.get("type", [None])[0]
            limit = int(params.get("limit", ["20"])[0])
            conn = _get_db()
            try:
                if dtype:
                    rows = conn.execute(
                        """SELECT ld.id, ld.digest_type, ld.digest_date, ld.title,
                                  ld.generation_duration_ms, ld.created_at,
                                  (SELECT COUNT(*) FROM learning_feedback WHERE digest_id = ld.id) as feedback_count
                           FROM learning_digests ld
                           WHERE ld.digest_type = ?
                           ORDER BY ld.created_at DESC LIMIT ?""",
                        (dtype, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT ld.id, ld.digest_type, ld.digest_date, ld.title,
                                  ld.generation_duration_ms, ld.created_at,
                                  (SELECT COUNT(*) FROM learning_feedback WHERE digest_id = ld.id) as feedback_count
                           FROM learning_digests ld
                           ORDER BY ld.created_at DESC LIMIT ?""",
                        (limit,),
                    ).fetchall()
            except Exception:
                rows = []
            conn.close()
            self._send_json([_row_to_dict(r) for r in rows])
            return

        m = re.match(r"^/api/learning/digests/(\d+)$", path)
        if m:
            did = int(m.group(1))
            conn = _get_db()
            try:
                digest = conn.execute(
                    "SELECT * FROM learning_digests WHERE id = ?", (did,)
                ).fetchone()
                if not digest:
                    conn.close()
                    self._send_json({"error": "Not found"}, 404)
                    return
                result = _row_to_dict(digest)
                # Parse sections JSON
                try:
                    result["sections"] = json.loads(result["sections"])
                except Exception:
                    pass
                # Attach feedback
                feedback = conn.execute(
                    """SELECT id, section_id, reaction, comment, created_at
                       FROM learning_feedback WHERE digest_id = ?
                       ORDER BY created_at""",
                    (did,),
                ).fetchall()
                result["feedback"] = [_row_to_dict(f) for f in feedback]
            except Exception:
                conn.close()
                self._send_json({"error": "Not found"}, 404)
                return
            conn.close()
            self._send_json(result)
            return

        if path == "/api/learning/profile":
            conn = _get_db()
            profile = {}
            try:
                rows = conn.execute(
                    "SELECT category, key, value FROM learning_profile"
                ).fetchall()
                for r in rows:
                    cat = r["category"]
                    if cat not in profile:
                        profile[cat] = {}
                    profile[cat][r["key"]] = r["value"]
            except Exception:
                pass
            conn.close()
            self._send_json(profile)
            return

        # ── React Hub (SPA) serving from hub/dist/ ────────────────
        # If hub/dist/ exists, serve static assets and fall back to
        # index.html for client-side routing. Otherwise, fall back to
        # the legacy Python-rendered hub.
        if os.path.isdir(HUB_DIR):
            # Try to serve a static file from hub/dist/
            # Strip leading slash for path join
            rel = path.lstrip("/")
            if not rel:
                rel = "index.html"
            candidate = os.path.join(HUB_DIR, rel)
            # Security: ensure resolved path is within HUB_DIR
            real_candidate = os.path.realpath(candidate)
            real_hub = os.path.realpath(HUB_DIR)
            if real_candidate.startswith(real_hub) and os.path.isfile(candidate):
                self._send_static_file(candidate)
                return
            # SPA fallback: serve index.html for unmatched routes
            index_path = os.path.join(HUB_DIR, "index.html")
            if os.path.isfile(index_path):
                self._send_static_file(index_path)
                return

        self._send_json({"error": "Not found"}, 404)

    # ── PATCH routes ────────────────────────────────────────────

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # PATCH task status (for live project page toggle)
        if re.match(r"^/api/tasks/\d+$", path):
            try:
                task_id = int(path.split("/")[-1])
            except ValueError:
                self._send_json({"error": "Invalid ID"}, 400)
                return
            data = self._read_body()
            new_status = data.get("status")
            if new_status not in ("todo", "in_progress", "done", "blocked"):
                self._send_json({"error": "status must be 'todo', 'in_progress', 'done', or 'blocked'"}, 400)
                return
            conn = _get_db()
            existing = conn.execute(
                "SELECT id, project_id, title, status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not existing:
                conn.close()
                self._send_json({"error": "Not found"}, 404)
                return
            completed_at = "datetime('now')" if new_status == "done" else "NULL"
            conn.execute(
                f"UPDATE tasks SET status = ?, completed_at = {completed_at}, updated_at = datetime('now') WHERE id = ?",
                (new_status, task_id),
            )
            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('project', ?, 'task_updated', ?, datetime('now'))""",
                (existing["project_id"], json.dumps({"task_id": task_id, "title": existing["title"], "old_status": existing["status"], "new_status": new_status})),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id, title, description, status, priority, due_date, completed_at FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            conn.close()
            self._send_json(_row_to_dict(row))
            return

        # PATCH analysis item (dismiss / un-dismiss)
        if re.match(r"^/api/analysis-items/\d+$", path):
            try:
                item_id = int(path.split("/")[-1])
            except ValueError:
                self._send_json({"error": "Invalid ID"}, 400)
                return
            data = self._read_body()
            new_status = data.get("status")
            if new_status not in ("open", "dismissed"):
                self._send_json({"error": "status must be 'open' or 'dismissed'"}, 400)
                return
            conn = _get_db()
            existing = conn.execute(
                "SELECT id, status FROM project_analysis_items WHERE id = ?", (item_id,)
            ).fetchone()
            if not existing:
                conn.close()
                self._send_json({"error": "Not found"}, 404)
                return
            if existing["status"] == "converted":
                conn.close()
                self._send_json({"error": "Cannot change status of converted item"}, 400)
                return
            conn.execute(
                "UPDATE project_analysis_items SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (new_status, item_id),
            )
            action = "dismissed" if new_status == "dismissed" else "reopened"
            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('project_analysis_item', ?, ?, ?, datetime('now'))""",
                (item_id, action, json.dumps({"status": new_status})),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM project_analysis_items WHERE id = ?", (item_id,)
            ).fetchone()
            conn.close()
            self._send_json(_row_to_dict(row))
            return

        # PATCH writing feedback (resolve/update status)
        m = re.match(r"^/api/writing/feedback/(\d+)$", path)
        if m:
            fid = int(m.group(1))
            data = self._read_body()
            conn = _get_db()
            existing = conn.execute("SELECT id, draft_id, status FROM draft_feedback WHERE id = ?", (fid,)).fetchone()
            if not existing:
                conn.close()
                self._send_json({"error": "Not found"}, 404)
                return
            updates = []
            params = []
            if "status" in data:
                if data["status"] not in ("open", "addressed", "dismissed", "deferred"):
                    conn.close()
                    self._send_json({"error": "Invalid status"}, 400)
                    return
                updates.append("status = ?")
                params.append(data["status"])
            if "resolution" in data:
                updates.append("resolution = ?")
                params.append(data["resolution"])
                updates.append("resolved_at = datetime('now')")
            if updates:
                params.append(fid)
                conn.execute(f"UPDATE draft_feedback SET {', '.join(updates)} WHERE id = ?", params)
                conn.commit()
            row = conn.execute("SELECT * FROM draft_feedback WHERE id = ?", (fid,)).fetchone()
            conn.close()
            self._send_json(_row_to_dict(row))
            return

        if not path.startswith("/api/auditions/"):
            self._send_json({"error": "Not found"}, 404)
            return

        try:
            aid = int(path.split("/")[-1])
        except ValueError:
            self._send_json({"error": "Invalid ID"}, 400)
            return

        data = self._read_body()
        if not data:
            self._send_json({"error": "No data"}, 400)
            return

        conn = _get_db()

        existing = conn.execute(
            "SELECT id FROM auditions WHERE id = ?", (aid,)
        ).fetchone()
        if not existing:
            conn.close()
            self._send_json({"error": "Not found"}, 404)
            return

        allowed_fields = {
            "project_name",
            "role_name",
            "role_type",
            "production_type",
            "casting_director",
            "casting_company",
            "source",
            "status",
            "deadline",
            "callback_date",
            "notes",
            "self_tape_specs",
            "sides_url",
            "source_url",
        }
        sets = []
        vals = []
        for key, val in data.items():
            if key in allowed_fields:
                sets.append(f"{key} = ?")
                vals.append(val)

        if data.get("status") == "submitted":
            sets.append("submitted_at = datetime('now')")

        if not sets:
            conn.close()
            self._send_json({"error": "No valid fields"}, 400)
            return

        sets.append("updated_at = datetime('now')")
        vals.append(aid)

        conn.execute(f"UPDATE auditions SET {', '.join(sets)} WHERE id = ?", vals)

        action = "status_changed" if "status" in data else "updated"
        details = json.dumps({k: v for k, v in data.items() if k in allowed_fields})
        conn.execute(
            """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
               VALUES ('audition', ?, ?, ?, datetime('now'))""",
            (aid, action, details),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM v_audition_pipeline WHERE id = ?", (aid,)
        ).fetchone()
        conn.close()
        self._send_json(_row_to_dict(row))

    # ── POST routes ─────────────────────────────────────────────

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # POST: convert analysis item to task
        if re.match(r"^/api/analysis-items/\d+/convert$", path):
            parts = path.split("/")
            try:
                item_id = int(parts[3])
            except (ValueError, IndexError):
                self._send_json({"error": "Invalid ID"}, 400)
                return
            conn = _get_db()
            item = conn.execute(
                "SELECT * FROM project_analysis_items WHERE id = ?", (item_id,)
            ).fetchone()
            if not item:
                conn.close()
                self._send_json({"error": "Not found"}, 404)
                return
            item = _row_to_dict(item)
            if item["status"] == "converted":
                conn.close()
                self._send_json(
                    {"error": "Already converted", "task_id": item["converted_task_id"]},
                    409,
                )
                return
            # Map priority to task priority
            priority_map = {"critical": "high", "high": "high", "medium": "medium", "low": "low"}
            task_priority = priority_map.get(item["priority"], "medium")
            # Build task description with provenance
            desc_parts = []
            if item["description"]:
                desc_parts.append(item["description"])
            if item["rationale"]:
                desc_parts.append(f"Rationale: {item['rationale']}")
            desc_parts.append(f"[From project analysis — {item['category'].replace('_', ' ')}]")
            desc_parts.append(f"Evidence: {item['grounded_in']}")
            task_desc = "\n\n".join(desc_parts)
            cursor = conn.execute(
                """INSERT INTO tasks (project_id, title, description, priority, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'todo', datetime('now'), datetime('now'))""",
                (item["project_id"], item["title"], task_desc, task_priority),
            )
            task_id = cursor.lastrowid
            conn.execute(
                """UPDATE project_analysis_items
                   SET status = 'converted', converted_task_id = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (task_id, item_id),
            )
            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('project_analysis_item', ?, 'converted_to_task', ?, datetime('now'))""",
                (item_id, json.dumps({"task_id": task_id, "title": item["title"]})),
            )
            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('task', ?, 'created', ?, datetime('now'))""",
                (task_id, json.dumps({"source": "project_analysis", "analysis_item_id": item_id})),
            )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM project_analysis_items WHERE id = ?", (item_id,)
            ).fetchone()
            conn.close()
            result = _row_to_dict(updated)
            result["task_id"] = task_id
            self._send_json(result, 201)
            return

        if path == "/api/auditions":
            data = self._read_body()
            project = data.get("project_name", "").strip()
            if not project:
                self._send_json({"error": "project_name required"}, 400)
                return

            conn = _get_db()
            source = data.get("source", "manual")
            agent_id = (
                2
                if source in ("castingworkbook", "actorsaccess", "weaudition")
                else None
            )

            cursor = conn.execute(
                """INSERT INTO auditions
                   (project_name, role_name, role_type, production_type,
                    casting_director, casting_company, agent_contact_id,
                    source, status, received_at, deadline, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', datetime('now'), ?, ?)""",
                (
                    project,
                    data.get("role_name"),
                    data.get("role_type"),
                    data.get("production_type"),
                    data.get("casting_director"),
                    data.get("casting_company"),
                    agent_id,
                    source,
                    data.get("deadline"),
                    data.get("notes"),
                ),
            )
            aid = cursor.lastrowid
            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('audition', ?, 'created', 'Added from audition board', datetime('now'))""",
                (aid,),
            )
            conn.commit()

            row = conn.execute(
                "SELECT * FROM v_audition_pipeline WHERE id = ?", (aid,)
            ).fetchone()
            conn.close()
            self._send_json(_row_to_dict(row), 201)
            return

        # ── Create Annotation ─────────────────────────────────────

        # ── Writing Module: Save Draft ─────────────────────────────────
        m = re.match(r"^/api/writing/drafts/(\d+)/save$", path)
        if m:
            draft_id = int(m.group(1))
            data = self._read_body()
            new_content = data.get("content")
            change_summary = (data.get("change_summary") or "").strip() or None

            if not new_content:
                self._send_json({"error": "content is required"}, 400)
                return

            conn = _get_db()
            draft = conn.execute(
                "SELECT id, current_version, title FROM writing_drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
            if not draft:
                conn.close()
                self._send_json({"error": "Draft not found"}, 404)
                return

            draft_dict = _row_to_dict(draft)
            old_version = draft_dict["current_version"]
            new_version = old_version + 1
            wc = len(new_content.split())

            conn.execute(
                """INSERT INTO draft_versions (draft_id, version_number, content, word_count, change_summary, created_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                (draft_id, new_version, new_content, wc, change_summary),
            )
            conn.execute(
                """UPDATE writing_drafts
                   SET current_version = ?, word_count = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (new_version, wc, draft_id),
            )
            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('writing_draft', ?, 'version_saved', ?, datetime('now'))""",
                (
                    draft_id,
                    json.dumps({
                        "version": new_version,
                        "word_count": wc,
                        "change_summary": change_summary,
                        "title": draft_dict["title"],
                    }),
                ),
            )
            conn.commit()
            conn.close()

            self._send_json({
                "draft_id": draft_id,
                "version_number": new_version,
                "word_count": wc,
                "change_summary": change_summary,
            }, 201)
            return

        # ── Writing Module: Process Open Feedback via Claude CLI ──────
        if path == "/api/writing/process-feedback":
            data = self._read_body()
            project_id = data.get("project_id")
            if not project_id:
                self._send_json({"error": "project_id is required"}, 400)
                return

            conn = _get_db()

            # 1. Get open feedback with draft context
            open_fb = conn.execute("""
                SELECT df.id, df.draft_id, df.feedback_type, df.content, df.highlighted_text,
                       d.title as draft_title, d.pov_character, d.synopsis, d.notes
                FROM draft_feedback df
                JOIN writing_drafts d ON d.id = df.draft_id
                WHERE d.project_id = ? AND df.status = 'open'
                ORDER BY d.sort_order, df.created_at
            """, (int(project_id),)).fetchall()

            if not open_fb:
                conn.close()
                self._send_json({"message": "No open feedback to process", "processed": 0})
                return

            open_items = [_row_to_dict(r) for r in open_fb]

            # 2. Get selective creative context (characters + active decisions only)
            context_parts = []
            try:
                for r in conn.execute("""
                    SELECT title, content FROM creative_context
                    WHERE project_id = ? AND context_type IN ('character', 'decision')
                    AND status = 'active'
                    ORDER BY context_type, title
                """, (int(project_id),)).fetchall():
                    d = _row_to_dict(r)
                    context_parts.append(f"### {d['title']}\n{d['content'][:2000]}")
            except Exception:
                pass

            # 3. Get project description
            proj = conn.execute("SELECT name, description FROM projects WHERE id = ?", (int(project_id),)).fetchone()
            proj_info = _row_to_dict(proj) if proj else {"name": "Unknown", "description": ""}

            conn.close()

            # 4. Build prompt
            context_block = "\n\n".join(context_parts[:10])  # Cap at 10 entries for token budget

            feedback_block = "\n\n".join([
                f"FEEDBACK #{fb['id']} on \"{fb['draft_title']}\" (POV: {fb['pov_character']})\n"
                f"Type: {fb['feedback_type']}\n"
                f"Chapter synopsis: {fb['synopsis']}\n"
                f"Chapter notes: {fb['notes'] or 'none'}\n"
                f"{'Highlighted: ' + fb['highlighted_text'] + chr(10) if fb['highlighted_text'] else ''}"
                f"Feedback: {fb['content']}"
                for fb in open_items
            ])

            prompt = f"""You are a creative collaborator on a literary fiction project: "{proj_info['name']}" — {proj_info['description'] or ''}

## Creative Context (characters, decisions)
{context_block}

## Open Feedback to Process
{feedback_block}

## Instructions
Respond to each feedback item. You are a creative collaborator, not an assistant — push back where warranted, offer alternatives, dig deeper. Be concise but substantive (2-4 sentences per item).

Return ONLY a JSON array. Each element must have exactly these fields:
- "id": the feedback ID number
- "resolution": your response text

Example: [{{"id": 4, "resolution": "The shoopuf scene works better understated..."}}]

Return ONLY the JSON array, no markdown fences, no other text."""

            # 5. Call claude CLI
            import subprocess
            try:
                result = subprocess.run(
                    ["/Users/mrlovelies/.local/bin/claude", "-p", prompt],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode != 0:
                    self._send_json({"error": f"Claude CLI error: {result.stderr[:200]}"}, 500)
                    return

                raw = result.stdout.strip()
                # Strip markdown fences if present
                if "```" in raw:
                    import re as _re
                    fence_match = _re.search(r'```(?:json)?\s*\n?(.*?)```', raw, _re.DOTALL)
                    if fence_match:
                        raw = fence_match.group(1).strip()
                # Extract JSON array from surrounding text
                bracket_start = raw.find('[')
                bracket_end = raw.rfind(']')
                if bracket_start != -1 and bracket_end != -1:
                    raw = raw[bracket_start:bracket_end + 1]
                raw = raw.strip()

                resolutions = json.loads(raw)
            except subprocess.TimeoutExpired:
                self._send_json({"error": "Processing timed out (2 min)"}, 504)
                return
            except json.JSONDecodeError as e:
                self._send_json({"error": f"Failed to parse response: {str(e)[:100]}", "raw": raw[:500]}, 500)
                return
            except Exception as e:
                self._send_json({"error": f"Processing failed: {str(e)[:200]}"}, 500)
                return

            # 6. Store resolutions
            conn = _get_db()
            processed = 0
            for item in resolutions:
                fb_id = item.get("id")
                resolution = item.get("resolution")
                if fb_id and resolution:
                    conn.execute("""
                        UPDATE draft_feedback
                        SET resolution = ?, status = 'addressed', resolved_at = datetime('now')
                        WHERE id = ? AND status = 'open'
                    """, (resolution, int(fb_id)))
                    processed += 1
            conn.commit()
            conn.close()

            self._send_json({"processed": processed, "total": len(open_items)}, 200)
            return

        # ── Writing Module: Create Feedback ───────────────────────────
        if path == "/api/writing/feedback":
            data = self._read_body()
            draft_id = data.get("draft_id")
            highlighted_text = (data.get("highlighted_text") or "").strip()
            note_content = (data.get("content") or "").strip()
            feedback_type = data.get("feedback_type", "note")
            author = data.get("author", "user")
            version_number = data.get("version_number")

            if not draft_id or not note_content:
                self._send_json({"error": "draft_id and content are required"}, 400)
                return
            if feedback_type not in ("note", "revision", "critique", "suggestion", "question"):
                self._send_json({"error": "Invalid feedback_type"}, 400)
                return
            if author not in ("user", "ai", "editor"):
                self._send_json({"error": "Invalid author"}, 400)
                return

            conn = _get_db()
            draft = conn.execute("SELECT id, current_version FROM writing_drafts WHERE id = ?", (int(draft_id),)).fetchone()
            if not draft:
                conn.close()
                self._send_json({"error": "Draft not found"}, 404)
                return

            vn = version_number if version_number else _row_to_dict(draft)["current_version"]
            cursor = conn.execute(
                """INSERT INTO draft_feedback (draft_id, version_number, feedback_type, author, highlighted_text, content)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (int(draft_id), vn, feedback_type, author, highlighted_text or None, note_content),
            )
            fid = cursor.lastrowid
            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('draft_feedback', ?, 'created', ?, datetime('now'))""",
                (fid, json.dumps({"draft_id": int(draft_id), "type": feedback_type, "author": author})),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM draft_feedback WHERE id = ?", (fid,)).fetchone()
            conn.close()
            self._send_json(_row_to_dict(row), 201)
            return

        if path == "/api/annotations":
            data = self._read_body()
            context_id = data.get("context_id")
            highlighted_text = (data.get("highlighted_text") or "").strip()
            note = (data.get("note") or "").strip()
            annotation_type = data.get("annotation_type", "observation")
            author = data.get("author", "user")

            if not context_id or not highlighted_text or not note:
                self._send_json({"error": "context_id, highlighted_text, and note are required"}, 400)
                return
            if annotation_type not in ("correction", "question", "idea", "observation"):
                self._send_json({"error": "Invalid annotation_type"}, 400)
                return
            if author not in ("user", "ai"):
                self._send_json({"error": "author must be 'user' or 'ai'"}, 400)
                return

            conn = _get_db()
            ctx = conn.execute("SELECT id FROM creative_context WHERE id = ?", (context_id,)).fetchone()
            if not ctx:
                conn.close()
                self._send_json({"error": "Context entry not found"}, 404)
                return

            cursor = conn.execute(
                """INSERT INTO lore_annotations (context_id, highlighted_text, note, annotation_type, author)
                   VALUES (?, ?, ?, ?, ?)""",
                (context_id, highlighted_text, note, annotation_type, author),
            )
            aid = cursor.lastrowid
            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('lore_annotation', ?, 'created', ?, datetime('now'))""",
                (aid, json.dumps({"context_id": context_id, "type": annotation_type, "author": author})),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM lore_annotations WHERE id = ?", (aid,)).fetchone()
            conn.close()
            self._send_json(_row_to_dict(row), 201)
            return

        # ── Create / Reply to Creative Thread ─────────────────────
        if path == "/api/threads":
            data = self._read_body()
            thread_id = data.get("thread_id")

            # Reply to existing thread (appends, never overwrites)
            if thread_id:
                response_text = (data.get("response") or "").strip()
                if not response_text:
                    self._send_json({"error": "response is required"}, 400)
                    return
                author = (data.get("author") or "user").strip()
                conn = _get_db()
                thread = conn.execute("SELECT * FROM creative_threads WHERE id = ?", (thread_id,)).fetchone()
                if not thread:
                    conn.close()
                    self._send_json({"error": "Thread not found"}, 404)
                    return
                existing = (thread["response"] or "").strip()
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                new_entry = f"[{author} · {timestamp}] {response_text}"
                combined = f"{existing}\n\n{new_entry}" if existing else new_entry
                conn.execute(
                    """UPDATE creative_threads
                       SET response = ?, status = 'answered', updated_at = datetime('now')
                       WHERE id = ?""",
                    (combined, thread_id),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM creative_threads WHERE id = ?", (thread_id,)).fetchone()
                conn.close()
                self._send_json(_row_to_dict(row))
                return

            # Create new thread
            prompt = (data.get("prompt") or "").strip()
            if not prompt:
                self._send_json({"error": "prompt is required"}, 400)
                return
            project_id = data.get("project_id")
            author = data.get("author", "user")
            thread_type = data.get("thread_type", "question")
            tags = data.get("tags", "")

            if author not in ("user", "ai"):
                self._send_json({"error": "author must be 'user' or 'ai'"}, 400)
                return
            if thread_type not in ("question", "provocation", "observation", "idea"):
                self._send_json({"error": "Invalid thread_type"}, 400)
                return

            conn = _get_db()
            cursor = conn.execute(
                """INSERT INTO creative_threads (project_id, author, thread_type, prompt, tags)
                   VALUES (?, ?, ?, ?, ?)""",
                (project_id, author, thread_type, prompt, tags or None),
            )
            tid = cursor.lastrowid
            conn.commit()
            row = conn.execute("SELECT * FROM creative_threads WHERE id = ?", (tid,)).fetchone()
            conn.close()
            self._send_json(_row_to_dict(row), 201)
            return

        # ── Learning Feedback ─────────────────────────────────────
        if path == "/api/learning/feedback":
            data = self._read_body()
            digest_id = data.get("digest_id")
            section_id = data.get("section_id")
            reaction = data.get("reaction")
            comment = data.get("comment")

            if not digest_id or not section_id or not reaction:
                self._send_json({"error": "digest_id, section_id, and reaction are required"}, 400)
                return

            valid_reactions = ("got_it", "tell_me_more", "too_basic", "too_advanced", "this_clicked")
            if reaction not in valid_reactions:
                self._send_json({"error": f"reaction must be one of: {', '.join(valid_reactions)}"}, 400)
                return

            conn = _get_db()
            # Verify digest exists
            digest = conn.execute(
                "SELECT id FROM learning_digests WHERE id = ?", (digest_id,)
            ).fetchone()
            if not digest:
                conn.close()
                self._send_json({"error": "Digest not found"}, 404)
                return

            # Delete any existing feedback for this section (mutually exclusive reactions)
            conn.execute(
                "DELETE FROM learning_feedback WHERE digest_id = ? AND section_id = ?",
                (digest_id, section_id),
            )
            cursor = conn.execute(
                """INSERT INTO learning_feedback (digest_id, section_id, reaction, comment, created_at)
                   VALUES (?, ?, ?, ?, datetime('now'))""",
                (digest_id, section_id, reaction, comment),
            )
            conn.commit()

            fb = conn.execute(
                "SELECT * FROM learning_feedback WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            conn.close()

            # Update learning profile incrementally
            try:
                import importlib.util
                profile_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "modules", "learning", "profile.py",
                )
                spec = importlib.util.spec_from_file_location("learning_profile", profile_path)
                profile_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(profile_mod)
                profile_mod.update_from_feedback(digest_id, section_id, reaction)
            except Exception:
                pass  # Profile update is best-effort

            self._send_json(_row_to_dict(fb), 201)
            return

        if path == "/api/shutdown":
            self._send_json({"status": "shutting down"})
            import threading

            threading.Thread(target=self.server.shutdown).start()
            return

        self._send_json({"error": "Not found"}, 404)

    # ── DELETE ────────────────────────────────────────────────────────
    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # ── Delete Writing Feedback ──────────────────────────────────
        m = re.match(r"^/api/writing/feedback/(\d+)$", path)
        if m:
            fid = int(m.group(1))
            conn = _get_db()
            row = conn.execute("SELECT id FROM draft_feedback WHERE id = ?", (fid,)).fetchone()
            if not row:
                conn.close()
                self._send_json({"error": "Not found"}, 404)
                return
            conn.execute("DELETE FROM draft_feedback WHERE id = ?", (fid,))
            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('draft_feedback', ?, 'deleted', '{}', datetime('now'))""",
                (fid,),
            )
            conn.commit()
            conn.close()
            self._send_json({"deleted": fid})
            return

        m = re.match(r"^/api/projects/(\d+)$", path)
        if m:
            pid = int(m.group(1))
            conn = _get_db()
            project = conn.execute(
                "SELECT id, name FROM projects WHERE id = ?", (pid,)
            ).fetchone()
            if not project:
                conn.close()
                self._send_json({"error": "Not found"}, 404)
                return

            project_name = project["name"]

            # Collect generated view files to delete from disk
            view_files = []
            for r in conn.execute(
                "SELECT filename FROM generated_views WHERE entity_type = 'project' AND entity_id = ?",
                (pid,),
            ).fetchall():
                view_files.append(r["filename"])

            # Also collect shared page files
            for r in conn.execute(
                "SELECT filename FROM generated_views WHERE filename LIKE ? OR filename LIKE ?",
                (f"share-project-{pid}-%", f"share-{project_name.lower().replace(' ', '-')}%"),
            ).fetchall():
                if r["filename"] not in view_files:
                    view_files.append(r["filename"])

            # Delete generated_views entries
            conn.execute(
                "DELETE FROM generated_views WHERE entity_type = 'project' AND entity_id = ?",
                (pid,),
            )

            # Delete activity log entries
            conn.execute(
                "DELETE FROM activity_log WHERE entity_type = 'project' AND entity_id = ?",
                (pid,),
            )

            # Delete entity tags
            conn.execute(
                "DELETE FROM entity_tags WHERE entity_type = 'project' AND entity_id = ?",
                (pid,),
            )

            # Delete notes on project
            conn.execute(
                "DELETE FROM notes WHERE entity_type = 'project' AND entity_id = ?",
                (pid,),
            )

            # Delete the project (cascades to tasks, milestones, analyses via FK constraints)
            conn.execute("DELETE FROM projects WHERE id = ?", (pid,))

            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('system', 0, 'project_deleted', ?, datetime('now'))""",
                (json.dumps({"project_id": pid, "name": project_name}),),
            )
            conn.commit()
            conn.close()

            # Delete HTML files from disk
            for filename in view_files:
                filepath = os.path.join(OUTPUT_DIR, filename)
                if os.path.isfile(filepath):
                    os.remove(filepath)
                # Also check share/ subdirectory
                share_path = os.path.join(OUTPUT_DIR, "share", filename)
                if os.path.isfile(share_path):
                    os.remove(share_path)

            self._send_json({"deleted": True, "name": project_name})
            return

        m = re.match(r"^/api/contacts/(\d+)$", path)
        if m:
            cid = int(m.group(1))
            conn = _get_db()
            contact = conn.execute(
                "SELECT id, name FROM contacts WHERE id = ?", (cid,)
            ).fetchone()
            if not contact:
                conn.close()
                self._send_json({"error": "Not found"}, 404)
                return

            contact_name = contact["name"]

            # Collect generated view files to delete from disk
            view_files = []
            for r in conn.execute(
                "SELECT filename FROM generated_views WHERE entity_type = 'contact' AND entity_id = ?",
                (cid,),
            ).fetchall():
                view_files.append(r["filename"])

            # Delete generated_views entries
            conn.execute(
                "DELETE FROM generated_views WHERE entity_type = 'contact' AND entity_id = ?",
                (cid,),
            )

            # Delete activity log entries
            conn.execute(
                "DELETE FROM activity_log WHERE entity_type = 'contact' AND entity_id = ?",
                (cid,),
            )

            # Delete entity tags
            conn.execute(
                "DELETE FROM entity_tags WHERE entity_type = 'contact' AND entity_id = ?",
                (cid,),
            )

            # Delete notes on contact
            conn.execute(
                "DELETE FROM notes WHERE entity_type = 'contact' AND entity_id = ?",
                (cid,),
            )

            # Delete the contact (cascades interactions, follow-ups, relationships via FK constraints)
            conn.execute("DELETE FROM contacts WHERE id = ?", (cid,))

            conn.execute(
                """INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
                   VALUES ('system', 0, 'contact_deleted', ?, datetime('now'))""",
                (json.dumps({"contact_id": cid, "name": contact_name}),),
            )
            conn.commit()
            conn.close()

            # Delete HTML files from disk
            for filename in view_files:
                filepath = os.path.join(OUTPUT_DIR, filename)
                if os.path.isfile(filepath):
                    os.remove(filepath)

            self._send_json({"deleted": True, "name": contact_name})
            return

        self._send_json({"error": "Not found"}, 404)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

    def handle_signal(sig, frame):
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    server = HTTPServer(("0.0.0.0", port), SoYHandler)
    print(
        json.dumps({"status": "running", "port": port, "url": f"http://localhost:{port}"})
    )
    sys.stdout.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
