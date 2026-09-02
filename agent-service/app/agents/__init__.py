

def model_for(state: dict, stage: str) -> str:
    """The model this run should use for ``role``.

    Most specific source wins: a requirement's own override, then the stored
    deployment setting, then the environment. Resolved per call rather than
    read at import, so changing a setting and regenerating uses the new model
    without a restart.

    Falls back to the environment if the settings store cannot be reached —
    losing a generation because a preference lookup failed would be a poor
    trade for a preference.
    """
    from .. import config

    try:
        from ..settings import model_for_stage

        return model_for_stage(stage, {"modelOverrides": state.get("models") or {}})
    except Exception:  # noqa: BLE001 — a preference must not break generation
        return (state.get("models") or {}).get(stage) or config.default_model_for(stage)
