"""`intake.skill.md` — the capability boundary.

The architecture requires this file to carry a capability boundary, a question
policy and its budget, scope-bounding rules, an output shape and the client's
vocabulary. These tests defend each, and defend the property that makes the file
worth having: an agent given one without a boundary does not error, it accepts
anything — so the failure is silent and lands four gates later.
"""

from __future__ import annotations

import pytest

import intake_skill
from intake_skill import DEFAULT, IncompleteSkillFile, SkillFile, validate


def flat(text: str) -> str:
    """Whitespace-normalised, because the source wraps at 80 columns and the
    phrases under test straddle line breaks."""
    return " ".join(text.lower().split())


class TestTheArchitecturesRequiredSections:
    @pytest.mark.parametrize("section", intake_skill.REQUIRED_SECTIONS)
    def test_the_default_carries_every_one(self, section):
        assert section in flat(DEFAULT)

    def test_a_file_missing_one_is_refused(self):
        with pytest.raises(IncompleteSkillFile, match="capability boundary"):
            validate("# Intake\n\nAsk some questions.")

    def test_the_refusal_names_every_missing_section(self):
        with pytest.raises(IncompleteSkillFile) as exc:
            validate("# Intake\n\nNothing useful here.")
        for section in intake_skill.REQUIRED_SECTIONS:
            assert section in str(exc.value)

    def test_the_default_validates(self):
        validate(DEFAULT)


class TestTheBoundary:
    def test_it_states_what_the_platform_builds(self):
        t = flat(DEFAULT)
        assert "react" in t and "node.js or python" in t and "postgresql" in t

    @pytest.mark.parametrize(
        "excluded",
        [
            "native mobile",
            "desktop application",
            "firmware",
            "games",
            "safety-critical",
            "call bots and chatbots",
        ],
    )
    def test_it_states_what_cannot_be_delivered_at_all(self, excluded):
        """Not difficulty judgements — the platform has no path to these."""
        assert excluded in flat(DEFAULT)

    def test_an_ai_feature_is_in_scope_but_an_ml_platform_is_not(self):
        """The distinction someone will argue, so it is written down."""
        t = flat(DEFAULT)
        assert "ai feature" in t and "is in scope" in t
        assert "trains and serves models is not" in t


class TestScopeBounding:
    def test_it_asks_before_concluding(self):
        """Most apparent breaches are a wording problem. Asking costs one
        question; guessing costs a project."""
        t = flat(DEFAULT)
        assert "on their phones" in t and "mobile browser" in t
        assert "read from the machines" in t

    def test_an_exclusion_is_recorded_rather_than_refused(self):
        t = flat(DEFAULT)
        assert "do not refuse" in t
        assert "improvise" in t
        assert "reviewer's decision at gate 1" in t


class TestTheQuestionBudget:
    def test_the_bounds_are_stated(self):
        assert "at most **ten** calls" in flat(DEFAULT)
        assert "there is no minimum" in flat(DEFAULT)
        assert "scope report" in flat(DEFAULT)

    def test_it_says_the_budget_is_enforced_in_code(self):
        """An instruction a model can talk itself out of is not a budget."""
        assert "enforced in code" in flat(DEFAULT)

    def test_it_gives_the_reason_rather_than_only_the_rule(self):
        """A rule with a reason survives an edit by someone who disagrees."""
        assert "quadratically" in flat(DEFAULT)

    def test_leftover_budget_is_not_something_to_spend(self):
        assert "leftover budget is not something to spend" in flat(DEFAULT)


class TestTheOutputShape:
    def test_out_of_scope_is_never_empty_on_a_real_requirement(self):
        """It is the section that prevents an argument at Gate 4 about what was
        agreed."""
        assert "never empty on a real requirement" in flat(DEFAULT)

    def test_success_is_recorded_in_the_requester_s_own_words(self):
        t = flat(DEFAULT)
        assert "in their own words" in t
        assert "survives contact with a uat session" in t

    def test_compliance_travels_with_the_requirement(self):
        assert "rediscovered at design" in flat(DEFAULT)


class TestTheGoverningRules:
    def test_nothing_is_recorded_without_a_source(self):
        assert "no requirement without a source" in flat(DEFAULT)

    def test_testability_pressure_is_applied_here(self):
        """Vagueness at intake survives three phases and surfaces at UAT."""
        t = flat(DEFAULT)
        assert "testable, or it does not exist" in t
        assert "acceptance criteria" in t

    def test_a_gap_is_never_closed_quietly(self):
        assert "never close a gap quietly" in flat(DEFAULT)

    def test_the_borrowed_rules_are_attributed(self):
        """MIT-0 asks nothing. Recording it tells a reader deciding whether to
        change a rule which ones were proven elsewhere."""
        doc = intake_skill.__doc__ or ""
        assert "MIT-0" in doc and "aidlc" in doc.lower()


class TestClientVocabulary:
    def test_it_is_a_marked_placeholder_not_invented_content(self):
        """No library knows the client's system names, and inventing them is
        how a synonym reaches the BRD and breaks entity matching two phases
        later."""
        assert "PLACEHOLDER" in DEFAULT
        assert "no client vocabulary is configured" in flat(DEFAULT)

    def test_it_says_what_goes_wrong_without_one(self):
        assert "breaks entity matching" in flat(DEFAULT)


class TestVersioningIsGitNotADatabase:
    def test_the_version_is_a_commit_not_a_counter(self):
        """Git already versions the file. A counter would create two answers to
        'which version produced this'."""
        s = SkillFile(content=DEFAULT, version="9f2c1ab", edited_by="u-vp")
        assert s.version == "9f2c1ab"

    def test_a_deployment_that_never_edited_one_is_running_the_default(self):
        """A true and citable state, not an error."""
        assert intake_skill.shipped().is_default is True

    def test_an_edited_file_is_not_the_default(self):
        assert SkillFile("# Boundary\n\nWeb only.", "abc123", "u-vp").is_default is False

    def test_it_lives_in_the_governance_repository(self):
        assert intake_skill.PATH == "skills/intake.skill.md"


class TestTheQuestionBudgetEnforcer:
    def test_the_tenth_slot_is_the_report_not_a_question(self):
        b = intake_skill.QuestionBudget()
        for _ in range(9):
            b.record_question()
        assert b.must_close() is True
        with pytest.raises(intake_skill.BudgetExhausted):
            b.record_question()

    def test_a_report_may_close_before_four_questions(self):
        b = intake_skill.QuestionBudget()
        b.require_closeable()
        assert b.may_close() is True

    def test_four_questions_may_close(self):
        b = intake_skill.QuestionBudget()
        for _ in range(4):
            b.record_question()
        b.require_closeable()
        assert b.may_close() is True
        assert b.must_close() is False


class TestTheSnapshot:
    def test_it_is_committed_beside_the_artefact_it_governed(self):
        """Beside, not referenced. A version number alone sends a reviewer to
        resolve it against a store that has since moved on."""
        assert intake_skill.snapshot_path_for("REQ-0250", "scope") == (
            "requirements/REQ-0250/scope/intake.skill.md"
        )


class TestInjection:
    def test_the_prompt_fragment_is_the_whole_file(self):
        """Injected whole, never retrieved: a boundary resolved by similarity
        search answers differently on different days."""
        s = intake_skill.shipped()
        assert intake_skill.prompt_section(s) == DEFAULT

    def test_it_stays_small_enough_to_inject_on_every_turn(self):
        """It is relied on as a stable cacheable prefix. Past a few thousand
        characters that argument stops holding and retrieval becomes the better
        trade — worth failing loudly rather than discovering as a cost line."""
        assert len(DEFAULT) < 9000
