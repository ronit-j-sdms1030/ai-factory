"""How a finished generation job writes its results back.

The job starts from a copy of the artifact taken minutes earlier. Anything
that happened in between — a comment, a revision request, another gate — is
absent from that copy, so writing it back wholesale would silently undo real
governance actions. These tests pin the narrower write that avoids it.
"""

from __future__ import annotations

import mongomock
import pytest

from app import db
from app.routes import artifacts as routes


@pytest.fixture
def artifacts(monkeypatch):
    fake = mongomock.MongoClient().db.artifacts
    monkeypatch.setattr(db, "artifacts", lambda: fake)
    return fake


def _seed(collection, **fields) -> dict:
    doc = {"_id": "a1", "currentStage": "pending_approval", "title": "Stock write-off", **fields}
    collection.insert_one(doc)
    return dict(doc)


class TestNarrowWriteBack:
    def test_generated_fields_are_persisted(self, artifacts):
        before = _seed(artifacts)
        after = {**before, "detailedReport": {"objective": "reduce write-off loss"}}

        routes._apply_generated(before, after)

        assert artifacts.find_one({"_id": "a1"})["detailedReport"]["objective"] == "reduce write-off loss"

    def test_a_concurrent_change_is_not_clobbered(self, artifacts):
        """Someone comments while the model is running; the comment must survive."""
        before = _seed(artifacts, discussionMessages=[])
        artifacts.update_one({"_id": "a1"}, {"$set": {"discussionMessages": [{"text": "any update?"}]}})

        after = {**before, "detailedReport": {"objective": "x"}}
        routes._apply_generated(before, after)

        saved = artifacts.find_one({"_id": "a1"})
        assert saved["discussionMessages"] == [{"text": "any update?"}]
        assert saved["detailedReport"] == {"objective": "x"}

    def test_nothing_is_written_when_nothing_changed(self, artifacts):
        before = _seed(artifacts)
        artifacts.update_one({"_id": "a1"}, {"$set": {"title": "renamed by someone else"}})

        routes._apply_generated(before, dict(before))

        assert artifacts.find_one({"_id": "a1"})["title"] == "renamed by someone else"


class TestStageGuard:
    def test_the_stage_advances_when_nobody_else_moved_it(self, artifacts):
        before = _seed(artifacts)
        after = {**before, "currentStage": "fsd_review", "detailedReport": {"objective": "x"}}

        routes._apply_generated(before, after)

        assert artifacts.find_one({"_id": "a1"})["currentStage"] == "fsd_review"

    def test_a_stage_someone_else_moved_is_left_alone(self, artifacts):
        """A late write must not drag a requirement backwards through a gate.

        The generated document still lands — it is the reason the job ran — but
        the stage belongs to whoever acted most recently.
        """
        before = _seed(artifacts)
        artifacts.update_one({"_id": "a1"}, {"$set": {"currentStage": "rejected"}})

        after = {**before, "currentStage": "fsd_review", "detailedReport": {"objective": "x"}}
        routes._apply_generated(before, after)

        saved = artifacts.find_one({"_id": "a1"})
        assert saved["currentStage"] == "rejected"
        assert saved["detailedReport"] == {"objective": "x"}


class TestPendingPhase:
    """What a transition will generate, used to label the job honestly."""

    def test_clearing_an_md_gate_schedules_the_brd(self):
        artifact = {"currentStage": "pending_approval", "approvalChain": [{"approverTiers": ["md"]}]}
        assert routes._pending_phase(artifact, {"approverTiers": ["md"]}) == "brd"

    def test_approved_without_screens_schedules_the_ui(self):
        artifact = {"currentStage": "approved", "detailedReport": {"objective": "x"}}
        assert routes._pending_phase(artifact, None) == "ui"

    def test_approved_with_approved_screens_schedules_the_split(self):
        """Screens alone are not enough — GATE 2 has to have cleared."""
        artifact = {"currentStage": "approved", "detailedReport": {"objective": "x"},
                    "ui": {"screens": []}, "uiApprovedAt": "now"}
        assert routes._pending_phase(artifact, None) == "workitems"

    def test_nothing_to_do_schedules_no_job(self):
        """A gate that generates nothing must not show a spinner that never resolves."""
        artifact = {
            "currentStage": "approved",
            "detailedReport": {"objective": "x"},
            "ui": {"screens": []},
            "uiApprovedAt": "now",
            "teamReports": [{"department": "Development"}],
        }
        assert routes._pending_phase(artifact, None) is None

    def test_a_tl_gate_alone_does_not_schedule_a_brd(self):
        """Only an MD/CEO gate entitles a requirement to its BRD."""
        artifact = {"currentStage": "pending_approval", "approvalChain": [{"approverTiers": ["md"]}]}
        assert routes._pending_phase(artifact, {"approverTiers": ["tl"]}) is None


class TestSave:
    """``create`` builds its artifact in memory, so saving must also insert."""

    def test_a_new_artifact_is_persisted(self, artifacts):
        routes._save({"_id": "new1", "title": "Damaged stock write-off"})
        assert artifacts.find_one({"_id": "new1"})["title"] == "Damaged stock write-off"

    def test_an_existing_artifact_is_replaced_not_duplicated(self, artifacts):
        artifacts.insert_one({"_id": "e1", "title": "before"})
        routes._save({"_id": "e1", "title": "after"})
        assert artifacts.count_documents({"_id": "e1"}) == 1
        assert artifacts.find_one({"_id": "e1"})["title"] == "after"


class TestOnlyApprovalsClearGates:
    """A gate is cleared by approving it, not by any action that passes through.

    The JavaScript gated generation on ``action === 'approve'``; the port
    dropped that, so submitting a requirement generated and published its BRD
    before a single approver had seen it.
    """

    CHAIN = [{"approverTiers": ["md"], "approvedBy": []}, {"approverTiers": ["vp"], "approvedBy": []}]

    def test_submitting_does_not_entitle_a_requirement_to_its_brd(self):
        artifact = {"currentStage": "pending_approval", "approvalChain": self.CHAIN, "currentApprovalIndex": 0}
        assert routes._pending_phase(artifact, None) is None

    def test_approving_the_md_gate_does(self):
        artifact = {"currentStage": "pending_approval", "approvalChain": self.CHAIN, "currentApprovalIndex": 0}
        assert routes._pending_phase(artifact, self.CHAIN[0]) == "brd"

    def test_reaching_approved_still_generates_without_a_cleared_step(self):
        """giveFinalFsdApproval and approveFsd end the chain without clearing a step."""
        artifact = {"currentStage": "approved", "detailedReport": {"objective": "x"}, "approvalChain": self.CHAIN}
        assert routes._pending_phase(artifact, None) == "ui"


class TestRegenerateUi:
    """Re-running the UI agent, which had no retry path at all.

    Screens could fail — and did, twelve out of twelve — with regenerateFsd
    and regenerateTeamSplit available but nothing to re-run the UI. The
    failure was visible in the job record and not actionable.
    """

    def test_regenerate_ui_is_a_known_action(self):
        assert "regenerateUi" in routes.KNOWN_ACTIONS

    def test_it_runs_as_a_ui_job(self):
        assert routes._REGENERATION_KINDS["regenerateUi"] == "ui"

    def test_it_needs_an_fsd_to_work_from(self):
        import pytest
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            routes._start_regeneration({"_id": "a1", "detailedReport": None}, "regenerateUi")
        assert exc.value.status_code == 400

    def test_the_old_screens_are_cleared_before_regenerating(self, monkeypatch):
        """A failed rerun must not leave last attempt's screens looking current."""
        artifact = {"_id": "a1", "detailedReport": {"objective": "x"}, "ui": {"screens": [{"name": "Old"}]}}
        monkeypatch.setattr(routes, "_maybe_generate_ui", lambda a: {"uiError": "boom"})

        routes._regenerate(artifact, "regenerateUi")

        assert artifact["ui"] is None
