"""Phase 2 — locked stack, screens, coverage repair, Gate 3."""

from __future__ import annotations

from pathlib import Path

import pytest

from phase1 import directory
from phase1.platform import Phase1
from tests.test_phase1 import finish_intake


def reach_design(p1: Phase1) -> str:
    run = p1.submit(directory.actor("u-requester"), "full_governance", "Book meeting rooms.")
    rid = run["requirement"]["id"]
    finish_intake(p1, rid)
    p1.decide(rid, directory.actor("u-po"), "approve")
    p1.decide(rid, directory.actor("u-bo"), "approve")
    p1.decide(rid, directory.actor("u-ctl"), "approve")
    return rid


def test_gate_2_lock_writes_architecture_and_screens(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_design(p1)
    run = p1.get(rid)
    assert run["phase"] == "awaiting_gate_3"
    assert run["stack_profile"]["id"] == "node"
    assert run["stack_profile"]["tests"] == "Vitest"
    names = {s["name"] for s in run["screens"]}
    assert "AdminCancel" in names
    assert "AdminCancel" in run["coverage"]["repaired"]
    assert "ADR-001" in p1.git.read(
        f"requirements/{rid}/design/adrs/ADR-001.md", f"design/{rid}"
    )
    assert run["preview_url"] == f"/preview/{rid}"


def test_originator_cannot_sign_gate_3(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_design(p1)
    with pytest.raises(Exception, match="cannot approve"):
        p1.decide(rid, directory.actor("u-requester"), "approve")


def test_gate_3_needs_all_three_roles(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_design(p1)
    first = p1.decide(rid, directory.actor("u-arch"), "approve")
    assert first["decision"]["satisfied"] is False
    p1.decide(rid, directory.actor("u-ux"), "approve")
    last = p1.decide(rid, directory.actor("u-ba"), "approve")
    assert last["phase"] == "awaiting_gate_4"
    assert last["awaiting"] == 4
    assert last["tickets"]
    assert last["sprint0"]["status"] == "green"
    assert p1.git.exists(f"requirements/{rid}/design/architecture.md", "main")


def test_gate_3_revise_rewrites_design(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_design(p1)
    before = p1.get(rid)["requirement"]["artefacts"]["design"]["sha"]
    revised = p1.decide(
        rid, directory.actor("u-arch"), "revise", reason="Send the screens back."
    )
    assert revised["phase"] == "awaiting_gate_3"
    assert revised["requirement"]["artefacts"]["design"]["sha"] != before
