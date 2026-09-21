"""Overview Writer — plain-language summary from the BRD only. Gates nothing."""

from __future__ import annotations

import re


def write(requirement_id: str, brd_text: str, *, skill: str = "") -> str:
    if skill:
        import skill_registry

        skill_registry.require(skill, "overview")
    objective = ""
    if "## Objective" in brd_text:
        objective = brd_text.split("## Objective", 1)[1].split("\n## ", 1)[0].strip()
    objective = objective or "Deliver the approved in-scope capabilities."
    objective = re.sub(r"\s+", " ", objective)
    return (
        f"# Product overview — {requirement_id}\n\n"
        f"{objective}\n\n"
        "This summary is for stakeholders. It does not gate planning or build.\n"
    )
