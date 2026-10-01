"""SQLite stand-in for the assembled product. Same schema the Node app writes."""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any

# Form fields the BRD never listed still get a column, so a Save keeps every input.
_SAFE_COL = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

from phase4 import product


def db_path(root: Path, requirement_id: str) -> Path:
    folder = Path(root) / "apps"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{requirement_id}.sqlite"


def connect(root: Path, requirement_id: str, brd_text: str) -> sqlite3.Connection:
    path = db_path(root, requirement_id)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    sql = product.migration_sql(product.parse_entities(brd_text))
    conn.executescript(sql.split("-- down")[0])
    return conn


def _ensure_columns(
    conn: sqlite3.Connection,
    resource: str,
    payload: dict[str, Any],
    allowed: set[str],
) -> set[str]:
    """Add a TEXT column for each safe form key the table does not have yet."""
    existing = set(allowed)
    for row in conn.execute(f"PRAGMA table_info({resource})").fetchall():
        existing.add(str(row[1]))
    for key in payload:
        if key in existing or not _SAFE_COL.fullmatch(str(key)):
            continue
        conn.execute(f"ALTER TABLE {resource} ADD COLUMN {key} TEXT")
        existing.add(key)
    return existing


def handle(
    root: Path,
    requirement_id: str,
    brd_text: str,
    method: str,
    resource: str,
    item_id: str = "",
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    entities = product.parse_entities(brd_text)
    tables = {product.table_name(name): fields for name, fields in entities}
    if resource == "health":
        return 200, {"ok": True, "id": requirement_id, "tables": sorted(tables)}
    if resource == "ai":
        room = str((body or {}).get("room") or "Room A")
        return 200, {
            "room": room,
            "hint": "Suggested next slot from recent bookings",
            "starts_at": str((body or {}).get("starts_at") or "10:00"),
        }
    if resource not in tables:
        return 404, {"error": "unknown resource"}
    conn = connect(root, requirement_id, brd_text)
    try:
        if method == "GET" and not item_id:
            rows = conn.execute(f"SELECT * FROM {resource}").fetchall()
            return 200, [dict(row) for row in rows]
        if method == "GET":
            row = conn.execute(f"SELECT * FROM {resource} WHERE id = ?", (item_id,)).fetchone()
            return (200, dict(row)) if row else (404, {"error": "missing"})
        if method == "POST":
            payload = dict(body or {})
            ident = str(payload.get("id") or f"{resource}-{int(time.time() * 1000)}")
            payload["id"] = ident
            allowed = _ensure_columns(conn, resource, payload, set(tables[resource]) | {"id"})
            cols = [key for key in payload if key in allowed]
            marks = ",".join("?" for _ in cols)
            conn.execute(
                f"INSERT OR REPLACE INTO {resource} ({','.join(cols)}) VALUES ({marks})",
                [payload[key] for key in cols],
            )
            conn.commit()
            return 201, payload
        if method == "DELETE" and item_id:
            conn.execute(f"DELETE FROM {resource} WHERE id = ?", (item_id,))
            conn.commit()
            return 200, {"ok": True}
        return 405, {"error": "method"}
    finally:
        conn.close()


def parse_preview_api(path: str) -> tuple[str, str, str] | None:
    """/preview/REQ-0010/api/rooms/abc → (REQ-0010, rooms, abc)."""
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 4 or parts[0] != "preview" or parts[2] != "api":
        return None
    rid = parts[1]
    resource = parts[3]
    item_id = parts[4] if len(parts) > 4 else ""
    return rid, resource, item_id

