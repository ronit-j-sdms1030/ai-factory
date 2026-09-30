"""BRD screen inventory — one list in Page behaviour, never under ### REQ."""

from __future__ import annotations

from pathlib import Path

from phase1 import brd
from phase2 import coverage

TEMPLATE = (Path(__file__).resolve().parents[1] / "skills" / "brd.template.md").read_text()

NESTED = """
# Functional specification — REQ-9

## 8. Functional requirements

### REQ-9-R01 — Sign in

**Source:** approved scope.

- **LoginScreen**: Auth entry again.
- **Given** a named user is signed in on a browser

### REQ-9-R02 — Loan desk

- **LoanManagement**: Checkout again.

## 11. Page behaviour

Intro stays.

- **LoginScreen**: Auth entry.
- **StaffDashboard**: Counts and chart.
- **LoanManagement**: Checkout.
- **LoginScreen**: Auth entry duplicate.

### REQ-9-R01 — wrongly nested

- **LoginScreen**: must not count

## 12. Data model

- **Book:** id, title
"""


def test_normalize_strips_nested_screens_and_dedupes_inventory():
    cleaned = brd.normalize_screen_inventory(NESTED)
    assert "- **LoginScreen**: Auth entry again." not in cleaned
    assert "- **LoanManagement**: Checkout again." not in cleaned
    assert "- **Given** a named user is signed in on a browser" in cleaned
    assert "### REQ-9-R01 — wrongly nested" not in cleaned
    pages = coverage.pages_from_brd(cleaned)
    assert [p["id"] for p in pages] == [
        "LoginScreen",
        "StaffDashboard",
        "LoanManagement",
    ]


def test_critique_flags_nested_screen_bullets():
    findings = brd.critique(NESTED, skill="")
    assert any("Page behaviour" in f for f in findings)


def test_draft_brd_has_flat_unique_page_section():
    scope = """
## Users
- Staff

## What happens today
- Paper

## In scope
- Staff sign in
- Staff check out a book
- Staff see the dashboard

## Out of scope
- Native apps

## Success
Books leave with a due date.

## Assumptions
- Browser only

## Open questions
- None
"""
    text = brd.draft_brd("REQ-9", scope, TEMPLATE, ["REQ-9-R01", "REQ-9-R02", "REQ-9-R03"])
    pages = coverage.pages_from_brd(text)
    assert len(pages) == len({p["id"] for p in pages})
    assert all(p["id"] not in {"Given", "When", "Then"} for p in pages)
    assert "- **Given**" in text
