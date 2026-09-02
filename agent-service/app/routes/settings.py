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

from .. import config, design_system, lessons, prompts
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


class DesignSystem(BaseModel):
    content: str


@router.get("/design-system")
def get_design_system(user: dict = Depends(current_user)):
    """The skill file every generated screen is written against.

    Readable by any internal user: SoW 11.0 has UI/UX and Business Analysts
    reviewing the screens, and reviewing a screen against a design system you
    cannot read is guesswork.
    """
    if not _may_read(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not available")
    return {
        **design_system.current(),
        "history": design_system.history(),
        "canEdit": user.get("tierId") in SETTINGS_TIERS,
    }


@router.put("/design-system")
def put_design_system(body: DesignSystem, user: dict = Depends(_require_editor)):
    """Replace the skill file, recording who changed it.

    SoW 4.0 requires skill files to carry change history and approver
    identity, so the previous text is kept as a revision rather than
    overwritten. Submitting nothing restores the built-in default — the agent
    is never left with no design system at all.
    """
    return design_system.set_content(body.content, user["id"])


class PromptBody(BaseModel):
    content: str


@router.get("/prompts")
def get_prompts(user: dict = Depends(current_user)):
    """The tunable prompt fragments, with their versions and history.

    Readable by any internal user for the same reason the model is: knowing
    what instructions produced a document is part of reviewing it.
    """
    if not _may_read(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not available")
    return {
        "prompts": prompts.listing(),
        "canEdit": user.get("tierId") in SETTINGS_TIERS,
    }


@router.get("/prompts/{name}/history")
def get_prompt_history(name: str, user: dict = Depends(current_user)):
    if not _may_read(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not available")
    try:
        return {"history": prompts.history(name)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/prompts/{name}")
def put_prompt(name: str, body: PromptBody, user: dict = Depends(_require_editor)):
    """Change one fragment, recording who changed it.

    SoW 4.0 requires prompt templates to carry change history and approver
    identity — the previous text is kept as a revision rather than
    overwritten. Submitting nothing restores the built-in default, so a bad
    edit never has to be reconstructed from memory.
    """
    try:
        return prompts.set_text(name, body.content, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class LessonStatus(BaseModel):
    status: str


@router.get("/lessons")
def get_lessons(agent: str | None = None, user: dict = Depends(current_user)):
    """What the agents have been taught, and what they are waiting to be told.

    Visible to every internal user. A rule silently steering every future
    screen is the thing to avoid here — if an agent is following an
    instruction, anyone reading its output should be able to see why.
    """
    if not _may_read(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not available")
    return {"lessons": lessons.listing(agent), "canEdit": user.get("tierId") in SETTINGS_TIERS}


@router.put("/lessons/{lesson_id}")
def set_lesson_status(lesson_id: str, body: LessonStatus, user: dict = Depends(_require_editor)):
    """Activate a proposed rule, or dismiss one that is wrong.

    Dismissed rules are kept rather than deleted: a rule somebody rejected is
    itself a fact about this deployment, and deleting it invites the extractor
    to propose the same thing next week.
    """
    try:
        changed = lessons.set_status(lesson_id, body.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not changed:
        raise HTTPException(status_code=404, detail="No such lesson, or it already had that status")
    return {"ok": True, "lessons": lessons.listing()}
