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


def test_draft_brd_names_screens_and_fields_for_ui():
    scope = """
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
Front Desk Visitor Book. Reception can see who is still inside.

## Assumptions
- Shared login

## Open questions
- None
"""
    text = brd.draft_brd(
        "REQ-0005",
        scope,
        TEMPLATE,
        [f"REQ-0005-R0{i}" for i in range(1, 7)],
    )
    assert "Front Desk Visitor Book" in text.splitlines()[0]
    pages = {page["id"]: page["description"] for page in coverage.pages_from_brd(text)}
    assert set(pages) == {"SignIn", "VisitorForm", "VisitorList"}
    assert "Visitor name" in pages["VisitorForm"]
    assert "Time In" in pages["VisitorForm"]
    assert "Sign out" in pages["VisitorList"]
    assert "No history" in pages["VisitorList"]
    assert "**Visitor:**" in text
    assert "time_in" in text
    assert "When they web form" not in text
    enriched = brd.enrich_scope(
        {
            "users": "Reception staff",
            "current_state": "A paper book",
            "success": "Front Desk Visitor Book",
            "in_scope": [
                "Web form to capture visitor name and the name of the person they are visiting",
            ],
            "out_of_scope": ["Historical visitor logs"],
            "open_questions": ["Who can correct a missed check-in?"],
        }
    )
    assert "Fields: Visitor name" in enriched["in_scope"][0]
    assert any(item.startswith("**VisitorForm**:") for item in enriched["screens"])
    assert enriched["open_questions"] == []
    rendered = brd.render_scope("REQ-0005", enriched)
    assert "## Screens" in rendered
    assert "VisitorForm" in brd.parse_scope(rendered)["screens"][0]
    assert not brd.refine_keeps_structure(text, text.split("### REQ-0005-R04")[0])
    assert not brd.refine_keeps_structure(text, text.replace("visitor_name", "title"))
