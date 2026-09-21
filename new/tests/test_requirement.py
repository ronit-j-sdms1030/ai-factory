"""The requirement record — the seam between the four Phase 1 components.

These tests defend the properties that only exist once the four are wired
together, and that no test of any one of them could have caught: that an
approval is all-or-nothing, that a revision vacates the approvals of the
document it replaced, and that the ledger is not fed artefacts it would then
report as orphans.
"""

from __future__ import annotations

import pytest

import requirement as R
import run_ledger as rl
from attestation import IncompleteAttestation
from gate_engine import GateRefused
from requirement import TransitionRefused, open_requirement
from workflow_templates import UnknownTemplate

RAISER = {"id": "u-raiser", "roles": ["business_analyst"], "team": "delivery"}
PO = {"id": "u-po", "roles": ["product_owner"], "team": "product"}
BO = {"id": "u-bo", "roles": ["business_owner"], "team": "business"}
CTL = {"id": "u-ctl", "roles": ["client_tech_lead"], "team": "client"}

PROVENANCE = dict(
    model="anthropic/claude-haiku-4.5", model_version="2026-05-01", prompt_version=3
)


@pytest.fixture
def req() -> R.Requirement:
    r = open_requirement("REQ-0250", "full_governance", RAISER)
    r.record_artefact("scope", "9f2c1ab", **PROVENANCE)
    return r


def cleared_gate_one(r: R.Requirement) -> R.Requirement:
    r.decide(1, "approve", PO)
    return r


class TestOpening:
    def test_the_chain_is_resolved_once_at_intake(self):
        """§2 step 2. Resolved per gate instead, a mid-flight group change would
        rewrite who was allowed to approve a decision already in progress."""
        r = open_requirement("REQ-0250", "full_governance", RAISER)
        assert r.approval_chain["1"] == ["product_owner"]
        assert sorted(r.approval_chain["2"]) == ["business_owner", "client_tech_lead"]

    def test_it_starts_at_the_first_gate_its_template_fires(self):
        assert open_requirement("REQ-1", "full_governance", RAISER).awaiting == 1

    def test_a_template_that_skips_early_gates_starts_where_it_starts(self):
        """Hotfix fires 5, 6, 7. Starting at 1 would park it on a gate that
        never fires and it would wait forever."""
        assert open_requirement("REQ-2", "hotfix", RAISER).awaiting == 5

    def test_an_anonymous_originator_is_refused(self):
        """Segregation of duties at gate 1 is unenforceable without a name."""
        with pytest.raises(TransitionRefused, match="named originator"):
            open_requirement("REQ-3", "full_governance", {"roles": ["x"]})

    def test_an_unknown_template_is_refused_rather_than_defaulted(self):
        with pytest.raises(UnknownTemplate):
            open_requirement("REQ-4", "fast_track", RAISER)


class TestOneApprovalIsAllOrNothing:
    def test_an_attestation_that_cannot_be_built_records_no_signature(self):
        """The ordering this module exists for. The obvious implementation
        writes the signature first and eventually leaves a gate approved with no
        evidence — the one inconsistency the trail exists to make impossible."""
        r = open_requirement("REQ-0250", "full_governance", RAISER)
        # Provenance-free artefact, reachable only by bypassing record_artefact.
        r.artefacts["scope"] = R.Artefact("scope", "9f2c1ab", "", "2026-05-01", "3")

        with pytest.raises(IncompleteAttestation):
            r.decide(1, "approve", PO)

        assert r.signatures == []
        assert r.attestations == []
        assert r.cleared == set()
        assert r.awaiting == 1
        assert not any(h["event"] == "decision" for h in r.history)

    def test_a_refused_actor_leaves_nothing_behind_either(self, req):
        with pytest.raises(GateRefused):
            req.decide(1, "approve", BO)  # not a product owner
        assert req.signatures == []
        assert req.attestations == []

    def test_an_accepted_approval_produces_all_four_effects(self, req):
        decision = req.decide(1, "approve", PO)
        assert decision.satisfied
        assert len(req.signatures) == 1
        assert len(req.attestations) == 1
        assert rl.approval(1, "REQ-0250") in req.ledger.children(rl.requirement("REQ-0250"))
        assert req.cleared == {1}


class TestTheArtefactBinding:
    def test_a_gate_with_nothing_written_cannot_be_approved(self):
        r = open_requirement("REQ-0250", "full_governance", RAISER)
        with pytest.raises(TransitionRefused, match="no artefact"):
            r.decide(1, "approve", PO)

    def test_the_attestation_names_the_artefact_that_was_approved(self, req):
        req.decide(1, "approve", PO)
        assert req.attestations[0]["subject"][0]["digest"] == {"gitCommit": "9f2c1ab"}

    def test_provenance_is_refused_at_write_time_not_at_the_gate(self):
        """Refused where the mistake is, rather than hours later with a
        reviewer waiting on an approval that cannot be attested."""
        r = open_requirement("REQ-0250", "full_governance", RAISER)
        with pytest.raises(TransitionRefused, match="model_version"):
            r.record_artefact("scope", "9f2c1ab", model="m", model_version="", prompt_version=3)

    def test_the_attestation_carries_the_artefacts_producer_not_the_approvers(self, req):
        """The model is a fact about the document, not about the reviewer."""
        req.decide(1, "approve", PO)
        assert req.attestations[0]["predicate"]["producedBy"]["model"] == PROVENANCE["model"]

    def test_a_stage_the_template_does_not_run_is_refused(self):
        with pytest.raises(TransitionRefused, match="does not run"):
            open_requirement("REQ-2", "hotfix", RAISER).record_artefact(
                "scope", "abc", **PROVENANCE
            )

    def test_an_approved_artefact_cannot_be_quietly_replaced(self, req):
        """Rewriting it would change what was approved while the approval sits
        there still pointing at it."""
        req.decide(1, "approve", PO)
        with pytest.raises(TransitionRefused, match="already cleared"):
            req.record_artefact("scope", "different", **PROVENANCE)


class TestARevisionVacatesTheApprovalsOfWhatItReplaced:
    def test_two_signatures_cannot_be_collected_across_two_documents(self, req):
        """The failure this property exists to prevent: gate 2 needs two
        reviewers, and without it the first reviewer's approval of draft one
        combines with the second reviewer's approval of draft two into a
        document nobody approved twice."""
        cleared_gate_one(req)
        req.record_artefact("brd", "brd-v1", **PROVENANCE)
        req.decide(2, "approve", BO)
        assert req.awaiting == 2  # one signature, gate still open

        req.decide(2, "revise", CTL)
        req.record_artefact("brd", "brd-v2", **PROVENANCE)

        assert req.live_signatures(2) == []
        req.decide(2, "approve", CTL)
        assert req.awaiting == 2, "one signature on v2 must not clear a two-signature gate"

    def test_the_superseded_signature_is_retained_not_deleted(self, req):
        """Who approved the version that was thrown away is an audit question."""
        cleared_gate_one(req)
        req.record_artefact("brd", "brd-v1", **PROVENANCE)
        req.decide(2, "approve", BO)
        req.record_artefact("brd", "brd-v2", **PROVENANCE)

        at_gate_two = [s for s in req.signatures if s.gate == 2]
        assert [s.artefact_sha for s in at_gate_two] == ["brd-v1"]
        assert req.live_signatures(2) == []

    def test_re_approving_the_new_document_clears_the_gate(self, req):
        cleared_gate_one(req)
        req.record_artefact("brd", "brd-v2", **PROVENANCE)
        req.decide(2, "approve", BO)
        req.decide(2, "approve", CTL)
        assert req.cleared == {1, 2}
        assert req.awaiting == 3

    def test_revise_clears_nothing_and_stays_at_the_gate(self, req):
        req.decide(1, "revise", PO)
        assert req.cleared == set()
        assert req.awaiting == 1
        assert req.state == "open"

    def test_a_revision_is_attested_like_any_other_decision(self, req):
        req.decide(1, "revise", PO)
        assert req.attestations[0]["predicate"]["decision"] == "revise"


class TestSequenceAndIdentity:
    def test_a_later_gate_cannot_be_approved_first(self, req):
        req.record_artefact("brd", "brd-v1", **PROVENANCE)
        with pytest.raises(GateRefused, match="waiting at gate 1"):
            req.decide(2, "approve", BO)

    def test_the_originator_cannot_approve_their_own_requirement(self):
        r = open_requirement("REQ-0250", "full_governance", PO)
        r.record_artefact("scope", "9f2c1ab", **PROVENANCE)
        with pytest.raises(GateRefused, match="cannot approve"):
            r.decide(1, "approve", PO)

    def test_a_gate_the_template_never_fires_is_refused(self):
        r = open_requirement("REQ-2", "hotfix", RAISER)
        with pytest.raises(TransitionRefused, match="does not fire gate 1"):
            r.decide(1, "approve", PO)

    def test_the_same_person_cannot_provide_both_signatures(self, req):
        cleared_gate_one(req)
        req.record_artefact("brd", "brd-v1", **PROVENANCE)
        both = {"id": "u-both", "roles": ["business_owner", "client_tech_lead"], "team": "b"}
        req.decide(2, "approve", both)
        with pytest.raises(GateRefused, match="already signed"):
            req.decide(2, "approve", both)


class TestDiscard:
    def test_it_closes_the_request(self, req):
        req.decide(1, "discard", PO)
        assert req.state == "discarded"
        assert not req.is_open

    def test_nothing_further_is_accepted(self, req):
        req.decide(1, "discard", PO)
        with pytest.raises(TransitionRefused, match="discarded"):
            req.decide(1, "approve", PO)

    def test_the_trail_is_retained(self, req):
        """§2: 'request closed, reason recorded, trail retained'."""
        req.decide(1, "discard", PO)
        assert len(req.attestations) == 1
        assert req.attestations[0]["predicate"]["decision"] == "discard"
        assert req.ledger.traces_to(rl.approval(1, "REQ-0250")) == {rl.requirement("REQ-0250")}


class TestTheLedgerIsNotFedPhaseOneArtefacts:
    def test_an_approval_traces_back_to_its_requirement(self, req):
        req.decide(1, "approve", PO)
        assert req.ledger.traces_to(rl.approval(1, "REQ-0250")) == {rl.requirement("REQ-0250")}

    def test_no_commit_node_is_manufactured(self, req):
        """A scope report has no ticket behind it because no work has been
        decomposed. Adding it would create a commit that orphans() then reports
        as a finding — a false one."""
        req.decide(1, "approve", PO)
        req.record_artefact("brd", "brd-v1", **PROVENANCE)
        req.decide(2, "approve", BO)
        req.decide(2, "approve", CTL)
        assert req.ledger.orphans("commit") == set()
        assert req.ledger.covered_by(rl.requirement("REQ-0250"), "commit") == set()


class TestTraceabilityIds:
    def test_they_are_numbered_under_the_requirement(self, req):
        assert req.allocate_traceability_ids(3) == [
            "REQ-0250-R01",
            "REQ-0250-R02",
            "REQ-0250-R03",
        ]

    def test_the_counter_only_climbs(self, req):
        req.allocate_traceability_ids(2)
        assert req.allocate_traceability_ids(1) == ["REQ-0250-R03"]

    def test_a_retired_id_is_never_reissued(self, req):
        """Reissuing it would silently repoint every ticket, test and commit
        that already cited it, and nothing in the trail would look wrong."""
        first = req.allocate_traceability_ids(2)
        req.retire_traceability_ids([first[1]])
        assert req.allocate_traceability_ids(1) == ["REQ-0250-R03"]
        assert first[1] in req.retired_traceability_ids

    def test_allocating_nothing_is_refused(self, req):
        with pytest.raises(TransitionRefused):
            req.allocate_traceability_ids(0)


class TestStateIsDerived:
    def test_it_completes_when_every_gate_its_template_fires_has_cleared(self):
        r = R.Requirement(
            id="REQ-9",
            template="full_governance",
            originator=RAISER,
            approval_chain={},
            gates=(1, 2),
            cleared={1, 2},
        )
        assert r.awaiting is None
        assert r.state == "complete"

    def test_a_complete_requirement_accepts_no_further_decisions(self):
        r = R.Requirement(
            id="REQ-9",
            template="full_governance",
            originator=RAISER,
            approval_chain={},
            gates=(1,),
            cleared={1},
        )
        with pytest.raises(TransitionRefused, match="cleared every gate"):
            r.decide(1, "approve", PO)


class TestPersistenceRoundTrip:
    def test_an_approval_survives_dump_and_load(self, req):
        req.decide(1, "approve", PO)
        restored = R.Requirement.load(req.dump())
        assert restored.awaiting == 2
        assert restored.signatures[0].artefact_sha == "9f2c1ab"
        assert restored.ledger.traces_to(rl.approval(1, "REQ-0250")) == {
            rl.requirement("REQ-0250")
        }

    def test_the_attestation_path_is_unique_per_decision(self, req):
        req.decide(1, "revise", PO)
        first = req.path_for_attestation(req.attestations[-1])
        req.decide(1, "approve", PO)
        second = req.path_for_attestation(req.attestations[-1])
        assert first != second
