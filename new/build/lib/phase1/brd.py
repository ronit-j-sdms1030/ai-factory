"""BRD agent: approved scope → structured document + critique pass.

The intake transcript is not an input. Contamination would be the previous
conversation leaking into a document the business owner then approves.
"""

from __future__ import annotations

import json
import re
from typing import Any

from phase1.llm import DeterministicBRDLLM


def render_scope(requirement_id: str, report: dict[str, Any]) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) or "- _(none)_"

    return "\n".join(
        [
            f"# Scope report — {requirement_id}",
            "",
            "## In scope",
            bullets(list(report.get("in_scope") or [])),
            "",
            "## Out of scope",
            bullets(list(report.get("out_of_scope") or [])),
            "",
            "## Success",
            str(report.get("success") or "").strip(),
            "",
            "## Assumptions",
            bullets(list(report.get("assumptions") or [])),
            "",
            "## Open questions",
            bullets(list(report.get("open_questions") or [])),
            "",
        ]
    )


def parse_scope(markdown: str) -> dict[str, Any]:
    sections = {"in_scope": [], "out_of_scope": [], "success": "", "assumptions": [], "open_questions": []}
    current = None
    success_lines: list[str] = []
    for line in markdown.splitlines():
        heading = line.strip().lower()
        if heading == "## in scope":
            current = "in_scope"
        elif heading == "## out of scope":
            current = "out_of_scope"
        elif heading == "## success":
            current = "success"
        elif heading == "## assumptions":
            current = "assumptions"
        elif heading == "## open questions":
            current = "open_questions"
        elif line.startswith("- ") and current in sections and current != "success":
            sections[current].append(line[2:].strip())
        elif current == "success" and line.strip() and not line.startswith("#"):
            success_lines.append(line.strip())
    sections["success"] = " ".join(success_lines)
    return sections


def _fill(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def draft_brd(
    requirement_id: str,
    scope_md: str,
    template: str,
    ids: list[str],
) -> str:
    scope = parse_scope(scope_md)
    req_blocks = []
    in_scope = scope["in_scope"] or ["Deliver the approved scope"]
    for i, item in enumerate(in_scope):
        tid = ids[i] if i < len(ids) else ids[-1]
        req_blocks.append(
            f"### {tid}\n\n**Source:** approved scope, in-scope item {i + 1}.\n\n"
            f"{item}\n\n**Acceptance criteria:**\n"
            f"- A named user can complete this capability in a browser.\n"
            f"- A test can fail this item independently of the others.\n"
        )
    return _fill(
        template,
        {
            "requirement_id": requirement_id,
            "objective": scope["success"] or "Deliver the approved in-scope capabilities.",
            "requirements": "\n".join(req_blocks),
            "page_behaviour": "Screens cover each in-scope capability; extra screens are reported at Gate 3.",
            "data_model": "PostgreSQL. Entities follow client vocabulary; shared entities have one owning department.",
            "security": "Entra SSO. No secrets in images. Personal data masked before model egress.",
            "assumptions": "\n".join(f"- {a}" for a in scope["assumptions"]) or "- None recorded.",
            "open_questions": "\n".join(f"- {q}" for q in scope["open_questions"]) or "- None.",
        },
    )


def critique(brd: str) -> list[str]:
    raw = DeterministicBRDLLM().complete([], skill="")
    data = json.loads(raw)
    findings = list(data.get("findings") or [])
    if not re.search(r"acceptance criteria", brd, re.I):
        findings.append("Acceptance criteria are missing — a later QA pass cannot be written.")
    return findings


def append_findings(brd: str, findings: list[str]) -> str:
    if not findings:
        return brd
    extra = "\n".join(f"- *(critique)* {f}" for f in findings)
    if "## Open questions" in brd:
        return brd.rstrip() + "\n" + extra + "\n"
    return brd.rstrip() + "\n\n## Open questions\n\n" + extra + "\n"
