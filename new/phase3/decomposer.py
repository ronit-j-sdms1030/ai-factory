"""Decomposer — tickets in dependency order, path allow-lists, stream assignment."""

from __future__ import annotations

import re
from typing import Any

import department_routing as routing
from stack_profiles import StackProfile


def _trace_ids(brd_text: str) -> list[str]:
    return re.findall(r"^###\s+(\S+)\s*$", brd_text, re.M)


def _paths(department: str, title: str, profile: StackProfile) -> list[str]:
    node = profile.id == "node"
    lowered = title.lower()
    if "schema" in lowered or "migration" in lowered:
        return ["prisma/schema.prisma", "prisma/migrations/**"] if node else ["alembic/versions/**"]
    if "screen" in lowered or "form" in lowered or "ui" in lowered:
        return ["src/ui/**"] if node else ["app/ui/**"]
    if department == "qa":
        return ["tests/e2e/**"] if node else ["tests/**"]
    if department == "ai":
        return ["src/ai/**"] if node else ["app/ai/**"]
    return ["src/api/**"] if node else ["app/api/**"]


def tickets(
    requirement_id: str,
    brd_text: str,
    screens: list[dict[str, Any]],
    profile: StackProfile,
    *,
    skill: str = "",
) -> list[dict[str, Any]]:
    if skill:
        import skill_registry

        skill_registry.require(skill, "decomposer")
    ids = _trace_ids(brd_text) or [f"{requirement_id}-R01"]
    items = [
        {
            "id": f"{requirement_id}-W1",
            "title": "schema + migration, including the exclusion constraint",
            "depends_on": [],
            "trace": ids[:1],
        },
        {
            "id": f"{requirement_id}-W2",
            "title": "availability API",
            "depends_on": [f"{requirement_id}-W1"],
            "trace": ids[:1],
        },
        {
            "id": f"{requirement_id}-W3",
            "title": "create booking API",
            "depends_on": [f"{requirement_id}-W1", f"{requirement_id}-W2"],
            "trace": ids[:2] if len(ids) > 1 else ids,
        },
        {
            "id": f"{requirement_id}-W4",
            "title": "cancel API + admin rule",
            "depends_on": [f"{requirement_id}-W3"],
            "trace": ids[-1:],
        },
    ]
    for index, screen in enumerate(screens[:3], start=5):
        parent = f"{requirement_id}-W2" if index == 5 else f"{requirement_id}-W{index - 1}"
        items.append(
            {
                "id": f"{requirement_id}-W{index}",
                "title": f"{screen.get('name') or 'Screen'} screen",
                "depends_on": [parent],
                "trace": ids[:1],
            }
        )
    result = []
    for item in items:
        department = routing.department_for(item["title"])
        result.append(
            {
                **item,
                "department": department,
                "paths": _paths(department, item["title"], profile),
            }
        )
    return result
