"""State-store adapters."""

from __future__ import annotations

import json
from typing import Any

from phase1.persist import Store as SQLiteStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, body JSONB NOT NULL, updated TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  seq BIGSERIAL PRIMARY KEY, requirement_id TEXT NOT NULL, kind TEXT NOT NULL,
  body JSONB NOT NULL, at TIMESTAMPTZ NOT NULL
);
"""


class PostgresStore:
    """psycopg-backed equivalent of the local SQLite store."""

    def __init__(self, dsn: str):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL adapter requires the 'postgres' extra") from exc
        self._conn = psycopg.connect(dsn)
        with self._conn.cursor() as cur:
            cur.execute(SCHEMA)
        self._conn.commit()

    def next_id(self) -> str:
        with self._conn.cursor() as cur:
            cur.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        try:
            number = int(str(row[0]).split("-")[-1]) + 1 if row else 1
        except ValueError:
            number = 1
        return f"REQ-{number:04d}"

    def put(self, requirement_id: str, body: dict[str, Any], *, at: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO runs(id,body,updated) VALUES(%s,%s::jsonb,%s) "
                "ON CONFLICT(id) DO UPDATE SET body=excluded.body,updated=excluded.updated",
                (requirement_id, json.dumps(body, sort_keys=True), at),
            )
        self._conn.commit()

    def get(self, requirement_id: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT body FROM runs WHERE id=%s", (requirement_id,))
            row = cur.fetchone()
        return dict(row[0]) if row else None

    def all(self) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT body FROM runs ORDER BY id")
            return [dict(row[0]) for row in cur.fetchall()]

    def append_event(
        self, requirement_id: str, kind: str, body: dict[str, Any], *, at: str
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events(requirement_id,kind,body,at) VALUES(%s,%s,%s::jsonb,%s)",
                (requirement_id, kind, json.dumps(body, sort_keys=True), at),
            )
        self._conn.commit()


__all__ = ["PostgresStore", "SQLiteStore"]
