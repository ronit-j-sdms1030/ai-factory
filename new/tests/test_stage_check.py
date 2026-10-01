"""Every stage is judged against every BRD requirement, the same way."""

from __future__ import annotations

from phase3 import decomposer, qa, stage_check
from stack_profiles import NODE

VISITOR_BRD = """
## 8. Functional requirements

### REQ-7-R01 — Sign in with the shared reception login
Acceptance criteria: Given the sign-in screen, When the receptionist enters the shared password, Then the visitor log opens.

### REQ-7-R02 — Record a visitor arriving
Acceptance criteria: Given the visitor form, When the receptionist saves name and host, Then the visitor appears in the visitor list.

### REQ-7-R03 — See who is on site today
Acceptance criteria: Given the visitor list, When it opens, Then each row shows name, host and arrived at.

## 11. Page behaviour

- **SignIn**: sign in with the shared reception login
- **VisitorForm**: record a visitor arriving: name, host
- **VisitorList**: each row shows name, host, arrived at

## 12. Data model

- **ReceptionLogin:** id, password_hash
- **Visitor:** id, name, host, arrived_at

Reception staff use one shared login.
"""

SCREENS = [{"name": "SignIn"}, {"name": "VisitorForm"}, {"name": "VisitorList"}]


def test_generated_plan_covers_every_requirement():
    tickets = decomposer.tickets("REQ-7", VISITOR_BRD, SCREENS, NODE)
    tests = qa.cases(VISITOR_BRD, SCREENS, NODE)
    check = stage_check.plan(VISITOR_BRD, SCREENS, tickets, tests, sprint0={"status": "green"})
    assert check["ok"], check
    assert [row["id"] for row in check["rows"]] == ["REQ-7-R01", "REQ-7-R02", "REQ-7-R03"]


def test_shared_login_gets_no_per_user_ownership_test_or_ai_ticket():
    tickets = decomposer.tickets("REQ-7", VISITOR_BRD, SCREENS, NODE)
    tests = qa.cases(VISITOR_BRD, SCREENS, NODE)
    assert not any("another user" in case["name"] for case in tests)
    critical = next(case for case in tests if case.get("critical"))
    assert "shared password" in critical["name"]
    assert not any(ticket["department"] == "ai" for ticket in tickets)


def test_sign_in_screen_waits_for_the_login_record_api():
    tickets = decomposer.tickets("REQ-7", VISITOR_BRD, SCREENS, NODE)
    sign_in = next(t for t in tickets if t.get("screen") == "SignIn")
    parent = next(t for t in tickets if t["id"] == sign_in["depends_on"][0])
    assert parent.get("entity") == "ReceptionLogin"
    form = next(t for t in tickets if t.get("screen") == "VisitorForm")
    assert next(t for t in tickets if t["id"] == form["depends_on"][0]).get("entity") == "Visitor"


def test_template_plan_traced_to_the_first_requirement_fails():
    tickets = [
        {"id": f"REQ-7-W{i}", "title": f"work {i}", "department": "development",
         "paths": ["src/api/**"], "depends_on": [], "trace": ["REQ-7-R01"]}
        for i in range(1, 4)
    ]
    tests = [{"id": "REQ-7-R01-T01", "name": "x", "criterion": "REQ-7-R01", "critical": True},
             {"id": "REQ-7-R01-T02", "name": "a user cannot change another user's record",
              "criterion": "auth"}]
    check = stage_check.plan(VISITOR_BRD, SCREENS, tickets, tests, sprint0={"status": "ungreen"})
    assert not check["ok"]
    assert check["missing"] == ["REQ-7-R01", "REQ-7-R02", "REQ-7-R03"]
    r01 = check["rows"][0]
    assert r01["gaps"] == ["no ticket builds the SignIn screen"]
    assert "no ticket traces it" in check["rows"][1]["gaps"]
    joined = " ".join(check["problems"])
    assert "Sprint 0 is ungreen" in joined
    assert "write the same files" in joined
    assert "shared login" in joined
    assert "needs a revision" in check["summary"]


def test_plan_without_schema_records_or_negative_tests_fails():
    """The REQ-0005 plan a reviewer rejected: covered on paper, not buildable."""
    brd = VISITOR_BRD + "\n- **AuditEntry:** id, action, at\n"
    all_ids = ["REQ-7-R01", "REQ-7-R02", "REQ-7-R03"]
    tickets = [
        {"id": "T1", "title": "API for Visitor records and authentication", "department": "development",
         "paths": ["src/api/visitor/**"], "depends_on": [], "trace": all_ids},
        {"id": "T2", "title": "SignIn Screen", "department": "development",
         "paths": ["src/ui/SignIn/**"], "depends_on": ["T1"], "trace": ["REQ-7-R01"]},
        {"id": "T3", "title": "VisitorForm Screen", "department": "development",
         "paths": ["src/ui/VisitorForm/**"], "depends_on": ["T1"], "trace": ["REQ-7-R02"]},
        {"id": "T4", "title": "VisitorList Screen", "department": "development",
         "paths": ["src/ui/VisitorList/**"], "depends_on": ["T1"], "trace": ["REQ-7-R03"]},
        {"id": "T5", "title": "QA harness", "department": "qa",
         "paths": ["src/test/**"], "depends_on": ["T1"], "trace": all_ids},
    ]
    tests = [
        {"id": "t1", "name": "the main list opens", "criterion": "REQ-7-R01", "critical": True},
        {"id": "t2", "name": "that row leaves the current list", "criterion": "REQ-7-R02"},
        {"id": "t3", "name": "that row leaves the current list", "criterion": "REQ-7-R03"},
        {"id": "t4", "name": "they see only the current records described in this item", "criterion": "REQ-7-R03"},
    ]
    check = stage_check.plan(brd, SCREENS, tickets, tests, sprint0={"status": "green"})
    assert not check["ok"]
    joined = " | ".join(check["problems"])
    assert "no schema/migration ticket" in joined
    assert "no ticket builds the ReceptionLogin record" in joined
    assert "no ticket builds the AuditEntry record" in joined
    assert "t2 and t3 have the same name" in joined
    assert "t4 is too vague" in joined
    assert "no negative test" in joined
    assert "no test that a wrong sign-in is refused" in joined
    assert "no test that the audit entry is written" in joined


def test_generated_plan_with_audit_still_passes():
    brd = VISITOR_BRD + "\n- **AuditEntry:** id, action, at\n"
    tickets = decomposer.tickets("REQ-7", brd, SCREENS, NODE)
    tests = qa.cases(brd, SCREENS, NODE)
    check = stage_check.plan(brd, SCREENS, tickets, tests, sprint0={"status": "green"})
    assert check["ok"], check


def test_last_requirement_does_not_swallow_page_behaviour():
    from phase3 import trace

    last = trace.requirements(VISITOR_BRD)[-1]
    assert "Page behaviour" not in last["body"]
    assert [p["id"] for p in trace.pages_for(last, trace.pages(VISITOR_BRD))] == ["VisitorList"]


FLAWED_BRD = """
## Requirements

### REQ-8-R01 — Sign in with the shared desk login
Sign in with the shared desk login.

**Acceptance criteria:**
- **Given** the desk opens the site
- **When** they sign in with the shared login
- **Then** the visitor list opens

### REQ-8-R02 — A 'Sign Out' action for each visitor
A 'Sign Out' action for each visitor.

**Acceptance criteria:**
- **Given** a visitor is on the list
- **When** they choose Sign out on one row
- **Then** that row leaves the current list

### REQ-8-R03 — Filter the visitor list to exclude people who signed out
Filter the visitor list to exclude people who signed out.

**Acceptance criteria:**
- **Given** a visitor is on the list
- **When** they choose Sign out on one row
- **Then** that row leaves the current list

### REQ-8-R04 — A dashboard view displaying a list of visitors inside
A dashboard view displaying a list of visitors inside. Fields: Badge number.

**Acceptance criteria:**
- **When** they open the list
- **Then** they see only the current records described in this item

## Page behaviour

- **SignIn**: Shared sign-in. Fields: login and password.
- **VisitorList**: A list of visitors inside. Columns match the form: Visitor name, Time In. Each row has Sign out.
- **Reports**: monthly visitor totals.

## Data model outline

- **User:** id, name, shared_login
- **Visitor:** id, visitor_name, time_in, signed_out_at

## Security design

One shared login. Role-appropriate views (staff vs manager).

## Open questions

- Which building is in scope?
- *(critique)* Confirm the owning department for shared records before decomposition.
"""


def test_brd_rules_find_untestable_and_inconsistent_requirements():
    check = stage_check.business(FLAWED_BRD)
    gaps = {row["id"]: " | ".join(row["gaps"]) for row in check["rows"]}
    assert "same When/Then as REQ-8-R02" in gaps["REQ-8-R03"]
    assert "placeholder text" in gaps["REQ-8-R04"]
    assert "no Given step" in gaps["REQ-8-R04"]
    assert 'field "Badge number" is on no screen and in no record' in gaps["REQ-8-R04"]
    problems = " | ".join(check["problems"])
    assert "screen Reports is not asked for by any requirement" in problems
    assert "open question not resolved: Which building is in scope?" in problems
    assert "names roles (manager) but the BRD uses one shared login" in problems
    assert "Confirm the owning department" not in problems
    assert any("reviewer prompt" in note for note in check["notes"])


def test_architecture_rules_need_an_api_for_every_action():
    arch = """# Architecture — REQ-8

Locked stack profile: **node**.

## Stack

- API: Express

## Modules

- `ui` — React screens for `SignIn`, `VisitorList`
- `ai` — Prompt + inference adapter

## Data model

- **User:** id, name, shared_login — owner `development`
- **Visitor:** id, visitor_name, time_in, signed_out_at

## API contracts

- `GET /api/visitors` — list Visitor
- `POST /api/visitors` — create Visitor
- `POST /api/ai/suggest` — suggestions

## Non-functional

- Browser only.

## Source BRD excerpt

Real-time filtering, polling, websocket.
"""
    brd = FLAWED_BRD.replace("Filter the visitor list", "Real-time filter of the visitor list")
    check = stage_check.architecture(brd, arch, None)
    gaps = {row["id"]: " | ".join(row["gaps"]) for row in check["rows"]}
    assert "no sign-in API contract" in gaps["REQ-8-R01"]
    assert "no API contract to update Visitor" in gaps["REQ-8-R02"]
    problems = " | ".join(check["problems"])
    assert "no Adrs section" not in problems
    assert "record Visitor has no owning department" in problems
    assert "BRD asks for no AI" in problems
    assert "never says how" in problems
    assert "SignIn asks for a password but no record stores a password hash" in problems


def test_business_analysis_follows_each_requirement_end_to_end():
    from phase2 import architect

    brd = VISITOR_BRD.replace(
        "### REQ-7-R03 — See who is on site today",
        "### REQ-7-R03 — A 'Sign Out' action for each visitor",
    ).replace(
        "- **VisitorForm**: record a visitor arriving: name, host",
        "- **VisitorForm**: record a visitor arriving. Fields: Name, Host, Badge number",
    )
    entities = [{"name": "Visitor", "fields": ["id", "name", "host"], "owner": "development"}]
    text = architect.render({**architect.decide("REQ-7", brd), "entities": entities})
    check = stage_check.analysis(brd, text, entities)
    gaps = " | ".join(gap for row in check["rows"] for gap in row["gaps"])
    assert 'VisitorForm shows "Badge number" but no record stores it' in gaps
    assert "Visitor.arrived_at" in gaps
    assert 'no screen offers the "Sign Out" action' in gaps


def test_factory_written_architecture_passes_its_own_rules():
    from phase2 import architect

    decision = architect.decide("REQ-7", VISITOR_BRD)
    text = architect.render(decision)
    for check in (
        stage_check.architecture(VISITOR_BRD, text, decision["entities"]),
        stage_check.analysis(VISITOR_BRD, text, decision["entities"]),
    ):
        assert check["ok"], check


def test_screens_check_names_the_missing_screen():
    check = stage_check.screens(
        VISITOR_BRD,
        [{"name": "SignIn", "source": ""}, {"name": "VisitorList", "source": ""}],
        [{"name": "Visitor", "fields": ["id", "name", "host", "arrived_at"]}],
    )
    row = next(r for r in check["rows"] if r["id"] == "REQ-7-R02")
    assert not row["satisfies"]
    assert any("VisitorForm" in gap for gap in row["gaps"])


def test_uat_check_fails_the_requirement_whose_test_failed():
    tests = qa.cases(VISITOR_BRD, SCREENS, NODE)
    results = [{"id": c["id"], "ok": c["criterion"] != "REQ-7-R02", "critical": False} for c in tests]
    check = stage_check.uat(VISITOR_BRD, tests, {"qa": {"results": results, "ok": True}, "dast": {"ok": True}})
    assert check["missing"] == ["REQ-7-R02"]


def test_all_stages_only_reports_stages_that_exist():
    out = stage_check.all_stages({"brd_text": VISITOR_BRD})
    assert set(out) == {"business"}
    assert out["business"]["ok"], out["business"]
    assert stage_check.all_stages({}) == {}
