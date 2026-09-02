"""The design-system skill file that constrains every generated screen.

SoW 11.0 commits that screens are "constrained by a Jakson design-system skill
file (tokens, components, accessibility rules)"; SoW 4.0 requires that file to
carry change history and approver identity.
"""

from __future__ import annotations

import mongomock
import pytest

from app import db, design_system


@pytest.fixture(autouse=True)
def store(monkeypatch):
    fake = mongomock.MongoClient().db
    monkeypatch.setattr(db, "db", lambda: fake)
    return fake


class TestTheDefault:
    def test_a_deployment_that_never_set_one_still_has_a_design_system(self):
        """An empty skill file would return the pipeline to models guessing,
        which is the failure this replaces."""
        skill = design_system.current()
        assert skill["isDefault"] is True
        assert skill["version"] == 0
        assert skill["content"].strip()

    def test_the_default_names_the_styling_mechanism(self):
        """The preview loads Tailwind; if the skill file told the model to do
        something else the two would disagree and screens would render
        unstyled — which is exactly what happened before this existed."""
        assert "Tailwind" in design_system.current()["content"]

    def test_the_default_covers_what_the_sow_names(self):
        content = design_system.current()["content"].lower()
        assert "token" in content or "colour token" in content
        assert "component" in content
        assert "accessibility" in content


class TestEditing:
    def test_setting_content_records_who_changed_it(self):
        skill = design_system.set_content("# Ours\n\nUse pill buttons.", "user-vp")
        assert skill["content"] == "# Ours\n\nUse pill buttons."
        assert skill["updatedBy"] == "user-vp"
        assert skill["isDefault"] is False
        assert skill["version"] == 1

    def test_each_edit_increments_the_version(self):
        design_system.set_content("v one", "a")
        design_system.set_content("v two", "b")
        assert design_system.current()["version"] == 2

    def test_the_previous_text_is_kept_as_a_revision(self):
        """SoW 4.0 requires change history, not just the current text."""
        design_system.set_content("first", "user-a")
        design_system.set_content("second", "user-b")

        past = design_system.history()
        assert len(past) == 1
        assert past[0]["content"] == "first"
        assert past[0]["updatedBy"] == "user-a"

    def test_history_is_newest_first(self):
        for i, who in enumerate(["a", "b", "c"]):
            design_system.set_content(f"version {i}", who)
        past = design_system.history()
        assert [p["content"] for p in past] == ["version 1", "version 0"]

    def test_history_is_bounded(self):
        for i in range(design_system.MAX_REVISIONS + 10):
            design_system.set_content(f"v{i}", "a")
        assert len(design_system.history()) <= design_system.MAX_REVISIONS

    def test_reverting_keeps_the_text_it_discarded(self):
        """Reverting is itself a change; the discarded text is what an auditor
        reviewing the revert needs to see."""
        design_system.set_content("the text being reverted", "user-vp")
        design_system.set_content("", "user-vp")

        past = design_system.history()
        assert [r["content"] for r in past] == ["the text being reverted"]

    def test_clearing_it_restores_the_default_rather_than_nothing(self):
        """The agent is never one edit away from having no design system."""
        design_system.set_content("something custom", "a")
        restored = design_system.set_content("   ", "a")
        assert restored["isDefault"] is True
        assert "Tailwind" in restored["content"]


class TestApplication:
    def test_the_prompt_section_carries_the_current_file(self):
        design_system.set_content("Always use pill buttons.", "a")
        section = design_system.prompt_section()
        assert "Always use pill buttons." in section
        assert "DESIGN SYSTEM" in section

    def test_the_prompt_section_says_the_skill_file_outranks_the_model(self):
        section = design_system.prompt_section()
        assert "outranks" in section.lower() or "follow it exactly" in section.lower()
