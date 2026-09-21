"""Monitor Agent — errors, latency, spend become Change Requests."""

from __future__ import annotations

from typing import Any


def snapshot(requirement_id: str, signals: dict[str, Any] | None = None) -> dict[str, Any]:
    signals = signals or {}
    incidents = list(signals.get("incidents") or [])
    return {
        "requirement_id": requirement_id,
        "errors": int(signals.get("errors") or 0),
        "latency_ms": int(signals.get("latency_ms") or 0),
        "spend": signals.get("spend") or "unknown",
        "incidents": incidents,
        "open_change_requests": [row["id"] for row in incidents],
    }
