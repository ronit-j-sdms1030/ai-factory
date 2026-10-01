"""QA Agent — test design from this BRD and these screens, before code.

One test per BRD requirement, named after what that requirement says must
happen, and then the cross-cutting checks the BRD actually asks for. A test
that contradicts the BRD (per-user ownership on a shared login) is not written.
"""

from __future__ import annotations

import re
from typing import Any

from phase3 import trace
from stack_profiles import StackProfile


def _needs_overlap(blob: str) -> bool:
    return any(token in blob for token in ("overlap", "double-book", "double book", "exclusion"))


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _outcome(requirement: dict[str, str]) -> str:
    criteria = str(requirement.get("criteria") or requirement.get("body") or "")
    found = re.search(r"\bThen\b[:\s]+(.+?)(?:\n|$)", criteria, re.I)
    if not found:
        return ""
    text = re.sub(r"[*_`]+", "", found.group(1)).strip().rstrip(".")
    return text[:160]


def _prefix(ids: list[str]) -> str:
    first = ids[0] if ids else "R01"
    return re.sub(r"-R\d+$", "", first) or first


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
    reqs = trace.requirements(brd_text) or [
        {"id": "R01", "title": "the primary capability", "body": "", "criteria": ""}
    ]
    ids = [row["id"] for row in reqs]
    framework = profile.tests
    blob = (brd_text or "").lower()
    screen_names = [str(screen.get("name") or "Screen") for screen in screens if screen.get("name")]
    designed: list[dict[str, Any]] = []

    from phase2 import contract
    from phase3.stage_check import _VAGUE

    for row in reqs:
        outcome = _outcome(row)
        name = _lower_first(row["title"])
        if outcome and not _VAGUE.search(outcome):
            name = f"{name}: {_lower_first(outcome)}"
        designed.append(
            {"id": f"{row['id']}-T01", "name": name, "framework": framework, "criterion": row["id"]}
        )

    prefix = _prefix(ids)

    def add(name: str, criterion: str, *, critical: bool = False) -> None:
        row = {
            "id": f"{prefix}-X{sum(1 for c in designed if '-X' in c['id']) + 1:02d}",
            "name": name,
            "framework": framework,
            "criterion": criterion,
        }
        if critical:
            row["critical"] = True
        designed.append(row)

    if _needs_overlap(blob):
        add("concurrency: two overlapping writes, exactly one succeeds", "overlap", critical=True)
    elif trace.shared_login(brd_text):
        add("a wrong shared password is refused and nothing is saved", "auth", critical=True)
    elif any(token in blob for token in ("cancel", "own record", "their own", "another user")):
        add("a user cannot change another user's record", "auth", critical=True)
    else:
        add("conflicting writes: exactly one succeeds and nothing is half-saved", "integrity", critical=True)
    brd_pages = trace.pages(brd_text)
    kinds = {
        str(page.get("id")): contract._kind(str(page.get("id") or ""), str(page.get("description") or ""))
        for page in brd_pages
    }
    if "auth" in kinds.values() and not trace.shared_login(brd_text):
        add("a wrong password is refused and the user stays on the sign-in screen", "auth")
    for page_id, kind in kinds.items():
        if kind == "form":
            add(f"saving {page_id} with a required field empty is refused and nothing is saved", "validation")
    if "audit" in blob:
        add("audit records who acted and when", "audit")
    if screen_names:
        add("screen coverage: " + ", ".join(screen_names) + " are reachable", "ui")
    else:
        add("screen coverage: every approved screen is reachable", "ui")
    if any(token in blob for token in ("native", "app store", "iphone", "android")):
        add("out-of-scope native clients are refused", "scope")
    else:
        add("an out-of-scope capability is refused", "scope")
    return designed


def render(cases_: list[dict[str, Any]]) -> str:
    lines = ["# Test design — before code", ""]
    for case in cases_:
        flag = " **(critical)**" if case.get("critical") else ""
        lines.append(f"- `{case['id']}` [{case['framework']}] {case['name']}{flag}")
    lines.append("")
    return "\n".join(lines)
