"""Decomposer — tickets in dependency order from this BRD and these screens.

Precedent is off. A split that copies the last booking project divides the
wrong work. Path allow-lists become the Phase 4 confine boundary.
"""

from __future__ import annotations

import re
from typing import Any

import department_routing as routing
from phase2 import architect
from stack_profiles import StackProfile

_CAP = re.compile(r"^###\s+(\S+)(?:\s+[—–-]\s+(.+))?\s*$", re.M)


def _trace_ids(brd_text: str) -> list[str]:
    return [match.group(1) for match in _CAP.finditer(brd_text or "")] or []


def _capabilities(brd_text: str) -> list[tuple[str, str]]:
    found = [
        (match.group(1), (match.group(2) or match.group(1)).strip())
        for match in _CAP.finditer(brd_text or "")
    ]
    return found


def _paths(department: str, title: str, profile: StackProfile) -> list[str]:
    node = profile.id == "node"
    lowered = title.lower()
    if department == "ai":
        return ["src/ai/**"] if node else ["app/ai/**"]
    if department == "qa":
        return ["tests/e2e/**"] if node else ["tests/**"]
    if "schema" in lowered or "migration" in lowered:
        return ["prisma/schema.prisma", "prisma/migrations/**"] if node else ["alembic/versions/**"]
    if "screen" in lowered or "form" in lowered or "ui" in lowered:
        return ["src/ui/**"] if node else ["app/ui/**"]
    return ["src/api/**"] if node else ["app/api/**"]


def _needs_overlap(blob: str) -> bool:
    return any(token in blob for token in ("overlap", "double-book", "double book", "exclusion"))


def _entity_names(brd_text: str, architecture_text: str = "") -> list[str]:
    rows = architect._entities((architecture_text or "") + "\n" + (brd_text or ""))
    names = [name for name, _fields in rows]
    repaired, _collisions = routing.repair_entities(names)
    return repaired


def tickets(
    requirement_id: str,
    brd_text: str,
    screens: list[dict[str, Any]],
    profile: StackProfile,
    *,
    skill: str = "",
    architecture_text: str = "",
) -> list[dict[str, Any]]:
    if skill:
        import skill_registry

        skill_registry.require(skill, "decomposer")
    ids = _trace_ids(brd_text) or [f"{requirement_id}-R01"]
    blob = f"{brd_text or ''} {architecture_text or ''}".lower()
    entities = _entity_names(brd_text, architecture_text)
    items: list[dict[str, Any]] = []

    schema_title = "schema + migration"
    if _needs_overlap(blob):
        schema_title += ", including the exclusion constraint"
    items.append(
        {
            "id": f"{requirement_id}-W1",
            "title": schema_title,
            "depends_on": [],
            "trace": ids[:1],
        }
    )
    api_parent = f"{requirement_id}-W1"
    for offset, name in enumerate(entities, start=2):
        tid = f"{requirement_id}-W{offset}"
        items.append(
            {
                "id": tid,
                "title": f"{name} API",
                "depends_on": [f"{requirement_id}-W1"],
                "trace": ids[:1],
            }
        )
        api_parent = tid
    if not entities:
        items.append(
            {
                "id": f"{requirement_id}-W2",
                "title": "records API",
                "depends_on": [f"{requirement_id}-W1"],
                "trace": ids[:1],
            }
        )
        api_parent = f"{requirement_id}-W2"

    next_index = len(items) + 1
    parent = api_parent
    for screen in screens:
        name = str(screen.get("name") or "Screen")
        tid = f"{requirement_id}-W{next_index}"
        items.append(
            {
                "id": tid,
                "title": f"{name} screen",
                "depends_on": [parent],
                "trace": ids[:1],
                "screen": name,
            }
        )
        parent = tid
        next_index += 1

    items.append(
        {
            "id": f"{requirement_id}-AI1",
            "title": "prompt + inference adapter for screen suggestions",
            "depends_on": [parent],
            "trace": ids[:1],
            "department": "ai",
        }
    )
    result = []
    for item in items:
        department = item.get("department") or routing.department_for(item["title"])
        result.append(
            {
                **item,
                "department": department,
                "paths": _paths(department, item["title"], profile),
            }
        )
    return result
