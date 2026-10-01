"""README kept on each requirement's git repo.

The website name comes from the intake scope report. The status line is the
gate the requirement is on right now, so every later commit refreshes it.
"""

from __future__ import annotations

import re
from typing import Any

import requirement as R

_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.M)

_GATES = {
    1: "Gate 1 — Scope. Waiting for the product owner.",
    2: "Gate 2 — Business requirements. Waiting for the business owner and the client tech lead.",
    3: "Gate 3 — Design. Waiting for the architect, business analyst, and UI/UX.",
    4: "Gate 4 — Plan. Waiting for the tech lead and the stream leads.",
    5: "Gate 5 — Build. Waiting for the senior engineer.",
    6: "Gate 6 — UAT. Waiting for the requester.",
    7: "Gate 7 — Release. Waiting for the release manager.",
}


def brief_from_scope(markdown: str) -> dict[str, str]:
    """Pull the intake sections the README shows."""
    text = markdown or ""
    matches = list(_SECTION.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).strip().lower()] = text[start:end].strip()
    return {
        "users": _clip(sections.get("users", "")),
        "today": _clip(sections.get("what happens today", "")),
        "in_scope": _clip(sections.get("in scope", ""), limit=800),
        "success": _clip(sections.get("success", "")),
    }


def render(requirement_id: str, body: dict[str, Any] | None) -> str:
    """Markdown for README.md: what this is, and which gate it is on."""
    row = body or {}
    title = str(row.get("display_title") or "").strip() or requirement_id
    brief = row.get("intake_brief") if isinstance(row.get("intake_brief"), dict) else {}
    lines = [
        f"# {title}",
        "",
        f"Requirement **{requirement_id}**.",
        "",
        "## Where it is",
        "",
        _status(row),
        "",
        "## What this requirement is",
        "",
    ]
    success = str(brief.get("success") or "").strip()
    today = str(brief.get("today") or "").strip()
    users = str(brief.get("users") or "").strip()
    scope = str(brief.get("in_scope") or "").strip()
    if success:
        lines.append(success)
        lines.append("")
    if users:
        lines.extend(["**Who it is for:** " + _one_line(users), ""])
    if today:
        lines.extend(["**What happens today:** " + _one_line(today), ""])
    if scope:
        lines.extend(["**In scope:**", "", scope, ""])
    if not any((success, users, today, scope)):
        request = _clip(str(row.get("request_text") or ""), limit=500)
        lines.append(request or "The intake report has not been written yet.")
        lines.append("")
    lines.extend(
        [
            "## Links",
            "",
            f"- Live preview: `/preview/{requirement_id}`",
            "",
        ]
    )
    return "\n".join(lines)


def _status(body: dict[str, Any]) -> str:
    data = body.get("requirement")
    if isinstance(data, dict) and data.get("id") and data.get("template"):
        try:
            req = R.Requirement.load(data)
        except (KeyError, TypeError, ValueError):
            req = None
        if req is not None:
            if req.state == "discarded":
                reason = (req.discarded_reason or "").strip()
                return "Discarded." + (f" {reason}" if reason else "")
            if req.awaiting is None:
                return "Complete. Gate 7 is signed and the product is released."
            return _GATES.get(req.awaiting, f"Gate {req.awaiting}.")
    awaiting = body.get("awaiting")
    if awaiting is None and str(body.get("phase") or "") == "complete":
        return "Complete. Gate 7 is signed and the product is released."
    try:
        gate = int(awaiting)
    except (TypeError, ValueError):
        return "Intake. The website name is not decided yet."
    return _GATES.get(gate, f"Gate {gate}.")


def _one_line(text: str) -> str:
    return " ".join(text.replace("- ", "").split())


def _clip(text: str, limit: int = 400) -> str:
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"
