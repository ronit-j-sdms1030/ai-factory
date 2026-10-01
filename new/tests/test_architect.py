"""Architect decides this BRD — not a booking template."""

from phase2 import architect
from stack_profiles import NODE, PYTHON


BOOKING = """
# Functional specification — REQ-0001

## 8. Functional requirements

### REQ-0001-R01 — Book a room

## 9. Non-functional requirements

- **Channel:** browser only
- **Access:** work login in the browser

## 11. Page behaviour

- **Availability**: pick a date and see free rooms
- **BookingForm**: book a room for a slot

## 12. Data model

- **Room:** id, name
- **Booking:** id, room_id, starts_at, ends_at

## 16. Open questions

- None.
"""

ANALYTICS = BOOKING.replace("Book a room", "Nightly analytics batch job / ETL pipeline")


def test_decide_locks_node_and_derives_this_brd():
    decision = architect.decide("REQ-0001", BOOKING)
    assert decision["profile"]["id"] == "node"
    assert decision["profile"]["database"] == "PostgreSQL"
    names = {row["name"] for row in decision["entities"]}
    assert names == {"Room", "Booking"}
    paths = {row["path"] for row in decision["contracts"]}
    assert "/api/rooms" in paths
    assert "/api/bookings" in paths
    assert "/api/ai/suggest" not in paths
    assert {row["id"] for row in decision["modules"]} == {"ui", "api", "data"}
    assert {"PATCH /api/bookings/:id"} <= {f"{r['method']} {r['path']}" for r in decision["contracts"]}
    text = architect.render(decision)
    assert "Entra SSO" not in text
    assert "Cookie session" in text
    assert any(row["id"] == "ADR-001" and "node" in row["title"] for row in decision["adrs"])
    assert decision["needs_overlap"] is False


def test_double_book_writes_integrity_adr():
    decision = architect.decide("REQ-0001", BOOKING + "\nStop double-booking rooms.\n")
    assert decision["needs_overlap"] is True
    assert "constraint" in decision["adrs"][1]["title"].lower()


def test_analytics_locks_python():
    decision = architect.decide("REQ-0001", ANALYTICS)
    assert decision["profile"]["id"] == PYTHON.id
    assert "Python" in decision["reason"]


def test_java_is_refused_not_adopted():
    decision = architect.decide("REQ-0001", BOOKING + "\nUse a Java Spring service.\n")
    assert "java" in decision["refused"]
    assert decision["profile"]["id"] == NODE.id


VISITOR = """
# Business Requirements Document — REQ-0005

## Requirements

### REQ-0005-R08 — Web form to capture visitor name

## Page behaviour

- **SignIn**: Shared sign-in. Fields: login and password.
- **VisitorForm**: Fields: Visitor name, Person they are visiting, Time In.
- **VisitorList**: Current visitors. Each row has Sign out.

## Data model outline

PostgreSQL. One owning department per shared entity.

- **User:** id, name, shared_login
- **Visitor:** id, visitor_name, person_they_are_visiting, time_in, signed_out_at, status
- **AuditEntry:** id, actor_id, action, at

## Open questions

- None.
"""


def test_visitor_book_uses_the_data_model_not_the_screen_names():
    decision = architect.decide("REQ-0005", VISITOR)
    names = {row["name"]: row["fields"] for row in decision["entities"]}
    assert set(names) == {"User", "Visitor", "AuditEntry"}
    assert "visitor_name" in names["Visitor"]
    assert "time_in" in names["Visitor"]
    assert "signed_out_at" in names["Visitor"]
    paths = {row["path"] for row in decision["contracts"]}
    assert "/api/visitors" in paths
    assert "/api/audit_entries" in paths
    assert "/api/sign_ins" not in paths
    assert "/api/visitor_forms" not in paths
    assert "/api/postgre_s_q_ls" not in paths
    text = architect.render(decision)
    assert "SignIn" in text
    assert "PostgreSQL:" not in text


def test_ai_module_only_when_the_brd_asks_for_ai():
    decision = architect.decide("REQ-0001", BOOKING + "\nRecommend a room from past bookings.\n")
    assert "ai" in {row["id"] for row in decision["modules"]}
    assert "/api/ai/suggest" in {row["path"] for row in decision["contracts"]}


def test_sign_in_page_gets_session_contract_and_password_hash():
    decision = architect.decide("REQ-0005", VISITOR)
    contracts = {f"{r['method']} {r['path']}" for r in decision["contracts"]}
    assert "POST /api/session" in contracts
    assert "DELETE /api/session" in contracts
    user = next(row for row in decision["entities"] if row["name"] == "User")
    assert "password_hash" in user["fields"]
    assert "POST /api/session" in decision["identity"]


def test_real_time_brd_states_how_lists_stay_current():
    decision = architect.decide("REQ-0005", VISITOR + "\nReal-time filtering of the visitor list.\n")
    assert any(item.startswith("Real-time:") for item in decision["nfrs"])


def test_admin_page_only_when_brd_names_it():
    assert architect.decide("REQ-0001", BOOKING)["extra_pages"] == []
    extra = architect.decide("REQ-0001", BOOKING + "\nAdmin can cancel any booking.\n")["extra_pages"]
    assert extra[0]["id"] == "AdminCancel"
