"""GitHub integration — signature verification, event parsing, CODEOWNERS.

The signature tests matter most: a webhook payload decides whether an approval
gate opens, so anyone able to forge one could advance a requirement past a
human gate.
"""

import hashlib
import hmac
import json

import pytest

from app.github_api import (
    GitHubConfig,
    GitHubNotConfigured,
    access_token,
    codeowners,
    load_config,
    parse_branch,
    parse_review_event,
    verify_signature,
)

SECRET = "s3cret"


def signed(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestSignature:
    def test_valid_signature_passes(self):
        body = b'{"a":1}'
        assert verify_signature(body, signed(body), SECRET)

    def test_wrong_secret_fails(self):
        body = b'{"a":1}'
        assert not verify_signature(body, signed(body, "other"), SECRET)

    def test_tampered_body_fails(self):
        assert not verify_signature(b'{"a":2}', signed(b'{"a":1}'), SECRET)

    def test_missing_signature_fails(self):
        assert not verify_signature(b"{}", None, SECRET)

    def test_unconfigured_secret_fails_closed(self):
        """No secret must mean no trust, not blanket trust."""
        body = b"{}"
        assert not verify_signature(body, signed(body), None)

    def test_malformed_signature_fails(self):
        assert not verify_signature(b"{}", "garbage", SECRET)


class TestBranchParsing:
    def test_recovers_artifact_and_stage(self):
        assert parse_branch("req/abc123/brd") == ("abc123", "brd")

    @pytest.mark.parametrize("branch", ["main", "feature/x", "req/only", "", "req/a/b/c"])
    def test_unrelated_branches_yield_nothing(self, branch):
        assert parse_branch(branch) == (None, None)


def review_payload(state, login="alice", branch="req/abc123/brd", number=7, body=""):
    return {
        "review": {"state": state, "user": {"login": login}, "body": body},
        "pull_request": {"number": number, "head": {"ref": branch}},
    }


class TestEventParsing:
    def test_approval_becomes_an_approve_action(self):
        e = parse_review_event(review_payload("approved"))
        assert (e.action, e.artifact_id, e.stage, e.pr_number) == ("approve", "abc123", "brd", 7)

    def test_changes_requested_becomes_revise(self):
        assert parse_review_event(review_payload("changes_requested")).action == "revise"

    def test_dismissed_becomes_reject(self):
        assert parse_review_event(review_payload("dismissed")).action == "reject"

    def test_state_matching_is_case_insensitive(self):
        assert parse_review_event(review_payload("APPROVED")).action == "approve"

    def test_a_comment_is_not_a_decision(self):
        """Treating a comment as approval would open a gate nobody cleared."""
        assert parse_review_event(review_payload("commented")) is None

    def test_unknown_state_is_ignored(self):
        assert parse_review_event(review_payload("pending")) is None

    def test_non_requirement_branch_parses_but_carries_no_artifact(self):
        e = parse_review_event(review_payload("approved", branch="main"))
        assert e.action == "approve" and e.artifact_id is None


class TestConfiguration:
    def test_absent_repo_disables_github(self, monkeypatch):
        monkeypatch.delenv("GITHUB_REPO", raising=False)
        assert load_config() is None

    def test_repo_enables_it(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPO", "stark/governance")
        cfg = load_config()
        assert cfg.repo == "stark/governance" and cfg.owner == "stark"

    def test_no_credentials_raises_rather_than_silently_failing(self):
        with pytest.raises(GitHubNotConfigured):
            access_token(GitHubConfig(repo="a/b"))

    def test_token_is_used_when_no_app_is_configured(self):
        assert access_token(GitHubConfig(repo="a/b", token="ghp_x")) == "ghp_x"


class TestCodeowners:
    def test_maps_tiers_and_departments_to_teams(self):
        out = codeowners("stark", ["Development", "Sales & Marketing"])
        assert "@stark/tier-vp" in out
        assert "/requirements/*/workitems/development/" in out
        assert "@stark/tl-sales-marketing" in out

    def test_prompt_changes_need_an_approver(self):
        """SoW 4.0 requires prompt templates to carry change history and approver identity."""
        assert "/prompts/" in codeowners("stark", [])


from app.routes.webhooks import _action_for, _may_act


class TestActionMapping:
    """A pull-request approval means different things at different stages."""

    def test_approval_at_pending_approval_is_an_approve(self):
        assert _action_for("pending_approval", "approve") == "approve"

    def test_approval_during_fsd_review_sends_the_fsd_onward(self):
        """fsd_review does not accept 'approve' at all — it accepts sendFsdToClient."""
        assert _action_for("fsd_review", "approve") == "sendFsdToClient"

    def test_approval_at_the_client_loop_and_final_gate(self):
        assert _action_for("fsd_pending_client", "approve") == "approveFsd"
        assert _action_for("fsd_final_approval", "approve") == "giveFinalFsdApproval"

    def test_rejection_only_applies_where_the_stage_models_it(self):
        assert _action_for("pending_approval", "reject") == "reject"
        assert _action_for("pending_approval", "revise") == "requestRevision"
        assert _action_for("fsd_review", "reject") is None

    def test_unknown_stage_yields_no_action(self):
        assert _action_for("approved", "approve") is None


class TestWebhookAuthorization:
    """Must mirror state_machine.transition, not invent a parallel rule."""

    def _artifact(self, stage, index=0):
        return {
            "currentStage": stage,
            "approvalChain": [
                {"approverTiers": ["md"], "approvedBy": []},
                {"approverTiers": ["vp"], "approvedBy": []},
            ],
            "currentApprovalIndex": index,
        }

    def test_pending_approval_follows_the_current_chain_step(self):
        art = self._artifact("pending_approval", index=1)
        assert _may_act(art, "brd", "vp")
        assert not _may_act(art, "brd", "md")

    def test_fsd_loop_uses_the_gate_zero_pool_not_the_current_index(self):
        art = self._artifact("fsd_review", index=1)
        assert _may_act(art, "brd", "md")
        assert not _may_act(art, "brd", "vp")

    def test_client_loop_admits_no_tier(self):
        """Originator-only; nobody can stand in for them via a review."""
        art = self._artifact("fsd_pending_client", index=1)
        assert not _may_act(art, "brd", "md")
        assert not _may_act(art, "brd", "vp")

    def test_exhausted_chain_admits_nobody(self):
        assert not _may_act(self._artifact("pending_approval", index=2), "brd", "vp")
