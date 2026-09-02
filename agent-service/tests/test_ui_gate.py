"""GATE 2 — the screens have to be approved before the split runs.

SoW 12.0: "code generation for the affected scope cannot start until UI
approval is recorded." Generating the decomposition regardless would make
that approval decorative, because the packages a team lead executes against
would already exist, scoped to an interface nobody had signed off.
"""

from __future__ import annotations

from app.routes import artifacts as routes

VP = {"id": "u-vp", "tierId": "vp", "isClient": False}
MD = {"id": "u-md", "tierId": "md", "isClient": False}
TL = {"id": "u-tl", "tierId": "tl", "isClient": False, "department": "Development"}
CLIENT = {"id": "u-c", "tierId": None, "isClient": True}

WITH_SCREENS = {
    "currentStage": "approved",
    "ui": {"screens": [{"name": "Dashboard", "route": "/", "source": "x"}]},
}


class TestWhoDecides:
    def test_the_vp_can_approve(self):
        assert routes._may_approve_ui(WITH_SCREENS, VP)

    def test_nobody_else_can(self):
        assert not routes._may_approve_ui(WITH_SCREENS, MD)
        assert not routes._may_approve_ui(WITH_SCREENS, TL)
        assert not routes._may_approve_ui(WITH_SCREENS, CLIENT)

    def test_there_is_nothing_to_approve_before_the_screens_exist(self):
        assert not routes._may_approve_ui({"currentStage": "approved", "ui": None}, VP)

    def test_an_unapproved_requirement_has_no_ui_gate_yet(self):
        assert not routes._may_approve_ui({**WITH_SCREENS, "currentStage": "fsd_review"}, VP)


class TestTheGateBlocks:
    """_pending_phase is what decides whether the split job starts."""

    def test_the_split_does_not_run_before_approval(self):
        artifact = {**WITH_SCREENS, "detailedReport": {"objective": "x"}}
        assert routes._pending_phase(artifact, None) is None

    def test_approval_releases_it(self):
        artifact = {**WITH_SCREENS, "detailedReport": {"objective": "x"}, "uiApprovedAt": "now"}
        assert routes._pending_phase(artifact, None) == "workitems"

    def test_screens_are_still_generated_before_the_gate(self):
        """The gate is on the split, not on producing the thing being reviewed."""
        artifact = {"currentStage": "approved", "detailedReport": {"objective": "x"}}
        assert routes._pending_phase(artifact, None) == "ui"


class TestGateState:
    def test_it_reports_blocking_while_unapproved(self):
        gate = routes._ui_gate(WITH_SCREENS, VP)
        assert gate["blocksSplit"] is True
        assert gate["canApprove"] is True
        assert gate["approvedAt"] is None

    def test_it_stops_blocking_once_approved(self):
        gate = routes._ui_gate({**WITH_SCREENS, "uiApprovedAt": "now"}, VP)
        assert gate["blocksSplit"] is False

    def test_it_does_not_block_before_screens_exist(self):
        gate = routes._ui_gate({"currentStage": "approved", "ui": None}, VP)
        assert gate["blocksSplit"] is False


class TestEditing:
    ORIGINATOR = {"id": "u-md", "tierId": "md", "isClient": False}
    OWNED = {**WITH_SCREENS, "originator": {"userId": "u-md", "tierId": "md"}}

    def test_the_vp_may_edit(self):
        assert routes._may_edit_ui(self.OWNED, VP)

    def test_the_originator_may_edit_their_own(self):
        assert routes._may_edit_ui(self.OWNED, self.ORIGINATOR)

    def test_a_team_lead_may_not(self):
        """A TL rewriting the interface after the split would change what the
        other departments were scoped against, without passing the gate again."""
        assert not routes._may_edit_ui(self.OWNED, TL)

    def test_a_client_may_not(self):
        assert not routes._may_edit_ui(self.OWNED, CLIENT)

    def test_there_is_nothing_to_edit_before_the_screens_exist(self):
        assert not routes._may_edit_ui({"currentStage": "approved", "ui": None}, VP)
