"""Compile-checking generated screens before a reviewer sees them.

The preview compiles each screen in the browser and strikes through failures,
but that is after publication — the screen is committed, the pull request is
open, and the pipeline has reported success. One of fourteen screens on a real
requirement (``AuditTrail``) reached GATE 2 in exactly that state.
"""

from __future__ import annotations

import pytest

from app import jsx_check
from app.agents import ui


requires_node = pytest.mark.skipif(
    not jsx_check.available(), reason="node or @babel/core is not available"
)


GOOD = 'const Dashboard = () => <div className="p-6">hi</div>;'
UNCLOSED_TAG = "const Broken = () => <div>oops</div;"
TRUNCATED = 'const Cut = () => { const label = "unterminated'


@requires_node
class TestTheCheck:
    def test_a_valid_screen_passes(self):
        assert jsx_check.compile_errors({"Dashboard": GOOD}) == {}

    def test_an_unclosed_tag_is_caught(self):
        errors = jsx_check.compile_errors({"Broken": UNCLOSED_TAG})
        assert "Broken" in errors
        assert "jsxTagEnd" in errors["Broken"]

    def test_a_truncated_screen_is_caught(self):
        """The failure mode that actually happens: output stops mid-string
        because the model ran out of room."""
        errors = jsx_check.compile_errors({"Cut": TRUNCATED})
        assert "Unterminated string" in errors["Cut"]

    def test_only_the_broken_screens_are_reported(self):
        errors = jsx_check.compile_errors({"Good": GOOD, "Bad": UNCLOSED_TAG})
        assert set(errors) == {"Bad"}

    def test_nothing_to_check_is_not_an_error(self):
        assert jsx_check.compile_errors({}) == {}


class TestWhenTheCheckerIsMissing:
    def test_it_reports_nothing_broken_rather_than_everything(self, monkeypatch):
        """An absent checker is an environment problem. Treating it as "every
        screen is broken" would fail every screen on a machine without node —
        far worse than publishing unchecked screens, which is what the
        pipeline did before this existed."""
        monkeypatch.setattr(jsx_check, "available", lambda: False)
        assert jsx_check.compile_errors({"Broken": UNCLOSED_TAG}) == {}


@requires_node
class TestRepair:
    def _screens(self):
        return [
            {"name": "Good", "route": "/good", "purpose": "p", "source": GOOD},
            {"name": "Bad", "route": "/bad", "purpose": "p", "source": UNCLOSED_TAG},
        ]

    def test_a_broken_screen_is_rewritten_and_kept(self, monkeypatch):
        calls = []

        def fake_write(brd, outline, roster, model, learned="", skill="", compile_error=""):
            calls.append((outline.name, compile_error))
            return GOOD

        monkeypatch.setattr(ui, "_write_screen", fake_write)
        screens = self._screens()
        still_broken = ui._repair_broken_screens(screens, {}, "", "m", "", "")

        assert still_broken == []
        assert [s["name"] for s in screens] == ["Good", "Bad"]
        assert next(s for s in screens if s["name"] == "Bad")["source"] == GOOD

    def test_only_the_broken_screen_is_regenerated(self, monkeypatch):
        """A working screen must not be touched — regenerating it would gamble
        output that was already right."""
        calls = []
        monkeypatch.setattr(
            ui, "_write_screen",
            lambda brd, outline, *a, **k: (calls.append(outline.name), GOOD)[1],
        )
        ui._repair_broken_screens(self._screens(), {}, "", "m", "", "")
        assert calls == ["Bad"]

    def test_the_parser_error_reaches_the_model(self, monkeypatch):
        seen = {}

        def fake_write(brd, outline, roster, model, learned="", skill="", compile_error=""):
            seen[outline.name] = compile_error
            return GOOD

        monkeypatch.setattr(ui, "_write_screen", fake_write)
        ui._repair_broken_screens(self._screens(), {}, "", "m", "", "")
        assert "jsxTagEnd" in seen["Bad"]

    def test_a_screen_that_stays_broken_is_reported_not_published(self, monkeypatch):
        """Publishing a screen that will not render is the failure this
        exists to prevent; the reviewer is told instead."""
        monkeypatch.setattr(ui, "_write_screen", lambda *a, **k: UNCLOSED_TAG)
        still_broken = ui._repair_broken_screens(self._screens(), {}, "", "m", "", "")
        assert still_broken == ["Bad"]

    def test_a_failed_repair_call_leaves_the_screen_broken(self, monkeypatch):
        monkeypatch.setattr(ui, "_write_screen", lambda *a, **k: None)
        still_broken = ui._repair_broken_screens(self._screens(), {}, "", "m", "", "")
        assert still_broken == ["Bad"]

    def test_retries_are_bounded(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            ui, "_write_screen",
            lambda brd, outline, *a, **k: (calls.append(outline.name), UNCLOSED_TAG)[1],
        )
        ui._repair_broken_screens(self._screens(), {}, "", "m", "", "")
        assert len(calls) == ui._MAX_COMPILE_RETRIES + 1


class TestTheRepairPrompt:
    def test_no_error_means_no_extra_turn(self):
        """The normal path must send exactly the messages it always did."""
        assert ui._repair_turn(None, "") == []

    def test_the_error_is_quoted_to_the_model(self):
        turn = ui._repair_turn(None, "Unterminated string constant. (1:28)")
        assert len(turn) == 1
        assert "Unterminated string constant. (1:28)" in turn[0]["content"]
        assert "change nothing else" in turn[0]["content"]
