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
