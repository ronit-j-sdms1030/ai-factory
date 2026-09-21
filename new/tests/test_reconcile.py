"""Missed GitHub reviews applied on tick."""

from __future__ import annotations

from pathlib import Path

from phase1 import directory
from phase1.platform import Phase1
from tests.test_phase3 import reach_plan


def test_tick_applies_github_review_without_webhook(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    p1.decide(rid, directory.actor("u-tl"), "approve")
    p1.decide(rid, directory.actor("u-sl-qa"), "approve")
    run = p1.get(rid)
    assert run["awaiting"] == 5
    ticket = run["tickets"][1]["id"]
    p1.git.list_open_pull_requests = lambda: [
        {
            "number": 11,
            "head": {"ref": f"feat/{ticket}"},
        }
    ]
    p1.git.list_reviews = lambda number: [
        {
            "id": 99,
            "state": "APPROVED",
            "user": {"login": "u-se"},
        }
    ]
    applied = p1.tick()
    assert rid in applied
    assert p1.get(rid)["awaiting"] == 6
    assert p1.tick() == []
