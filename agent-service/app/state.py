"""Shared graph state.

Replaces the arguments threaded between the JavaScript service functions and
the ``currentStage`` string the routes interpret. Every node reads and writes
this one typed object; LangGraph checkpoints it at each node boundary, which
is what makes a gate resumable days later.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def _merge(existing: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    """Last-write-wins merge, used for the approvals map."""
    return {**(existing or {}), **(incoming or {})}


class PipelineState(TypedDict, total=False):
    # ── identity ─────────────────────────────────────────────────────────────
    artifact_id: str
    title: str
    # {"user_id": str, "tier_id": str | None} — tier_id None means external client
    originator: dict[str, Any]

    # ── intake ───────────────────────────────────────────────────────────────
    # Appended to, never replaced, so a resumed turn adds rather than clobbers.
    chat_history: Annotated[list[dict[str, str]], operator.add]
    requirement: dict[str, Any]

    # ── downstream artefacts ─────────────────────────────────────────────────
    brd: dict[str, Any]
    critique_rounds: int
    ui: dict[str, Any]
    work_items: dict[str, Any]

    # ── governance ───────────────────────────────────────────────────────────
    # Chain of {"approver_tiers": [...], "mode": "any", "approved_by": [...]}
    approval_chain: list[dict[str, Any]]
    current_approval_index: int
    # gate name -> decision record
    approvals: Annotated[dict[str, Any], _merge]
    # Set when a gate rejects outright; the graph routes to END.
    rejected: dict[str, Any]
