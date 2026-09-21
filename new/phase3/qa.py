"""QA Agent — test design before any application code exists."""

from __future__ import annotations

import re
from typing import Any

from stack_profiles import StackProfile


def cases(
    brd_text: str,
    screens: list[dict[str, Any]],
    profile: StackProfile,
    *,
    skill: str = "",
) -> list[dict[str, Any]]:
    if skill:
        import skill_registry

        skill_registry.require(skill, "qa")
    ids = re.findall(r"^###\s+(\S+)\s*$", brd_text, re.M) or ["R01"]
    framework = profile.tests
    designed = [
        {
            "id": f"{ids[0]}-T01",
            "name": "availability for a chosen date",
            "framework": framework,
            "criterion": ids[0],
        },
        {
            "id": f"{ids[0]}-T02",
            "name": "book a room for a time slot",
            "framework": framework,
            "criterion": ids[min(1, len(ids) - 1)],
        },
        {
            "id": f"{ids[0]}-T03",
            "name": "concurrency: two overlapping bookings, exactly one succeeds",
            "framework": framework,
            "criterion": "overlap",
            "critical": True,
        },
        {
            "id": f"{ids[0]}-T04",
            "name": "user cancels their own booking",
            "framework": framework,
            "criterion": "cancel",
        },
        {
            "id": f"{ids[0]}-T05",
            "name": "admin cancels any booking",
            "framework": framework,
            "criterion": "admin",
        },
        {
            "id": f"{ids[0]}-T06",
            "name": "audit records who booked and when",
            "framework": framework,
            "criterion": "audit",
        },
        {
            "id": f"{ids[0]}-T07",
            "name": "screen coverage: every approved screen is reachable",
            "framework": framework,
            "criterion": "ui",
        },
        {
            "id": f"{ids[0]}-T08",
            "name": "out-of-scope native clients are refused",
            "framework": framework,
            "criterion": "scope",
        },
    ]
    if len(designed) < 8:
        designed.append(
            {
                "id": f"{ids[0]}-T09",
                "name": "acceptance criterion is independently fail-able",
                "framework": framework,
                "criterion": ids[0],
            }
        )
    del screens  # screens constrain coverage T07; generation does not wait on them
    return designed[:8]


def render(cases_: list[dict[str, Any]]) -> str:
    lines = ["# Test design — before code", ""]
    for case in cases_:
        flag = " **(critical)**" if case.get("critical") else ""
        lines.append(f"- `{case['id']}` [{case['framework']}] {case['name']}{flag}")
    lines.append("")
    return "\n".join(lines)
