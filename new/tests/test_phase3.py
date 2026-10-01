"""Phase 3 — Sprint 0, tickets, tests before code, Gate 4 AND-sign."""

from __future__ import annotations

from pathlib import Path

import pytest

import department_routing as routing
from phase1 import directory
from phase1.platform import Phase1
from phase3 import decomposer, qa, trace
from stack_profiles import NODE
from tests.test_phase2 import reach_design

ATTENDANCE_BRD = """
## 8. Functional requirements

### REQ-9-R01 — Clock in at the gate
### REQ-9-R02 — Export this week's timesheet

## 11. Page behaviour

- **Timesheet**: export this week's hours

## 12. Data model

- **Employee:** id, name
- **AttendanceEvent:** id, employee_id, recorded_at
"""


def reach_plan(p1: Phase1) -> str:
    rid = reach_design(p1)
    p1.decide(rid, directory.actor("u-arch"), "approve")
    p1.decide(rid, directory.actor("u-ba"), "approve")
    p1.decide(rid, directory.actor("u-ux"), "approve")
    return rid


def test_gate_3_writes_plan_tickets_and_tests(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    run = p1.get(rid)
    assert run["phase"] == "awaiting_gate_4"
    assert run["sprint0"]["status"] == "green"
    assert len(run["tickets"]) >= 5
    assert all(ticket["paths"] for ticket in run["tickets"])
    req_ids = [row["id"] for row in trace.requirements(run["brd_text"])]
    assert req_ids
    for req_id in req_ids:
        assert any(case["criterion"] == req_id for case in run["tests"]), req_id
        assert any(req_id in (t.get("trace") or []) for t in run["tickets"]), req_id
    assert any(case.get("critical") for case in run["tests"])
    assert run["tests"][0]["framework"] == "Vitest"
    assert p1.git.read(f"requirements/{rid}/plan/plan.md", f"plan/{rid}").startswith("# Plan")
    assert p1.git.exists(f"requirements/{rid}/plan/sprint0/ci.yml", f"plan/{rid}")
    assert p1.git.exists(f"requirements/{rid}/plan/sprint0/CODEOWNERS", f"plan/{rid}")
    assert p1.git.exists(f"requirements/{rid}/plan/sprint0/allowlist.txt", f"plan/{rid}")
    assert run["overview"]
    assert run["team_reports"]
    teams = {row["team"] for row in run["team_reports"]}
    assert "development" in teams
    assert all(row.get("integratesInto") for row in run["team_reports"])
    ai = next((t for t in run["tickets"] if t.get("id", "").endswith("-AI1")), None)
    assert (ai is not None) == trace.wants_ai(run["brd_text"])
    if ai is not None:
        assert "ai" in teams
        assert ai["department"] == "ai"
        assert any(path.startswith("src/ai") or path.startswith("app/ai") for path in ai["paths"])
    titles = " ".join(ticket["title"] for ticket in run["tickets"])
    assert any(ticket.get("screen") for ticket in run["tickets"])
    assert any(case.get("criterion") == "ui" and "reachable" in case["name"] for case in run["tests"])


def sign_gate_4(p1: Phase1, rid: str):
    """Tech lead + Development, AI, and QA stream leads."""
    p1.decide(rid, directory.actor("u-tl"), "approve")
    p1.decide(rid, directory.actor("u-sl-dev"), "approve")
    p1.decide(rid, directory.actor("u-sl-ai"), "approve")
    return p1.decide(rid, directory.actor("u-sl-qa"), "approve")


def test_gate_4_needs_every_named_stream_signer(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    first = p1.decide(rid, directory.actor("u-tl"), "approve")
    assert first["decision"]["satisfied"] is False
    p1.decide(rid, directory.actor("u-sl-dev"), "approve")
    p1.decide(rid, directory.actor("u-sl-ai"), "approve")
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
    p1.decide(rid, directory.actor("u-ba"), "approve")
    p1.decide(rid, directory.actor("u-ux"), "approve")
    planned = p1.get(rid)
    assert planned["sprint0"]["status"] == "skipped"
    assert not p1.git.exists(f"requirements/{rid}/plan/sprint0/ci.yml", f"plan/{rid}")


def test_decomposer_follows_this_brd_not_booking():
    screens = [{"name": "Timesheet"}]
    tickets = decomposer.tickets("REQ-9", ATTENDANCE_BRD, screens, NODE)
    titles = " ".join(ticket["title"] for ticket in tickets).lower()
    assert "availability" not in titles
    assert "booking" not in titles
    assert any("Employee API" in ticket["title"] for ticket in tickets)
    assert any(ticket.get("screen") == "Timesheet" for ticket in tickets)
    assert not any(ticket["id"].endswith("-AI1") for ticket in tickets)
    for req_id in ("REQ-9-R01", "REQ-9-R02"):
        assert any(req_id in ticket["trace"] for ticket in tickets[1:]), req_id


def test_ai_ticket_only_when_the_brd_asks_for_ai():
    brd = ATTENDANCE_BRD + "\n### REQ-9-R03 — Recommend a shift from past attendance\n"
    tickets = decomposer.tickets("REQ-9", brd, [{"name": "Timesheet"}], NODE)
    ai = next(ticket for ticket in tickets if ticket["id"].endswith("-AI1"))
    assert ai["department"] == "ai"
    assert "REQ-9-R03" in ai["trace"]


def test_screen_ticket_waits_for_its_record_api_and_owns_its_folder():
    screens = [{"name": "Timesheet"}]
    tickets = decomposer.tickets("REQ-9", ATTENDANCE_BRD, screens, NODE)
    screen = next(ticket for ticket in tickets if ticket.get("screen") == "Timesheet")
    assert screen["paths"] == ["src/ui/Timesheet/**"]
    api_paths = [tuple(t["paths"]) for t in tickets if t.get("entity") and not t.get("screen")]
    assert len(api_paths) == len(set(api_paths))
    parent = next(t for t in tickets if t["id"] == screen["depends_on"][0])
    assert parent["title"].endswith("API")


def test_qa_uses_approved_screens_and_this_brd():
    screens = [{"name": "Timesheet"}]
    cases = qa.cases(ATTENDANCE_BRD, screens, NODE)
    assert [case["criterion"] for case in cases[:2]] == ["REQ-9-R01", "REQ-9-R02"]
    assert any(case.get("critical") for case in cases)
    assert any("Timesheet" in case["name"] and case["criterion"] == "ui" for case in cases)
    assert not any("book a room" in case["name"] for case in cases)
    assert not any("overlapping bookings" in case["name"] for case in cases)


def test_booking_brd_keeps_exclusion_and_overlap_critical():
    brd = ATTENDANCE_BRD + "\nStop double-booking rooms.\n## 12. Data model\n- **Room:** id, name\n- **Booking:** id, room_id, starts_at, ends_at\n"
    tickets = decomposer.tickets("REQ-9", brd, [{"name": "BookRoom"}], NODE)
    assert any("exclusion" in ticket["title"] for ticket in tickets)
    cases = qa.cases(brd, [{"name": "BookRoom"}], NODE)
    critical = next(case for case in cases if case.get("critical"))
    assert "overlap" in critical["criterion"]


def test_ai_adapter_ticket_routes_to_ai():
    assert (
        routing.department_for("prompt + inference adapter for screen suggestions")
        == "ai"
    )


def test_entity_spelling_is_repaired():
    repaired, collisions = routing.repair_entities(["Return_Items", "ReturnItems", "Booking"])
    assert "ReturnItems" in repaired
    assert "Booking" in repaired
    assert collisions
