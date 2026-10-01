"""Architect Agent — lock one profile, then decide this BRD's shape.

Precedent retrieval is disabled. Modules, data model, contracts, NFRs and
ADRs come from this BRD plus the pre-approved profile set. An unconstrained
agent would pick a new database every run; this one cannot.
"""

from __future__ import annotations

import re
from typing import Any

import stack_profiles
from phase1 import brd as brd_mod
from phase4.product import parse_entities, table_name
from stack_profiles import StackProfile

_HEADING = re.compile(r"^##(?:\s+\d+\.)?\s+(.+?)\s*$", re.M)
_BULLET = re.compile(r"^[-*]\s+(?:\*\*)?(.+?)(?:\*\*)?\s*$", re.M)
def lock_profile(brd_text: str, *, skill: str = "") -> StackProfile:
    if skill:
        import skill_registry

        skill_registry.require(skill, "architect")
    return stack_profiles.choose(brd_text)


def decide(requirement_id: str, brd_text: str, *, skill: str = "") -> dict[str, Any]:
    """Derive the architecture for this BRD and lock one approved profile."""
    profile = lock_profile(brd_text, skill=skill)
    from phase2 import coverage

    from phase3 import trace

    pages = coverage.pages_from_brd(brd_text)
    sign_in = _sign_in_page(pages)
    entities = _with_password(_entities(brd_text), sign_in)
    nfrs = _nfrs(brd_text, profile)
    blob = (brd_text or "").lower()
    if _REALTIME.search(blob):
        nfrs.append(
            "Real-time: every list refetches after each write and polls every 5 seconds; "
            "no websocket server on the locked profile."
        )
    needs_overlap = any(token in blob for token in ("overlap", "double-book", "double book", "exclusion"))
    # An AI module the BRD never asked for is unplanned scope for every later gate.
    needs_ai = trace.wants_ai(brd_text)
    refused = stack_profiles.off_profile_hits(brd_text, profile)
    reason = _lock_reason(brd_text, profile)
    modules = _modules(profile, pages, entities, needs_ai)
    contracts = _contracts(entities, needs_ai, sign_in=sign_in is not None)
    extra_pages = _extra_pages(brd_text, pages)
    adrs = [
        _adr_stack(requirement_id, profile, reason, refused),
        _adr_integrity(requirement_id, profile, entities, needs_overlap),
        _adr_identity(requirement_id),
    ]
    return {
        "requirement_id": requirement_id,
        "profile": profile.dump(),
        "reason": reason,
        "pages": pages,
        "modules": modules,
        "entities": [{"name": name, "fields": fields, "owner": "development"} for name, fields in entities],
        "contracts": contracts,
        "nfrs": nfrs,
        "identity": (
            (
                f"The {sign_in['id']} screen posts to `POST /api/session`, which checks the login "
                "against a hashed password and sets a cookie session; `DELETE /api/session` ends it. "
                if sign_in is not None
                else ""
            )
            + "Factory reviewers use the cookie session + demo directory (PHASE1_DEV_MODE). "
            "Entra is client-SoW commercial and out of this demo path."
        ),
        "refused": refused,
        "needs_overlap": needs_overlap,
        "needs_ai": needs_ai,
        "extra_pages": extra_pages,
        "adrs": adrs,
        "architecture": "",
    }


def render(decision: dict[str, Any], *, note: str = "") -> str:
    profile = decision["profile"]
    modules = "\n".join(
        f"- `{row['id']}` — {row['boundary']} (paths: {', '.join(row['paths'])})"
        for row in decision["modules"]
    )
    model = "\n".join(
        f"- **{row['name']}:** {', '.join(row['fields'])} — owner `{row['owner']}`"
        for row in decision["entities"]
    ) or "- No entities extracted from this BRD."
    contracts = "\n".join(
        f"- `{row['method']} {row['path']}` — {row['purpose']}"
        for row in decision["contracts"]
    )
    nfrs = "\n".join(f"- {item}" for item in decision["nfrs"])
    refused = (
        "\n".join(f"- `{item}` — outside the locked profile; refused at Gate 3" for item in decision["refused"])
        or "- none"
    )
    mermaid = _diagram(profile, decision["needs_ai"])
    body = "\n".join(
        [
            f"# Architecture — {decision['requirement_id']}",
            "",
            f"Locked stack profile: **{profile['id']}**.",
            "",
            decision["reason"],
            "",
            "## Stack",
            "",
            f"- Frontend: {profile['frontend']}",
            f"- API: {profile['api']}",
            f"- Data access: {profile['data_access']}",
            f"- Migrations: {profile['migrations']}",
            f"- Tests: {profile['tests']}",
            f"- Lint: {profile['lint']}",
            f"- Database: {profile['database']}",
            "",
            "## Modules",
            "",
            modules,
            "",
            (
                "Development and AI are folders in one app, not two deploys."
                if decision["needs_ai"]
                else "One app: screens, API and data are folders in one deploy."
            ),
            "",
            "## Data model",
            "",
            model,
            "",
            "Shared records have one owning department and one spelling.",
            "",
            "## API contracts",
            "",
            contracts,
            "",
            f"Identity: {decision['identity']}",
            "",
            "## Diagram",
            "",
            mermaid,
            "",
            "## Non-functional",
            "",
            nfrs,
            "",
            "## Refused (off-profile)",
            "",
            refused,
            "",
            "## ADRs",
            "",
            "\n".join(f"- `{row['id']}` — {row['title']}" for row in decision["adrs"]),
            "",
            "## Source BRD excerpt",
            "",
            (decision.get("brd_excerpt") or "")[:2000],
            "",
        ]
    )
    note = str(note or "").strip()
    if note:
        body = body.rstrip() + "\n\n## Architect notes\n\n" + note + "\n"
    return body


def dump_report(architecture: str, profile: StackProfile, decision: dict[str, Any] | None = None) -> dict[str, Any]:
    decision = decision or {}
    return {
        "objective": architecture.split("\n", 1)[0].lstrip("# ").strip(),
        "architecture": architecture,
        "architectureDiagram": "flowchart LR\n  UI --> API --> DB",
        "techStack": [
            {"layer": "frontend", "choice": profile.frontend, "rationale": f"locked {profile.id} profile"},
            {"layer": "api", "choice": profile.api, "rationale": f"locked {profile.id} profile"},
            {"layer": "data", "choice": profile.data_access, "rationale": f"locked {profile.id} profile"},
            {"layer": "database", "choice": profile.database, "rationale": f"locked {profile.id} profile"},
        ],
        "modules": decision.get("modules") or [],
        "dataModel": decision.get("entities") or [],
        "apiContracts": decision.get("contracts") or [],
        "nfrs": decision.get("nfrs") or [],
        "adrs": [{"id": row["id"], "title": row["title"]} for row in decision.get("adrs") or []],
        "securityDesign": [
            decision.get("identity")
            or "Cookie session + demo directory. Entra is out of this demo path.",
            "No secrets in images",
            "Personal data is masked before model egress",
        ],
        "openQuestions": [],
        "assumptions": ["Precedent retrieval stayed disabled for this architecture."],
        "refused": decision.get("refused") or [],
    }


def _section(brd_text: str, *names: str) -> str:
    text = brd_text or ""
    wanted = {name.lower() for name in names}
    matches = list(_HEADING.finditer(text))
    for index, match in enumerate(matches):
        title = re.sub(r"^\d+\.\s*", "", match.group(1).strip()).lower()
        # "Data model outline" is the same section as "Data model".
        if title in wanted or any(title.startswith(name + " ") for name in wanted):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            return text[start:end].strip()
    return ""


def _entities(brd_text: str) -> list[tuple[str, list[str]]]:
    """Records from the data-model section. Screen names are not tables."""
    section = _section(brd_text, "data model")
    found = parse_entities(section) if section else []
    if not found:
        found = parse_entities(brd_text or "")
    if found:
        return found
    return brd_mod._entities(brd_text or "")


def _nfrs(brd_text: str, profile: StackProfile) -> list[str]:
    section = _section(brd_text, "non-functional requirements", "non-functional")
    items = [_clean_bullet(match.group(1)) for match in _BULLET.finditer(section)]
    locked = [
        f"Locked profile `{profile.id}`: {profile.frontend} + {profile.api} + {profile.database}.",
        "Browser only. A third runtime (JVM, .NET, Go) is outside the profile.",
        "Personal data is masked before model egress.",
        "Cookie session + demo directory. Entra is not live on this path.",
    ]
    seen = {item.lower() for item in locked}
    for item in items:
        if item and item.lower() not in seen and "entra" not in item.lower():
            locked.append(item)
            seen.add(item.lower())
    return locked


def _clean_bullet(text: str) -> str:
    return re.sub(r"^\*\*(.+?)\*\*:\s*", r"\1: ", text).strip()


def _lock_reason(brd_text: str, profile: StackProfile) -> str:
    if profile.id == "python":
        return (
            "The BRD names analytics, a batch job, or an ETL pipeline. "
            "Those map to the Python track. PostgreSQL stays fixed."
        )
    return (
        "The BRD is an interactive browser product. "
        f"The Node track is the default approved profile ({profile.api}, {profile.tests}). "
        "PostgreSQL is fixed on both tracks so a run cannot invent a database."
    )


def _modules(
    profile: StackProfile,
    pages: list[dict[str, str]],
    entities: list[tuple[str, list[str]]],
    needs_ai: bool,
) -> list[dict[str, Any]]:
    node = profile.id != "python"
    screens = ", ".join(f"`{page['id']}`" for page in pages) or "BRD pages"
    resources = ", ".join(f"`/{table_name(name)}`" for name, _ in entities) or "records"
    modules = [
        {
            "id": "ui",
            "boundary": f"React screens for {screens}",
            "paths": ["src/ui/**"] if node else ["app/ui/**"],
        },
        {
            "id": "api",
            "boundary": f"{profile.api} resources {resources}",
            "paths": ["src/api/**"] if node else ["app/api/**"],
        },
        {
            "id": "data",
            "boundary": f"{profile.migrations} against {profile.database} (SQLite file in the local demo)",
            "paths": ["prisma/**"] if node else ["alembic/**"],
        },
    ]
    if needs_ai:
        modules.append(
            {
                "id": "ai",
                "boundary": "Prompt + inference adapter for screen suggestions, same process, /api/ai",
                "paths": ["src/ai/**"] if node else ["app/ai/**"],
            }
        )
    return modules


_REALTIME = re.compile(r"real[- ]?time|\blive\b|instantly|immediately", re.I)
_PASSWORD_FIELD = re.compile(r"password|hash|secret|credential", re.I)


def _sign_in_page(pages: list[dict[str, str]]) -> dict[str, str] | None:
    from phase2 import contract

    for page in pages:
        if contract._kind(str(page.get("id") or ""), str(page.get("description") or "")) == "auth":
            return page
    return None


def _with_password(
    entities: list[tuple[str, list[str]]], sign_in: dict[str, str] | None
) -> list[tuple[str, list[str]]]:
    """A sign-in screen that asks for a password needs a record that stores its hash."""
    if sign_in is None or "password" not in str(sign_in.get("description") or "").lower():
        return entities
    if any(_PASSWORD_FIELD.search(field) for _name, fields in entities for field in fields):
        return entities
    from phase3 import trace

    owner = trace.record_for_page(sign_in, entities)
    if not owner:
        return entities
    return [(name, [*fields, "password_hash"] if name == owner else fields) for name, fields in entities]


def _contracts(
    entities: list[tuple[str, list[str]]], needs_ai: bool, *, sign_in: bool = False
) -> list[dict[str, str]]:
    rows = [{"method": "GET", "path": "/api/health", "purpose": "assembled app liveness"}]
    if sign_in:
        rows.extend(
            [
                {"method": "POST", "path": "/api/session", "purpose": "sign in; a wrong login or password is refused"},
                {"method": "DELETE", "path": "/api/session", "purpose": "sign out of the app"},
            ]
        )
    for name, _fields in entities:
        resource = table_name(name)
        rows.extend(
            [
                {"method": "GET", "path": f"/api/{resource}", "purpose": f"list {name}"},
                {"method": "GET", "path": f"/api/{resource}/:id", "purpose": f"read {name}"},
                {"method": "POST", "path": f"/api/{resource}", "purpose": f"create {name}"},
                {"method": "PATCH", "path": f"/api/{resource}/:id", "purpose": f"update {name}"},
                {"method": "DELETE", "path": f"/api/{resource}/:id", "purpose": f"remove {name}"},
            ]
        )
    if needs_ai:
        rows.append(
            {
                "method": "POST",
                "path": "/api/ai/suggest",
                "purpose": "screen suggestions from the AI module in the same app",
            }
        )
    return rows


def _extra_pages(brd_text: str, pages: list[dict[str, str]]) -> list[dict[str, str]]:
    blob = (brd_text or "").lower()
    if not ("admin" in blob and "cancel" in blob):
        return []
    if any("admin" in (page.get("id") or "").lower() for page in pages):
        return []
    return [{"id": "AdminCancel", "description": "Admin cancels any booking"}]


def _diagram(profile: dict[str, str], needs_ai: bool) -> str:
    lines = [
        "```mermaid",
        "flowchart LR",
        f"  UI[{profile['frontend']} screens] --> API[{profile['api']}]",
        f"  API --> DB[{profile['database']}]",
    ]
    if needs_ai:
        lines.append("  UI --> AI[src/ai]")
        lines.append("  AI --> API")
    lines.append("```")
    return "\n".join(lines)


def _adr_stack(requirement_id: str, profile: StackProfile, reason: str, refused: list[str]) -> dict[str, str]:
    refused_line = ", ".join(refused) if refused else "none"
    body = "\n".join(
        [
            f"# ADR-001 — Lock the `{profile.id}` stack profile",
            "",
            f"Requirement: {requirement_id}",
            "",
            "## Context",
            "",
            "Left unconstrained, an agent recommends a different database on every project.",
            "The factory has two pre-approved profiles. Both keep React and PostgreSQL.",
            "",
            "## Decision",
            "",
            reason,
            "",
            f"Frontend {profile.frontend}. API {profile.api}. Data {profile.data_access}.",
            f"Migrations {profile.migrations}. Tests {profile.tests}. Lint {profile.lint}.",
            f"Database {profile.database}.",
            "",
            "## Consequences",
            "",
            "Tickets, tests and CI inherit this lock. A third runtime fails the profile.",
            f"Off-profile tokens refused: {refused_line}.",
            "",
        ]
    )
    return {"id": "ADR-001", "title": f"Lock the `{profile.id}` stack profile", "body": body}


def _adr_integrity(
    requirement_id: str,
    profile: StackProfile | dict[str, str],
    entities: list[tuple[str, list[str]]] | list[dict[str, Any]],
    needs_overlap: bool,
) -> dict[str, str]:
    dumped = profile.dump() if isinstance(profile, StackProfile) else profile
    if needs_overlap:
        title = f"Overlap prevention is a {dumped['database']} constraint"
        context = (
            "Two people clicking book in the same second will both pass an "
            "application-level check and both insert."
        )
        decision = (
            f"Enforce exclusion in {dumped['database']}, not in {dumped['api']} application code. "
            f"Migrations use {dumped['migrations']}. Tests use {dumped['tests']}."
        )
        consequences = (
            "Concurrency tests must hit the database. A suite that only mocks the "
            "API will pass and still lose the race in production."
        )
    else:
        names = []
        for item in entities:
            if isinstance(item, dict):
                names.append(item.get("name") or "")
            else:
                names.append(item[0])
        title = "Shared entities have one owner and one spelling"
        context = "Two streams inventing " + (" / ".join(names[:3]) or "the same record") + " under different names."
        decision = (
            f"The data model is locked from this BRD. {dumped['migrations']} owns schema. "
            "Each shared entity has one department owner."
        )
        consequences = "A later ticket cannot introduce a rival table name or a second database."
    body = "\n".join(
        [
            f"# ADR-002 — {title}",
            "",
            f"Requirement: {requirement_id}",
            "",
            "## Context",
            "",
            context,
            "",
            "## Decision",
            "",
            decision,
            "",
            "## Consequences",
            "",
            consequences,
            "",
        ]
    )
    return {"id": "ADR-002", "title": title, "body": body}


def _adr_identity(requirement_id: str) -> dict[str, str]:
    body = "\n".join(
        [
            "# ADR-003 — Demo identity is cookie login, not Entra",
            "",
            f"Requirement: {requirement_id}",
            "",
            "## Context",
            "",
            "The commercial SoW names Entra. This walkthrough must run without tenant keys.",
            "",
            "## Decision",
            "",
            "Cookie session + PHASE1_DEV_MODE directory. API contracts do not claim Entra SSO.",
            "",
            "## Consequences",
            "",
            "Reviewers sign in as gate roles. Replacing this with Entra is a later client integration,",
            "not a decision this run is allowed to invent.",
            "",
        ]
    )
    return {"id": "ADR-003", "title": "Demo identity is cookie login, not Entra", "body": body}
