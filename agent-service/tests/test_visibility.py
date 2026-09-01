"""Role-based visibility and redaction.

These cover the clauses of the artifact list query and the redaction rules —
the logic that decides which requirements each role can see, and how much of
each. Pure functions over fixture documents, so no database or network.

A regression here would silently expose another department's work or hide a
requirement from its approver, neither of which surfaces as an error.
"""

from app.routes.artifacts import _redact, _visible_team_reports


def artifact(**overrides):
    base = {
        "_id": "a1",
        "title": "T",
        "originator": {"userId": "orig", "tierId": "md"},
        "currentStage": "approved",
        "teamReports": [{"team": "Development"}, {"team": "QA"}, {"team": "AI"}],
        "teamReportEditHistory": [
            {"department": "Development", "content": "dev note"},
            {"department": "QA", "content": "qa note"},
        ],
        "discussionMessages": [{"userId": "u9", "message": "hello"}],
        "discussionShares": [],
        "approvalChain": [{"approverTiers": ["md"], "approvedBy": []}],
    }
    return {**base, **overrides}


def user(tier, *, department=None, is_client=False, uid="u1"):
    return {"id": uid, "tierId": tier, "department": department, "isClient": is_client}


class TestTeamReportVisibility:
    def test_tl_sees_only_their_own_department(self):
        got = _visible_team_reports(artifact(), user("tl", department="Development"))
        assert [r["team"] for r in got] == ["Development"]

    def test_vp_sees_none_here(self):
        """VP reads team data through the code-generation endpoints instead."""
        assert _visible_team_reports(artifact(), user("vp")) == []

    def test_md_sees_none_here(self):
        assert _visible_team_reports(artifact(), user("md")) == []

    def test_client_sees_none(self):
        assert _visible_team_reports(artifact(), user(None, is_client=True)) == []

    def test_tl_without_a_department_sees_none(self):
        assert _visible_team_reports(artifact(), user("tl")) == []

    def test_explicit_share_grants_another_department(self):
        art = artifact(discussionShares=[{"toUserId": "u1", "sharedTeam": "QA"}])
        got = _visible_team_reports(art, user("tl", department="Development"))
        assert sorted(r["team"] for r in got) == ["Development", "QA"]

    def test_a_share_to_someone_else_grants_nothing(self):
        art = artifact(discussionShares=[{"toUserId": "someone-else", "sharedTeam": "QA"}])
        got = _visible_team_reports(art, user("tl", department="Development"))
        assert [r["team"] for r in got] == ["Development"]


class TestEditHistoryRedaction:
    def test_tl_sees_only_their_own_department_notes(self):
        out = _redact(artifact(), user("tl", department="Development"))
        assert [e["department"] for e in out["teamReportEditHistory"]] == ["Development"]

    def test_non_tl_sees_no_edit_history(self):
        assert _redact(artifact(), user("vp"))["teamReportEditHistory"] == []


class TestDiscussionRedaction:
    def test_leadership_sees_an_active_thread(self):
        assert len(_redact(artifact(), user("vp"))["discussionMessages"]) == 1

    def test_leadership_sees_nothing_when_no_thread_exists(self):
        art = artifact(discussionMessages=[])
        assert _redact(art, user("md"))["discussionMessages"] == []

    def test_tl_on_the_requirement_sees_the_thread(self):
        out = _redact(artifact(), user("tl", department="Development"))
        assert len(out["discussionMessages"]) == 1

    def test_tl_from_an_uninvolved_department_sees_nothing(self):
        art = artifact(teamReports=[{"team": "QA"}])
        out = _redact(art, user("tl", department="Development"))
        assert out["discussionMessages"] == []

    def test_client_never_sees_the_internal_thread(self):
        assert _redact(artifact(), user(None, is_client=True))["discussionMessages"] == []


class TestRedactionShape:
    def test_object_id_is_stringified_for_json(self):
        assert _redact(artifact(), user("vp"))["_id"] == "a1"

    def test_redaction_does_not_mutate_the_source_document(self):
        art = artifact()
        _redact(art, user("vp"))
        assert len(art["teamReports"]) == 3
        assert len(art["discussionMessages"]) == 1
