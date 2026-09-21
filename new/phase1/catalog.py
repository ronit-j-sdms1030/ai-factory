"""Backstage Component entities — one per requirement."""

from __future__ import annotations

from typing import Any


def entities(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for row in runs:
        req = row.get("requirement") or {}
        rid = str(req.get("id") or "")
        if not rid:
            continue
        items.append(
            {
                "apiVersion": "backstage.io/v1alpha1",
                "kind": "Component",
                "metadata": {
                    "name": rid.lower(),
                    "title": rid,
                    "annotations": {
                        "governed.io/requirement": rid,
                        "governed.io/phase": str(row.get("phase") or ""),
                    },
                },
                "spec": {
                    "type": "service",
                    "lifecycle": str(row.get("phase") or "unknown"),
                    "owner": "group:default/platform",
                    "system": "governed-factory",
                },
            }
        )
    return items
