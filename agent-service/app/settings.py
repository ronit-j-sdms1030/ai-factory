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
    unknown = [role for role in models if role not in config.AGENT_ROLES]
    if unknown:
        raise ValueError(f"Unknown stage(s): {', '.join(unknown)}")

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


def model_for_role(role: str, artifact: dict[str, Any] | None = None) -> str:
    """The model a stage should use, most specific source winning."""
    override = ((artifact or {}).get("modelOverrides") or {}).get(role)
    if override:
        return override
    return stored_models().get(role) or config.default_model_for(role)


def roles_view(artifact: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every stage, what it will use, and where that came from — for the UI."""
    stored = stored_models()
    return [
        {
            "role": role,
            **meta,
            "model": model_for_role(role, artifact),
            "stored": stored.get(role),
            "environmentDefault": config.default_model_for(role),
            "source": (
                "artifact" if ((artifact or {}).get("modelOverrides") or {}).get(role)
                else "setting" if stored.get(role)
                else "environment"
            ),
        }
        for role, meta in config.AGENT_ROLES.items()
    ]
