"""State machine parity tests.

These encode the authorization rules and routing special cases from
``stateMachine.service.js``. They are the guard against the port silently
changing who may approve what.
"""

import pytest

from app.config import resolve_approval_chain
from app.state_machine import TransitionError, transition


def make_artifact(originator_tier, stage="pending_approval", index=0):
    return {
        "currentStage": stage,
        "originator": {"userId": "orig-1", "tierId": originator_tier},
        "approvalChain": [
            {"approverTiers": s.approver_tiers, "mode": s.mode, "approved_by": [], "approvedBy": []}
            for s in resolve_approval_chain(originator_tier)
        ],
        "currentApprovalIndex": index,
        "history": [],
    }


def actor(tier, user_id="u-1"):
    return {"user_id": user_id, "tier_id": tier}


class TestApprovalAuthorization:
    def test_wrong_tier_cannot_approve(self):
        art = make_artifact("md")  # gate 0 is md
        with pytest.raises(TransitionError, match="Unauthorized approver"):
            transition(art, "approve", actor("tl"))

    def test_correct_tier_advances_the_chain(self):
        art = make_artifact("md")
        transition(art, "approve", actor("md"))
        assert art["currentApprovalIndex"] == 1
        assert art["currentStage"] == "pending_approval"  # VP gate still ahead

    def test_final_gate_approval_marks_approved(self):
        art = make_artifact("md", index=1)  # at the VP gate
        transition(art, "approve", actor("vp"))
        assert art["currentStage"] == "approved"

    def test_vp_originated_single_gate_completes_in_one_approval(self):
        art = make_artifact("vp")
        transition(art, "approve", actor("md"))
        assert art["currentStage"] == "approved"

    def test_approval_is_recorded_with_actor_and_timestamp(self):
        art = make_artifact("md")
        transition(art, "approve", actor("md", "alice"))
        recorded = art["approvalChain"][0]["approvedBy"][0]
        assert recorded["userId"] == "alice"
        assert recorded["tierId"] == "md"
        assert recorded["timestamp"] is not None


class TestVpRejectionGuard:
    """A VP at gate 1 is reviewing an FSD that already cleared MD/CEO."""

    def test_vp_cannot_reject_a_reviewed_fsd(self):
        art = make_artifact("md", index=1)
        with pytest.raises(TransitionError, match="cannot reject this reviewed FSD"):
            transition(art, "reject", actor("vp"))

    def test_vp_can_still_reject_at_gate_zero(self):
        art = make_artifact("pm")  # gate 0 includes vp
        transition(art, "reject", actor("vp"))
        assert art["currentStage"] == "rejected"


class TestFsdLoopRouting:
    def test_self_origin_md_routes_to_vp_not_a_peer(self):
        """An MD must not be able to nominate a co-equal rubber stamp."""
        art = make_artifact("md", stage="fsd_review", index=1)
        transition(art, "sendFsdToClient", actor("md"))
        assert art["currentStage"] == "pending_approval"  # the VP gate

    def test_self_origin_md_cannot_choose_a_final_approver(self):
        art = make_artifact("md", stage="fsd_review", index=1)
        with pytest.raises(TransitionError, match="always route to VP"):
            transition(art, "sendFsdToClient", actor("md"), final_approver_tier="ceo")

    def test_tl_origin_skips_the_client_loop_and_goes_to_vp(self):
        art = make_artifact("tl", stage="fsd_review", index=1)
        transition(art, "sendFsdToClient", actor("md"))
        assert art["currentStage"] == "pending_approval"

    def test_client_origin_does_enter_the_client_loop(self):
        art = make_artifact("client", stage="fsd_review", index=1)
        transition(art, "sendFsdToClient", actor("md"))
        assert art["currentStage"] == "fsd_pending_client"

    def test_vp_origin_approve_fsd_completes_without_a_redundant_gate(self):
        art = make_artifact("vp", stage="fsd_pending_client", index=1)
        transition(art, "approveFsd", actor("vp", "orig-1"))
        assert art["currentStage"] == "approved"

    def test_reviewer_pool_is_gate_zero_not_a_hardcoded_tier(self):
        art = make_artifact("client", stage="fsd_review", index=1)
        with pytest.raises(TransitionError, match="reviewer pool"):
            transition(art, "sendFsdToClient", actor("tl"))

    def test_only_the_originator_may_act_in_the_client_loop(self):
        art = make_artifact("client", stage="fsd_pending_client", index=1)
        with pytest.raises(TransitionError, match="only the originator"):
            transition(art, "approveFsd", actor(None, "someone-else"))


class TestGuards:
    def test_unknown_action_for_the_stage_is_rejected(self):
        art = make_artifact("md", stage="draft")
        with pytest.raises(TransitionError, match="Cannot perform"):
            transition(art, "approve", actor("md"))

    def test_submit_without_a_chain_is_rejected(self):
        art = make_artifact("md", stage="draft")
        art["approvalChain"] = []
        with pytest.raises(TransitionError, match="no approval chain"):
            transition(art, "submit", actor("md"))

    def test_submit_resets_the_chain_index(self):
        art = make_artifact("md", stage="revision_requested", index=3)
        transition(art, "submit", actor("md"))
        assert art["currentApprovalIndex"] == 0
        assert art["currentStage"] == "pending_approval"

    def test_every_transition_appends_history(self):
        art = make_artifact("md", stage="draft")
        transition(art, "submit", actor("md"), comment="please review")
        entry = art["history"][-1]
        assert entry["action"] == "submit"
        assert entry["actorId"] == "u-1"
        assert entry["comment"] == "please review"
