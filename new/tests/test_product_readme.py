"""The requirement README names the website and the current gate."""

from phase1.product_readme import brief_from_scope, render


def test_readme_uses_the_intake_website_and_the_current_gate():
    scope = """# Scope report — REQ-0001

## Users
Library staff and members

## What happens today
Loans are written in a paper book

## In scope
- Search the catalogue
- Check out a book

## Success
Staff can see who has each book.
"""
    brief = brief_from_scope(scope)
    text = render(
        "REQ-0001",
        {
            "display_title": "Community Library",
            "intake_brief": brief,
            "requirement": {
                "id": "REQ-0001",
                "template": "software",
                "originator": {"id": "u-requester"},
                "approval_chain": {},
                "gates": [1, 2, 3, 4, 5, 6, 7],
                "artefacts": {},
                "signatures": [],
                "attestations": [],
                "cleared": [1, 2, 3],
                "history": [],
                "discarded_reason": "",
                "allocated": 0,
                "retired": [],
                "ledger_edges": [],
            },
        },
    )
    assert text.startswith("# Community Library")
    assert "Gate 4 — Plan" in text
    assert "Search the catalogue" in text
    assert "who has each book" in text


def test_readme_says_complete_after_the_last_gate():
    text = render(
        "REQ-0009",
        {
            "display_title": "Travel Company",
            "requirement": {
                "id": "REQ-0009",
                "template": "software",
                "originator": {"id": "u-requester"},
                "approval_chain": {},
                "gates": [1, 2],
                "artefacts": {},
                "signatures": [],
                "attestations": [],
                "cleared": [1, 2],
                "history": [],
                "discarded_reason": "",
                "allocated": 0,
                "retired": [],
                "ledger_edges": [],
            },
        },
    )
    assert "# Travel Company" in text
    assert "Complete" in text
