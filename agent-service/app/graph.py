"""Graph assembly — agents as nodes, approval gates as interrupts.

Replaces the JavaScript ``stateMachine.service.js`` transition table plus the
stage strings the routes interpret. A gate is an ``interrupt``: the graph
checkpoints, stops, and resumes when a decision arrives — possibly days later,
possibly on a different process.

Sequencing lives here, not in the reviewer. GitHub reviews (and human
approvals generally) are unordered, but approval chains are strictly ordered,
so the graph decides which tier is being asked and ignores an approval that
arrives from a later gate early.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agents.brd import brd_agent
from .agents.decomposition import decomposition_agent
from .agents.intake import intake_agent
from .agents.ui import ui_agent
from .config import resolve_approval_chain
from .state import PipelineState

GateDecision = Literal["approve", "reject", "revise"]


def _current_tiers(state: PipelineState) -> list[str]:
    chain = state.get("approval_chain") or []
    index = state.get("current_approval_index", 0)
    return list(chain[index]["approver_tiers"]) if index < len(chain) else []


def _gate(state: PipelineState, *, name: str, artefact_key: str, advance_chain: bool) -> Any:
    """Pause for a human decision on one artefact.

    ``advance_chain`` distinguishes gates that consume a step of the
    originator's approval chain (the BRD gate) from those that do not (UI and
    work-item gates, which have fixed approver roles).
    """
    decision = interrupt(
        {
            "kind": "gate",
            "gate": name,
            "approver_tiers": _current_tiers(state) if advance_chain else None,
            "artefact": state.get(artefact_key),
        }
    )

    action: GateDecision = decision.get("action", "approve")

    if action == "reject":
        return Command(goto=END, update={"rejected": {"gate": name, **decision}})

    if action == "revise":
        # Back to the agent that produced the artefact.
        producer = {"GATE_1_BRD": "brd_agent", "GATE_2_UI": "ui_agent", "GATE_3_WORKITEMS": "decomposition_agent"}
        return Command(goto=producer[name], update={"approvals": {name: decision}})

    update: dict[str, Any] = {"approvals": {name: decision}}

    if advance_chain:
        chain = [dict(step) for step in (state.get("approval_chain") or [])]
        index = state.get("current_approval_index", 0)
        if index < len(chain):
            chain[index] = {
                **chain[index],
                "approved_by": [*chain[index].get("approved_by", []), decision],
            }
            update["approval_chain"] = chain
            update["current_approval_index"] = index + 1

    return Command(goto=_next_after(name), update=update)


def _next_after(name: str) -> str:
    return {
        "GATE_1_BRD": "ui_agent",
        "GATE_2_UI": "decomposition_agent",
        "GATE_3_WORKITEMS": END,
    }[name]


def brd_gate(state: PipelineState) -> Any:
    return _gate(state, name="GATE_1_BRD", artefact_key="brd", advance_chain=True)


def ui_gate(state: PipelineState) -> Any:
    return _gate(state, name="GATE_2_UI", artefact_key="ui", advance_chain=False)


def workitems_gate(state: PipelineState) -> Any:
    return _gate(state, name="GATE_3_WORKITEMS", artefact_key="work_items", advance_chain=False)


def seed_chain(state: PipelineState) -> dict:
    """Build the approval chain from the originator's tier, once, at entry."""
    tier = (state.get("originator") or {}).get("tier_id")
    return {
        "approval_chain": [
            {"approver_tiers": step.approver_tiers, "mode": step.mode, "approved_by": []}
            for step in resolve_approval_chain(tier)
        ],
        "current_approval_index": 0,
    }


def build_graph(checkpointer=None):
    """Compile the pipeline. A checkpointer is required for interrupts to work."""
    builder = StateGraph(PipelineState)

    builder.add_node("seed_chain", seed_chain)
    builder.add_node("intake_agent", intake_agent)
    builder.add_node("brd_agent", brd_agent)
    builder.add_node("brd_gate", brd_gate)
    builder.add_node("ui_agent", ui_agent)
    builder.add_node("ui_gate", ui_gate)
    builder.add_node("decomposition_agent", decomposition_agent)
    builder.add_node("workitems_gate", workitems_gate)

    builder.add_edge(START, "seed_chain")
    builder.add_edge("seed_chain", "intake_agent")
    builder.add_edge("intake_agent", "brd_agent")
    builder.add_edge("brd_agent", "brd_gate")
    builder.add_edge("ui_agent", "ui_gate")
    builder.add_edge("decomposition_agent", "workitems_gate")
    # Gates route onward with Command(goto=...), so they need no static edges.

    return builder.compile(checkpointer=checkpointer)
