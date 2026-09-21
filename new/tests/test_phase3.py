"""Phase 3 — Sprint 0, tickets, tests before code, Gate 4 AND-sign."""

from __future__ import annotations

from pathlib import Path

import pytest

import department_routing as routing
from phase1 import directory
from phase1.platform import Phase1
from tests.test_phase2 import reach_design


def reach_plan(p1: Phase1) -> str:
    rid = reach_design(p1)
    p1.decide(rid, directory.actor("u-arch"), "approve")
    p1.decide(rid, directory.actor("u-ux"), "approve")
    p1.decide(rid, directory.actor("u-ba"), "approve")
    return rid


def test_gate_3_writes_plan_tickets_and_tests(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    run = p1.get(rid)
    assert run["phase"] == "awaiting_gate_4"
    assert run["sprint0"]["status"] == "green"
    assert len(run["tickets"]) >= 5
    assert all(ticket["paths"] for ticket in run["tickets"])
    assert len(run["tests"]) == 8
    assert any(case.get("critical") for case in run["tests"])
    assert run["tests"][0]["framework"] == "Vitest"
    assert p1.git.read(f"requirements/{rid}/plan/plan.md", f"plan/{rid}").startswith("# Plan")
    assert p1.git.exists(f"requirements/{rid}/plan/sprint0/ci.yml", f"plan/{rid}")
    assert p1.git.exists(f"requirements/{rid}/plan/sprint0/CODEOWNERS", f"plan/{rid}")
    assert p1.git.exists(f"requirements/{rid}/plan/sprint0/allowlist.txt", f"plan/{rid}")
    assert run["overview"]
    assert run["team_reports"]


def test_gate_4_needs_every_named_stream_signer(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    first = p1.decide(rid, directory.actor("u-tl"), "approve")
    assert first["decision"]["satisfied"] is False
    last = p1.decide(rid, directory.actor("u-sl-qa"), "approve")
    assert last["phase"] == "awaiting_gate_5"
    assert last["awaiting"] == 5
    assert last["build"]["branches"]
    assert p1.git.exists(f"requirements/{rid}/plan/plan.md", "main")


def test_originator_cannot_sign_gate_4(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    with pytest.raises(Exception, match="no role entitled"):
        p1.decide(rid, directory.actor("u-requester"), "approve")


def test_brownfield_skips_sprint_0(tmp_path: Path):
    p1 = Phase1(tmp_path)
    run = p1.submit(directory.actor("u-requester"), "brownfield_change", "Add cancel.")
    rid = run["requirement"]["id"]
    from tests.test_phase1 import finish_intake

    finish_intake(p1, rid)
    p1.decide(rid, directory.actor("u-po"), "approve")
    p1.decide(rid, directory.actor("u-bo"), "approve")
    p1.decide(rid, directory.actor("u-ctl"), "approve")
    p1.decide(rid, directory.actor("u-arch"), "approve")
    p1.decide(rid, directory.actor("u-ux"), "approve")
    p1.decide(rid, directory.actor("u-ba"), "approve")
    planned = p1.get(rid)
    assert planned["sprint0"]["status"] == "skipped"
    assert not p1.git.exists(f"requirements/{rid}/plan/sprint0/ci.yml", f"plan/{rid}")


def test_entity_spelling_is_repaired():
    repaired, collisions = routing.repair_entities(["Return_Items", "ReturnItems", "Booking"])
    assert "ReturnItems" in repaired
    assert "Booking" in repaired
    assert collisions
