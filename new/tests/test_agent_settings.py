"""Per-agent OpenRouter settings and cycle-cost estimate."""

from __future__ import annotations

from pathlib import Path

from phase1 import agent_settings


def test_unknown_agent_is_refused(tmp_path: Path):
    try:
        agent_settings.set_models(tmp_path, {"not-an-agent": "openai/gpt-4o"}, "u-po")
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_all_agents_are_configurable(tmp_path: Path):
    picked = {name: "openai/gpt-4o-mini" for name in agent_settings.AGENT_ROLES}
    stored = agent_settings.set_models(tmp_path, picked, "u-po")
    assert set(stored) == set(agent_settings.AGENT_ROLES)
    view = agent_settings.roles_view(tmp_path)
    assert len(view) == 12
    assert all(row["source"] == "setting" for row in view)


def test_cycle_cost_uses_gpt4o_mini_floor(tmp_path: Path):
    cost = agent_settings.cycle_cost(tmp_path)
    assert cost["currency"] == "USD"
    assert 0.01 <= cost["usd"] <= 0.05
    assert len(cost["agents"]) == 12
    assert cost["agents"][0]["prompt"] > 0


def test_ui_context_defaults_to_fetch_and_survives_model_save(tmp_path: Path):
    assert agent_settings.ui_context(tmp_path) == "fetch"
    agent_settings.set_ui_context(tmp_path, "bundle", "u-po")
    agent_settings.set_models(tmp_path, {"ui": "openai/gpt-4o-mini"}, "u-po")
    assert agent_settings.ui_context(tmp_path) == "bundle"
    assert agent_settings.stored(tmp_path)["ui"] == "openai/gpt-4o-mini"
    assert agent_settings.set_ui_context(tmp_path, "fetch", "u-po") == "fetch"


def test_quality_defaults_cover_every_agent_under_one_dollar(tmp_path: Path):
    stored = agent_settings.preset_defaults(tmp_path)
    assert stored == agent_settings.QUALITY_DEFAULTS
    assert set(stored) == set(agent_settings.AGENT_ROLES)
    cost = agent_settings.cycle_cost(tmp_path)
    assert cost["usd"] < 1.0
