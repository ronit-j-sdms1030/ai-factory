"""The pipeline as an actually-executing LangGraph, all four agents on it.

The failure these guard against is subtle and this project already had it
once: a graph that is declared, drawn by Studio, and never on the execution
path. These tests assert the graph *runs*, that a gate is a real resumable
pause, and that the intake loop bills one model call per message rather than
two.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app import pipeline_graph


ARTIFACT = {"_id": "artifact-1", "content": {"summary": "S"}, "chatHistory": [], "title": "T"}


@pytest.fixture
def graph(monkeypatch):
    """The real graph and gates, with the agents stubbed.

    The topology, the interrupts and the resume logic are the code under
    test; the model calls are not.
    """
    calls: list[str] = []
    turns = {"remaining": 2}

    def fake_chat_turn(history, label):
        calls.append("intake")
        if turns["remaining"] > 0:
            turns["remaining"] -= 1
            return {"type": "reply", "text": f"question {len(calls)}"}
        return {"type": "ready"}

    class _Req:
        title = "Structured"

        def model_dump(self, **_):
            return {"title": "Structured", "summary": "S"}

    def fake_finalize(history):
        calls.append("finalize")
        return _Req()

    monkeypatch.setattr(pipeline_graph, "run_chat_turn", fake_chat_turn)
    monkeypatch.setattr(pipeline_graph, "finalize_requirement", fake_finalize)
    monkeypatch.setattr(pipeline_graph, "_history_of", lambda aid: ({}, []))
    monkeypatch.setattr(
        pipeline_graph, "_brd_node",
        lambda state: (calls.append("brd"), {"brd": {"objective": "O"}})[1],
    )
    monkeypatch.setattr(
        pipeline_graph, "_ui_node",
        lambda state: (calls.append("ui"), {"ui": {"screens": [{"name": "A"}]}})[1],
    )
    monkeypatch.setattr(
        pipeline_graph, "_workitems_node",
        lambda state: (calls.append("workitems"), {"work_items": {"packages": []}})[1],
    )

    compiled = pipeline_graph.build_pipeline_graph(checkpointer=InMemorySaver())
    monkeypatch.setattr(pipeline_graph, "graph", lambda: compiled)
    return compiled, calls


class TestTheIntakeLoop:
    def test_one_message_costs_exactly_one_model_call(self, graph):
        """`interrupt()` is the first statement in the node for this reason. A
        model call placed above it would run again on resume — billing twice
        per message and appending the reply twice."""
        _, calls = graph
        pipeline_graph.intake_turn(dict(ARTIFACT), "MD")
        assert calls == ["intake"]

    def test_it_asks_again_until_the_agent_says_ready(self, graph):
        _, calls = graph
        artifact = dict(ARTIFACT)
        first = pipeline_graph.intake_turn(artifact, "MD")
        second = pipeline_graph.intake_turn(artifact, "MD")

        assert first["turn"]["type"] == "reply"
        assert second["turn"]["type"] == "reply"
        assert calls == ["intake", "intake"]

    def test_the_conversation_finishes_into_a_requirement(self, graph):
        _, calls = graph
        artifact = dict(ARTIFACT)
        for _ in range(3):
            result = pipeline_graph.intake_turn(artifact, "MD")

        assert result["turn"]["type"] == "ready"
        assert result["requirement"]["title"] == "Structured"
        assert calls == ["intake", "intake", "intake", "finalize"]

    def test_a_message_after_finalisation_does_not_blow_through_the_gate(self, graph):
        """Observed for real: a fourth message after the conversation ended
        resumed `gate_requirement` and started generating the BRD, with no
        approval recorded. The route's stage check would normally stop the
        call arriving, but a gate that relies on its caller's guard is the
        transitive kind this project has been removing."""
        _, calls = graph
        artifact = dict(ARTIFACT)
        for _ in range(3):
            pipeline_graph.intake_turn(artifact, "MD")
        calls.clear()

        result = pipeline_graph.intake_turn(artifact, "MD")

        assert result["turn"]["type"] == "ready"
        assert result["requirement"]["title"] == "Structured"
        assert calls == []          # nothing ran — no BRD, no extra intake turn

    def test_it_stops_at_the_requirement_gate(self, graph):
        """Finalizing must not run straight on into the BRD — gate 0 is a real
        approval and generating past it would make it decorative."""
        compiled, calls = graph
        artifact = dict(ARTIFACT)
        for _ in range(3):
            pipeline_graph.intake_turn(artifact, "MD")

        state = compiled.get_state(pipeline_graph._config(artifact["_id"]))
        assert state.next == ("gate_requirement",)
        assert "brd" not in calls


class TestGenerationResumes:
    def _through_intake(self, artifact):
        for _ in range(3):
            pipeline_graph.intake_turn(artifact, "MD")

    def test_the_brd_resumes_the_thread_intake_left(self, graph):
        """The whole point of one thread per requirement: the BRD continues
        the conversation's graph rather than starting a second one."""
        _, calls = graph
        artifact = dict(ARTIFACT)
        self._through_intake(artifact)
        calls.clear()

        out = pipeline_graph.advance(artifact, "brd")
        assert out["brd"] == {"objective": "O"}
        assert calls == ["brd"]

    def test_each_gate_pauses_before_the_next_agent(self, graph):
        compiled, calls = graph
        artifact = dict(ARTIFACT)
        self._through_intake(artifact)
        calls.clear()

        pipeline_graph.advance(artifact, "brd")
        assert compiled.get_state(pipeline_graph._config(artifact["_id"])).next == ("gate_brd",)

        pipeline_graph.advance(artifact, "ui")
        assert compiled.get_state(pipeline_graph._config(artifact["_id"])).next == ("gate_ui",)

        out = pipeline_graph.advance(artifact, "workitems")
        assert out["work_items"] == {"packages": []}
        assert calls == ["brd", "ui", "workitems"]

    def test_state_carries_between_the_agents(self, graph):
        """The BRD produced by one node reaches the next without the route
        threading it through — that carrying is what the graph is for."""
        compiled, _ = graph
        artifact = dict(ARTIFACT)
        self._through_intake(artifact)
        pipeline_graph.advance(artifact, "brd")
        pipeline_graph.advance(artifact, "ui")

        values = compiled.get_state(pipeline_graph._config(artifact["_id"])).values
        assert values["requirement"]["title"] == "Structured"
        assert values["brd"] == {"objective": "O"}
        assert values["ui"]["screens"] == [{"name": "A"}]


class TestWhenThereIsNoThread:
    def test_a_legacy_artifact_still_generates(self, graph):
        """An artifact created before intake joined the graph has no thread to
        resume. Mongo is authoritative, so the phase runs directly rather than
        the requirement being stuck."""
        _, calls = graph
        artifact = {**ARTIFACT, "_id": "legacy", "detailedReport": {"objective": "O"}}
        out = pipeline_graph.advance(artifact, "ui")
        assert out["ui"]["screens"] == [{"name": "A"}]
        assert calls == ["ui"]

    def test_starting_a_thread_at_brd_never_opens_a_conversation(self, graph):
        """The graph begins at intake, so a fresh-start branch here would run
        the intake agent when the caller asked for a BRD."""
        _, calls = graph
        artifact = {**ARTIFACT, "_id": "legacy-2"}
        pipeline_graph.advance(artifact, "brd")
        assert calls == ["brd"]
        assert "intake" not in calls


class TestWithoutACheckpointer:
    def test_generation_still_works(self, monkeypatch):
        """MongoDBSaver builds indexes on construction, so it needs privileges
        the rest of the service does not. If it cannot be built, the pipeline
        must degrade to unresumable — not stop."""
        calls: list[str] = []
        monkeypatch.setattr(
            pipeline_graph, "_brd_node",
            lambda state: (calls.append("brd"), {"brd": {"objective": "O"}})[1],
        )
        uncheckpointed = pipeline_graph.build_pipeline_graph(checkpointer=None)
        monkeypatch.setattr(pipeline_graph, "graph", lambda: uncheckpointed)

        out = pipeline_graph.advance(dict(ARTIFACT), "brd")
        assert out["brd"] == {"objective": "O"}
        assert calls == ["brd"]

    def test_intake_still_works(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            pipeline_graph, "run_chat_turn",
            lambda h, l: (calls.append("intake"), {"type": "reply", "text": "q"})[1],
        )
        monkeypatch.setattr(pipeline_graph, "_history_of", lambda aid: ({}, []))
        uncheckpointed = pipeline_graph.build_pipeline_graph(checkpointer=None)
        monkeypatch.setattr(pipeline_graph, "graph", lambda: uncheckpointed)

        result = pipeline_graph.intake_turn(dict(ARTIFACT), "MD")
        assert result["turn"]["text"] == "q"
        assert calls == ["intake"]


class TestRegeneration:
    def test_regenerating_does_not_resume_past_the_phase(self, graph):
        """A reviewer asking for a fresh BRD wants another BRD, not the next
        artefact. Resuming the thread would hand back screens instead."""
        _, calls = graph
        artifact = dict(ARTIFACT)
        for _ in range(3):
            pipeline_graph.intake_turn(artifact, "MD")
        pipeline_graph.advance(artifact, "brd")
        calls.clear()

        out = pipeline_graph.regenerate(artifact, "brd")
        assert out["brd"] == {"objective": "O"}
        assert calls == ["brd"]


class TestPosition:
    def test_it_reads_the_next_phase_from_the_artifact(self):
        assert pipeline_graph._position({}) == "brd"
        assert pipeline_graph._position({"detailedReport": {"o": 1}}) == "ui"
        assert pipeline_graph._position({"detailedReport": {"o": 1}, "ui": {"s": 1}}) == "workitems"
        assert pipeline_graph._position(
            {"detailedReport": {"o": 1}, "ui": {"s": 1}, "teamReports": [{}]}
        ) is None
