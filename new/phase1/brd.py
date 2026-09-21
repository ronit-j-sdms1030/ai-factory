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
            "## Users",
            str(report.get("users") or "").strip() or "- Named users in a browser",
            "",
            "## What happens today",
            str(report.get("current_state") or "").strip() or "- _(not stated)_",
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
            "## Non-functional",
            bullets(list(report.get("non_functional") or [])),
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
    sections: dict[str, Any] = {
        "in_scope": [],
        "out_of_scope": [],
        "success": "",
        "assumptions": [],
        "open_questions": [],
        "users": "",
        "current_state": "",
        "non_functional": [],
    }
    current = None
    prose: dict[str, list[str]] = {"success": [], "users": [], "current_state": []}
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
        elif heading == "## users":
            current = "users"
        elif heading == "## what happens today":
            current = "current_state"
        elif heading in {"## non-functional", "## non-functional requirements"}:
            current = "non_functional"
        elif line.startswith("- ") and current in {"in_scope", "out_of_scope", "assumptions", "open_questions", "non_functional"}:
            sections[current].append(line[2:].strip())
        elif current in prose and line.strip() and not line.startswith("#"):
            prose[current].append(line.strip().lstrip("- ").strip())
    for key, lines in prose.items():
        sections[key] = " ".join(lines)
    return sections


def _fill(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def _clip(text: str, limit: int = 180) -> str:
    text = " ".join((text or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _page_entries(items: list[str]) -> list[tuple[str, str]]:
    used: set[str] = set()
    entries: list[tuple[str, str]] = []
    for item in items:
        words = re.findall(r"[A-Za-z][A-Za-z0-9]+", item)
        name = "".join(w[:1].upper() + w[1:].lower() for w in words[:3]) or "Screen"
        name = name[:28]
        n = name
        i = 2
        while n in used:
            n = f"{name}{i}"
            i += 1
        used.add(n)
        entries.append((n, _clip(item, 140)))
    if not entries:
        entries.append(("Primary", "Deliver the approved in-scope capabilities in a browser."))
    return entries


def _page_lines(items: list[str]) -> str:
    return "\n".join(f"- **{name}**: {desc}" for name, desc in _page_entries(items))


def _users(in_scope: list[str], assumptions: list[str]) -> str:
    for item in in_scope + assumptions:
        if item.lower().startswith("used by "):
            return f"- {item}\n- Managers or reviewers named on later gates see only what their role allows."
    return (
        "- Named staff who do the work in a browser (phone or laptop).\n"
        "- A manager or reviewer who needs to see the same-day picture without chasing people."
    )


def _current_state(assumptions: list[str]) -> str:
    for item in assumptions:
        if item.lower().startswith("today"):
            return item
    return "Today the work is done by hand (paper, chat, or a spreadsheet) and reconstructed at period end."


def _gherkin(item: str) -> str:
    action = item[0].lower() + item[1:] if item else "use this capability"
    return (
        "- **Given** a named user is signed in on a browser\n"
        f"- **When** they {action}\n"
        "- **Then** the result is visible in the product the same day, without a call, chat or paper register\n"
        "- **And** an automated test can fail this item without failing the others"
    )


def _journeys(pages: list[tuple[str, str]]) -> str:
    nodes = " --> ".join(name for name, _ in pages)
    lines = [
        "Staff sign in, complete the in-scope work, and a reviewer can see the outcome without a side channel.",
        "",
        "```mermaid",
        "flowchart LR",
        "  SignIn[Sign in] --> " + (pages[0][0] if pages else "Home"),
    ]
    for i, (name, _) in enumerate(pages[:-1]):
        nxt = pages[i + 1][0]
        lines.append(f"  {name} --> {nxt}")
    lines.append("```")
    if nodes:
        lines.append("")
        lines.append(f"Primary path: Sign in → {nodes}.")
    return "\n".join(lines)


def _entities(blob: str) -> list[tuple[str, list[str]]]:
    text = blob.lower()
    if any(k in text for k in ("attend", "roster", "check in", "timesheet")):
        return [
            ("Employee", ["id", "name", "work_login", "manager_id"]),
            ("AttendanceEvent", ["id", "employee_id", "kind", "recorded_at", "in_office"]),
            ("CorrectionRequest", ["id", "employee_id", "status", "decided_by"]),
            ("TimesheetExport", ["id", "period", "created_at"]),
        ]
    if any(k in text for k in ("book", "room", "slot")):
        return [
            ("User", ["id", "name", "email"]),
            ("Room", ["id", "name"]),
            ("Booking", ["id", "room_id", "user_id", "starts_at", "ends_at"]),
            ("AuditEntry", ["id", "actor_id", "action", "at"]),
        ]
    return [
        ("User", ["id", "name", "work_login"]),
        ("Record", ["id", "owner_id", "title", "status", "due_at"]),
        ("AuditEntry", ["id", "actor_id", "action", "at"]),
    ]


def _data_model_text(entities: list[tuple[str, list[str]]]) -> str:
    lines = [
        "PostgreSQL. One owning department per shared entity. Fields below are the minimum the screens need.",
        "",
    ]
    for name, fields in entities:
        lines.append(f"- **{name}:** {', '.join(fields)}")
    return "\n".join(lines)


def _er_diagram(entities: list[tuple[str, list[str]]]) -> str:
    lines = ["erDiagram"]
    names = [name for name, _ in entities]
    for name, fields in entities:
        field_block = " ".join(f"{f} string" for f in fields)
        lines.append(f"  {name} {{ {field_block} }}")
    if len(names) >= 2:
        lines.append(f"  {names[0]} ||--o{{ {names[1]} : has")
    if len(names) >= 3:
        lines.append(f"  {names[0]} ||--o{{ {names[2]} : raises")
    if len(names) >= 4:
        lines.append(f"  {names[0]} ||--o{{ {names[3]} : exports")
    return "\n".join(lines)


def _nfr() -> str:
    return "\n".join(
        [
            "- **Channel:** responsive website. Phone and laptop browser. No app-store install.",
            "- **Stack:** React frontend; Node.js or Python backend, locked at Gate 3; PostgreSQL.",
            "- **Access:** work login in the browser. No secrets in images.",
            "- **Data:** personal data stays in the tenant; mask it before any model egress.",
            "- **Same-day:** a completed action is visible to the named reviewer without a side channel.",
            "- **Accessibility:** keyboard reachable, labelled fields, contrast from the design system.",
        ]
    )


def draft_brd(
    requirement_id: str,
    scope_md: str,
    template: str,
    ids: list[str],
    *,
    skill: str = "",
) -> str:
    if skill:
        import skill_registry

        skill_registry.require(skill, "brd")
    from phase1.rails import as_string_list, shape_scope_report, title_from_request

    parsed = parse_scope(scope_md)
    shaped = shape_scope_report(
        {
            "type": "scope_report",
            "in_scope": parsed["in_scope"],
            "out_of_scope": parsed["out_of_scope"],
            "success": parsed["success"],
            "assumptions": parsed["assumptions"],
            "open_questions": parsed["open_questions"],
            "users": parsed.get("users") or "",
            "current_state": parsed.get("current_state") or "",
            "non_functional": parsed.get("non_functional") or [],
        },
        request_text=" ".join(parsed["in_scope"][:2]) or parsed["success"],
    )
    in_scope = shaped["in_scope"] or ["Deliver the approved scope"]
    assumptions = as_string_list(shaped.get("assumptions"))
    out_lines = as_string_list(shaped.get("out_of_scope"))
    questions = as_string_list(shaped.get("open_questions"))
    success = _clip(shaped["success"] or "Deliver the approved in-scope capabilities.", 400)
    title = title_from_request(" ".join(in_scope[:1]))
    pages = _page_entries(in_scope)
    blob = " ".join(in_scope + out_lines + [success])
    entities = _entities(blob)

    req_blocks = []
    for i, item in enumerate(in_scope):
        tid = ids[i] if i < len(ids) else ids[-1]
        req_blocks.append(
            f"### {tid} — {_clip(item, 80)}\n\n"
            f"**Source:** approved scope, in-scope item {i + 1}.\n\n"
            f"{_clip(item, 220)}\n\n"
            f"**Acceptance criteria:**\n{_gherkin(item)}\n"
        )

    values = {
        "requirement_id": f"{requirement_id} — {title}",
        "objective": success,
        "summary": (
            f"This specification turns the approved Gate 1 scope into a testable product description "
            f"for **{title}**. It is the document Gate 2 signs. Screens at Gate 3 must cover §11."
        ),
        "users": parsed.get("users") or _users(in_scope, assumptions),
        "current_state": parsed.get("current_state") or _current_state(assumptions),
        "success": success,
        "in_scope": "\n".join(f"- {item}" for item in in_scope),
        "out_of_scope": "\n".join(f"- {item}" for item in out_lines)
        or "- Native apps, extra hardware, and anything not listed in §6.",
        "requirements": "\n".join(req_blocks),
        "nfr": _nfr(),
        "journeys": _journeys(pages),
        "page_behaviour": _page_lines(in_scope),
        "data_model": _data_model_text(entities),
        "er_diagram": _er_diagram(entities),
        "security": (
            "Work login in the browser. Role-appropriate views (staff vs manager). "
            "No secrets in images. Personal data stays in the tenant and is masked before model egress."
        ),
        "integrations": (
            "- Browser only for this release.\n"
            "- Spreadsheet / CSV export if in scope; no payroll feed unless listed in §6.\n"
            "- Calendar or directory systems are open questions until named in §16."
        ),
        "assumptions": "\n".join(f"- {a}" for a in assumptions)
        or "- People use a browser on a phone or laptop.",
        "open_questions": "\n".join(f"- {q}" for q in questions) or "- None.",
    }
    return _fill(template, values)


def critique(brd: str, *, skill: str = "") -> list[str]:
    raw = DeterministicBRDLLM().complete([], skill=skill)
    data = json.loads(raw)
    findings = list(data.get("findings") or [])
    if not re.search(r"acceptance criteria", brd, re.I):
        findings.append("Acceptance criteria are missing — a later QA pass cannot be written.")
    return findings


def append_findings(brd: str, findings: list[str]) -> str:
    if not findings:
        return brd
    extra = "\n".join(f"- *(critique)* {f}" for f in findings)
    if re.search(r"^##(?:\s+\d+\.)?\s+Open questions\b", brd, re.M | re.I):
        return brd.rstrip() + "\n" + extra + "\n"
    return brd.rstrip() + "\n\n## Open questions\n\n" + extra + "\n"
