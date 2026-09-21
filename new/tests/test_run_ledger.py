"""The Run Ledger.

The architecture's traceability claim is that asking "why does this exist?" of
anything the pipeline produced resolves to a numbered requirement a named person
approved. These tests defend that in both directions, and defend the ledger's
ability to *name* the cases where it fails — an audit structure that can only
assert absence proves nothing.
"""

from __future__ import annotations

import pytest

import run_ledger as rl
from run_ledger import Ledger, UnknownRelation


@pytest.fixture
def ledger() -> Ledger:
    """One requirement, fully traced: REQ-0250 → ticket → test + commit → gate 5."""
    led = Ledger()
    req = rl.requirement("REQ-0250")
    tkt = rl.ticket("W3")
    led.link(req, tkt)
    led.link(tkt, rl.test("TC-07"))
    led.link(tkt, rl.commit("9f2c1ab"))
    led.link(rl.test("TC-07"), rl.commit("9f2c1ab"))
    led.link(rl.commit("9f2c1ab"), rl.approval(5, "REQ-0250"))
    led.link(req, rl.approval(1, "REQ-0250"))
    return led


class TestBackwards:
    def test_a_commit_resolves_to_the_requirement_behind_it(self, ledger):
        """The audit question: why is this line here?"""
        assert ledger.traces_to(rl.commit("9f2c1ab")) == {rl.requirement("REQ-0250")}

    def test_a_test_resolves_to_its_requirement(self, ledger):
        assert ledger.traces_to(rl.test("TC-07")) == {rl.requirement("REQ-0250")}

    def test_who_signed_for_a_commit_is_answerable(self, ledger):
        approvals = ledger.approvals_for(rl.commit("9f2c1ab"))
        assert rl.approval(5, "REQ-0250") in approvals

    def test_a_commit_with_nothing_behind_it_traces_to_nothing(self):
        """Not an error — a finding. This is the shape of code reaching the
        repository without a requirement."""
        led = Ledger()
        led.link(rl.commit("deadbee"), rl.approval(5, "REQ-9999"))
        assert led.traces_to(rl.commit("deadbee")) == set()


class TestForwards:
    def test_a_requirement_names_the_tests_covering_it(self, ledger):
        """The gate question: is this requirement actually covered?"""
        assert ledger.covered_by(rl.requirement("REQ-0250"), "test") == {rl.test("TC-07")}

    def test_a_requirement_names_the_commits_that_implemented_it(self, ledger):
        assert ledger.covered_by(rl.requirement("REQ-0250"), "commit") == {rl.commit("9f2c1ab")}

    def test_descendants_reach_through_intermediate_nodes(self, ledger):
        """Requirement → ticket → commit → approval, at any depth."""
        assert rl.approval(5, "REQ-0250") in ledger.descendants(rl.requirement("REQ-0250"))


class TestTheModelIsClosed:
    def test_an_undefined_relation_is_refused(self):
        """A ledger accepting any edge can answer any question and prove none:
        the value of 'this commit traces to that requirement' depends on the
        path being one the model intends."""
        led = Ledger()
        with pytest.raises(UnknownRelation, match="does not model"):
            led.link(rl.commit("abc"), rl.requirement("REQ-1"))

    def test_the_refusal_lists_what_is_defined(self):
        led = Ledger()
        with pytest.raises(UnknownRelation, match="requirement → ticket"):
            led.link(rl.test("T1"), rl.requirement("REQ-1"))

    def test_the_defined_relations_match_the_architecture(self):
        """'requirement to ticket, test, commit, approval' — the graph the
        architecture names."""
        assert ("requirement", "ticket") in rl.ALLOWED_EDGES
        assert ("ticket", "test") in rl.ALLOWED_EDGES
        assert ("ticket", "commit") in rl.ALLOWED_EDGES
        assert ("commit", "approval") in rl.ALLOWED_EDGES


class TestIntegrity:
    def test_it_can_name_orphans_rather_than_only_assert_their_absence(self):
        """A test covering nothing is exactly what the traceability claim says
        cannot exist, so the ledger should be able to point at one."""
        led = Ledger()
        led.link(rl.requirement("REQ-1"), rl.ticket("W1"))
        led.link(rl.ticket("W1"), rl.commit("aaa"))
        led.link(rl.ticket("W9"), rl.commit("bbb"))  # ticket with no requirement
        assert led.orphans("commit") == {rl.commit("bbb")}

    def test_a_fully_traced_run_has_no_orphans(self, ledger):
        assert ledger.orphans("commit") == set()
        assert ledger.orphans("test") == set()

    def test_it_is_append_only(self, ledger):
        """A descoped requirement keeps its edges. That work was done and then
        dropped is part of the record, and a ledger that can forget is one
        whose silence proves nothing."""
        assert not hasattr(ledger, "unlink")
        assert not hasattr(ledger, "remove")

    def test_duplicate_links_do_not_accumulate(self, ledger):
        before = ledger.children(rl.requirement("REQ-0250"))
        ledger.link(rl.requirement("REQ-0250"), rl.ticket("W3"))
        assert ledger.children(rl.requirement("REQ-0250")) == before


class TestItSurvivesMalformedInput:
    def test_a_cycle_does_not_hang_the_traversal(self):
        """Audit code runs when something has already gone wrong. Returning
        what it can beats hanging."""
        led = Ledger()
        led.link(rl.ticket("W1"), rl.commit("aaa"))
        led.link(rl.commit("aaa"), rl.approval(5, "REQ-1"))
        led._forward[rl.approval(5, "REQ-1")].add(rl.ticket("W1"))  # forced cycle
        assert rl.commit("aaa") in led.descendants(rl.ticket("W1"))

    def test_an_unknown_node_returns_empty_rather_than_raising(self):
        assert Ledger().traces_to(rl.commit("never-seen")) == set()
