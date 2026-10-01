from phase1 import rails


def test_shape_scope_report_does_not_paste_the_transcript():
    dumped = {
        "type": "scope_report",
        "in_scope": [
            "A web application for I need a simple smart attendance app for our office. "
            "People should mark in and out, replacing: Today people write their name in a register.",
            "Built from the request: Start a guided requirement intake conversation.",
        ],
        "out_of_scope": [
            "Not a fingerprint machine, not payroll, not a visitor desk. Not a separate iPhone app."
        ],
        "success": "Afterwards I should not be guessing who showed up.",
        "open_questions": [],
    }
    shaped = rails.shape_scope_report(
        dumped,
        request_text="I need a simple smart attendance app for our office. People mark in and out.",
    )
    assert shaped["title"].lower().startswith("a simple smart attendance") or "attendance" in shaped["title"].lower()
    assert all("Built from the request" not in item for item in shaped["in_scope"])
    assert all(len(item) < 220 for item in shaped["in_scope"])
    assert shaped["out_of_scope"]
    assert "guessing" in shaped["success"]


def test_shape_scope_report_drops_repeated_lines():
    shaped = rails.shape_scope_report(
        {
            "type": "scope_report",
            "title": "Hall diary",
            "in_scope": ["Staff book a room", "staff book a room", "Staff book a room"],
            "out_of_scope": ["A phone bot"],
            "success": "A booking is stored.",
        }
    )
    assert shaped["in_scope"] == ["Staff book a room"]


def test_conversation_skips_bootstrap_and_keeps_today():
    today = (
        "Today contractors write their name in a paper book in the site cabin, "
        "or send a photo in a WhatsApp group. The supervisor only knows who is "
        "on site by walking over or calling the cabin."
    )
    afterwards = (
        "A contractor on site should be able to say they have arrived or left "
        "from their phone, and the supervisor should already know who is there."
    )
    report = rails.scope_from_conversation(
        [
            {"role": "user", "content": "Start a guided requirement intake conversation."},
            {"role": "user", "content": (
                "We need a website so contractors on our building sites can check in "
                "and check out on their phone. The site supervisor should see who is "
                "on site today without calling the cabin."
            )},
            {"role": "user", "content": today},
            {"role": "user", "content": afterwards},
            {"role": "user", "content": (
                "No iPhone or Android app from a store. No cameras. No paying people "
                "from this website."
            )},
        ]
    )
    assert all("guided requirement intake" not in item.lower() for item in report["in_scope"])
    assert any("contractor" in item.lower() for item in report["in_scope"])
    assert "paper book" in report["current_state"]
    assert "phone" in report["success"].lower()
    assert any("iphone" in item.lower() or "android" in item.lower() for item in report["out_of_scope"])
    assert all("missed check-in" not in item.lower() for item in report["open_questions"])
    assert all("month end" not in item.lower() for item in report["open_questions"])


def test_card_title_rejects_the_afterwards_paragraph():
    title = rails.card_title(
        "A contractor on site should be able to say they have arrived or left from their phone, and the supervisor should already know who is there",
        ["Used by contractors on site.", "Check in and check out on a phone browser"],
        "A contractor on site should be able to say they have arrived.",
    )
    assert "should already know" not in title.lower()
    assert "check in" in title.lower() or "phone" in title.lower()
    assert rails.card_title("Room booking", ["Used by facilities"], "People can book") == "Room booking"

