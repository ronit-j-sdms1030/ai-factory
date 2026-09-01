"""Graph mechanics — interrupt, checkpoint, resume, and gate routing.

No network. Agent nodes are replaced with stubs so these tests exercise the
orchestration itself: that a gate genuinely pauses, that state survives the
pause, and that approve/reject/revise route where they should.
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.config import MAX_CLARIFYING_QUESTIONS, resolve_approval_chain
from app.graph import brd_gate, seed_chain, ui_gate, workitems_gate
from app.state import PipelineState


def _stub_graph():
    """The real gates and chain seeding, with agents stubbed to constants."""
    builder = StateGraph(PipelineState)
    builder.add_node("seed_chain", seed_chain)
    builder.add_node("intake_agent", lambda s: {"requirement": {"title": "T"}, "title": "T"})
    builder.add_node("brd_agent", lambda s: {"brd": {"objective": "o"}})
    builder.add_node("brd_gate", brd_gate)
    builder.add_node("ui_agent", lambda s: {"ui": {"screens": []}})
    builder.add_node("ui_gate", ui_gate)
    builder.add_node("decomposition_agent", lambda s: {"work_items": {"packages": []}})
    builder.add_node("workitems_gate", workitems_gate)
    builder.add_edge(START, "seed_chain")
    builder.add_edge("seed_chain", "intake_agent")
    builder.add_edge("intake_agent", "brd_agent")
    builder.add_edge("brd_agent", "brd_gate")
    builder.add_edge("ui_agent", "ui_gate")
    builder.add_edge("decomposition_agent", "workitems_gate")
    return builder.compile(checkpointer=InMemorySaver())


def _cfg(thread="t1"):
    return {"configurable": {"thread_id": thread}}


def _run(graph, cfg):
    return graph.invoke({"artifact_id": "a1", "originator": {"user_id": "u1", "tier_id": "md"}}, cfg)


class TestGateInterrupts:
    def test_graph_pauses_at_the_first_gate(self):
        graph, cfg = _stub_graph(), _cfg()
        result = _run(graph, cfg)
        assert "__interrupt__" in result
        payload = result["__interrupt__"][0].value
        assert payload["gate"] == "GATE_1_BRD"
        assert payload["artefact"] == {"objective": "o"}

    def test_gate_asks_the_tier_the_chain_specifies(self):
        """MD-originated: gate 0 is MD, so that is who the pause names."""
        graph, cfg = _stub_graph(), _cfg("t-tier")
        result = _run(graph, cfg)
        assert result["__interrupt__"][0].value["approver_tiers"] == ["md"]

    def test_state_survives_the_pause(self):
        graph, cfg = _stub_graph(), _cfg("t-state")
        _run(graph, cfg)
        snapshot = graph.get_state(cfg)
        assert snapshot.values["requirement"] == {"title": "T"}
        assert snapshot.values["brd"] == {"objective": "o"}

    def test_approval_resumes_and_reaches_the_next_gate(self):
        graph, cfg = _stub_graph(), _cfg("t-resume")
        _run(graph, cfg)
        result = graph.invoke(Command(resume={"action": "approve", "user_id": "u1"}), cfg)
        assert result["__interrupt__"][0].value["gate"] == "GATE_2_UI"

    def test_full_run_reaches_the_end_through_three_gates(self):
        graph, cfg = _stub_graph(), _cfg("t-full")
        _run(graph, cfg)
        seen = []
        for _ in range(3):
            state = graph.get_state(cfg)
            if not state.tasks or not state.tasks[0].interrupts:
                break
            seen.append(state.tasks[0].interrupts[0].value["gate"])
            graph.invoke(Command(resume={"action": "approve", "user_id": "u1"}), cfg)
        assert seen == ["GATE_1_BRD", "GATE_2_UI", "GATE_3_WORKITEMS"]
        assert graph.get_state(cfg).next == ()


class TestGateRouting:
    def test_reject_ends_the_run_and_records_why(self):
        graph, cfg = _stub_graph(), _cfg("t-reject")
        _run(graph, cfg)
        result = graph.invoke(Command(resume={"action": "reject", "reason": "out of scope"}), cfg)
        assert result["rejected"]["gate"] == "GATE_1_BRD"
        assert result["rejected"]["reason"] == "out of scope"
        assert graph.get_state(cfg).next == ()

    def test_revise_returns_to_the_producing_agent(self):
        graph, cfg = _stub_graph(), _cfg("t-revise")
        _run(graph, cfg)
        result = graph.invoke(Command(resume={"action": "revise", "note": "thin"}), cfg)
        # Back through brd_agent, so it pauses at the same gate again.
        assert result["__interrupt__"][0].value["gate"] == "GATE_1_BRD"

    def test_approval_advances_the_chain_index(self):
        graph, cfg = _stub_graph(), _cfg("t-chain")
        _run(graph, cfg)
        assert graph.get_state(cfg).values["current_approval_index"] == 0
        graph.invoke(Command(resume={"action": "approve", "tier_id": "md"}), cfg)
        assert graph.get_state(cfg).values["current_approval_index"] == 1

    def test_non_chain_gates_do_not_consume_a_chain_step(self):
        """UI and work-item gates have fixed approvers, not chain steps."""
        graph, cfg = _stub_graph(), _cfg("t-nochain")
        _run(graph, cfg)
        graph.invoke(Command(resume={"action": "approve"}), cfg)   # GATE_1 -> index 1
        graph.invoke(Command(resume={"action": "approve"}), cfg)   # GATE_2, no advance
        assert graph.get_state(cfg).values["current_approval_index"] == 1


class TestApprovalChain:
    def test_md_self_approves_then_vp(self):
        assert [s.approver_tiers for s in resolve_approval_chain("md")] == [["md"], ["vp"]]

    def test_vp_escalates_upward_with_no_vp_gate(self):
        """A VP never approves their own requirement."""
        chain = resolve_approval_chain("vp")
        assert [s.approver_tiers for s in chain] == [["md", "ceo"]]
        assert not any("vp" in s.approver_tiers for s in chain)

    def test_client_is_the_default_for_no_tier(self):
        assert [s.approver_tiers for s in resolve_approval_chain(None)] == [["md", "ceo"], ["vp"]]

    def test_unknown_tier_is_rejected(self):
        with pytest.raises(ValueError):
            resolve_approval_chain("intern")

    def test_question_ceiling_leaves_room_for_the_checklist(self):
        assert MAX_CLARIFYING_QUESTIONS >= 5
