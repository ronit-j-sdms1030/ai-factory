"""SQLite stand-in for Postgres: requirement record, chain, workflow position.

Append-only event log sits beside the current snapshot so a redeploy cannot
lose a decision that had already been applied.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def next_id(self) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        n = 1
        if row:
            try:
                n = int(str(row[0]).split("-")[-1]) + 1
            except ValueError:
                n = 1
        return f"REQ-{n:04d}"

    def put(self, requirement_id: str, body: dict[str, Any], *, at: str) -> None:
        payload = json.dumps(body, sort_keys=True)
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs(id, body, updated) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET body=excluded.body, updated=excluded.updated",
                (requirement_id, payload, at),
            )
            self._conn.commit()

    def get(self, requirement_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT body FROM runs WHERE id = ?", (requirement_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT body FROM runs ORDER BY id").fetchall()
        return [json.loads(r[0]) for r in rows]

    def append_event(self, requirement_id: str, kind: str, body: dict[str, Any], *, at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(requirement_id, kind, body, at) VALUES(?,?,?,?)",
                (requirement_id, kind, json.dumps(body, sort_keys=True), at),
            )
            self._conn.commit()
