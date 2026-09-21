"""Workflow templates.

A template decides which stages a run performs. The architecture is precise
about what it may *not* decide, and those limits are what these tests defend:
it cannot skip a gate belonging to a stage it runs, it cannot renumber gates,
and it cannot trade away screen generation.

Each of those, left unenforced, turns "ceremony proportional to risk" into a
way of opting out of governance — which is the same sentence read by someone
under deadline.
"""

from __future__ import annotations

import pytest

import gate_engine
import workflow_templates as wt
from workflow_templates import UnknownTemplate, for_name


class TestEveryTemplateIsWellFormed:
    def test_each_names_when_it_applies(self):
        """A template nobody can tell when to pick gets picked by seniority."""
        for name, t in wt.TEMPLATES.items():
            assert t.applies_when.strip(), name

    def test_stages_are_a_subset_of_the_canonical_order(self):
        for name, t in wt.TEMPLATES.items():
            assert set(t.stages) <= set(wt.STAGES), name

    def test_stages_never_reorder(self):
        """The order a run performs stages in does not vary by template. Only
        which of them run does."""
        for name, t in wt.TEMPLATES.items():
            canonical = [s for s in wt.STAGES if s in t.stages]
            assert list(t.stages) == canonical, name

    def test_every_gate_a_template_fires_is_a_real_gate(self):
        for name, t in wt.TEMPLATES.items():
            for g in t.gates:
                assert g in gate_engine.GATES, f"{name} fires unknown gate {g}"


class TestATemplateCannotSkipAGate:
    def test_running_a_stage_fires_its_gate(self):
        """The rule that stops a template becoming a way out of governance."""
        for name, t in wt.TEMPLATES.items():
            for stage in t.stages:
                assert t.fires(wt.STAGE_GATES[stage]), f"{name} runs {stage} without its gate"

    def test_not_running_a_stage_fires_nothing(self):
        for name, t in wt.TEMPLATES.items():
            for stage in set(wt.STAGES) - set(t.stages):
                assert not t.fires(wt.STAGE_GATES[stage]), f"{name} fires a gate for skipped {stage}"

    def test_gates_are_derived_from_stages_not_declared_beside_them(self):
        """Two lists that must agree are two lists that can disagree, and the
        failure mode would be a gate quietly not firing."""
        t = for_name("internal_tool")
        assert t.gates == tuple(wt.STAGE_GATES[s] for s in t.stages)


class TestCanonicalNumbering:
    def test_a_folded_gate_leaves_a_hole_rather_than_renumbering(self):
        """Internal-tool folds UAT into release. It fires 1-5 and 7 — not
        "1 to 6" with everything shifted down. Renumbering would make two runs
        incomparable in the audit trail."""
        assert for_name("internal_tool").gates == (1, 2, 3, 4, 5, 7)

    def test_full_governance_fires_all_seven(self):
        assert for_name("full_governance").gates == (1, 2, 3, 4, 5, 6, 7)

    def test_brownfield_fires_all_seven_too(self):
        """It differs in scope, not in ceremony: a BRD delta and only the
        screens the change touches."""
        assert for_name("brownfield_change").gates == (1, 2, 3, 4, 5, 6, 7)


class TestScreensAreNeverTradedAway:
    def test_no_template_skips_ui_except_hotfix(self):
        """Screens are what the business approves, so the stage producing them
        cannot be the one dropped for speed."""
        for name, t in wt.TEMPLATES.items():
            if name == "hotfix":
                continue
            assert t.ui_scope != "none", f"{name} skips UI generation"

    def test_hotfix_skips_it_only_because_there_is_nothing_to_generate(self):
        assert for_name("hotfix").ui_scope == "none"
        assert "design" not in for_name("hotfix").stages

    def test_brownfield_reduces_scope_rather_than_skipping(self):
        t = for_name("brownfield_change")
        assert t.ui_scope == "changed_only"
        assert t.runs("design")

    def test_ui_scope_is_not_a_boolean(self):
        """'Skip the UI' must not be a state reachable by flipping a flag."""
        assert {t.ui_scope for t in wt.TEMPLATES.values()} <= {"all", "changed_only", "none"}


class TestHotfix:
    def test_it_never_bypasses_the_merge_gate(self):
        """The one gate the architecture names as non-negotiable here."""
        assert for_name("hotfix").fires(5)

    def test_production_release_is_still_gated(self):
        """'Still fully attested' is incompatible with an ungated release."""
        assert for_name("hotfix").fires(7)

    def test_the_sla_is_shortened_never_removed(self):
        """A production incident with no response deadline is the failure the
        timer exists to prevent."""
        t = for_name("hotfix")
        assert 0 < t.sla_multiplier < 1
        assert wt.sla_for("hotfix", base_hours=48) == 12

    def test_the_reading_of_the_specification_is_recorded(self):
        """'Merge gate only' is ambiguous. The reading taken is written down so
        it can be settled deliberately rather than found in an audit."""
        assert "hotfix.gates" in wt.AMBIGUOUS
        assert "merge gate only" in wt.AMBIGUOUS["hotfix.gates"].lower()


class TestSelection:
    def test_an_unknown_name_is_refused_not_defaulted(self):
        """Defaulting to full governance looks safe and is wrong: a typo would
        silently produce a different run shape from the one selected, and the
        audit trail would record the shape rather than the mistake."""
        with pytest.raises(UnknownTemplate, match="no workflow template"):
            for_name("ful_governance")

    def test_the_refusal_lists_what_exists(self):
        with pytest.raises(UnknownTemplate, match="full_governance"):
            for_name("nonsense")


class TestTheApprovalChain:
    def test_it_covers_exactly_the_gates_the_template_fires(self):
        chain = wt.approval_chain("internal_tool")
        assert set(chain) == {"1", "2", "3", "4", "5", "7"}

    def test_it_names_the_roles_each_gate_needs(self):
        assert wt.approval_chain("full_governance")["2"] == ["business_owner", "client_tech_lead"]

    def test_hotfix_resolves_a_chain_for_its_three_gates(self):
        assert set(wt.approval_chain("hotfix")) == {"5", "6", "7"}
