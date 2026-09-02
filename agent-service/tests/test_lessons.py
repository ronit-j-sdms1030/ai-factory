"""Turning reviewer corrections into standing instructions.

Every edit is a statement about what the agent got wrong, and each one used to
be applied to a single screen and discarded — so the same correction arrived
again on the next requirement.
"""

from __future__ import annotations

import mongomock
import pytest

from app import db, lessons


@pytest.fixture(autouse=True)
def store(monkeypatch):
    fake = mongomock.MongoClient().db
    monkeypatch.setattr(db, "db", lambda: fake)
    return fake


class TestWhatActivatesItself:
    def test_a_formatting_rule_applies_immediately(self):
        """Worst case is a date format somebody corrects again — self-limiting,
        and requiring approval would mean nobody ever benefits from the loop."""
        doc = lessons.record("ui", "Format dates as DD/MM/YYYY.", "formatting", "e", "reviewer edit")
        assert doc["status"] == "active"
        assert "Format dates as DD/MM/YYYY." in lessons.active("ui")

    def test_wording_and_convention_also_apply(self):
        lessons.record("ui", "Call it 'Leave', not 'Time off'.", "wording", "e", "s")
        lessons.record("ui", "Right-align currency columns.", "convention", "e", "s")
        assert len(lessons.active("ui")) == 2

    def test_a_behaviour_rule_waits_for_a_human(self):
        """A bad one degrades every screen after it, and the audit trail would
        show the agent as having always behaved that way."""
        doc = lessons.record("ui", "Always add a bulk-approve action.", "behaviour", "e", "s")
        assert doc["status"] == "proposed"
        assert lessons.active("ui") == []


class TestRepeats:
    def test_the_same_rule_is_counted_not_duplicated(self):
        lessons.record("ui", "Format dates as DD/MM/YYYY.", "formatting", "e", "s")
        again = lessons.record("ui", "format dates as   DD/MM/YYYY.", "formatting", "e", "s")
        assert again["timesSeen"] == 2
        assert len(lessons.listing("ui")) == 1

    def test_repeats_rank_first(self):
        """One correction may be taste; the same one three times is a standard."""
        lessons.record("ui", "Rule A.", "formatting", "e", "s")
        for _ in range(3):
            lessons.record("ui", "Rule B.", "formatting", "e", "s")
        assert lessons.active("ui")[0] == "Rule B."


class TestApplication:
    def test_the_prompt_section_is_empty_when_nothing_is_learned(self):
        assert lessons.prompt_section("ui") == ""

    def test_learned_rules_reach_the_prompt(self):
        lessons.record("ui", "Format dates as DD/MM/YYYY.", "formatting", "e", "s")
        section = lessons.prompt_section("ui")
        assert "Format dates as DD/MM/YYYY." in section
        assert "corrections reviewers have already made" in section

    def test_one_agent_does_not_inherit_another_agent_rules(self):
        lessons.record("brd", "Name the standard.", "convention", "e", "s")
        assert lessons.active("ui") == []

    def test_the_prompt_cannot_grow_without_bound(self):
        for i in range(40):
            lessons.record("ui", f"Rule {i}.", "formatting", "e", "s")
        assert len(lessons.active("ui")) == lessons.MAX_ACTIVE


class TestReview:
    def test_a_proposed_rule_can_be_activated(self):
        doc = lessons.record("ui", "Always show a status column.", "behaviour", "e", "s")
        assert lessons.set_status(str(doc["_id"]), "active")
        assert "Always show a status column." in lessons.active("ui")

    def test_a_dismissed_rule_stops_applying_but_is_kept(self):
        """A rejected rule is a fact about this deployment; deleting it invites
        the extractor to propose the same thing next week."""
        doc = lessons.record("ui", "Use serif headings.", "formatting", "e", "s")
        lessons.set_status(str(doc["_id"]), "dismissed")
        assert lessons.active("ui") == []
        assert len(lessons.listing("ui")) == 1

    def test_an_unknown_id_is_refused_rather_than_crashing(self):
        assert lessons.set_status("not-an-id", "active") is False
