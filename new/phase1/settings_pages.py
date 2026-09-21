"""Settings payloads for the canonical UI — always skill-file templates.

Git may be empty; OpenRouter may be down. This module still returns the shipped
skills so Settings is readable in the demo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import intake_skill
import skill_registry
from phase1 import agent_settings, model_catalogue

SKILLS = skill_registry.SHIPPED_SKILLS


def may_edit(actor: dict[str, Any]) -> bool:
    if actor.get("tierId") in {"po", "bo"}:
        return True
    return bool(set(actor.get("roles") or []) & {"product_owner", "business_owner"})


def _read(relative: str, fallback: str = "") -> str:
    path = SKILLS / relative
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return fallback


def models(root: Path) -> dict[str, Any]:
    try:
        choices = model_catalogue.choices()
    except Exception:
        choices = [
            {"model": model_id, "name": model_id, "detail": note, "note": note}
            for model_id, note in model_catalogue.NOTES.items()
        ]
    try:
        roles = agent_settings.roles_view(root)
        cost = agent_settings.cycle_cost(root)
    except Exception:
        roles = [
            {
                "role": name,
                "label": meta["label"],
                "detail": meta["detail"],
                "model": agent_settings.default_model(),
                "stored": None,
                "environmentDefault": agent_settings.default_model(),
                "source": "environment",
            }
            for name, meta in agent_settings.AGENT_ROLES.items()
        ]
        cost = {"usd": 0, "currency": "USD", "assumption": "unavailable"}
    return {"roles": roles, "choices": choices, "cycleCost": cost}


def intake_skill_doc(git: Any) -> dict[str, Any]:
    try:
        skill = git.skill()
        return {
            "content": skill.content,
            "isDefault": skill.is_default,
            "version": skill.version,
            "history": [],
        }
    except Exception:
        shipped = intake_skill.shipped()
        return {
            "content": shipped.content,
            "isDefault": True,
            "version": shipped.version,
            "history": [],
        }


def design_system_doc(git: Any) -> dict[str, Any]:
    try:
        content = git.design_system()
    except Exception:
        content = ""
    if not content.strip():
        content = _read("design-system.skill.md")
    return {
        "content": content,
        "isDefault": True,
        "version": "shipped",
        "history": [],
    }


def prompts(root: Path | None = None) -> list[dict[str, Any]]:
    rows = []
    for agent, meta in agent_settings.AGENT_ROLES.items():
        try:
            bundle = skill_registry.load_bundle(agent, root=root)
            content = bundle.content
            version = bundle.version
        except (KeyError, skill_registry.IncompleteSkillFile, OSError):
            content = _read(f"{agent}.skill.md") or (
                f"# {meta['label']}\n\n{meta['detail']}\n"
            )
            version = "shipped"
        rows.append(
            {
                "name": agent,
                "label": meta["label"],
                "agent": agent,
                "detail": meta["detail"],
                "content": content,
                "isDefault": True,
                "version": version,
            }
        )
    return rows
