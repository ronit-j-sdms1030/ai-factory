"""The response shape the intake UI actually reads.

``artifacts.py`` states that response shapes match the JavaScript so the
existing frontend works against either service. Intake had drifted: the port
returned ``message``/``type`` where the frontend reads ``reply``/
``reviewReady``. Nothing failed loudly — assigning an undefined value to
``textContent`` renders an *empty* bubble, so every question the agent asked
came back blank and the session could never finish.

A renamed key is invisible at runtime, so it is pinned here instead.
"""

from __future__ import annotations

import mongomock
import pytest

from app import db, pipeline_graph
from app.routes import artifacts as routes

MD = {"id": "u1", "tierId": "md", "isClient": False, "department": None}


class _Published:
    """Stands in for PublishResult."""

    pull_request_url = "https://github.com/org/repo/pull/13"
    pull_request_number = 13
    branch = "req/abc/requirement"
    commit = None
    error = None


@pytest.fixture
def collection(monkeypatch):
    fake = mongomock.MongoClient().db.artifacts
    monkeypatch.setattr(db, "artifacts", lambda: fake)
    monkeypatch.setattr(routes, "publish", lambda **_: _Published())
    # The compiled graph is cached module-wide and holds a checkpointer bound
    # to whatever database existed when it was built. Left alone, the first
    # test to build it would hand its Mongo to every test after.
    monkeypatch.setattr(pipeline_graph, "_graph", None)
    return fake


class TestChatStart:
    def test_the_opening_line_is_returned_as_reply(self, collection):
        body = routes.chat_start(actor=MD)
        assert body["reply"]                       # the frontend reads body.reply
        assert "artifactId" in body

    def test_the_opener_is_also_recorded_in_the_transcript(self, collection):
        body = routes.chat_start(actor=MD)
        stored = collection.find_one({})
        assert stored["chatHistory"][0]["content"] == body["reply"]


class TestChatMessage:
    def _start(self, collection):
        return routes.chat_start(actor=MD)["artifactId"]

    def test_a_question_comes_back_under_reply(self, collection, monkeypatch):
        monkeypatch.setattr(pipeline_graph, "run_chat_turn", lambda *a, **k: {"type": "reply", "text": "Who uses it?"})
        artifact_id = self._start(collection)

        body = routes.chat_message(artifact_id, message="Build a warehouse tool", actor=MD)

        assert body["reply"] == "Who uses it?"
        assert body["done"] is False

    def test_finishing_sets_reviewReady(self, collection, monkeypatch):
        """The frontend gates its review-and-send block on this exact key."""
        monkeypatch.setattr(pipeline_graph, "run_chat_turn", lambda *a, **k: {"type": "ready", "text": ""})
        monkeypatch.setattr(pipeline_graph, "finalize_requirement", lambda *a, **k: _Requirement())
        artifact_id = self._start(collection)

        body = routes.chat_message(artifact_id, message="that's everything", actor=MD)

        assert body["reviewReady"] is True
        assert body["artifact"]["title"] == "Warehouse Tool"

    def test_the_closing_summary_survives_reopening(self, collection, monkeypatch):
        monkeypatch.setattr(pipeline_graph, "run_chat_turn", lambda *a, **k: {"type": "ready", "text": ""})
        monkeypatch.setattr(pipeline_graph, "finalize_requirement", lambda *a, **k: _Requirement())
        artifact_id = self._start(collection)

        routes.chat_message(artifact_id, message="that's everything", actor=MD)

        last = collection.find_one({})["chatHistory"][-1]
        assert last["role"] == "assistant"
        assert "Review it below" in last["content"]

    def test_intake_stays_in_clarifying_until_explicitly_submitted(self, collection, monkeypatch):
        """Finishing the conversation is not the same as sending the requirement."""
        monkeypatch.setattr(pipeline_graph, "run_chat_turn", lambda *a, **k: {"type": "ready", "text": ""})
        monkeypatch.setattr(pipeline_graph, "finalize_requirement", lambda *a, **k: _Requirement())
        artifact_id = self._start(collection)

        routes.chat_message(artifact_id, message="done", actor=MD)

        assert collection.find_one({})["currentStage"] == "clarifying"


class _Requirement:
    title = "Warehouse Tool"

    def model_dump(self, **_):
        return {"title": self.title, "summary": "A warehouse tool."}


class TestRequirementIsLinkedToGit:
    """The pull request opened at finalisation must survive on the artifact.

    Publishing happens after the artifact is saved, so the branch and pull
    request it records need a second save. Without one the requirement was
    committed, pushed and opened as a PR while the artifact showed no Git
    links — the record existed everywhere except where anyone looks.
    """

    def test_the_pull_request_is_persisted(self, collection, monkeypatch):
        monkeypatch.setattr(pipeline_graph, "run_chat_turn", lambda *a, **k: {"type": "ready", "text": ""})
        monkeypatch.setattr(pipeline_graph, "finalize_requirement", lambda *a, **k: _Requirement())
        artifact_id = routes.chat_start(actor=MD)["artifactId"]

        routes.chat_message(artifact_id, message="done", actor=MD)

        stored = collection.find_one({})
        assert stored["pullRequests"]["requirement"].endswith("/pull/13")
        assert stored["gitBranches"]["requirement"] == "req/abc/requirement"
