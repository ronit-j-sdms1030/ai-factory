"""The Gate Engine's decision core.

The architecture has the policy engine ask three things of every approval: is
this identity in the approver set, is it *not* the originator, and is this the
gate we are actually waiting on. Each is here because a different thing goes
wrong without it, so each is tested for the failure rather than the happy path.

These are the tests that decide whether a governance claim is true. If the
segregation-of-duties check is wrong, the Scope of Work commitment that "a
requester cannot approve their own artefact" is false regardless of what any
document says.
"""

from __future__ import annotations

import pytest

import gate_engine
from gate_engine import GateRefused, evaluate

REQUESTER = {"id": "u-requester", "roles": ["product_owner"], "team": "business"}
PRODUCT_OWNER = {"id": "u-po", "roles": ["product_owner"], "team": "business"}
BUSINESS_OWNER = {"id": "u-bo", "roles": ["business_owner"], "team": "business"}
CLIENT_TECH_LEAD = {"id": "u-ctl", "roles": ["client_tech_lead"], "team": "engineering"}


def requirement(**over) -> dict:
    base = {"id": "REQ-0250", "originator": {"id": "u-requester"}, "gates": {}}
    base.update(over)
    return base


def signed(gate: int, *sigs: tuple[str, str, str]) -> dict:
    """A requirement with signatures already recorded: (role, actorId, team)."""
    return requirement(
        gates={str(gate): {"signatures": [{"role": r, "actorId": a, "team": t} for r, a, t in sigs]}}
    )


class TestTheApproverSet:
    def test_an_entitled_role_may_act(self):
        d = evaluate(1, "approve", PRODUCT_OWNER, requirement())
        assert d.satisfied is True

    def test_an_unentitled_role_is_refused(self):
        actor = {"id": "u-dev", "roles": ["senior_engineer"], "team": "engineering"}
        with pytest.raises(GateRefused, match="no role entitled"):
            evaluate(1, "approve", actor, requirement())

    def test_the_refusal_names_what_was_expected(self):
        """A refusal is evidence, and evidence that does not say what would
        have worked sends the approver to guess."""
        actor = {"id": "u-x", "roles": [], "team": "business"}
        with pytest.raises(GateRefused, match="product_owner"):
            evaluate(1, "approve", actor, requirement())


class TestSegregationOfDuties:
    def test_the_requester_cannot_approve_their_own_scope(self):
        """The check GitHub cannot make. It blocks a pull request author
        approving their own PR and knows nothing about who raised the
        requirement the PR belongs to."""
        with pytest.raises(GateRefused, match="raised this requirement"):
            evaluate(1, "approve", REQUESTER, requirement())

    def test_seniority_does_not_override_it(self):
        """An originator holding every role is still the originator. This is
        the case that matters, because it is the one someone will argue."""
        boss = {"id": "u-requester", "roles": ["product_owner", "business_owner"], "team": "business"}
        with pytest.raises(GateRefused, match="raised this requirement"):
            evaluate(1, "approve", boss, requirement())

    def test_nobody_signs_the_same_gate_twice(self):
        """Otherwise one approver satisfies a two-signature gate alone."""
        req = signed(2, ("business_owner", "u-bo", "business"))
        with pytest.raises(GateRefused, match="already signed"):
            evaluate(2, "approve", BUSINESS_OWNER, req)

    def test_uat_deliberately_allows_the_originator(self):
        """A UAT reviewer is often the person who raised the requirement, and
        should be — they know whether it does the job. The separation that
        matters was enforced at scope and at merge."""
        actor = {"id": "u-requester", "roles": ["business_stakeholder"], "team": "business"}
        assert evaluate(6, "approve", actor, requirement()).satisfied is True


class TestSequence:
    def test_an_approval_for_a_gate_that_is_not_open_is_refused(self):
        """Reviews are unordered; approval chains are not."""
        with pytest.raises(GateRefused, match="waiting at gate 1"):
            evaluate(2, "approve", BUSINESS_OWNER, requirement(), expected_gate=1)

    def test_the_open_gate_is_accepted(self):
        assert evaluate(1, "approve", PRODUCT_OWNER, requirement(), expected_gate=1).satisfied

    def test_sequence_is_checked_before_identity(self):
        """Refused for the honest reason. An out-of-order approval reported as
        an authorisation problem sends someone to find a more senior approver,
        which will not help."""
        nobody = {"id": "u-x", "roles": [], "team": "business"}
        with pytest.raises(GateRefused, match="not open"):
            evaluate(3, "approve", nobody, requirement(), expected_gate=1)


class TestMultipleSignatures:
    def test_gate_2_is_not_satisfied_by_one_signature(self):
        d = evaluate(2, "approve", BUSINESS_OWNER, requirement())
        assert d.satisfied is False
        assert "client_tech_lead" in d.awaiting

    def test_gate_2_completes_on_the_second_distinct_role(self):
        req = signed(2, ("business_owner", "u-bo", "business"))
        d = evaluate(2, "approve", CLIENT_TECH_LEAD, req)
        assert d.satisfied is True

    def test_gate_2_requires_two_distinct_teams(self):
        """Two signatures from one team is one perspective twice."""
        req = signed(2, ("business_owner", "u-bo", "business"))
        same_team = {"id": "u-ctl2", "roles": ["client_tech_lead"], "team": "business"}
        d = evaluate(2, "approve", same_team, req)
        assert d.satisfied is False
        assert "distinct teams" in d.note

    def test_gate_4_needs_every_stream_lead(self):
        """A dependency one department accepts and another has not seen is the
        defect this gate exists to catch."""
        tl = {"id": "u-tl", "roles": ["tech_lead"], "team": "eng"}
        d = evaluate(4, "approve", tl, requirement())
        assert d.satisfied is False
        assert d.awaiting == ["stream_lead"]

    def test_a_single_approver_gate_completes_at_once(self):
        assert evaluate(1, "approve", PRODUCT_OWNER, requirement()).satisfied is True

    def test_gate_3_requires_architect_before_ba_and_ui(self):
        ba = {"id": "u-ba", "roles": ["business_analyst"], "team": "business"}
        ux = {"id": "u-ux", "roles": ["ui_ux"], "team": "design"}
        arch = {"id": "u-arch", "roles": ["architect"], "team": "eng"}
        with pytest.raises(GateRefused, match="requires architect"):
            evaluate(3, "approve", ba, requirement())
        with pytest.raises(GateRefused, match="requires architect"):
            evaluate(3, "approve", ux, requirement())
        first = evaluate(3, "approve", arch, requirement())
        assert first.satisfied is False
        assert first.awaiting == ["business_analyst", "ui_ux"]
        after_arch = signed(3, ("architect", "u-arch", "eng"))
        with pytest.raises(GateRefused, match="requires business_analyst"):
            evaluate(3, "approve", ux, after_arch)
        second = evaluate(3, "approve", ba, after_arch)
        assert second.satisfied is False
        assert second.awaiting == ["ui_ux"]
        after_ba = signed(
            3,
            ("architect", "u-arch", "eng"),
            ("business_analyst", "u-ba", "business"),
        )
        last = evaluate(3, "approve", ux, after_ba)
        assert last.satisfied is True


class TestOutcomes:
    def test_discard_exists_only_at_the_first_two_gates(self):
        """After the design is approved, work is corrected rather than
        abandoned — a rejection at gate 6 is a corrective ticket."""
        for gate in (1, 2):
            assert "discard" in gate_engine.GATES[gate].outcomes
        for gate in (3, 4, 5, 6, 7):
            assert "discard" not in gate_engine.GATES[gate].outcomes

    def test_an_outcome_the_gate_does_not_offer_is_refused(self):
        with pytest.raises(GateRefused, match="not an outcome"):
            evaluate(4, "discard", {"id": "u-tl", "roles": ["tech_lead"]}, requirement())

    def test_a_revision_resolves_the_gate_without_countersignature(self):
        """There is nothing to countersign about a revision."""
        d = evaluate(2, "revise", BUSINESS_OWNER, requirement())
        assert d.satisfied is True
        assert d.awaiting == []

    def test_gate_5_offers_request_changes_not_reject(self):
        assert gate_engine.GATES[5].outcomes == frozenset({"approve", "request_changes"})

    def test_an_unknown_gate_is_refused(self):
        with pytest.raises(GateRefused, match="no gate 9"):
            evaluate(9, "approve", PRODUCT_OWNER, requirement())


class TestTheChainIsResolvedOnce:
    def test_it_covers_only_the_gates_this_run_fires(self):
        """A template decides which gates fire. The chain describes those."""
        chain = gate_engine.resolve_approval_chain({"id": "u-1"}, [1, 2, 3, 4, 5, 7])
        assert set(chain) == {"1", "2", "3", "4", "5", "7"}
        assert "6" not in chain

    def test_it_names_the_roles_each_gate_needs(self):
        chain = gate_engine.resolve_approval_chain({"id": "u-1"}, [2])
        assert chain["2"] == ["business_owner", "client_tech_lead"]

    def test_an_unknown_gate_is_dropped_rather_than_invented(self):
        assert gate_engine.resolve_approval_chain({"id": "u-1"}, [1, 99]) == {
            "1": ["product_owner"]
        }
