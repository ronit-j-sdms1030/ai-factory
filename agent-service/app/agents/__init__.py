

def model_for(state: dict, role: str) -> str:
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

    override = (state.get("models") or {}).get(role)
    if override:
        return override
    try:
        from ..settings import stored_models

        return stored_models().get(role) or config.default_model_for(role)
    except Exception:  # noqa: BLE001 — a preference must not break generation
        return config.default_model_for(role)
