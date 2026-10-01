"""Architecture and screens share one contract, for every requirement."""

from pathlib import Path

import requirement as R
from phase1 import directory
from phase1.platform import Phase1
from phase2 import contract, ui

SKILL = (Path(__file__).resolve().parent.parent / "skills/design-system.skill.md").read_text()

BRD = """
# Business Requirements Document — REQ-0005

## Page behaviour

- **SignIn**: Shared sign-in. Fields: login and password. Success opens the main list.
- **VisitorForm**: Fields: Visitor name, Person they are visiting, Time In (set to the current time when the record is saved, not typed). Saving adds the visitor to the current list.
- **VisitorList**: A dashboard view of visitors still inside. Columns match the form: Visitor name, Person they are visiting, Time In. Each row has Sign out.

## Data model outline

PostgreSQL. One owning department per shared entity.

- **User:** id, name, shared_login
- **Visitor:** id, visitor_name, person_they_are_visiting, time_in, signed_out_at, status
- **AuditEntry:** id, actor_id, action, at
"""

LOCKED = [
    {"name": "User", "fields": ["id", "name", "shared_login"], "owner": "development"},
    {
        "name": "Visitor",
        "fields": ["id", "visitor_name", "person_they_are_visiting", "time_in", "signed_out_at", "status"],
        "owner": "development",
    },
    {"name": "AuditEntry", "fields": ["id", "actor_id", "action", "at"], "owner": "development"},
]

SCRAPED = [
    {"name": "SignIn", "fields": ["id", "name"]},
    {"name": "VisitorForm", "fields": ["id", "name"]},
    {"name": "PostgreSQL", "fields": ["id", "name"]},
]


def test_screen_names_and_the_database_are_not_records():
    issues = contract.problems(BRD, SCRAPED)
    assert any("SignIn is a screen" in item for item in issues)
    assert any("PostgreSQL is the database" in item for item in issues)
    assert any("missing record Visitor" in item for item in issues)
    assert contract.problems(BRD, LOCKED) == []


def test_screens_use_the_locked_visitor_fields():
    built = ui.build_screens(BRD, SKILL, entities=LOCKED)
    by_name = {screen["name"]: screen["source"] for screen in built["screens"]}
    assert "Login" in by_name["SignIn"]
    assert "Password" in by_name["SignIn"]
    assert "Work email" not in by_name["SignIn"]
    form = by_name["VisitorForm"]
    assert "Visitor name" in form
    assert "Person they are visiting" in form
    assert "Time In is set when the record is saved" in form
    assert 'label="Time In"' not in form
    listing = by_name["VisitorList"]
    assert "Visitor name" in listing
    assert "Person they are visiting" in listing
    assert "Time In" in listing
    assert "Sign out" in listing
    assert "On shelf" not in listing
    named_only = (
        "function VisitorForm() { return ( <Page><p>Visitor name, Person they are visiting, Time In</p>"
        "<Field name=\"name\" label=\"Name\" /></Page> ); }\n"
    )
    guessed = named_only
    replaced = ui.build_screens(BRD, SKILL, entities=LOCKED, sources={"VisitorForm": guessed})
    source = next(screen["source"] for screen in replaced["screens"] if screen["name"] == "VisitorForm")
    assert "Person they are visiting" in source


def test_architecture_coverage_names_each_brd_criterion():
    from phase2 import architect

    specified = BRD + """
## Requirements

### REQ-0005-R08 — Web form to capture visitor name and who they are visiting

Fields: Visitor name, Person they are visiting, Time In.

**Acceptance criteria:**

Given a named user is signed in
When they submit Visitor name, Person they are visiting
Then the new record appears on the current list
"""
    decision = architect.decide("REQ-0005", specified)
    decision["brd_excerpt"] = specified
    text = architect.render(decision)
    rows = {row["id"]: row for row in contract.coverage_rows(specified, text)}
    assert rows["REQ-0005-R08"]["satisfies"] is True
    assert "Visitor name" in rows["REQ-0005-R08"]["criteria"]
    assert not rows["REQ-0005-R08"]["criteria"].startswith("*")
    assert any("visitor_name" in item for item in rows["REQ-0005-R08"]["covered"])
    thin = text.replace("time_in", "title")
    again = {row["id"]: row for row in contract.coverage_rows(specified, thin)}
    assert again["REQ-0005-R08"]["satisfies"] is False
    assert any("time_in" in gap for gap in again["REQ-0005-R08"]["gaps"])


def test_gate_3_records_a_design_artefact_that_was_only_text(tmp_path: Path):
    platform = Phase1(tmp_path)
    record = R.open_requirement("REQ-0009", "full_governance", directory.actor("u-requester"))
    body = {
        "requirement": record.dump(),
        "architecture_text": "# Architecture — REQ-0009\n\nVisitor\n",
    }
    platform.store.put("REQ-0009", body, at="2026-01-01T00:00:00Z")
    platform.git.commit_files(
        "design/REQ-0009",
        {"requirements/REQ-0009/design/architecture.md": body["architecture_text"]},
        "architecture",
        author="design-agent",
        email="design@local",
    )
    loaded = R.Requirement.load(body["requirement"])
    platform._ensure_design_artefact(loaded, body)
    saved = platform.store.get("REQ-0009") or {}
    assert "design" in ((saved.get("requirement") or {}).get("artefacts") or {})
    platform._ensure_design_artefact(R.Requirement.load(saved["requirement"]), saved)
    again = ((platform.store.get("REQ-0009") or {}).get("requirement") or {}).get("artefacts") or {}
    assert "design" in again


def test_startup_aligns_a_bad_architecture_and_leaves_a_signed_one(tmp_path: Path):
    platform = Phase1(tmp_path)
    open_req = R.open_requirement("REQ-0007", "full_governance", directory.actor("u-requester"))
    platform.store.put(
        "REQ-0007",
        {
            "requirement": open_req.dump(),
            "brd_text": BRD,
            "architecture_text": "PostgreSQL is a table.",
            "architecture_decision": {"entities": SCRAPED},
        },
        at="2026-01-01T00:00:00Z",
    )
    signed = R.open_requirement("REQ-0008", "full_governance", directory.actor("u-requester"))
    signed.cleared.update({1, 2, 3})
    platform.store.put(
        "REQ-0008",
        {
            "requirement": signed.dump(),
            "brd_text": BRD,
            "architecture_text": "leave this signed text",
            "architecture_decision": {"entities": SCRAPED},
        },
        at="2026-01-01T00:00:00Z",
    )

    assert platform.sync_design_accuracy() == ["REQ-0007"]
    saved = platform.store.get("REQ-0007") or {}
    text = saved.get("architecture_text") or ""
    assert "visitor_name" in text
    assert "postgre_s_q_ls" not in text
    assert contract.problems(BRD, (saved.get("architecture_decision") or {}).get("entities")) == []
    assert (platform.store.get("REQ-0008") or {}).get("architecture_text") == "leave this signed text"
    assert platform.sync_design_accuracy() == []
