

def model_for(state: dict, role: str) -> str:
    """The model this run should use for ``role``.

    A requirement may override any stage; anything it does not name falls back
    to the deployment default. Resolved per call rather than read once at
    import, so changing an override and regenerating actually uses the new
    model.
    """
    from .. import config

    return (state.get("models") or {}).get(role) or config.default_model_for(role)
