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
            "## Screens",
            bullets(list(report.get("screens") or [])),
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
        "screens": [],
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
        elif heading == "## screens":
            current = "screens"
        elif heading in {"## non-functional", "## non-functional requirements"}:
            current = "non_functional"
        elif line.startswith("- ") and current in {
            "in_scope",
            "out_of_scope",
            "assumptions",
            "open_questions",
            "non_functional",
            "screens",
        }:
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


_AUTH = re.compile(r"\b(sign[\s-]?in|log[\s-]?in|authentication|shared login)\b", re.I)
_ROW_ACTION = re.compile(r"\b(sign[\s-]?out|signed out|departure)\b", re.I)
_FILTER = re.compile(r"\b(filter|search|hide|exclude)\b", re.I)
_TIME = re.compile(r"\b(time in|current (?:system )?time|timestamp)\b", re.I)
_FORM = re.compile(r"\b(form|capture|enter|add a|register)\b", re.I)
_LIST = re.compile(r"\b(dashboard|displaying a list|\blist\b|\btable\b)\b", re.I)
_SUBJECTS = (
    "visitor",
    "book",
    "loan",
    "room",
    "member",
    "order",
    "ticket",
    "patient",
    "appointment",
    "employee",
)


def _subject_noun(items: list[str]) -> str:
    blob = " ".join(items).lower()
    for word in _SUBJECTS:
        if re.search(rf"\b{word}s?\b", blob):
            return word[:1].upper() + word[1:]
    return "Record"


def _kind(item: str) -> str:
    if _AUTH.search(item):
        return "auth"
    if _TIME.search(item) and not _FORM.search(item):
        return "time"
    if _ROW_ACTION.search(item):
        return "signout"
    if _FILTER.search(item) and _LIST.search(item):
        return "filter"
    if _FORM.search(item):
        return "form"
    if _LIST.search(item):
        return "list"
    return "screen"


def _capture_fields(item: str) -> list[str]:
    match = re.search(r"\b(?:capture|enter|record)\b\s+(.+)", item, re.I)
    tail = match.group(1) if match else ""
    tail = re.split(r"\b(?:when|so that|with a|fields:)\b|\.", tail, maxsplit=1, flags=re.I)[0]
    parts = re.split(r"\s+\band\b\s+", tail, flags=re.I)
    fields: list[str] = []
    for part in parts:
        part = re.sub(r"^(a|an|the)\s+", "", part.strip(" ."), flags=re.I)
        part = re.sub(r"^name of the\s+", "", part, flags=re.I)
        if part and len(part) < 80:
            fields.append(part[0].upper() + part[1:])
    return fields


def _pascal_name(item: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", item)
    stop = {"a", "an", "the", "to", "for", "of", "and", "staff", "user", "users"}
    kept = [word for word in words if word.lower() not in stop][:3]
    name = "".join(word[:1].upper() + word[1:].lower() for word in kept) or "Screen"
    return name[:28]


def _ui_screens(items: list[str], out_of_scope: list[str]) -> list[tuple[str, str]]:
    """Screens a UI pass can draw: fields and actions, not one stub per bullet.

    Sign-in, the entry form, and the list are separate screens. A timestamp,
    a sign-out button, or a filter is behaviour on those screens.
    """
    groups: dict[str, list[str]] = {
        "auth": [],
        "form": [],
        "time": [],
        "list": [],
        "signout": [],
        "filter": [],
        "screen": [],
    }
    for item in items:
        groups[_kind(item)].append(item)
    noun = _subject_noun(items)
    blocked = " ".join(out_of_scope).lower()
    screens: list[tuple[str, str]] = []
    if groups["auth"]:
        account = ""
        if "individual" in blocked or "separate account" in blocked or "role-based" in blocked:
            account = " Individual accounts are out of scope."
        screens.append(
            (
                "SignIn",
                "Shared sign-in. Fields: login and password. Success opens the main list."
                + account,
            )
        )
    fields = []
    for item in groups["form"]:
        fields.extend(_capture_fields(item))
    if groups["time"]:
        quoted = []
        for item in groups["time"]:
            quoted.extend(re.findall(r"['\"]([^'\"]+)['\"]", item))
        label = quoted[0] if quoted else "Time in"
        fields.append(f"{label} (set to the current time when the record is saved, not typed)")
    if groups["form"] or groups["time"]:
        shown = ", ".join(fields) if fields else "the values named in scope"
        screens.append(
            (
                f"{noun}Form",
                f"Fields: {shown}. Saving adds the {noun.lower()} to the current list.",
            )
        )
    if groups["list"] or groups["signout"] or groups["filter"]:
        lead = groups["list"][0] if groups["list"] else f"List of current {noun.lower()} records."
        if lead and lead[-1] not in ".!?":
            lead += "."
        bits = [lead]
        if fields:
            bits.append("Columns match the form: " + ", ".join(fields) + ".")
        if groups["signout"]:
            bits.append("Each row has Sign out. That row then leaves this list.")
        if groups["filter"]:
            bits.append(groups["filter"][0].rstrip(".") + ".")
        if "histor" in blocked or "previous day" in blocked or "past date" in blocked:
            bits.append("No history and no previous days.")
        list_name = "Dashboard" if noun == "Record" else f"{noun}List"
        screens.append((list_name, " ".join(bits)))
    used = {name for name, _ in screens}
    for item in groups["screen"]:
        name = _pascal_name(item)
        n = name
        i = 2
        while n in used:
            n = f"{name}{i}"
            i += 1
        used.add(n)
        screens.append((n, _clip(item, 180)))
    if not screens:
        screens.append(("Primary", "Deliver the approved in-scope capabilities in a browser."))
    return screens


def _page_entries(items: list[str]) -> list[tuple[str, str]]:
    return _ui_screens(items, [])


def _page_lines(items: list[str], out_of_scope: list[str] | None = None) -> str:
    return "\n".join(
        f"- **{name}**: {desc}" for name, desc in _ui_screens(items, out_of_scope or [])
    )


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


def _object_phrase(item: str, *leads: str) -> str:
    """The thing the item talks about, e.g. "list of all visitors inside" → "all visitors inside"."""
    for lead in leads:
        match = re.search(lead + r"\s+(.+?)(?:\.|$)", item or "", re.I)
        if match:
            return match.group(1).strip()
    return ""


def _gherkin(item: str) -> str:
    kind = _kind(item)
    # "filter the list to exclude people who signed out" is a filter, not the sign-out action.
    if kind == "signout" and _FILTER.search(item) and _LIST.search(item):
        kind = "filter"
    given = "- **Given** a named user is signed in on a browser"
    if kind == "auth":
        given = "- **Given** the desk opens the site in a browser"
        when = "they sign in with the shared login"
        then = "the main list opens and they are not asked to create a personal account"
    elif kind == "time":
        when = "they save a new record"
        then = "the time field is the current time and cannot be typed over"
    elif kind == "form":
        fields = _capture_fields(item)
        when = "they submit " + (", ".join(fields) if fields else "the form")
        then = "the new record appears on the current list"
    elif kind == "signout":
        when = "they choose Sign out on one row"
        then = "that row leaves the current list"
    elif kind == "filter":
        hidden = _object_phrase(item, r"\bexclude", r"\bhide", r"\bfilter out")
        when = "a row changes while the list is on screen"
        then = (
            f"{hidden} drop off the list without a page reload"
            if hidden
            else "rows that no longer match drop off the list without a page reload"
        )
    elif kind == "list":
        shown = _object_phrase(item, r"\blist of", r"\btable of", r"\bshowing", r"\bdisplaying")
        when = "they open the list"
        then = f"the list shows {shown} and no other rows" if shown else "the list shows only the rows this requirement names"
    else:
        action = item[0].lower() + item[1:] if item else "use this capability"
        when = f"they {action}"
        then = "the screen shows the result of that action"
    return (
        f"{given}\n"
        f"- **When** {when}\n"
        f"- **Then** {then}\n"
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


def _product_title(success: str, in_scope: list[str]) -> str:
    from phase1.rails import title_from_request

    first = (success or "").split(".")[0].strip()
    words = first.split()
    if 2 <= len(words) <= 8 and len(first) <= 60:
        return first[0].upper() + first[1:]
    return title_from_request(" ".join(in_scope[:1]) or success)


def _entities_for_ui(blob: str, items: list[str]) -> list[tuple[str, list[str]]]:
    """Use a real record when the scope names one. Keep the keyword models."""
    stock = _entities(blob)
    if len(stock) < 2 or stock[1][0] != "Record":
        return stock
    noun = _subject_noun(items)
    if noun == "Record":
        return stock
    fields = ["id"]
    for item in items:
        if _kind(item) != "form":
            continue
        for label in _capture_fields(item):
            slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:32]
            if slug and slug not in fields:
                fields.append(slug)
    if any(_kind(item) == "time" for item in items) and "time_in" not in fields:
        fields.append("time_in")
    if any(_kind(item) == "signout" for item in items) and "signed_out_at" not in fields:
        fields.append("signed_out_at")
    if "status" not in fields:
        fields.append("status")
    login = "shared_login" if any(_kind(item) == "auth" for item in items) else "work_login"
    return [
        ("User", ["id", "name", login]),
        (noun, fields),
        ("AuditEntry", ["id", "actor_id", "action", "at"]),
    ]


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


def enrich_scope(report: dict[str, Any]) -> dict[str, Any]:
    """Make the intake report enough for a BRD to draw screens.

    Fields the requester already named are written onto the form bullet.
    Screens are listed once. Empty users, today, or success stay as open
    questions instead of a blank section. Nothing is invented.
    """
    shaped = dict(report)
    in_scope = [str(item).strip() for item in (shaped.get("in_scope") or []) if str(item).strip()]
    out_scope = [str(item).strip() for item in (shaped.get("out_of_scope") or []) if str(item).strip()]
    thickened: list[str] = []
    for item in in_scope:
        if _kind(item) == "form" and "fields:" not in item.lower():
            fields = _capture_fields(item)
            if fields:
                item = item.rstrip(".") + ". Fields: " + ", ".join(fields) + "."
        thickened.append(item)
    shaped["in_scope"] = thickened
    shaped["screens"] = [
        f"**{name}**: {desc}" for name, desc in _ui_screens(thickened, out_scope)
    ]
    questions = [
        str(item).strip()
        for item in (shaped.get("open_questions") or [])
        if str(item).strip() and not re.search(r"missed check-in|week or month end", str(item), re.I)
    ]
    if not str(shaped.get("users") or "").strip():
        questions.append("Who uses this was not stated.")
    if not str(shaped.get("current_state") or "").strip():
        questions.append("What happens today was not stated.")
    if not str(shaped.get("success") or "").strip():
        questions.append("What success looks like was not stated.")
    if not thickened:
        questions.append("What is in scope was not stated.")
    seen: set[str] = set()
    unique: list[str] = []
    for item in questions:
        key = item.lower()
        if key in seen or key in {"none", "none.", "_(none)_"}:
            continue
        seen.add(key)
        unique.append(item)
    shaped["open_questions"] = unique
    return shaped


def scope_needs_fill(markdown: str) -> bool:
    """True when this scope report still cannot drive a screen list.

    A report that already names its screens and writes ``Fields:`` on form
    bullets is left alone, so the same check can run for every requirement
    on startup without rewriting finished work.
    """
    text = markdown or ""
    lowered = text.lower()
    if "## in scope" not in lowered and "## users" not in lowered:
        return False
    parsed = parse_scope(text)
    enriched = enrich_scope(parsed)
    if not enriched.get("in_scope") and not enriched.get("screens"):
        return False
    if [str(item).strip() for item in (parsed.get("in_scope") or [])] != list(
        enriched.get("in_scope") or []
    ):
        return True
    if "## screens" not in lowered:
        return bool(enriched.get("screens"))
    if list(parsed.get("screens") or []) != list(enriched.get("screens") or []):
        return True
    return any(
        re.search(r"missed check-in|week or month end", str(item), re.I)
        for item in (parsed.get("open_questions") or [])
    )


def brd_already_drawn(brd_text: str, screens: list[str]) -> bool:
    """True when the specification already names every screen and its fields.

    No screen list means there is nothing new to draw. A startup pass then
    skips that requirement instead of replacing a specification that is
    already usable.
    """
    if not screens:
        return True
    current = brd_text or ""
    for line in screens:
        match = re.match(r"^\*\*([^*]+)\*\*:\s*(.*)$", str(line).strip())
        if not match:
            return False
        name, desc = match.group(1).strip(), match.group(2)
        if f"**{name}**" not in current:
            return False
        parts = re.split(r"fields:\s*", desc, maxsplit=1, flags=re.I)
        if len(parts) != 2:
            continue
        for label in re.split(r",| and ", parts[1]):
            label = label.strip(" .")
            if len(label) >= 2 and label.lower() not in current.lower():
                return False
    return True


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
    from phase1.rails import as_string_list, shape_scope_report

    parsed = enrich_scope(parse_scope(scope_md))
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
    title = _product_title(success, in_scope)
    pages = _ui_screens(in_scope, out_lines)
    stated_screens = [str(item).strip() for item in (parsed.get("screens") or []) if str(item).strip()]
    if stated_screens:
        page_behaviour = "\n".join(
            item if item.startswith("- ") else f"- {item}" for item in stated_screens
        )
    else:
        page_behaviour = _page_lines(in_scope, out_lines)
    blob = " ".join(in_scope + out_lines + [success])
    entities = _entities_for_ui(blob, in_scope)

    req_blocks = []
    seen_criteria: set[str] = set()
    for i, item in enumerate(in_scope):
        tid = ids[i] if i < len(ids) else ids[-1]
        criteria = _gherkin(item)
        key = re.sub(r"\s+", " ", criteria.split("**When**", 1)[-1]).lower()
        if key in seen_criteria:
            # Two items with one When/Then cannot be tested apart.
            criteria = re.sub(
                r"(- \*\*Then\*\* [^\n]+)",
                lambda m: m.group(1) + f" (specifically: {_clip(item, 120).rstrip('.')})",
                criteria,
                count=1,
            )
        seen_criteria.add(key)
        req_blocks.append(
            f"### {tid} — {_clip(item, 80)}\n\n"
            f"**Source:** approved scope, in-scope item {i + 1}.\n\n"
            f"{_clip(item, 600)}\n\n"
            f"**Acceptance criteria:**\n{criteria}\n"
        )
    shared_desk = any(
        token in " ".join(in_scope + assumptions).lower()
        for token in ("shared login", "shared account", "shared credential", "one login")
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
        "page_behaviour": page_behaviour,
        "data_model": _data_model_text(entities),
        "er_diagram": _er_diagram(entities),
        "security": (
            (
                "One shared desk login in the browser, stored as a password hash. Everyone signed in sees the same views. "
                if shared_desk
                else "Work login in the browser. Role-appropriate views (staff vs manager). "
            )
            + "No secrets in images. Personal data stays in the tenant and is masked before model egress."
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


def refine_keeps_structure(drafted: str, refined: str) -> bool:
    """A model rewrite that drops a requirement or a screen is not usable."""
    if not refined or not re.search(r"Page behaviour", refined, re.I) or "### " not in refined:
        return False
    draft_ids = set(re.findall(r"REQ-\d+-R\d+", drafted))
    if draft_ids - set(re.findall(r"REQ-\d+-R\d+", refined)):
        return False
    draft_screens = set(re.findall(r"^- \*\*([^*]+)\*\*:", drafted, re.M))
    refined_screens = set(re.findall(r"^- \*\*([^*]+)\*\*:", refined, re.M))
    if draft_screens - refined_screens:
        return False
    # A rewrite that keeps the screen names but drops the data model is not usable.
    # The architect and the screens both read those fields.
    for match in re.finditer(r"^- \*\*([^*]+):\*\*\s*(.+)$", drafted, re.M):
        name = match.group(1).strip()
        if name not in refined:
            return False
        for field in (part.strip() for part in match.group(2).split(",")):
            if field and field not in refined:
                return False
    return True


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
