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
    # Screens wait until BA signs — Gate 2 only locks architecture.
    assert run["screens"] == []
    architecture = p1.git.read(f"requirements/{rid}/design/architecture.md", f"design/{rid}")
    assert "## Data model" in architecture
    assert "## API contracts" in architecture
    assert "Entra SSO" not in architecture
    assert "/api/" in architecture
    assert "ADR-001" in p1.git.read(
        f"requirements/{rid}/design/adrs/ADR-001.md", f"design/{rid}"
    )
    assert "ADR-002" in p1.git.read(
        f"requirements/{rid}/design/adrs/ADR-002.md", f"design/{rid}"
    )
    assert "cookie" in p1.git.read(
        f"requirements/{rid}/design/adrs/ADR-003.md", f"design/{rid}"
    ).lower()
    assert (run.get("preview_host") or {}).get("status") == "substitute"


def test_originator_cannot_sign_gate_3(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_design(p1)
    with pytest.raises(Exception, match="cannot approve"):
        p1.decide(rid, directory.actor("u-requester"), "approve")


def test_gate_3_is_architect_then_ba_then_ui(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_design(p1)
    with pytest.raises(Exception, match="requires architect"):
        p1.decide(rid, directory.actor("u-ba"), "approve")
    with pytest.raises(Exception, match="requires architect"):
        p1.decide(rid, directory.actor("u-ux"), "approve")
    first = p1.decide(rid, directory.actor("u-arch"), "approve")
    assert first["decision"]["satisfied"] is False
    assert first["screens"] == []
    sha_after_arch = p1.get(rid)["requirement"]["artefacts"]["design"]["sha"]
    with pytest.raises(Exception, match="requires business_analyst"):
        p1.decide(rid, directory.actor("u-ux"), "approve")
    second = p1.decide(rid, directory.actor("u-ba"), "approve")
    assert second["decision"]["satisfied"] is False
    assert second["screens"], "screens appear after BA signs"
    assert p1.get(rid)["requirement"]["artefacts"]["design"]["sha"] == sha_after_arch, (
        "screen gen must not re-record design SHA (would vacate Architect signature)"
    )
    last = p1.decide(rid, directory.actor("u-ux"), "approve")
    assert last["phase"] == "awaiting_gate_4"
    assert last["awaiting"] == 4
    assert last["tickets"]
    assert last["sprint0"]["status"] == "green"
    assert p1.git.exists(f"requirements/{rid}/design/architecture.md", "main")


def test_gate_3_needs_all_three_roles(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_design(p1)
    first = p1.decide(rid, directory.actor("u-arch"), "approve")
    assert first["decision"]["satisfied"] is False
    p1.decide(rid, directory.actor("u-ba"), "approve")
    last = p1.decide(rid, directory.actor("u-ux"), "approve")
    assert last["phase"] == "awaiting_gate_4"
    assert last["awaiting"] == 4
    assert last["tickets"]
    assert last["sprint0"]["status"] == "green"
    assert p1.git.exists(f"requirements/{rid}/design/architecture.md", "main")


def test_gate_3_revise_screens_leaves_architecture(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_design(p1)
    p1.decide(rid, directory.actor("u-arch"), "approve")
    p1.decide(rid, directory.actor("u-ba"), "approve")
    before = p1.git.read(f"requirements/{rid}/design/architecture.md", f"design/{rid}")
    assert p1.get(rid)["screens"], "screens exist after BA for UI revise"
    revised = p1.decide(
        rid, directory.actor("u-ux"), "revise", reason="Send the screens back."
    )
    assert revised["phase"] == "awaiting_gate_3"
    after = p1.git.read(f"requirements/{rid}/design/architecture.md", f"design/{rid}")
    assert after == before


def test_gate_3_revise_architecture_keeps_screens(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_design(p1)
    # Architecture-only at Gate 3 open — revise architecture stays screen-empty.
    assert p1.get(rid)["screens"] == []
    revised = p1.decide(
        rid, directory.actor("u-arch"), "revise", reason="The stack lock needs a clearer ADR."
    )
    assert revised["screens"] == []
    assert revised["phase"] == "awaiting_gate_3"
