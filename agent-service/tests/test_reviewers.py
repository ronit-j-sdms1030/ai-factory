"""Reviewer resolution and the GitHub account mapping.

A gate with nobody assigned looks healthy and waits forever, so the important
cases here are the ones where resolution comes up empty.
"""

import pytest

from app import reviewers


class FakeUsers:
    def __init__(self, docs): self.docs = docs
    def find(self, query, projection=None):
        tiers = (query.get("tierId") or {}).get("$in", [])
        want_mapped = "$nin" in (query.get("githubLogin") or {})
        for d in self.docs:
            if d.get("tierId") not in tiers or d.get("isClient"):
                continue
            has = bool(d.get("githubLogin"))
            if want_mapped and has:
                yield d
            elif not want_mapped and not has:
                yield d


@pytest.fixture
def users(monkeypatch):
    docs = [
        {"tierId": "md", "email": "md@x.com", "githubLogin": "md-gh", "isClient": False},
        {"tierId": "ceo", "email": "ceo@x.com", "isClient": False},              # unmapped
        {"tierId": "vp", "email": "vp@x.com", "githubLogin": "vp-gh", "isClient": False},
        {"tierId": "tl", "email": "tl@x.com", "githubLogin": "tl-gh", "isClient": False},
    ]
    monkeypatch.setattr(reviewers.db, "users", lambda: FakeUsers(docs))
    return docs


class TestTeamSlugs:
    def test_stage_maps_to_tier_teams(self):
        assert reviewers.team_slugs("brd") == ["tier-md", "tier-ceo", "tier-vp"]

    def test_ui_and_workitems_are_vp_gates(self):
        assert reviewers.team_slugs("ui") == ["tier-vp"]
        assert reviewers.team_slugs("workitems") == ["tier-vp"]

    def test_unknown_stage_yields_nothing(self):
        assert reviewers.team_slugs("nope") == []

    def test_department_slug_handles_ampersands(self):
        assert reviewers.team_slug_for_department("Sales & Marketing") == "tl-sales-marketing"


class TestLoginResolution:
    def test_returns_only_mapped_approvers_for_the_stage(self, users):
        assert reviewers.github_logins_for("workitems") == ["vp-gh"]

    def test_a_tier_without_a_mapping_is_simply_absent(self, users):
        """CEO has no githubLogin, so only MD can be requested for a requirement."""
        assert reviewers.github_logins_for("requirement") == ["md-gh"]

    def test_the_author_is_excluded(self, users):
        """GitHub rejects requesting a review from the pull request's own author."""
        assert reviewers.github_logins_for("workitems", exclude_login="vp-gh") == []

    def test_unrelated_tiers_are_not_included(self, users):
        assert "tl-gh" not in reviewers.github_logins_for("brd")


class TestUnmappedReporting:
    def test_names_approvers_who_cannot_be_reached(self, users):
        """Surfacing this is the difference between a clear error and a silent stall."""
        assert reviewers.unmapped_reviewers("requirement") == ["ceo@x.com"]

    def test_fully_mapped_stage_reports_nothing(self, users):
        assert reviewers.unmapped_reviewers("workitems") == []
