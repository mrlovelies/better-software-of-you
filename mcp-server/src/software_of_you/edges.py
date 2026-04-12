"""Edge creation helpers for the entity_edges graph.

Call these after primary record creation to keep the relationship graph
growing as the user works. All functions are idempotent (INSERT OR IGNORE)
and failure-tolerant — a failed edge never breaks the primary operation.
"""

import json
import logging

logger = logging.getLogger(__name__)


def create_edge(src_type, src_id, dst_type, dst_id, edge_type,
                weight=1.0, source="auto", metadata=None):
    """Create a single entity_edge. Silently skips duplicates."""
    if not src_id or not dst_id:
        return
    from software_of_you.db import get_connection
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO entity_edges
               (src_type, src_id, dst_type, dst_id, edge_type, weight, source, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (src_type, int(src_id), dst_type, int(dst_id), edge_type,
             weight, source, json.dumps(metadata) if metadata else None),
        )
        conn.commit()
    except Exception as e:
        logger.debug("Edge creation skipped: %s", e)
    finally:
        conn.close()


def create_edges(edges):
    """Create multiple entity_edges in one transaction.

    Each edge is a dict with keys: src_type, src_id, dst_type, dst_id, edge_type.
    Optional keys: weight (default 1.0), source (default 'auto'), metadata.
    """
    edges = [e for e in edges if e.get("src_id") and e.get("dst_id")]
    if not edges:
        return
    from software_of_you.db import get_connection
    conn = get_connection()
    try:
        for edge in edges:
            conn.execute(
                """INSERT OR IGNORE INTO entity_edges
                   (src_type, src_id, dst_type, dst_id, edge_type, weight, source, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (edge["src_type"], int(edge["src_id"]),
                 edge["dst_type"], int(edge["dst_id"]),
                 edge["edge_type"], edge.get("weight", 1.0),
                 edge.get("source", "auto"),
                 json.dumps(edge["metadata"]) if edge.get("metadata") else None),
            )
        conn.commit()
    except Exception as e:
        logger.debug("Batch edge creation issue: %s", e)
    finally:
        conn.close()


def last_id_for(table, where_clause="1=1", params=()):
    """Get the most recent row ID from a table. Single-user SQLite safe."""
    from software_of_you.db import execute
    rows = execute(
        f"SELECT MAX(id) as max_id FROM {table} WHERE {where_clause}", params
    )
    if rows and rows[0]["max_id"] is not None:
        return rows[0]["max_id"]
    return None
