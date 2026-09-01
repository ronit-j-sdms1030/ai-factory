"""What the list endpoint sends for generated screens.

A design is around 300KB of React and the list returns every artifact the
viewer can see, so shipping the source made the workspace slower to load than
the pipeline was to run it. The list needs the screens to exist and be named;
the code is served once, by the preview, when somebody looks.
"""

from __future__ import annotations

from app.routes.artifacts import _ui_summary

UI = {
    "screens": [
        {"name": "ClockInterface", "route": "/clock", "purpose": "Clock in", "source": "x" * 20000},
        {"name": "MyLeave", "route": "/leave", "purpose": "Balances", "source": "y" * 20000},
    ],
    "clarifications": ["Should rejections notify immediately?"],
}


def test_sources_are_not_sent():
    summary = _ui_summary(UI)
    assert all("source" not in s for s in summary["screens"])
    assert len(str(summary)) < 500


def test_names_routes_and_purposes_survive():
    summary = _ui_summary(UI)
    assert [s["name"] for s in summary["screens"]] == ["ClockInterface", "MyLeave"]
    assert summary["screens"][0]["route"] == "/clock"
    assert summary["screens"][0]["purpose"] == "Clock in"


def test_clarifications_are_kept():
    """They are the agent's open questions — the reviewer needs them."""
    assert _ui_summary(UI)["clarifications"] == ["Should rejections notify immediately?"]


def test_no_ui_stays_none():
    assert _ui_summary(None) is None
    assert _ui_summary({}) is None
