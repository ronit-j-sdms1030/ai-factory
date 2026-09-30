"""Anything that is not a React + Node/Python browser app is the same miss.

A passing word must not trip this. A library site stays a web app even when
it also mentions a side product this stack cannot ship.
"""

from __future__ import annotations

import pytest

from phase2.design import clarification_needed
from stack_profiles import BOUNDARY_MARK, delivery_fit, exclusion_line, outside_only_scope


def test_a_plain_web_app_is_in_stack():
    fit = delivery_fit(
        "Community library. Staff record loans on a screen. Members log in and see what is due."
    )
    assert fit["web_app"] is True
    assert fit["outside"] == []
    assert fit["core_outside"] is False


@pytest.mark.parametrize(
    "text",
    [
        "Staff can chat with a member about an overdue loan on the loan screen.",
        "No chatbot. Just the loan screens and a member login.",
        "Members use it on their phones, in a mobile browser.",
        "A real-time dashboard of loans for the front desk.",
        "The catalogue screen uses AI to suggest the next book.",
        "The React screens are written in JavaScript.",
        "Staff make phone calls about overdue loans from the loan screen.",
    ],
)
def test_wording_is_not_a_different_product(text):
    fit = delivery_fit(text)
    assert fit["core_outside"] is False
    assert fit["outside"] == []


@pytest.mark.parametrize(
    ("text", "boundary"),
    [
        ("Build a WhatsApp chatbot that answers member questions.", "conversation_interface"),
        ("We need a call bot that answers inbound phone calls after hours.", "conversation_interface"),
        ("Ship an iOS app to the app store.", "native_mobile"),
        ("A desktop application with an installer for the front desk.", "desktop"),
        ("Firmware for the door sensor.", "embedded"),
        ("A video game where you shelve books.", "game"),
        ("A Java Spring Boot service that prices trades.", "other_runtime"),
        ("A platform that does model training and serves models.", "training_platform"),
        ("Safety-critical medical device software for the infusion pump.", "safety_critical"),
        ("A high-frequency trading engine.", "latency_platform"),
    ],
)
def test_a_non_browser_product_is_outside(text, boundary):
    fit = delivery_fit(text)
    assert boundary in fit["outside"]
    assert fit["core_outside"] is True


def test_a_portal_plus_another_product_keeps_the_portal():
    fit = delivery_fit(
        "A staff portal with loan screens, a chatbot for questions, and an iOS app."
    )
    assert fit["web_app"] is True
    assert fit["core_outside"] is False
    assert "conversation_interface" in fit["outside"]
    assert "native_mobile" in fit["outside"]
    line = exclusion_line(
        "A staff portal with loan screens, a chatbot for questions, and an iOS app."
    )
    assert "call bot or chatbot" in line.lower()
    assert "native mobile app" in line.lower()
    assert "react browser app" in line.lower()


def test_outside_scope_does_not_invent_screens():
    report = outside_only_scope("Build a Telegram bot for book renewals")
    assert report["type"] == "scope_report"
    assert "react browser app" in report["out_of_scope"][0].lower()
    assert "screen" not in " ".join(report["in_scope"]).lower()
    assert "react" in report["in_scope"][0].lower()


def test_design_stops_when_the_brd_is_outside_the_stack():
    question = clarification_needed(
        "# BRD\n\n## Page behaviour\n\n### Chat\nA WhatsApp chatbot answers questions.\n"
    )
    assert question and BOUNDARY_MARK in question

    ios = clarification_needed("# BRD\n\nShip an Android app to the play store.\n")
    assert ios and "native mobile app" in ios.lower()


def test_design_still_builds_a_normal_brd():
    brd = """# BRD

## 1. Page behaviour

### Loans
Staff record a loan on the loan screen.
"""
    assert clarification_needed(brd) is None
