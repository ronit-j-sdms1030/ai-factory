"""UAT URL — Argo when a cluster exists; local preview otherwise."""

from __future__ import annotations

from typing import Any


def uat(requirement_id: str, cluster: str | None = None) -> dict[str, Any]:
    slug = requirement_id.lower()
    if cluster:
        return {
            "url": f"https://{slug}.uat.{cluster}",
            "application": f"{slug}-uat",
            "status": "synced",
            "note": "Argo Application synced",
        }
    return {
        "url": f"/preview/{requirement_id}",
        "application": f"{slug}-uat",
        "status": "substitute",
        "note": "no kube credentials; Gate 3 preview HTML is the UAT artefact",
    }
