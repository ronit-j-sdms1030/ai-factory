"""Execution Router — engine choice is an audited decision."""

from __future__ import annotations

import os
from typing import Any


def choose(ticket: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any]:
    values = env if env is not None else os.environ
    title = str(ticket.get("title") or "").lower()
    paths = list(ticket.get("paths") or [])
    if "schema" in title or "migration" in title or any("prisma" in p or "alembic" in p for p in paths):
        engine = "agentless"
        reason = "localised schema change"
    elif len(paths) > 1 or "screen" in title:
        engine = "openhands" if values.get("OPENHANDS_URL") else "agentless"
        reason = "multi-file work"
    else:
        engine = "swe-agent" if values.get("SWE_AGENT") else "agentless"
        reason = "exploratory ticket"
    return {"engine": engine, "reason": reason, "ticket": ticket.get("id")}
