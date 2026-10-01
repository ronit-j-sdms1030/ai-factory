"""Decomposer — tickets in dependency order from this BRD and these screens.

Precedent is off. A split that copies the last booking project divides the
wrong work. Path allow-lists become the Phase 4 confine boundary.

Every BRD requirement is traced by at least one ticket. A screen ticket waits
on the API of the record it reads or writes, not on the screen before it.
Each ticket owns its own folder, so two builders never write the same files.
"""

from __future__ import annotations

import re
from typing import Any

import department_routing as routing
from phase3 import trace
from stack_profiles import StackProfile


def _paths(department: str, title: str, profile: StackProfile) -> list[str]:
    node = profile.id == "node"
    lowered = title.lower()
    if department == "ai":
        return ["src/ai/**"] if node else ["app/ai/**"]
    if department == "qa":
        return ["tests/e2e/**"] if node else ["tests/**"]
    if "schema" in lowered or "migration" in lowered:
        return ["prisma/schema.prisma", "prisma/migrations/**"] if node else ["alembic/versions/**"]
    if "screen" in lowered or "form" in lowered or "ui" in lowered:
        return ["src/ui/**"] if node else ["app/ui/**"]
    return ["src/api/**"] if node else ["app/api/**"]


def _folder(kind: str, name: str, profile: StackProfile) -> list[str]:
    root = "src" if profile.id == "node" else "app"
    return [f"{root}/{kind}/{name}/**"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "feature"


def _needs_overlap(blob: str) -> bool:
    return any(token in blob for token in ("overlap", "double-book", "double book", "exclusion"))


def _entity_names(brd_text: str, architecture_text: str = "") -> list[str]:
    return [name for name, _fields in trace.entities(brd_text, architecture_text)]


def tickets(
    requirement_id: str,
    brd_text: str,
    screens: list[dict[str, Any]],
    profile: StackProfile,
    *,
    skill: str = "",
    architecture_text: str = "",
) -> list[dict[str, Any]]:
    if skill:
        import skill_registry

        skill_registry.require(skill, "decomposer")
    from phase4.product import table_name

    reqs = trace.requirements(brd_text) or [
        {"id": f"{requirement_id}-R01", "title": "Primary capability", "body": "", "criteria": ""}
    ]
    ids = [row["id"] for row in reqs]
    blob = f"{brd_text or ''} {architecture_text or ''}".lower()
    records = trace.entities(brd_text, architecture_text)
    brd_pages = trace.pages(brd_text)
    tied = {row["id"]: trace.pages_for(row, brd_pages) for row in reqs}
    about = {row["id"]: trace.records_for(row, records, tied[row["id"]]) for row in reqs}
    items: list[dict[str, Any]] = []

    schema_title = "schema + migration"
    if _needs_overlap(blob):
        schema_title += ", including the exclusion constraint"
    schema_id = f"{requirement_id}-W1"
    items.append({"id": schema_id, "title": schema_title, "depends_on": [], "trace": list(ids)})

    api_for: dict[str, str] = {}
    for name, _fields in records:
        tid = f"{requirement_id}-W{len(items) + 1}"
        api_for[name] = tid
        items.append(
            {
                "id": tid,
                "title": f"{name} API",
                "depends_on": [schema_id],
                "trace": [rid for rid in ids if name in about[rid]],
                "entity": name,
                "paths": _folder("api", table_name(name), profile),
            }
        )
    if not records:
        tid = f"{requirement_id}-W2"
        items.append(
            {
                "id": tid,
                "title": "records API",
                "depends_on": [schema_id],
                "trace": list(ids),
            }
        )

    for screen in screens:
        name = str(screen.get("name") or "Screen")
        page = trace.screen_page(name, brd_pages)
        record = trace.record_for_page(page, records)
        parent = api_for.get(record) or (items[1]["id"] if len(items) > 1 else schema_id)
        traced = [
            rid
            for rid in ids
            if page is not None and any(p.get("id") == page.get("id") for p in tied[rid])
        ]
        items.append(
            {
                "id": f"{requirement_id}-W{len(items) + 1}",
                "title": f"{name} screen",
                "depends_on": [parent],
                "trace": traced,
                "screen": name,
                "entity": record,
                "paths": _folder("ui", name, profile),
            }
        )

    covered = {rid for item in items[1:] for rid in item.get("trace") or []}
    for row in reqs:
        if row["id"] in covered:
            continue
        owner = next((api_for[name] for name in about[row["id"]] if name in api_for), schema_id)
        items.append(
            {
                "id": f"{requirement_id}-W{len(items) + 1}",
                "title": row["title"],
                "depends_on": [owner],
                "trace": [row["id"]],
                "paths": _folder("api", _slug(row["title"]), profile),
            }
        )

    if trace.wants_ai(brd_text):
        ai_trace = [
            row["id"]
            for row in reqs
            if trace.wants_ai(f"{row['title']} {row.get('body') or ''}")
        ] or ids[:1]
        items.append(
            {
                "id": f"{requirement_id}-AI1",
                "title": "prompt + inference adapter for screen suggestions",
                "depends_on": [next(iter(api_for.values()), schema_id)],
                "trace": ai_trace,
                "department": "ai",
            }
        )

    result = []
    for item in items:
        department = item.get("department") or routing.department_for(item["title"])
        result.append(
            {
                **item,
                "department": department,
                "paths": item.get("paths") or _paths(department, item["title"], profile),
            }
        )
    return result
