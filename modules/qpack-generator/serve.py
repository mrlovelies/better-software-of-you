#!/usr/bin/env python3
"""
QPack HTTP API — Lightweight server exposing QPack functionality over HTTP.

Endpoints:
    GET  /api/qpacks             — List all available QPack files with metadata
    GET  /api/qpacks/{module}    — Get a specific QPack file content
    GET  /api/suggestions        — Get smart suggestions
    POST /api/qpacks/execute     — Execute a question
    POST /api/qpacks/route       — Route a natural language query
    GET  /api/qpacks/health      — Run health check and return report

Usage:
    python3 modules/qpack-generator/serve.py              # Start on :8788
    python3 modules/qpack-generator/serve.py --port 9000  # Custom port
"""

import argparse
import json
import sqlite3
import sys
import traceback
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
QPACK_DIR = Path(__file__).resolve().parents[2] / "qpacks"

# Module root for imports
_mod_dir = Path(__file__).resolve().parent
if str(_mod_dir) not in sys.path:
    sys.path.insert(0, str(_mod_dir))


DEFAULT_PORT = 8788


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_qpack(module: str) -> dict | None:
    """Load a single QPack file by module name."""
    filepath = QPACK_DIR / f"{module}.qpack.json"
    if filepath.exists():
        return json.loads(filepath.read_text())
    return None


def _load_all_qpacks() -> dict:
    """Load all QPack files from disk."""
    qpacks = {}
    if QPACK_DIR.exists():
        for f in sorted(QPACK_DIR.glob("*.qpack.json")):
            try:
                data = json.loads(f.read_text())
                module = data.get("module", f.stem.replace(".qpack", ""))
                qpacks[module] = data
            except (json.JSONDecodeError, OSError):
                pass
    return qpacks


def _find_question(question_id: str) -> dict | None:
    """Find a question across all QPack files."""
    qpacks = _load_all_qpacks()
    for module_name, qpack in qpacks.items():
        for q in qpack.get("questions", []):
            if q.get("id") == question_id:
                return q
    return None


def _execute_question(question: dict, params: dict = None) -> dict:
    """Execute a question's context queries and return raw results."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    queries = {}
    for cq in question.get("context_queries", []):
        key = cq.get("key", "unknown")
        sql = cq.get("sql", "")

        # Substitute parameters — sanitize to prevent SQL injection
        if params:
            import re
            for pkey, pval in params.items():
                sanitized = re.sub(r"[;'\"\-\-\\]", "", str(pval))
                sql = sql.replace(f"{{{pkey}}}", sanitized)

        try:
            rows = db.execute(sql).fetchall()
            columns = []
            row_dicts = []
            if rows:
                columns = list(rows[0].keys())
                row_dicts = [dict(row) for row in rows]
            queries[key] = {
                "rows": row_dicts,
                "columns": columns,
                "row_count": len(row_dicts),
            }
        except sqlite3.OperationalError as e:
            queries[key] = {
                "rows": [],
                "columns": [],
                "row_count": 0,
                "error": str(e),
            }

    db.close()
    return {"queries": queries}


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class QPackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the QPack API."""

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400):
        self._send_json({"error": message}, status=status)

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if path == "/api/qpacks":
            self._handle_list_qpacks()
        elif path == "/api/suggestions":
            self._handle_suggestions()
        elif path == "/api/qpacks/health":
            self._handle_health()
        elif path.startswith("/api/qpacks/"):
            module = path.split("/api/qpacks/")[1]
            self._handle_get_qpack(module)
        else:
            self._send_error_json("Not found", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/qpacks/execute":
            self._handle_execute()
        elif path == "/api/qpacks/route":
            self._handle_route()
        else:
            self._send_error_json("Not found", 404)

    # -- GET /api/qpacks --
    def _handle_list_qpacks(self):
        qpacks = _load_all_qpacks()
        result = []
        for module, data in qpacks.items():
            questions = data.get("questions", [])
            result.append({
                "module": module,
                "version": data.get("version"),
                "persona": data.get("persona", {}).get("name"),
                "question_count": len(questions),
                "featured": [q["id"] for q in questions if q.get("featured")],
                "data_tier": data.get("data_tier"),
                "generated_at": data.get("generated_at"),
            })
        self._send_json({"qpacks": result, "count": len(result)})

    # -- GET /api/qpacks/{module} --
    def _handle_get_qpack(self, module: str):
        data = _load_qpack(module)
        if data is None:
            self._send_error_json(f"QPack '{module}' not found", 404)
            return
        self._send_json(data)

    # -- GET /api/suggestions --
    def _handle_suggestions(self):
        try:
            from suggestions import get_smart_suggestions
            suggestions = get_smart_suggestions()
            self._send_json({"suggestions": suggestions})
        except ImportError:
            # suggestions.py not available — return featured questions as fallback
            qpacks = _load_all_qpacks()
            featured = []
            for module, data in qpacks.items():
                for q in data.get("questions", []):
                    if q.get("featured"):
                        featured.append({
                            "question_id": q["id"],
                            "label": q.get("label", ""),
                            "short_label": q.get("short_label", ""),
                            "module": module,
                        })
            self._send_json({"suggestions": featured, "source": "featured_fallback"})
        except Exception as e:
            self._send_error_json(f"Suggestions error: {e}", 500)

    # -- POST /api/qpacks/execute --
    def _handle_execute(self):
        body = self._read_body()
        question_id = body.get("question_id")
        params = body.get("params", {})

        if not question_id:
            self._send_error_json("Missing 'question_id' in request body")
            return

        question = _find_question(question_id)
        if not question:
            self._send_error_json(f"Question '{question_id}' not found", 404)
            return

        try:
            execution_result = _execute_question(question, params)

            # Format the answer
            try:
                from formatter import format_answer
                formatted = format_answer(question, execution_result)
            except ImportError:
                # Formatter not available — return raw results
                formatted = {
                    "format": "raw",
                    "execution_result": execution_result,
                }

            self._send_json({
                "question_id": question_id,
                "label": question.get("label", ""),
                "answer": formatted,
            })
        except Exception as e:
            self._send_error_json(f"Execution error: {e}", 500)

    # -- POST /api/qpacks/route --
    def _handle_route(self):
        body = self._read_body()
        query = body.get("query", "").strip()

        if not query:
            self._send_error_json("Missing 'query' in request body")
            return

        # Simple keyword-based routing across all QPack questions
        query_lower = query.lower()
        qpacks = _load_all_qpacks()

        scored = []
        for module, data in qpacks.items():
            for q in data.get("questions", []):
                score = 0
                keywords = q.get("keywords", [])
                label = q.get("label", "").lower()

                # Keyword match scoring
                for kw in keywords:
                    if kw.lower() in query_lower:
                        score += 2

                # Label word overlap
                label_words = set(label.split())
                query_words = set(query_lower.split())
                overlap = label_words & query_words
                score += len(overlap)

                # Featured bonus
                if q.get("featured"):
                    score += 1

                if score > 0:
                    scored.append({
                        "question_id": q["id"],
                        "label": q.get("label", ""),
                        "short_label": q.get("short_label", ""),
                        "module": module,
                        "score": score,
                        "requires_llm": q.get("requires_llm", False),
                        "answer_format": q.get("answer_format"),
                        "parameterized": q.get("parameterized", False),
                    })

        # Sort by score descending
        scored.sort(key=lambda x: x["score"], reverse=True)

        self._send_json({
            "query": query,
            "matches": scored[:10],
            "best_match": scored[0] if scored else None,
        })

    # -- GET /api/qpacks/health --
    def _handle_health(self):
        try:
            from steps.health import run_standalone
            report = run_standalone()
            self._send_json(report)
        except Exception as e:
            self._send_error_json(f"Health check error: {e}\n{traceback.format_exc()}", 500)

    def log_message(self, format, *args):
        """Override to add timestamp to log output."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sys.stdout.write(f"[{timestamp}] {self.address_string()} - {format % args}\n")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="QPack HTTP API server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to listen on (default: {DEFAULT_PORT})")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), QPackHandler)

    print(f"\n{'='*50}")
    print(f"  QPack API Server")
    print(f"  http://{args.host}:{args.port}")
    print(f"  QPack dir: {QPACK_DIR}")
    print(f"  DB: {DB_PATH}")
    print(f"{'='*50}")
    print(f"\n  Endpoints:")
    print(f"    GET  /api/qpacks           — List QPacks")
    print(f"    GET  /api/qpacks/{{module}} — Get QPack")
    print(f"    GET  /api/suggestions      — Smart suggestions")
    print(f"    POST /api/qpacks/execute   — Execute question")
    print(f"    POST /api/qpacks/route     — Route natural query")
    print(f"    GET  /api/qpacks/health    — Health report")
    print(f"\n  Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
