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

from .. import config, invariants, llm
from ..schemas import Decomposition
from ..state import PipelineState

log = logging.getLogger(__name__)


@traceable(name="Decomposition Agent")
def decomposition_agent(state: PipelineState) -> dict:
    brd = state["brd"]

    decomposition = llm.call_structured(
        model=config.REPORT_MODEL,
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
                    "THESE PACKAGES MUST RECOMBINE INTO ONE WORKING PRODUCT. Each department generates "
                    "its code from its own package alone, never seeing another's, so what you write "
                    "here is the only thing keeping the modules compatible:\n"
                    "- Shared entities must be spelled identically in every package, character for "
                    "character, copied verbatim from the BRD data model. 'Return_Items' in one package "
                    "and 'ReturnItems' in another produces foreign keys that do not resolve — a broken "
                    "build, not a cosmetic mismatch.\n"
                    "- Include entities a department only reads, marked owned_by_this_department false, "
                    "or it will invent its own incompatible version of the same thing.\n"
                    "- Exactly one department owns each entity — never zero. An entity read-only in "
                    "every package means nobody generates its schema and the table does not exist.\n\n"
                    "Do not copy the BRD's diagrams or full security design into every package."
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
        ],
    )

    # Prompt compliance is not sufficient on its own — a real split returned
    # four entities owned by nobody. Repair deterministically.
    decomposition = invariants.normalize_entity_ownership(decomposition)

    report = invariants.entity_ownership_report(decomposition)
    cycles = invariants.dependency_cycles(decomposition)
    dangling = invariants.dangling_dependencies(decomposition)

    if report["unowned"] or report["multiply_owned"]:
        log.error("ownership normalisation left defects: %s", report)
    if cycles:
        log.error("dependency cycles in work items: %s", cycles)
    if dangling:
        log.error("dangling work-item dependencies: %s", dangling)

    return {
        "work_items": {
            **decomposition.model_dump(by_alias=True),
            "integrity": {**report, "cycles": cycles, "dangling": dangling},
        }
    }
