"""Per-agent OpenRouter model choice. File-backed; next generation picks it up."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from phase1 import model_catalogue

AGENT_ROLES: dict[str, dict[str, str]] = {
    "intake": {
        "label": "Intake",
        "detail": "Guided conversation and scope report (Gate 1)",
    },
    "brd": {
        "label": "BRD",
        "detail": "Business requirements document (Gate 2)",
    },
    "architect": {
        "label": "Architect",
        "detail": "Stack lock, ADRs, architecture (Gate 3)",
    },
    "ui": {
        "label": "UI / UX",
        "detail": "Screens against the design system (Gate 3)",
    },
    "decomposer": {
        "label": "Decomposer",
        "detail": "Tickets and path allow-lists (Gate 4)",
    },
    "qa": {
        "label": "QA",
        "detail": "Tests before code, then UAT cases (Gates 4 and 6)",
    },
    "devops": {
        "label": "DevOps",
        "detail": "Sprint 0, UAT URL, canary, rollback (Gates 4, 6, 7)",
    },
    "overview": {
        "label": "Overview",
        "detail": "Non-gating briefing for stream leads",
    },
    "build": {
        "label": "Build / Execution Router",
        "detail": "Agentless / OpenHands / SWE-agent tickets (Gate 5)",
    },
    "review": {
        "label": "Review bench",
        "detail": "Correctness, security, architecture, quality",
    },
    "adversary": {
        "label": "Adversary",
        "detail": "Tries to prove the artefact wrong",
    },
    "monitor": {
        "label": "Monitor",
        "detail": "Errors, latency, spend → change requests",
    },
}

# One full_governance cycle: LLM calls that fire if MODEL_ADAPTER=litellm.
CYCLE_USAGE: dict[str, dict[str, int]] = {
    "intake": {"calls": 5, "prompt": 2500, "completion": 400},
    "brd": {"calls": 2, "prompt": 4000, "completion": 1500},
    "architect": {"calls": 1, "prompt": 5000, "completion": 2000},
    "ui": {"calls": 1, "prompt": 4000, "completion": 2500},
    "decomposer": {"calls": 1, "prompt": 3500, "completion": 1200},
    "qa": {"calls": 1, "prompt": 2500, "completion": 800},
    "devops": {"calls": 1, "prompt": 2000, "completion": 600},
    "overview": {"calls": 1, "prompt": 2500, "completion": 800},
    "build": {"calls": 1, "prompt": 2000, "completion": 400},
    "review": {"calls": 1, "prompt": 3000, "completion": 800},
    "adversary": {"calls": 1, "prompt": 2500, "completion": 600},
    "monitor": {"calls": 1, "prompt": 1500, "completion": 400},
}

_FALLBACK_MINI = {"prompt_per_million": 0.15, "completion_per_million": 0.60}

# Fallback when an agent has no row in agent-models.json.
CHEAP_DEFAULT = "openai/gpt-4o-mini"

# Best quality-per-role pack that still keeps one full cycle well under $1.
QUALITY_DEFAULTS: dict[str, str] = {
    "intake": "anthropic/claude-haiku-4.5",
    "brd": "deepseek/deepseek-v3.2",
    "architect": "openai/gpt-4.1-mini",
    "ui": "qwen/qwen3-coder",
    "decomposer": "openai/gpt-4.1-mini",
    "qa": "qwen/qwen3-coder",
    "devops": "openai/gpt-4.1-mini",
    "overview": "openai/gpt-4o-mini",
    "build": "openai/gpt-4o-mini",
    "review": "anthropic/claude-haiku-4.5",
    "adversary": "anthropic/claude-haiku-4.5",
    "monitor": "openai/gpt-4o-mini",
}


def default_model() -> str:
    return os.getenv("LITELLM_MODEL") or CHEAP_DEFAULT


def ensure_cheap_defaults(root: Path) -> dict[str, str]:
    """Write the quality pack on a blank runtime. Does not clobber a saved set."""
    if stored(root):
        return stored(root)
    return set_models(root, QUALITY_DEFAULTS, "platform")


def preset_defaults(root: Path) -> dict[str, str]:
    """Overwrite every agent with the quality pack."""
    return set_models(root, QUALITY_DEFAULTS, "platform")


def _path(root: Path) -> Path:
    return Path(root) / "agent-models.json"


def _load(root: Path) -> dict[str, Any]:
    path = _path(root)
    if not path.exists():
        return {"models": {}}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"models": {}}
    if not isinstance(data, dict):
        return {"models": {}}
    data.setdefault("models", {})
    return data


def _write(root: Path, data: dict[str, Any]) -> None:
    _path(root).write_text(json.dumps(data, indent=2) + "\n")


def stored(root: Path) -> dict[str, str]:
    return {key: value for key, value in (_load(root).get("models") or {}).items() if value}


def ui_context(root: Path) -> str:
    """`fetch` = one screen + local skill tools. `bundle` = dump every UI skill."""
    raw = str(_load(root).get("uiContext") or "fetch").strip().lower()
    return "bundle" if raw == "bundle" else "fetch"


def set_ui_context(root: Path, mode: str, actor_id: str) -> str:
    chosen = "bundle" if str(mode or "").strip().lower() == "bundle" else "fetch"
    data = _load(root)
    data["uiContext"] = chosen
    data["updatedBy"] = actor_id
    _write(root, data)
    return chosen


def set_models(root: Path, models: dict[str, str], actor_id: str) -> dict[str, str]:
    unknown = [name for name in models if name not in AGENT_ROLES]
    if unknown:
        raise ValueError("unknown agent(s): " + ", ".join(unknown))
    data = _load(root)
    current = {key: value for key, value in (data.get("models") or {}).items() if value}
    for role, model in models.items():
        if str(model).strip():
            current[role] = str(model).strip()
        else:
            current.pop(role, None)
    data["models"] = current
    data["updatedBy"] = actor_id
    _write(root, data)
    return current


def model_for(root: Path, agent: str) -> str:
    return stored(root).get(agent) or default_model()


def roles_view(root: Path) -> list[dict[str, Any]]:
    chosen = stored(root)
    env = default_model()
    rows = []
    for agent, meta in AGENT_ROLES.items():
        rows.append(
            {
                "role": agent,
                "label": meta["label"],
                "detail": meta["detail"],
                "model": chosen.get(agent) or env,
                "stored": chosen.get(agent),
                "environmentDefault": env,
                "source": "setting" if chosen.get(agent) else "environment",
            }
        )
    return rows


def cycle_cost(root: Path) -> dict[str, Any]:
    priced = {row["model"]: row for row in model_catalogue.choices()}
    agents = []
    total = 0.0
    for agent, usage in CYCLE_USAGE.items():
        model_id = model_for(root, agent)
        rates = priced.get(model_id) or {}
        prompt_m = float(rates.get("prompt_per_million") or _FALLBACK_MINI["prompt_per_million"])
        completion_m = float(
            rates.get("completion_per_million") or _FALLBACK_MINI["completion_per_million"]
        )
        if model_catalogue.is_free(model_id):
            prompt_m = completion_m = 0.0
        calls = usage["calls"]
        cost = (
            calls
            * (
                usage["prompt"] * prompt_m / 1_000_000
                + usage["completion"] * completion_m / 1_000_000
            )
        )
        total += cost
        agents.append(
            {
                "role": agent,
                "model": model_id,
                "calls": calls,
                "prompt": usage["prompt"],
                "completion": usage["completion"],
                "usd": round(cost, 6),
            }
        )
    return {
        "usd": round(total, 4),
        "currency": "USD",
        "assumption": "one full_governance cycle; OpenRouter list prices; agentless build",
        "agents": agents,
    }
