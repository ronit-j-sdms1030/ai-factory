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
    assert "/api/ai/suggest" in paths
    assert {row["id"] for row in decision["modules"]} == {"ui", "api", "data", "ai"}
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


def test_admin_page_only_when_brd_names_it():
    assert architect.decide("REQ-0001", BOOKING)["extra_pages"] == []
    extra = architect.decide("REQ-0001", BOOKING + "\nAdmin can cancel any booking.\n")["extra_pages"]
    assert extra[0]["id"] == "AdminCancel"
