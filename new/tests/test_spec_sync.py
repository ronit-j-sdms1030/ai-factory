"""Startup fills every thin scope and BRD, then leaves them alone."""

from __future__ import annotations

from pathlib import Path

import requirement as R
from phase1 import brd, directory
from phase1.platform import Phase1

THIN = """
# Scope report — REQ-0007

## Users
Reception staff using a shared login

## What happens today
A paper book is used and names get lost.

## In scope
- Shared authentication for reception staff to access the system via a web browser
- Web form to capture visitor name and the name of the person they are visiting
- Automatic recording of the current system time as 'Time In' when a visitor is registered
- A dashboard view displaying a list of all visitors currently marked as inside the building
- A 'Sign Out' action for each visitor to record their departure
- Real-time filtering of the visitor list to exclude individuals who have already signed out

## Out of scope
- Historical visitor logs
- Individual user accounts

## Success
Front Desk Visitor Book

## Open questions
- Who can correct a missed check-in?
"""


def test_sync_fills_every_thin_specification_once(tmp_path: Path):
    platform = Phase1(tmp_path)
    record = R.open_requirement(
        "REQ-0007", "full_governance", directory.actor("u-requester")
    )
    platform.store.put(
        "REQ-0007",
        {"requirement": record.dump(), "display_title": "Front Desk Visitor Book"},
        at="2026-01-01T00:00:00Z",
    )
    empty = R.open_requirement(
        "REQ-0008", "full_governance", directory.actor("u-requester")
    )
    platform.store.put(
        "REQ-0008",
        {"requirement": empty.dump()},
        at="2026-01-01T00:00:00Z",
    )
    platform.git.commit_files(
        "scope/REQ-0007",
        {"requirements/REQ-0007/scope/scope-report.md": THIN},
        "thin scope",
        author="platform",
        email="platform@local",
    )
    platform.git.commit_files(
        "brd/REQ-0007",
        {
            "requirements/REQ-0007/brd/brd.md": (
                "# Functional specification — REQ-0007\n\n"
                "### REQ-0007-R01 — old\n\nA short draft.\n"
            )
        },
        "thin brd",
        author="platform",
        email="platform@local",
    )

    updated = platform.sync_specifications()

    assert updated == ["REQ-0007"]
    scope = platform.git.read(
        "requirements/REQ-0007/scope/scope-report.md", "scope/REQ-0007"
    )
    assert "## Screens" in scope
    assert "**VisitorForm**" in scope
    assert "Fields: Visitor name" in scope
    assert "missed check-in" not in scope.lower()
    assert not brd.scope_needs_fill(scope)
    specification = platform.git.read(
        "requirements/REQ-0007/brd/brd.md", "brd/REQ-0007"
    )
    assert "**VisitorForm**" in specification
    assert "**SignIn**" in specification
    assert "**VisitorList**" in specification
    assert "Visitor name" in specification
    assert "REQ-0007-R01" in specification
    assert "REQ-0007-R06" in specification
    saved = platform.store.get("REQ-0007") or {}
    assert int((saved.get("requirement") or {}).get("allocated") or 0) >= 6

    assert platform.sync_specifications() == []
    again = platform.git.read("requirements/REQ-0007/brd/brd.md", "brd/REQ-0007")
    assert again == specification


def test_return_to_scope_gate_regenerates_the_report(tmp_path: Path):
    platform = Phase1(tmp_path)
    record = R.open_requirement(
        "REQ-0007", "full_governance", directory.actor("u-requester")
    )
    record.record_artefact(
        "scope",
        "old",
        model="deterministic/intake",
        model_version="phase1-1",
        prompt_version="intake.skill.md",
    )
    record.record_artefact(
        "brd",
        "old-brd",
        model="deterministic/brd",
        model_version="phase1-1",
        prompt_version="brd.template.md",
    )
    record.cleared.add(1)
    platform.store.put(
        "REQ-0007",
        {
            "requirement": record.dump(),
            "display_title": "Front Desk Visitor Book",
            "request_text": "Front Desk Visitor Book",
            "messages": [],
            "budget": {"asked": 0, "minimum": 0, "maximum": 10, "calls": 0},
        },
        at="2026-01-01T00:00:00Z",
    )
    platform.git.commit_files(
        "scope/REQ-0007",
        {"requirements/REQ-0007/scope/scope-report.md": THIN},
        "thin scope",
        author="platform",
        email="platform@local",
    )
    platform.git.commit_files(
        "brd/REQ-0007",
        {"requirements/REQ-0007/brd/brd.md": "# Functional specification\n"},
        "thin brd",
        author="platform",
        email="platform@local",
    )

    result = platform.return_to_scope_gate("REQ-0007")

    assert result["phase"] == "awaiting_gate_1"
    assert result["awaiting"] == 1
    assert "## Screens" in result["scope_text"]
    assert "**VisitorForm**" in result["scope_text"]
    assert result["brd_text"] == ""
    assert "brd" not in (result["requirement"]["artefacts"])
    assert 1 not in (result["requirement"]["cleared"])
    assert "brd/REQ-0007" not in platform.git._run(["git", "branch", "--list"])
