"""Deployment settings — currently which model runs each stage.

Restricted to MD, CEO and VP. Model choice is a cost-and-quality decision for
the whole pipeline rather than a personal preference: a change here affects
every requirement generated afterwards, so it belongs with the people
accountable for the output.

Deliberately not a client-visible setting at all. It trades Stark Digital's
cost against Stark Digital's quality, and a client has no basis for that
trade.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .. import config
from .. import settings as app_settings
from ..auth import current_user

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_TIERS = ("md", "ceo", "vp")


def _may_read(user: dict) -> bool:
    return not user.get("isClient")


def _require_editor(user: dict = Depends(current_user)) -> dict:
    if user.get("isClient") or user.get("tierId") not in SETTINGS_TIERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only MD, CEO or VP can change which models the pipeline uses",
        )
    return user


class ModelSettings(BaseModel):
    models: dict[str, str]


@router.get("/models")
def get_models(user: dict = Depends(current_user)):
    """Every stage, the model it will use, and where that came from.

    Readable by any internal user — knowing which model wrote a document is
    part of reading it — while changing one is restricted.
    """
    if not _may_read(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not available")
    return {
        "roles": app_settings.roles_view(),
        "choices": config.model_choices(),
        "canEdit": user.get("tierId") in SETTINGS_TIERS,
    }


@router.put("/models")
def put_models(body: ModelSettings, user: dict = Depends(_require_editor)):
    """Set or clear the model for one or more stages.

    An empty value clears the setting and returns that stage to the
    environment, so a choice can be undone without knowing what it reverts to.
    Any id the provider router understands is accepted, not only the listed
    choices — a model that turns out to suit a stage should be usable the day
    it appears, not after a deploy.
    """
    try:
        stored = app_settings.set_models(body.models, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"models": stored, "roles": app_settings.roles_view()}
