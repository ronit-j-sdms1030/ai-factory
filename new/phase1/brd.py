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


# Screen inventory lines: "- **LoginScreen**: staff signs in"
_PAGE_BULLET = re.compile(r"^[-*]\s+\*{0,2}([^*:\n]+?)\*{0,2}\s*:\s*(.+)$")
_PAGE_SECTION = re.compile(r"^##(?:\s+\d+\.)?\s+Page behaviour\b", re.I)
_H2 = re.compile(r"^##\s+")
_H3 = re.compile(r"^###\s+")
_GHERKIN_LABELS = frozenset({"given", "when", "then", "and", "but", "source"})


def _fold_page(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _is_screen_token(name: str) -> bool:
    """PascalCase component ids — not Given/When/Source acceptance labels."""
    token = (name or "").strip()
    if not token or _fold_page(token) in _GHERKIN_LABELS:
        return False
    return bool(re.fullmatch(r"[A-Z][A-Za-z0-9]+", token))


def normalize_screen_inventory(brd: str) -> str:
    """One screen list lives in ``## Page behaviour`` — nowhere else.

    LLM refine and some templates paste ``- **Screen**: …`` under each
    ``### REQ-…`` block. Downstream then minted LoginScreen2 / LoanManagement3.
    Strip those nested copies and dedupe the §11 inventory for every BRD.
    """
    if not (brd or "").strip():
        return brd

    lines = brd.splitlines(keepends=True)
    inventory: list[tuple[str, str]] = []
    seen: set[str] = set()
    in_page = False
    under_h3_in_page = False
    for line in lines:
        stripped = line.strip()
        if _PAGE_SECTION.match(stripped):
            in_page = True
            under_h3_in_page = False
            continue
        if _H2.match(stripped) and not _PAGE_SECTION.match(stripped):
            in_page = False
            under_h3_in_page = False
            continue
        if in_page and _H3.match(stripped):
            under_h3_in_page = True
            continue
        if not in_page or under_h3_in_page:
            continue
        match = _PAGE_BULLET.match(stripped)
        if not match:
            continue
        name, desc = match.group(1).strip(), match.group(2).strip()
        key = _fold_page(name)
        if not key or key in seen:
            continue
        seen.add(key)
        inventory.append((name, desc))
    inventory_keys = set(seen)

    out: list[str] = []
    in_page = False
    under_h3 = False
    inventory_emitted = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if _PAGE_SECTION.match(stripped):
            in_page = True
            under_h3 = False
            inventory_emitted = False
            out.append(line)
            i += 1
            continue
        if _H2.match(stripped) and not _PAGE_SECTION.match(stripped):
            if in_page and not inventory_emitted:
                for name, desc in inventory:
                    out.append(f"- **{name}**: {desc}\n")
                inventory_emitted = True
            in_page = False
            under_h3 = False
            out.append(line)
            i += 1
            continue
        if _H3.match(stripped):
            under_h3 = True
            if in_page:
                i += 1
                while i < len(lines):
                    nxt = lines[i].strip()
                    if _H2.match(nxt):
                        break
                    if _H3.match(nxt):
                        i += 1
                        continue
                    i += 1
                continue
            out.append(line)
            i += 1
            continue

        match = _PAGE_BULLET.match(stripped)
        if match:
            name = match.group(1).strip()
            key = _fold_page(name)
            if in_page and not under_h3:
                # Rebuild from collected inventory once; skip raw bullets.
                if not inventory_emitted:
                    for inv_name, inv_desc in inventory:
                        out.append(f"- **{inv_name}**: {inv_desc}\n")
                    inventory_emitted = True
                i += 1
                continue
            if under_h3 and (
                key in inventory_keys or _is_screen_token(name)
            ):
                i += 1
                continue
        out.append(line)
        i += 1

    if in_page and not inventory_emitted:
        for name, desc in inventory:
            out.append(f"- **{name}**: {desc}\n")
    return "".join(out)


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
    # Library / catalogue before generic "book a room" heuristic.
    if any(
        k in text
        for k in ("library", "catalogue", "catalog", "isbn", "overdue", "check out", "checkout")
    ):
        return [
            ("User", ["id", "name", "email", "role", "password_hash", "created_at"]),
            ("Book", ["id", "title", "author", "isbn", "status", "created_at"]),
            ("Loan", ["id", "book_id", "member_id", "checked_out_at", "due_date", "returned_at", "status", "created_at"]),
            ("ImportLog", ["id", "imported_by", "file_name", "records_processed", "status", "created_at"]),
        ]
    if any(k in text for k in ("room", "slot", "meeting room", "book a room", "booking")):
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
    return normalize_screen_inventory(_fill(template, values))


def critique(brd: str, *, skill: str = "") -> list[str]:
    raw = DeterministicBRDLLM().complete([], skill=skill)
    data = json.loads(raw)
    findings = list(data.get("findings") or [])
    if not re.search(r"acceptance criteria", brd, re.I):
        findings.append("Acceptance criteria are missing — a later QA pass cannot be written.")
    # Nested screen bullets under ### REQ mint duplicate Gate 3 screens.
    in_req = False
    for line in (brd or "").splitlines():
        stripped = line.strip()
        if _PAGE_SECTION.match(stripped):
            in_req = False
            continue
        if _H2.match(stripped):
            in_req = bool(re.match(r"^##(?:\s+\d+\.)?\s+Functional requirements\b", stripped, re.I))
            continue
        if in_req and _H3.match(stripped):
            continue
        if in_req and _PAGE_BULLET.match(stripped):
            findings.append(
                "Screen inventory lines appear under ### requirements — "
                "keep '- **Screen**: …' only in ## Page behaviour."
            )
            break
    return findings


def append_findings(brd: str, findings: list[str]) -> str:
    if not findings:
        return brd
    extra = "\n".join(f"- *(critique)* {f}" for f in findings)
    if re.search(r"^##(?:\s+\d+\.)?\s+Open questions\b", brd, re.M | re.I):
        return brd.rstrip() + "\n" + extra + "\n"
    return brd.rstrip() + "\n\n## Open questions\n\n" + extra + "\n"
