"""Change Request — post-release change re-enters at Phase 1 with the BRD sha."""

from __future__ import annotations

from typing import Any

import connectors


def draft(
    *,
    requirement_id: str,
    brd_sha: str,
    justification: str,
    impact: str,
    actor: dict[str, Any],
    change_id: str,
    client_id: str = "demo",
) -> dict[str, Any]:
    sync = connectors.call(
        "change_mgmt",
        "open",
        {
            "id": change_id,
            "source_requirement": requirement_id,
            "brd_sha": brd_sha,
            "justification": justification,
            "impact": impact,
        },
        client_id=client_id,
        actor=actor,
        write=True,
    )
    return {
        "id": change_id,
        "source_requirement": requirement_id,
        "brd_sha": brd_sha,
        "justification": justification,
        "impact": impact,
        "submitter": actor.get("id"),
        "status": "open",
        "connector": sync,
    }
