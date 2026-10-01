"""Does this stage's artefact satisfy every BRD requirement?

One check per stage, all built on the same requirement → screen → record
mapping (phase3.trace). Every review card reads these rows, so a reviewer
sees exactly which requirement is missing and can send the stage back for a
revision instead of asking whether it is "enough".

Each stage returns {"ok", "rows", "problems", "summary"}. A row is
{"id", "title", "satisfies", "gaps", "covered"}; problems are stage-wide.
"""

from __future__ import annotations

import re
from typing import Any

from phase2 import contract
from phase3 import trace

STAGES = ("business", "architecture", "analysis", "screens", "plan", "build", "uat", "release")


def _result(
    rows: list[dict[str, Any]],
    problems: list[str],
    label: str,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """notes are reported but do not fail the stage (e.g. a signed BRD seen at Gate 3)."""
    missing = [row["id"] for row in rows if not row.get("satisfies")]
    ok = not missing and not problems
    if ok:
        summary = f"The {label} covers all {len(rows)} BRD requirements."
    else:
        parts = []
        if missing:
            parts.append(f"{len(missing)} of {len(rows)} requirements not satisfied ({', '.join(missing)})")
        if problems:
            parts.append(f"{len(problems)} problem{'s' if len(problems) != 1 else ''}")
        summary = f"The {label} needs a revision: " + "; ".join(parts) + "."
    return {
        "ok": ok,
        "rows": rows,
        "problems": problems,
        "notes": list(notes or []),
        "summary": summary,
        "missing": missing,
    }


def _row(req: dict[str, str], gaps: list[str], covered: list[str]) -> dict[str, Any]:
    return {
        "id": req["id"],
        "title": req["title"],
        "satisfies": not gaps,
        "gaps": gaps,
        "covered": covered,
    }


def _entity_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    decision = row.get("architecture_decision") or {}
    stored = [item for item in decision.get("entities") or [] if isinstance(item, dict)]
    if stored:
        return stored
    return [
        {"name": name, "fields": fields}
        for name, fields in trace.entities(
            str(row.get("brd_text") or ""), str(row.get("architecture_text") or "")
        )
    ]


# --- shared text helpers -------------------------------------------------

_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.M)
_OPEN_NONE = re.compile(r"^(none|n/?a|no open questions?|nothing open)\.?$", re.I)
_ROLE_WORDS = re.compile(
    r"\b(manager|supervisor|admin(?:istrator)?s?|role[- ]based|per[- ]user|individual accounts?)\b", re.I
)
_CONTRACT = re.compile(r"`(GET|POST|PUT|PATCH|DELETE)\s+(/\S+?)`")
_REALTIME_ASK = re.compile(r"real[- ]?time|\blive (list|view|update)|instantly", re.I)
_REALTIME_HOW = re.compile(r"real[- ]?time|poll|refetch|websocket|server-sent|\bsse\b|auto[- ]?refresh", re.I)
_PASSWORD = re.compile(r"password|hash|secret|credential", re.I)
_QUOTED_ACTION = re.compile(r"['‘’\"“”]([A-Z][A-Za-z ]{1,30})['‘’\"“”]")
# Verb stems that say what a requirement does to its record.
_OPERATIONS = (
    ("create", re.compile(r"\b(captur|add|creat|regist|submit|book|enter|new)\w*", re.I)),
    ("list", re.compile(r"\b(list|view|dashboard|display|show|see|filter|search|report)\w*", re.I)),
    # "marked as inside" / "closed tickets" describe a state, not an action on the record.
    ("update", re.compile(r"\b(sign[- ]?out\b|updat|edit|chang|mark(?!ed)|cancel|approv|reject|close(?!d)|complete\b|check[- ]?out|depart)\w*", re.I)),
    ("delete", re.compile(r"\b(delet|remov|purg)\w*", re.I)),
)


def _sections(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    matches = list(_HEADING.finditer(text or ""))
    for index, match in enumerate(matches):
        title = re.sub(r"^\d+\.\s*", "", match.group(1)).strip().lower()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        found.setdefault(title, text[match.end():end].strip())
    return found


def _section(text: str, *names: str) -> str:
    sections = _sections(text)
    for name in names:
        for title, body in sections.items():
            if title == name or title.startswith(name + " ") or title.startswith(name):
                return body
    return ""


def _bullets(body: str) -> list[str]:
    return [
        re.sub(r"[*_`]+", "", match.group(1)).strip()
        for match in re.finditer(r"^\s*[-*]\s+(.+)$", body or "", re.M)
    ]


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _label_matches_field(label: str, fields: list[str]) -> bool:
    key = _fold(label)
    if not key:
        return True
    for field in fields:
        other = _fold(field)
        if not other:
            continue
        if key == other or (len(key) >= 4 and key in other) or (len(other) >= 4 and other in key):
            return True
    return False


def _architecture_body(architecture_text: str) -> str:
    # The architecture carries a copy of the BRD; only the architect's own text counts.
    return re.split(r"^##\s+Source BRD excerpt\b", architecture_text or "", maxsplit=1, flags=re.M)[0]


_STEP = re.compile(r"\b(Given|When|Then|And)\b[:\s]+(.*?)(?=[,;]?\s*\b(?:Given|When|Then|And)\b|\n|$)")


def _criteria_lines(criteria: str) -> dict[str, str]:
    """Given/When/Then steps, as bullets or written inline on one line.

    Keywords are matched capitalised so "then" inside a sentence is not a step.
    """
    lines: dict[str, str] = {}
    flat = re.sub(r"[*_`]+", "", criteria or "")
    for match in _STEP.finditer(flat):
        key = match.group(1).lower()
        if key not in lines:
            lines[key] = match.group(2).strip().rstrip(".")
    return lines


def _auth_page(pages: list[dict[str, str]]) -> dict[str, str] | None:
    for page in pages:
        if contract._kind(str(page.get("id") or ""), str(page.get("description") or "")) == "auth":
            return page
    return None


def _contracts(architecture_text: str) -> list[tuple[str, str]]:
    body = _section(_architecture_body(architecture_text), "api contracts")
    return [(m.group(1), m.group(2)) for m in _CONTRACT.finditer(body)]


def _operation_gaps(
    req: dict[str, str],
    tied: list[dict[str, str]],
    about: list[str],
    contracts: list[tuple[str, str]],
) -> list[str]:
    """API operations the requirement needs, checked against the written contracts."""
    from phase4.product import table_name

    gaps: list[str] = []
    if any(contract._kind(str(p.get("id") or ""), str(p.get("description") or "")) == "auth" for p in tied):
        if not any(
            method == "POST" and re.search(r"session|login|auth|sign[-_]?in", path, re.I)
            for method, path in contracts
        ):
            gaps.append("no sign-in API contract (e.g. POST /api/session)")
        return gaps
    if not about:
        return gaps
    record = about[0]
    collection = f"/api/{table_name(record)}"
    text = trace.statement(req)
    for operation, pattern in _OPERATIONS:
        if not pattern.search(text):
            continue
        if operation == "create":
            ok = ("POST", collection) in contracts
        elif operation == "list":
            ok = ("GET", collection) in contracts
        elif operation == "update":
            ok = any(
                (method in {"PATCH", "PUT"} and path.startswith(collection + "/"))
                or (method == "POST" and path.startswith(collection + "/:"))
                for method, path in contracts
            )
        else:
            ok = any(method == "DELETE" and path.startswith(collection) for method, path in contracts)
        if not ok:
            gaps.append(f"no API contract to {operation} {record}")
    return gaps


# --- BRD (Gate 2) ----------------------------------------------------------


def business(brd_text: str) -> dict[str, Any]:
    """Is the BRD itself complete, testable and consistent?"""
    reqs = trace.requirements(brd_text)
    pages = trace.pages(brd_text)
    records = trace.entities(brd_text)
    page_text = " ".join(f"{p.get('id')} {p.get('description')}" for p in pages).lower()
    rows = []
    signatures: dict[tuple[str, str], str] = {}
    for req in reqs:
        gaps: list[str] = []
        covered: list[str] = []
        criteria = str(req.get("criteria") or "")
        lines = _criteria_lines(criteria)
        if not criteria.strip():
            gaps.append("no acceptance criteria")
        else:
            for part in ("given", "when", "then"):
                if not lines.get(part):
                    gaps.append(f"acceptance criteria have no {part.title()} step")
            outcome = lines.get("then", "")
            if outcome and _VAGUE.search(outcome):
                gaps.append(f'the Then outcome is placeholder text: "{outcome[:80]}"')
            if lines.get("when") and outcome:
                key = (_fold(lines["when"]), _fold(outcome))
                if key in signatures:
                    gaps.append(
                        f"same When/Then as {signatures[key]}, so a test cannot tell the two apart"
                    )
                else:
                    signatures[key] = req["id"]
            if not gaps:
                covered.append("Given/When/Then")
        tied = trace.pages_for(req, pages)
        about = trace.records_for(req, records, tied)
        covered.extend(f"screen {page['id']}" for page in tied)
        covered.extend(f"record {name}" for name in about)
        if not tied and not about:
            gaps.append("not tied to a BRD screen or record")
        for label in contract._stated_labels(trace.statement(req)):
            on_page = label.lower() in page_text
            in_model = any(_label_matches_field(label, fields) for _name, fields in records)
            if not on_page and not in_model:
                gaps.append(f'field "{label}" is on no screen and in no record')
        rows.append(_row(req, gaps, covered))

    problems: list[str] = []
    if not reqs:
        problems.append("the BRD has no numbered requirements (### REQ-nnnn-Rnn)")
    ids = [req["id"] for req in reqs]
    for dup in sorted({rid for rid in ids if ids.count(rid) > 1}):
        problems.append(f"requirement id {dup} is used twice")
    if reqs and not pages:
        problems.append("no Page behaviour section: screens cannot be designed or checked")
    if reqs and not records:
        problems.append("no data model: records and fields cannot be designed or checked")
    for page in pages:
        if not any(page in trace.pages_for(req, pages) for req in reqs):
            problems.append(f"screen {page['id']} is not asked for by any requirement")
    for name, fields in records:
        if len([f for f in fields if f != "id"]) == 0:
            problems.append(f"record {name} has no fields")
    notes: list[str] = []
    for item in _bullets(_section(brd_text, "open questions")):
        if _OPEN_NONE.match(item):
            continue
        # The factory's own critique lines are prompts for reviewers, not unanswered questions.
        if item.lower().startswith("(critique)"):
            notes.append(f"reviewer prompt: {item[len('(critique)'):].strip()[:160]}")
        else:
            problems.append(f"open question not resolved: {item[:120]}")
    if trace.shared_login(brd_text):
        security = _section(brd_text, "security design", "security")
        roles = sorted({m.group(1).lower() for m in _ROLE_WORDS.finditer(security)})
        if roles:
            problems.append(
                "the security design names roles (" + ", ".join(roles) + ") but the BRD uses one shared login"
            )
    return _result(rows, problems, "BRD", notes)


# --- Architecture (Gate 3, architect) ---------------------------------------


def architecture(brd_text: str, architecture_text: str, entities: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Does the architecture give every requirement a screen, fields and an API?"""
    body = _architecture_body(architecture_text)
    reqs = {req["id"]: req for req in trace.requirements(brd_text)}
    pages = trace.pages(brd_text)
    records = trace.entities(brd_text, body)
    contracts = _contracts(architecture_text)
    rows = []
    for item in contract.coverage_rows(brd_text, body):
        gaps = list(item["gaps"])
        req = reqs.get(item["id"])
        if req is not None:
            tied = trace.pages_for(req, pages)
            gaps.extend(_operation_gaps(req, tied, trace.records_for(req, records, tied), contracts))
        rows.append(
            {
                "id": item["id"],
                "title": item["title"],
                "satisfies": not gaps,
                "gaps": gaps,
                "covered": list(item["covered"]),
            }
        )

    problems: list[str] = []
    if not body.strip():
        return _result(rows, ["no architecture written yet"], "architecture")
    problems.extend(contract.problems(brd_text, entities) if entities else [])
    sections = _sections(body)
    for name in ("stack", "modules", "data model", "api contracts", "non-functional"):
        if not any(title.startswith(name) for title in sections):
            problems.append(f"no {name.title()} section")
    if "locked stack profile" not in body.lower():
        problems.append("no locked stack profile")
    model = _section(body, "data model")
    for name, _fields in records:
        line = next((ln for ln in model.splitlines() if f"**{name}" in ln), "")
        if line and "owner" not in line.lower():
            problems.append(f"record {name} has no owning department")
    if not trace.wants_ai(brd_text) and (
        re.search(r"^-\s+`ai`", _section(body, "modules"), re.M) or any("/api/ai" in p for _m, p in contracts)
    ):
        problems.append("an AI module or /api/ai contract is designed but the BRD asks for no AI")
    if _REALTIME_ASK.search(brd_text or "") and not _REALTIME_HOW.search(
        _section(body, "non-functional") + " " + _section(body, "adrs")
    ):
        problems.append("the BRD asks for real-time updates but the architecture never says how (polling, refetch, websocket)")
    auth = _auth_page(pages)
    if auth is not None and "password" in str(auth.get("description") or "").lower():
        if not _PASSWORD.search(model):
            problems.append(f"{auth['id']} asks for a password but no record stores a password hash")
    already = any("no sign-in API contract" in gap for row in rows for gap in row["gaps"])
    if auth is not None and not already and not any(
        method == "POST" and re.search(r"session|login|auth|sign[-_]?in", path, re.I) for method, path in contracts
    ):
        problems.append(f"{auth['id']} has no sign-in API contract (e.g. POST /api/session)")
    return _result(rows, problems, "architecture")


# --- Business analysis (Gate 3, business analyst) ---------------------------


def analysis(
    brd_text: str,
    architecture_text: str,
    entities: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Can each requirement be carried out end to end on the designed product?

    For every requirement: its screen is designed, every field its screen
    shows maps to a stored field, its named action is on the screen, and the
    API can perform what it asks. BRD defects are listed as notes: the BRD is
    signed at Gate 2 and changes through a change request, not a Gate 3 revise.
    """
    body = _architecture_body(architecture_text)
    pages = trace.pages(brd_text)
    records = (
        [(str(e.get("name")), [str(f) for f in e.get("fields") or []]) for e in entities or [] if e.get("name")]
        or trace.entities(brd_text, body)
    )
    arch = architecture(brd_text, architecture_text, entities)
    arch_gaps = {row["id"]: row["gaps"] for row in arch["rows"]}
    rows = []
    for req in trace.requirements(brd_text):
        gaps = list(arch_gaps.get(req["id"], []))
        covered: list[str] = []
        tied = trace.pages_for(req, pages)
        for page in tied:
            record = trace.record_for_page(page, records)
            fields = next((f for n, f in records if n == record), []) or [
                f for _n, fs in records for f in fs
            ]
            for label in contract._stated_labels(str(page.get("description") or "")):
                if _label_matches_field(label, fields):
                    covered.append(f"{page['id']}.{label}")
                else:
                    gaps.append(f'{page["id"]} shows "{label}" but no record stores it')
        for action in _QUOTED_ACTION.findall(req["title"]):
            if not any(action.lower() in str(p.get("description") or "").lower() for p in tied):
                gaps.append(f'no screen offers the "{action}" action')
        rows.append(_row(req, list(dict.fromkeys(gaps)), covered))
    brd = business(brd_text)
    notes = [
        f"BRD {row['id']}: {gap}" for row in brd["rows"] for gap in row["gaps"]
    ] + [f"BRD: {problem}" for problem in brd["problems"]]
    if notes:
        notes = [
            "These are BRD defects. The BRD is signed at Gate 2, so they need a change request.",
            *notes,
        ]
    return _result(rows, list(arch["problems"]), "business analysis", notes)


def screens(
    brd_text: str, screen_rows: list[dict[str, Any]], entities: list[dict[str, Any]]
) -> dict[str, Any]:
    reqs = trace.requirements(brd_text)
    pages = trace.pages(brd_text)
    by_page: dict[str, dict[str, Any]] = {}
    for screen in screen_rows:
        page = trace.screen_page(str(screen.get("name") or ""), pages)
        if page and page["id"] not in by_page:
            by_page[page["id"]] = screen
    page_gaps: dict[str, list[str]] = {}
    for page in pages:
        screen = by_page.get(page["id"])
        if screen is None:
            page_gaps[page["id"]] = [f"no {page['id']} screen"]
            continue
        binding = contract.screen_contract(page, entities)
        source = str(screen.get("source") or "")
        if contract.covers(source, binding):
            page_gaps[page["id"]] = []
            continue
        missing = [
            label
            for label in (binding or {}).get("typed") or (binding or {}).get("labels") or []
            if f'label="{label}"' not in source and f"<th>{label}</th>" not in source
        ]
        page_gaps[page["id"]] = [
            f"{screen.get('name')} is missing " + (", ".join(missing) if missing else "the locked fields")
        ]
    rows = []
    for req in reqs:
        tied = trace.pages_for(req, pages)
        gaps = [gap for page in tied for gap in page_gaps.get(page["id"], [])]
        covered = [str(by_page[page["id"]].get("name")) for page in tied if page["id"] in by_page]
        if not tied:
            rows.append({**_row(req, [], []), "satisfies": True, "note": "no screen named"})
            continue
        rows.append(_row(req, gaps, covered))
    problems = [] if screen_rows else ["no screens generated yet"]
    return _result(rows, problems, "screens")


def plan(
    brd_text: str,
    screen_rows: list[dict[str, Any]],
    tickets: list[dict[str, Any]],
    tests: list[dict[str, Any]],
    *,
    sprint0: dict[str, Any] | None = None,
    architecture_text: str = "",
) -> dict[str, Any]:
    reqs = trace.requirements(brd_text)
    pages = trace.pages(brd_text)
    screen_names = {str(s.get("name") or "") for s in screen_rows}
    screen_tickets = {
        str(t.get("screen") or "")
        or str(t.get("title") or "")[: -len(" screen")]
        for t in tickets
        if t.get("screen") or str(t.get("title") or "").lower().endswith(" screen")
    }
    all_ids = {req["id"] for req in reqs}

    def blanket(ticket: dict[str, Any]) -> bool:
        # A ticket that claims every requirement says nothing about who builds which one.
        if str(ticket.get("department")) == "qa":
            return True
        return len(all_ids) >= 3 and all_ids <= set(ticket.get("trace") or [])

    rows = []
    for req in reqs:
        gaps: list[str] = []
        traced = [str(t.get("id")) for t in tickets if req["id"] in (t.get("trace") or [])]
        specific = [
            str(t.get("id"))
            for t in tickets
            if req["id"] in (t.get("trace") or []) and not blanket(t)
        ]
        checked = [str(c.get("id")) for c in tests if str(c.get("criterion") or "") == req["id"]]
        if not traced:
            gaps.append("no ticket traces it")
        elif not specific:
            gaps.append("only tickets that claim every requirement trace it (" + ", ".join(traced) + ")")
        if not checked:
            gaps.append("no test checks it")
        for page in trace.pages_for(req, pages):
            names = [n for n in screen_names if trace.screen_page(n, [page])]
            if names and not any(n in screen_tickets for n in names):
                gaps.append(f"no ticket builds the {names[0]} screen")
        rows.append(_row(req, gaps, [*traced, *checked]))

    problems: list[str] = []
    status = str((sprint0 or {}).get("status") or "")
    if sprint0 is not None and status not in {"green", "skipped"}:
        problems.append(f"Sprint 0 is {status or 'not run'}: CI, IaC and the allow-list are not ready")
    seen: dict[tuple[str, ...], str] = {}
    for ticket in tickets:
        key = tuple(sorted(str(p) for p in ticket.get("paths") or []))
        if not key or any("prisma" in p or "alembic" in p for p in key):
            continue
        if key in seen:
            problems.append(
                f"{seen[key]} and {ticket.get('id')} write the same files ({', '.join(key)})"
            )
        else:
            seen[key] = str(ticket.get("id"))
    if any(str(t.get("department")) == "ai" for t in tickets) and not trace.wants_ai(brd_text):
        problems.append("an AI ticket is planned but the BRD asks for no AI")
    if trace.shared_login(brd_text):
        for case in tests:
            name = str(case.get("name") or "").lower()
            if "another user" in name or "own record" in name:
                problems.append(
                    f"{case.get('id')} tests per-user ownership, but the BRD uses one shared login"
                )
    records = trace.entities(brd_text, architecture_text)
    api_by_record = {str(t.get("entity")): str(t.get("id")) for t in tickets if t.get("entity") and not t.get("screen")}
    for ticket in tickets:
        if not ticket.get("screen"):
            continue
        record = trace.record_for_page(trace.screen_page(str(ticket["screen"]), pages), records)
        need = api_by_record.get(record)
        if need and need not in (ticket.get("depends_on") or []):
            problems.append(f"{ticket.get('id')} ({ticket['screen']}) does not wait for the {record} API ({need})")
    problems.extend(_record_problems(tickets, records))
    problems.extend(_test_problems(brd_text, tests, records, pages))
    if not any(case.get("critical") for case in tests):
        problems.append("no critical test")
    if not tickets:
        problems.append("no tickets")
    return _result(rows, problems, "plan")


_VAGUE = re.compile(
    r"described in this item|this item|as described|\bthe current records\b|\btbd\b|\btodo\b|works as expected|\betc\b",
    re.I,
)
_NEGATIVE = re.compile(r"refus|reject|invalid|wrong|denied|empty|missing|blank|not saved|nothing is saved|error|fail", re.I)


def _is_schema(ticket: dict[str, Any]) -> bool:
    title = str(ticket.get("title") or "").lower()
    paths = " ".join(str(p) for p in ticket.get("paths") or []).lower()
    return "schema" in title or "migration" in title or any(
        token in paths for token in ("prisma", "alembic", "migration")
    )


def _record_problems(tickets: list[dict[str, Any]], records: list[tuple[str, list[str]]]) -> list[str]:
    if not records:
        return []
    from phase4.product import table_name

    problems = []
    if not any(_is_schema(t) for t in tickets):
        problems.append(
            "no schema/migration ticket creates the records (" + ", ".join(name for name, _ in records) + ")"
        )
    for name, _fields in records:
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).lower()
        table = table_name(name)
        owned = any(
            not _is_schema(t)
            and (
                str(t.get("entity") or "") == name
                or name.lower() in str(t.get("title") or "").lower()
                or spaced in str(t.get("title") or "").lower()
                or any(f"/{table}/" in str(p) or f"/{name.lower()}/" in str(p).lower() for p in t.get("paths") or [])
            )
            for t in tickets
        )
        if not owned:
            problems.append(f"no ticket builds the {name} record")
    return problems


def _test_problems(
    brd_text: str,
    tests: list[dict[str, Any]],
    records: list[tuple[str, list[str]]],
    pages: list[dict[str, str]],
) -> list[str]:
    problems = []
    by_name: dict[str, str] = {}
    for case in tests:
        name = str(case.get("name") or "").strip()
        key = re.sub(r"\s+", " ", name.lower())
        if key in by_name:
            problems.append(f"{by_name[key]} and {case.get('id')} have the same name, so one does not test its own requirement")
        else:
            by_name[key] = str(case.get("id"))
        if _VAGUE.search(name):
            problems.append(f"{case.get('id')} is too vague to run: \"{name[:90]}\"")
    names = " ".join(str(c.get("name") or "") for c in tests).lower()
    if tests and not _NEGATIVE.search(names):
        problems.append("no negative test (bad input or refused access is never tested)")
    has_sign_in = any(contract._kind(str(p.get("id") or ""), str(p.get("description") or "")) == "auth" for p in pages)
    if has_sign_in and not any(
        re.search(r"password|sign.?in|login|log in", str(c.get("name") or ""), re.I)
        and _NEGATIVE.search(str(c.get("name") or ""))
        for c in tests
    ):
        problems.append("no test that a wrong sign-in is refused")
    wants_audit = "audit" in (brd_text or "").lower() or any("audit" in name.lower() for name, _ in records)
    if wants_audit and "audit" not in names:
        problems.append("no test that the audit entry is written")
    return problems


def build(
    brd_text: str,
    tickets: list[dict[str, Any]],
    tests: list[dict[str, Any]],
    built: dict[str, Any],
) -> dict[str, Any]:
    reqs = trace.requirements(brd_text)
    blocking = [str(item) for item in built.get("blocking") or []]
    rows = []
    for req in reqs:
        traced = [str(t.get("id")) for t in tickets if req["id"] in (t.get("trace") or [])]
        gaps: list[str] = []
        if not traced:
            gaps.append("no ticket built it")
        stuck = [tid for tid in traced if any(tid in line for line in blocking)]
        if stuck:
            gaps.append("blocked build: " + ", ".join(stuck))
        if not any(str(c.get("criterion") or "") == req["id"] for c in tests):
            gaps.append("no test to run against it")
        rows.append(_row(req, gaps, traced))
    problems = []
    if not built:
        problems.append("no build yet")
    elif built.get("ok") is False:
        problems.extend(blocking[:6] or ["the build is not ok"])
    return _result(rows, problems, "build")


def uat(brd_text: str, tests: list[dict[str, Any]], report: dict[str, Any]) -> dict[str, Any]:
    reqs = trace.requirements(brd_text)
    results = {str(r.get("id")): r for r in ((report.get("qa") or {}).get("results") or [])}
    rows = []
    for req in reqs:
        mine = [str(c.get("id")) for c in tests if str(c.get("criterion") or "") == req["id"]]
        gaps: list[str] = []
        if not mine:
            gaps.append("no test ran against it")
        failed = [tid for tid in mine if tid in results and not results[tid].get("ok")]
        if failed:
            gaps.append("failed: " + ", ".join(failed))
        missing = [tid for tid in mine if results and tid not in results]
        if missing:
            gaps.append("not run: " + ", ".join(missing))
        rows.append(_row(req, gaps, [tid for tid in mine if tid not in failed]))
    problems = []
    if not report:
        problems.append("UAT has not run")
    else:
        qa_report = report.get("qa") or {}
        if qa_report.get("ok") is False:
            failed = [str(r.get("id")) for r in qa_report.get("results") or [] if not r.get("ok")]
            problems.append("critical tests failed: " + (", ".join(failed) or "see QA report"))
        if (report.get("dast") or {}).get("ok") is False:
            problems.append("the security scan (ZAP) did not pass")
        if (report.get("load") or {}).get("ok") is False:
            problems.append("the load test did not pass")
    return _result(rows, problems, "UAT")


def release(brd_text: str, report: dict[str, Any], uat_check: dict[str, Any]) -> dict[str, Any]:
    rows = [
        {**row, "gaps": list(row.get("gaps") or [])} for row in uat_check.get("rows") or []
    ]
    problems = []
    if not report:
        problems.append("no release prepared")
    else:
        admission = report.get("admission") or {}
        if admission and not admission.get("allowed"):
            problems.append(f"admission refused ({admission.get('status') or 'not allowed'})")
        if not (report.get("rollback") or {}).get("mechanisms"):
            problems.append("no rollback plan")
    return _result(rows, problems, "release")


def all_stages(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Checks for every stage that has an artefact, keyed by stage name."""
    brd_text = str(row.get("brd_text") or "")
    if not brd_text.strip():
        return {}
    architecture_text = str(row.get("architecture_text") or "")
    screen_rows = list(row.get("screens") or [])
    tickets = list(row.get("tickets") or [])
    tests = list(row.get("tests") or [])
    entities = _entity_rows(row)
    out: dict[str, dict[str, Any]] = {"business": business(brd_text)}
    if architecture_text:
        out["architecture"] = architecture(brd_text, architecture_text, entities)
        out["analysis"] = analysis(brd_text, architecture_text, entities)
    if screen_rows:
        out["screens"] = screens(brd_text, screen_rows, entities)
    if tickets or tests:
        out["plan"] = plan(
            brd_text,
            screen_rows,
            tickets,
            tests,
            sprint0=row.get("sprint0") if row.get("sprint0") is not None else None,
            architecture_text=architecture_text,
        )
    if row.get("build"):
        out["build"] = build(brd_text, tickets, tests, row.get("build") or {})
    if row.get("uat"):
        out["uat"] = uat(brd_text, tests, row.get("uat") or {})
    if row.get("release"):
        out["release"] = release(brd_text, row.get("release") or {}, out.get("uat") or uat(brd_text, tests, {}))
    return out
