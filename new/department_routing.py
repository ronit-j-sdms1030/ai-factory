"""Department routing rules — the one prompt BMAD does not have.

Tickets are assigned to organisational streams, not epics. Shared entities
must have one owner and one spelling.
"""

from __future__ import annotations

import re
from typing import Any

DEPARTMENTS = ("development", "qa", "devops", "ai", "sales")

_RULES: tuple[tuple[str, str], ...] = (
    (r"schema|migration|exclusion|prisma|alembic", "development"),
    (r"\bapi\b|endpoint|backend", "development"),
    (r"screen|form|ui|frontend|booking form|availability", "development"),
    (r"cancel|admin", "development"),
    (r"model|prompt|inference|eval", "ai"),
    (r"pipeline|ci |iac|opentofu|secret|environment", "devops"),
    (r"test case|e2e|qa ", "qa"),
    (r"overview|sales|brief", "sales"),
)


def department_for(title: str, *, default: str = "development") -> str:
    blob = title.lower()
    for pattern, department in _RULES:
        if re.search(pattern, blob):
            return department
    return default


def fold_entity(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def canonical_entity(name: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts) or name


def entities_from_text(*blobs: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for blob in blobs:
        for match in re.findall(r"\b([A-Z][A-Za-z]+(?:[A-Z][A-Za-z]+)+)\b", blob):
            key = fold_entity(match)
            if key not in seen:
                seen.add(key)
                found.append(canonical_entity(match))
        for match in re.findall(r"`([A-Za-z][A-Za-z0-9_]*)`", blob):
            key = fold_entity(match)
            if key not in seen:
                seen.add(key)
                found.append(canonical_entity(match))
    return found


def assign_owners(entities: list[str], tickets: list[dict[str, Any]]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for entity in entities:
        key = fold_entity(entity)
        for ticket in tickets:
            if fold_entity(entity) in fold_entity(ticket.get("title") or "") or fold_entity(
                entity
            ) in fold_entity(" ".join(ticket.get("paths") or [])):
                owners[key] = ticket["department"]
                break
        owners.setdefault(key, "development")
    return {canonical_entity(name): owners[fold_entity(name)] for name in entities}


def naming_collisions(entities: list[str]) -> list[dict[str, str]]:
    groups: dict[str, list[str]] = {}
    for name in entities:
        groups.setdefault(fold_entity(name), []).append(name)
    return [
        {"canonical": canonical_entity(names[0]), "aliases": names}
        for names in groups.values()
        if len(set(names)) > 1
    ]


def repair_entities(entities: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    collisions = naming_collisions(entities)
    repaired = []
    seen = set()
    for name in entities:
        canonical = next(
            (c["canonical"] for c in collisions if fold_entity(name) == fold_entity(c["canonical"])),
            canonical_entity(name),
        )
        if fold_entity(canonical) not in seen:
            seen.add(fold_entity(canonical))
            repaired.append(canonical)
    return repaired, collisions
