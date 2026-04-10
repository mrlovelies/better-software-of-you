#!/usr/bin/env python3
"""
SoY shared logging — structured error logging and event emission.

Usage:
    from soy_logging import get_logger, emit_event, log_error

    logger = get_logger("learning")
    logger.warning("Something unexpected: %s", detail)

    emit_event("digest_generated", "learning", "Daily digest: 6 sections",
               entity_type="digest", metadata={"sections": 6})

    log_error("learning", error, context={"stage": "generation"})
"""

import json
import logging
import os
import socket
import sqlite3
import sys
import traceback
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "software-of-you" / "soy.db"
LOG_DIR = Path.home() / ".local" / "share" / "software-of-you" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

THIS_MACHINE = socket.gethostname()


def get_logger(name: str) -> logging.Logger:
    """Get a file-backed logger for a SoY module."""
    logger = logging.getLogger(f"soy.{name}")
    if not logger.handlers:
        fh = logging.FileHandler(LOG_DIR / f"{name}.log")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
        logger.setLevel(logging.WARNING)
    return logger


def emit_event(
    event_type: str,
    source: str,
    summary: str,
    entity_type: str = None,
    entity_id: int = None,
    metadata: dict = None,
):
    """Write an event to the unified event bus."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.execute(
            """INSERT INTO events (event_type, source, summary, entity_type, entity_id, metadata, machine)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event_type,
                source,
                summary,
                entity_type,
                entity_id,
                json.dumps(metadata) if metadata else None,
                THIS_MACHINE,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        # Last resort — don't let event emission crash the caller
        print(f"[EVENT] [{source}] {event_type}: {summary}", file=sys.stderr)


def log_error(source: str, error: Exception, context: dict = None):
    """Log an error to the event bus and stderr."""
    tb = traceback.format_exc()
    summary = f"{type(error).__name__}: {error}"

    # Write to event bus
    emit_event(
        "error",
        source,
        summary,
        metadata={
            "traceback": tb,
            "context": context or {},
        },
    )

    # Also write to module log file
    logger = get_logger(source)
    logger.error("%s | context=%s", summary, context or {})

    # And stderr for cron job visibility
    print(f"[ERROR] [{source}] {summary}", file=sys.stderr)
