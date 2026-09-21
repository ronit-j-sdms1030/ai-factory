"""Four-mechanism rollback — one decision, four runbooks."""

from __future__ import annotations

from typing import Any


def plan(requirement_id: str, image_tag: str = "previous") -> dict[str, Any]:
    return {
        "requirement_id": requirement_id,
        "mechanisms": {
            "image_tag_revert": image_tag,
            "feature_flag_off": f"release-{requirement_id.lower()}",
            "migration_down": True,
            "pitr": True,
        },
    }
