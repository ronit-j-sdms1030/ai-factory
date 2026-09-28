"""QA Agent — test design from this BRD and these screens, before code."""

from __future__ import annotations

import re
from typing import Any

from stack_profiles import StackProfile

_CAP = re.compile(r"^###\s+(\S+)(?:\s+[—–-]\s+(.+))?\s*$", re.M)


def _capabilities(brd_text: str) -> list[tuple[str, str]]:
    return [
        (match.group(1), (match.group(2) or match.group(1)).strip())
        for match in _CAP.finditer(brd_text or "")
    ]


def _needs_overlap(blob: str) -> bool:
    return any(token in blob for token in ("overlap", "double-book", "double book", "exclusion"))


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
    caps = _capabilities(brd_text)
    ids = [cap[0] for cap in caps] or ["R01"]
    titles = [cap[1] for cap in caps]
    framework = profile.tests
    blob = (brd_text or "").lower()
    screen_names = [str(screen.get("name") or "Screen") for screen in screens if screen.get("name")]
    designed: list[dict[str, Any]] = []

    def add(suffix: str, name: str, criterion: str, *, critical: bool = False) -> None:
        row = {
            "id": f"{ids[0]}-{suffix}",
            "name": name,
            "framework": framework,
            "criterion": criterion,
        }
        if critical:
            row["critical"] = True
        designed.append(row)

    first = titles[0] if titles else "the primary capability"
    second = titles[1] if len(titles) > 1 else first
    add("T01", first[0].lower() + first[1:] if first else "happy path", ids[0])
    add("T02", second[0].lower() + second[1:] if second else "write path", ids[min(1, len(ids) - 1)])
    if _needs_overlap(blob):
        add(
            "T03",
            "concurrency: two overlapping writes, exactly one succeeds",
            "overlap",
            critical=True,
        )
    elif any(token in blob for token in ("cancel", "own")):
        add("T03", "a user cannot change another user's record", "auth", critical=True)
    else:
        add("T03", "conflicting writes: exactly one succeeds", ids[0], critical=True)
    if "cancel" in blob:
        add("T04", "user cancels or withdraws their own record", "cancel")
    else:
        add("T04", f"a clear failure of {first}", ids[0])
    if "admin" in blob:
        add("T05", "admin override is recorded", "admin")
    elif len(titles) > 2:
        add("T05", titles[2][0].lower() + titles[2][1:], ids[min(2, len(ids) - 1)])
    else:
        add("T05", "out-of-policy action is refused", "scope")
    if "audit" in blob:
        add("T06", "audit records who acted and when", "audit")
    else:
        add("T06", "the change is visible the same day without a side channel", ids[0])
    if screen_names:
        add(
            "T07",
            "screen coverage: " + ", ".join(screen_names) + " are reachable",
            "ui",
        )
    else:
        add("T07", "screen coverage: every approved screen is reachable", "ui")
    if any(token in blob for token in ("native", "app store", "iphone", "android")):
        add("T08", "out-of-scope native clients are refused", "scope")
    else:
        add("T08", "an out-of-scope capability is refused", "scope")
    return designed[:8]


def render(cases_: list[dict[str, Any]]) -> str:
    lines = ["# Test design — before code", ""]
    for case in cases_:
        flag = " **(critical)**" if case.get("critical") else ""
        lines.append(f"- `{case['id']}` [{case['framework']}] {case['name']}{flag}")
    lines.append("")
    return "\n".join(lines)
