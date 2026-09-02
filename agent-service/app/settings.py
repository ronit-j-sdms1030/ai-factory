"""Deployment settings that can be changed without a restart.

Model choice belongs here rather than only in the environment. Which model
suits a stage is learned by running it — this project moved three stages onto
different models on evidence in a single afternoon, each time by editing
config and restarting a service. A setting that requires a deploy to change
is one nobody changes.

Resolution order, most specific first:

1. the artifact's own ``modelOverrides`` — one requirement doing something unusual
2. the stored setting — what this deployment has settled on
3. the environment, then the code default — what it started from

Read on every call rather than cached, so a change takes effect on the next
generation instead of the next restart. These are a handful of short strings
fetched beside work that takes minutes; the round trip does not matter.
"""

from __future__ import annotations

from typing import Any

from . import config, db

_MODELS_KEY = "models"


def _collection():
    return db.db()["settings"]


def stored_models() -> dict[str, str]:
    """Model chosen per stage, for stages that have one. Empty is normal."""
    doc = _collection().find_one({"_id": _MODELS_KEY}) or {}
    return {k: v for k, v in (doc.get("models") or {}).items() if v}


def set_models(models: dict[str, str], actor_id: str) -> dict[str, str]:
    """Store or clear a model per stage.

    An empty value clears the setting and returns that stage to the
    environment, so a choice can be undone without knowing what it is
    reverting to.
    """
    unknown = [agent for agent in models if agent not in config.AGENT_ROLES]
    if unknown:
        raise ValueError(f"Unknown agent(s): {', '.join(unknown)}")

    current = stored_models()
    for role, model in models.items():
        if model.strip():
            current[role] = model.strip()
        else:
            current.pop(role, None)

    _collection().update_one(
        {"_id": _MODELS_KEY},
        {"$set": {"models": current, "updatedBy": actor_id}},
        upsert=True,
    )
    return current


def model_for_stage(stage: str, artifact: dict[str, Any] | None = None) -> str:
    """The model an internal stage should use, most specific source winning.

    Settings are keyed by *agent*, not by stage: choosing a model for the
    intake agent sets it for both the conversation and the structuring it
    does. A stage no agent owns — the screen plan — follows the environment,
    because the thing that suits writing React is measurably wrong for
    planning a schema.
    """
    owner = config.STAGE_OWNER.get(stage)
    overrides = (artifact or {}).get("modelOverrides") or {}
    chosen = overrides.get(stage) or (overrides.get(owner) if owner else None)
    if chosen:
        return chosen
    stored = stored_models().get(owner) if owner else None
    return stored or config.default_model_for(stage)


def roles_view(artifact: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Each agent, the model it will use, and where that came from — for the UI."""
    stored = stored_models()
    overrides = (artifact or {}).get("modelOverrides") or {}
    view = []
    for agent, meta in config.AGENT_ROLES.items():
        primary = meta["stages"][0]
        view.append(
            {
                "role": agent,
                "label": meta["label"],
                "detail": meta["detail"],
                "model": model_for_stage(primary, artifact),
                "stored": stored.get(agent),
                "environmentDefault": config.default_model_for(primary),
                "source": (
                    "artifact" if overrides.get(agent) or overrides.get(primary)
                    else "setting" if stored.get(agent)
                    else "environment"
                ),
            }
        )
    return view
