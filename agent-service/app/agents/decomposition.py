"""Decomposition agent — approved BRD to dependency-ordered work items.

Two-level by design. The source documents specify "scoped, dependency-ordered
work items" and never mention departments; the existing platform splits into
five fixed departments with team-lead ownership. This emits both: a real
dependency graph across work items, each assigned an owning department. The
graph satisfies the specification, departmental governance survives intact.

Departments generate code independently and never see each other's packages,
so the data model carried here is the only thing keeping the finished modules
compatible. Before it was included, one department created a table
``Return_Items`` while another wrote a foreign key against ``ReturnItems`` —
a combination that cannot build.
"""

from __future__ import annotations

import json
import logging

from langsmith import traceable

from .. import config, invariants, llm, prompts
from ..schemas import Decomposition
from ..state import PipelineState

log = logging.getLogger(__name__)


def _split(brd: dict, model: str, defects: str = "") -> Decomposition:
    """One split. ``defects`` turns it into a second attempt at a broken one."""
    return llm.call_structured(
        model=model,
        schema=Decomposition,
        max_tokens=8000,
        retries=1,
        timeout=120.0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are the delivery lead inside Stark Digital's AI Software Factory. Decompose "
                    "this approved BRD into work items and department packages.\n\n"
                    f"Departments are exactly: {', '.join(config.TEAM_DEPARTMENTS)}. Assign work to "
                    "whichever genuinely have something to do; skip the rest. Never invent a "
                    "department.\n\n"
                    "WORK ITEMS must carry real dependency edges. If item B needs an API or table that "
                    "item A creates, B depends_on A. The graph must be acyclic and every depends_on "
                    "must reference an id that exists.\n\n"
                    + prompts.text("decomposition.contract")
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Objective:\n{brd['objective']}\n\n"
                    f"Architecture:\n{brd['architecture']}\n\n"
                    f"Tech stack:\n{json.dumps(brd['techStack'], indent=2)}\n\n"
                    f"Data model:\n{json.dumps(brd['dataModel'], indent=2)}\n\n"
                    f"Timeline:\n{json.dumps(brd['timeline'], indent=2)}"
                ),
            },
            *(
                [
                    {
                        "role": "user",
                        "content": (
                            "Your previous split had these defects. Produce the complete "
                            "decomposition again with every one of them fixed, keeping everything "
                            "that was already correct:\n\n" + defects
                        ),
                    }
                ]
                if defects
                else []
            ),
        ],
    )


def _integrity(decomposition: Decomposition) -> dict:
    """Everything known to be wrong with a split. Empty lists mean healthy."""
    return {
        **invariants.entity_ownership_report(decomposition),
        "cycles": invariants.dependency_cycles(decomposition),
        "dangling": invariants.dangling_dependencies(decomposition),
    }


def _describe(integrity: dict) -> str:
    """The defects, phrased for the model that produced them."""
    lines = []
    if integrity["dangling"]:
        lines.append(
            "These depends_on entries reference work item ids that do not exist. Either point them "
            "at a real id or remove them: " + ", ".join(integrity["dangling"])
        )
    if integrity["cycles"]:
        lines.append(
            "These dependency cycles mean no valid execution order exists: "
            + "; ".join(" -> ".join(c) for c in integrity["cycles"])
        )
    if integrity["inconsistent_spelling"]:
        lines.append(
            "The same entity is spelled differently across packages, which produces foreign keys "
            "that do not resolve. Use one spelling, copied verbatim from the BRD data model: "
            + ", ".join(integrity["inconsistent_spelling"])
        )
    if integrity["unowned"]:
        lines.append(
            "No department owns these entities, so nobody generates their schema: "
            + ", ".join(integrity["unowned"])
        )
    if integrity["multiply_owned"]:
        lines.append(
            "More than one department claims to own these entities: "
            + ", ".join(integrity["multiply_owned"])
        )
    return "\n".join(f"- {line}" for line in lines)


@traceable(name="Decomposition Agent")
def decomposition_agent(state: PipelineState) -> dict:
    brd = state["brd"]
    model = model_for(state, "decomposition")

    decomposition = _split(brd, model)
    # Prompt compliance is not sufficient on its own — a real split returned
    # four entities owned by nobody. Repair deterministically.
    decomposition = invariants.normalize_entity_ownership(decomposition)
    integrity = _integrity(decomposition)

    # One retry with the defects handed back, the same bargain the UI agent
    # makes with a compile error: a model shown its own broken output fixes it
    # far more often than one asked to try again from the brief. A real split
    # shipped eleven dangling edges pointing at a work item that never
    # existed, detected and logged and published regardless.
    if _describe(integrity):
        log.warning("split has integrity defects, retrying once: %s", integrity)
        try:
            retried = _split(brd, model, defects=_describe(integrity))
            retried = invariants.normalize_entity_ownership(retried)
            retried_integrity = _integrity(retried)
            if not _describe(retried_integrity):
                decomposition, integrity = retried, retried_integrity
            elif len(_describe(retried_integrity)) < len(_describe(integrity)):
                decomposition, integrity = retried, retried_integrity
        except RuntimeError as exc:
            # A failed retry keeps the first split, which is still a usable
            # set of packages. Losing them to a second bad response would be
            # a worse outcome than a graph with an edge missing.
            log.warning("retry of the split failed, keeping the first: %s", exc)

    # Whatever survived the retry is repaired rather than shipped: a graph
    # nobody can execute is worth less than one missing an edge somebody can
    # add back. Every dropped edge is recorded and reaches the pull request.
    repairs = invariants.repair_dependencies(decomposition)
    integrity = _integrity(decomposition)

    if _describe(integrity):
        log.error("integrity defects survived repair: %s", integrity)
    if repairs["dropped_dangling"] or repairs["broke_cycles"]:
        log.warning("repaired the work-item graph: %s", repairs)

    return {
        "work_items": {
            **decomposition.model_dump(by_alias=True),
            "integrity": {**integrity, "repairs": repairs},
            "provenance": {
                "model": model,
                "promptVersions": prompts.versions("decomposition"),
            },
        }
    }
