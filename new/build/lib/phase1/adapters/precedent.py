"""Approved-BRD precedent retrieval adapters."""

from __future__ import annotations

import json
from typing import Any

from phase1.adapters.protocols import Precedent


class DisabledPrecedentRetriever:
    def search(self, query: str, *, limit: int = 5, filters=None) -> list[Precedent]:
        del query, limit, filters
        return []


class PgvectorPrecedentRetriever:
    """Retrieves vectors supplied by an explicit embedding function."""

    def __init__(self, dsn: str, embed, *, table: str = "approved_brd_precedents"):
        if not table.replace("_", "").isalnum():
            raise ValueError("unsafe precedent table name")
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("pgvector adapter requires the 'postgres' extra") from exc
        self._conn = psycopg.connect(dsn)
        self.embed = embed
        self.table = table
        with self._conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {self.table} ("
                "document_id TEXT PRIMARY KEY, content TEXT NOT NULL, "
                "metadata JSONB NOT NULL DEFAULT '{}'::jsonb, embedding vector NOT NULL)"
            )
        self._conn.commit()

    def search(
        self, query: str, *, limit: int = 5, filters: dict[str, Any] | None = None
    ) -> list[Precedent]:
        vector = self.embed(query)
        sql = (
            f"SELECT document_id,content,metadata,1-(embedding <=> %s::vector) score "
            f"FROM {self.table} WHERE metadata @> %s::jsonb "
            "ORDER BY embedding <=> %s::vector LIMIT %s"
        )
        encoded = "[" + ",".join(str(float(x)) for x in vector) + "]"
        with self._conn.cursor() as cur:
            cur.execute(sql, (encoded, json.dumps(filters or {}), encoded, limit))
            rows = cur.fetchall()
        return [
            Precedent(str(row[0]), str(row[1]), float(row[3]), dict(row[2] or {}))
            for row in rows
        ]
