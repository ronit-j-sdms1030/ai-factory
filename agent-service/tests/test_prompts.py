"""Versioned prompt fragments.

SoW 4.0 commits that prompt templates are "stored as versioned repository
artefacts with change history and approver identity"; SoW 7.0 lists prompt
version among the audit fields every run must record. Before this, every
prompt was a string literal inside an agent function — in Git, but only as
code, changed by whoever edits Python and reviewed as a diff by another
developer.
"""

from __future__ import annotations

import mongomock
import pytest

from app import db, prompts


@pytest.fixture(autouse=True)
def store(monkeypatch):
    fake = mongomock.MongoClient().db
    monkeypatch.setattr(db, "db", lambda: fake)
    return fake


class TestDefaults:
    def test_a_deployment_that_never_edited_one_still_has_text(self):
        for name in prompts.PROMPTS:
            entry = prompts.current(name)
            assert entry["content"].strip()
            assert entry["isDefault"] is True
            assert entry["version"] == 0

    def test_every_fragment_names_the_agent_it_governs(self):
        agents = {p["agent"] for p in prompts.listing()}
        assert agents <= {"intake", "brd", "ui", "decomposition"}

    def test_an_unknown_fragment_is_refused(self):
        with pytest.raises(ValueError):
            prompts.current("brd.doesNotExist")


class TestEditing:
    def test_a_change_records_who_made_it(self):
        entry = prompts.set_text("brd.rigour", "Be rigorous.", "user-vp")
        assert entry["content"] == "Be rigorous."
        assert entry["updatedBy"] == "user-vp"
        assert entry["version"] == 1
        assert entry["isDefault"] is False

    def test_the_previous_text_is_kept_as_a_revision(self):
        """SoW 4.0 requires change history, not just the current text."""
        prompts.set_text("brd.rigour", "first", "user-a")
        prompts.set_text("brd.rigour", "second", "user-b")

        past = prompts.history("brd.rigour")
        assert len(past) == 1
        assert past[0]["content"] == "first"
        assert past[0]["updatedBy"] == "user-a"

    def test_clearing_restores_the_shipped_default(self):
        """A bad edit never has to be reconstructed from memory."""
        original = prompts.current("ui.visualPolish")["content"]
        prompts.set_text("ui.visualPolish", "something wrong", "a")
        restored = prompts.set_text("ui.visualPolish", "   ", "a")

        assert restored["isDefault"] is True
        assert restored["content"] == original

    def test_reverting_keeps_the_text_it_discarded(self):
        """Reverting is itself a change, and the discarded text is exactly what
        an auditor reviewing the revert needs to see. Clearing without
        recording it lost the only copy."""
        prompts.set_text("ui.visualPolish", "the text being reverted", "user-vp")
        prompts.set_text("ui.visualPolish", "", "user-vp")

        past = prompts.history("ui.visualPolish")
        assert [r["content"] for r in past] == ["the text being reverted"]

    def test_editing_one_fragment_leaves_the_others_alone(self):
        prompts.set_text("brd.rigour", "changed", "a")
        assert prompts.current("brd.critique")["isDefault"] is True

    def test_history_is_bounded(self):
        for i in range(prompts.MAX_REVISIONS + 8):
            prompts.set_text("brd.rigour", f"v{i}", "a")
        assert len(prompts.history("brd.rigour")) <= prompts.MAX_REVISIONS


class TestTheAuditStamp:
    def test_versions_start_at_zero_and_track_edits(self):
        assert prompts.versions("brd") == {"brd.rigour": 0, "brd.critique": 0}
        prompts.set_text("brd.rigour", "changed", "a")
        assert prompts.versions("brd")["brd.rigour"] == 1

    def test_versions_can_be_scoped_to_one_agent(self):
        ui_versions = prompts.versions("ui")
        assert set(ui_versions) == {"ui.visualPolish", "ui.productShape"}

    def test_every_fragment_appears_when_unscoped(self):
        assert set(prompts.versions()) == set(prompts.PROMPTS)


class TestTheCommittedCopy:
    def test_an_agent_document_carries_its_fragments_and_versions(self):
        """Committed beside the artefact so a reviewer can read the
        instructions it was produced against without resolving a version
        number against a database that has since moved on."""
        prompts.set_text("brd.rigour", "Be rigorous about X.", "user-vp")
        doc = prompts.as_markdown("brd")

        assert "# Prompts used — brd agent" in doc
        assert "Be rigorous about X." in doc
        assert "`brd.rigour` · version 1" in doc
        assert "built-in default" in doc          # critique is still unedited

    def test_an_agent_with_no_fragments_produces_nothing(self):
        assert prompts.as_markdown("intake") == ""


class TestResilience:
    def test_an_unreadable_store_falls_back_to_the_default(self, monkeypatch):
        """Generating against the shipped prompt is a correct outcome;
        generating against no instructions is not."""
        def broken():
            raise RuntimeError("mongo is down")

        monkeypatch.setattr(db, "db", broken)
        assert prompts.text("brd.rigour") == prompts.PROMPTS["brd.rigour"]["default"]
