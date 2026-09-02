"""Deterministic repairs and checks applied to model output.

Every function here exists because a prompt instruction alone proved
insufficient in the JavaScript implementation. They are pure and fully
testable — no model, no network — which is the point: the guarantees that
matter should not themselves depend on a model complying.
"""

from __future__ import annotations

from .config import FRONTEND_OWNING_DEPARTMENT
from .schemas import Decomposition


def normalize_entity_ownership(decomposition: Decomposition) -> Decomposition:
    """Guarantee exactly one owning department per entity.

    Both failure modes break a combined build in the same way and both were
    observed in practice:

    * **Owned by nobody** — every department marks the entity read-only, each
      assuming another creates the table, so no schema is generated for it at
      all. A real split returned four entities in this state.
    * **Owned by two** — two departments each generate an authoritative
      schema for the same table, which then conflict.

    Unowned entities default to the frontend-owning department when it touches
    the entity, since it owns the core application and database; otherwise the
    first department listing it, which at least guarantees the schema exists.
    """
    rows: dict[str, list[tuple[str, object]]] = {}
    for package in decomposition.packages:
        for entry in package.data_model:
            rows.setdefault(entry.entity, []).append((package.team, entry))

    for entity, entries in rows.items():
        owners = [(team, entry) for team, entry in entries if entry.owned_by_this_department]  # type: ignore[attr-defined]

        if len(owners) == 1:
            continue

        if len(owners) > 1:
            for _team, entry in owners[1:]:
                entry.owned_by_this_department = False  # type: ignore[attr-defined]
            continue

        preferred = next((e for t, e in entries if t == FRONTEND_OWNING_DEPARTMENT), None)
        chosen = preferred if preferred is not None else entries[0][1]
        chosen.owned_by_this_department = True  # type: ignore[attr-defined]

    return decomposition


def entity_ownership_report(decomposition: Decomposition) -> dict[str, list[str]]:
    """Integrity report over a decomposition. Empty lists mean healthy."""
    owners: dict[str, list[str]] = {}
    mentioned: set[str] = set()
    spellings: dict[str, set[str]] = {}

    for package in decomposition.packages:
        for entry in package.data_model:
            mentioned.add(entry.entity)
            spellings.setdefault(entry.entity.lower().replace("_", "").replace(" ", ""), set()).add(entry.entity)
            if entry.owned_by_this_department:
                owners.setdefault(entry.entity, []).append(package.team)

    return {
        "unowned": sorted(e for e in mentioned if e not in owners),
        "multiply_owned": sorted(e for e, teams in owners.items() if len(teams) > 1),
        "inconsistent_spelling": sorted(
            " vs ".join(sorted(variants)) for variants in spellings.values() if len(variants) > 1
        ),
    }


def dependency_cycles(decomposition: Decomposition) -> list[list[str]]:
    """Return any dependency cycles among work items.

    New relative to the JavaScript implementation, which had no ordering
    between department packages at all. A cycle means no valid execution
    order exists, so it must block the gate rather than surface at build time.
    """
    graph = {item.id: list(item.depends_on) for item in decomposition.work_items}
    cycles: list[list[str]] = []
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(graph, WHITE)

    def visit(node: str, path: list[str]) -> None:
        colour[node] = GREY
        for dep in graph.get(node, []):
            if dep not in graph:
                continue  # dangling dependency — reported separately
            if colour[dep] == GREY:
                cycles.append(path[path.index(dep):] + [dep])
            elif colour[dep] == WHITE:
                visit(dep, path + [dep])
        colour[node] = BLACK

    for node in graph:
        if colour[node] == WHITE:
            visit(node, [node])
    return cycles


def dangling_dependencies(decomposition: Decomposition) -> list[str]:
    """Work-item dependencies that reference an id that does not exist."""
    ids = {item.id for item in decomposition.work_items}
    return sorted(
        f"{item.id} -> {dep}"
        for item in decomposition.work_items
        for dep in item.depends_on
        if dep not in ids
    )

def repair_dependencies(decomposition: Decomposition) -> dict[str, list[str]]:
    """Drop the edges that make a graph unusable, recording every one.

    The last resort, after the model has been given its own defects back and
    still produced them. Follows ``normalize_entity_ownership``: a defect the
    prompt cannot reliably avoid is repaired deterministically rather than
    shipped, because a graph nobody can execute is worth less than a graph
    missing an edge somebody can add back.

    Nothing is dropped silently. Both lists land on the artefact and in the
    pull request body, so the edge that disappeared is visible to the reviewer
    approving the split.
    """
    ids = {item.id for item in decomposition.work_items}

    dropped_dangling: list[str] = []
    for item in decomposition.work_items:
        kept = []
        for dep in item.depends_on:
            if dep in ids:
                kept.append(dep)
            else:
                dropped_dangling.append(f"{item.id} -> {dep}")
        item.depends_on = kept

    broke_cycles: list[str] = []
    # One edge per pass, re-detecting each time: breaking an edge can resolve
    # several overlapping cycles at once, and dropping one per reported cycle
    # would remove more of the ordering than necessary.
    while True:
        cycles = dependency_cycles(decomposition)
        if not cycles:
            break
        cycle = cycles[0]
        tail, head = cycle[-2], cycle[-1]
        item = next((i for i in decomposition.work_items if i.id == tail), None)
        if item is None or head not in item.depends_on:
            # Cannot locate the closing edge — stop rather than spin.
            break
        item.depends_on = [d for d in item.depends_on if d != head]
        broke_cycles.append(f"{tail} -> {head}")

    return {
        "dropped_dangling": sorted(dropped_dangling),
        "broke_cycles": sorted(broke_cycles),
    }
