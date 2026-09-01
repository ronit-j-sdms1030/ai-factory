"""Artifact endpoints — creation, visibility, guided intake, and approvals.

Port of ``backend/src/routes/artifact.routes.js``, scoped to intake through
the FSD split. Code generation is out of scope and lives in the Express
backend.

Paths and response shapes match the JavaScript so the existing frontend works
against either service.
"""

from __future__ import annotations

import logging
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel

from .. import db
from ..agents.brd import brd_agent
from ..agents.decomposition import decomposition_agent
from ..agents.intake import finalize_requirement, run_chat_turn
from ..auth import actor_from, current_user
from ..config import originator_label, resolve_approval_chain
from ..state_machine import TransitionError, transition

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

OPENING_LINES = {
    "client": "Tell me what you're picturing — even rough is fine. I'll help shape it into something buildable.",
    "internal": "Describe the capability or system you need. I'll ask a few follow-ups, then structure it into a requirement ready for review.",
}

KNOWN_ACTIONS = {
    "approve", "reject", "requestRevision", "resubmit", "submit",
    "proposeChanges", "acceptChanges", "editFsd", "sendFsdToClient",
    "approveFsd", "giveFinalFsdApproval", "regenerateFsd",
    "regenerateTeamSplit", "requestTeamRevision",
}


# ── helpers ──────────────────────────────────────────────────────────────────
def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Artifact not found")


def _load(artifact_id: str) -> dict[str, Any]:
    found = db.artifacts().find_one({"_id": _oid(artifact_id)})
    if not found:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return found


def _save(artifact: dict[str, Any]) -> None:
    db.artifacts().replace_one({"_id": artifact["_id"]}, artifact)


def _cleared_md_ceo_gate(step: dict[str, Any] | None) -> bool:
    return bool(step) and any(t in ("md", "ceo") for t in step.get("approverTiers", []))


def _chain_never_has_md_ceo_gate(chain: list[dict[str, Any]]) -> bool:
    return not any(t in ("md", "ceo") for s in chain for t in s.get("approverTiers", []))


def _is_self_origin_md_ceo(artifact: dict[str, Any]) -> bool:
    return (artifact.get("originator") or {}).get("tierId") in ("md", "ceo")


def _visible_team_reports(artifact: dict[str, Any], actor: dict[str, Any]) -> list:
    """Only a TL sees team packages here, and only their own department's.

    Everyone else receives an empty list — VP and MD read team data through
    the code-generation endpoints instead. Reproduced from the JavaScript
    rather than widened, since changing it would alter what the workspace
    shows each role.
    """
    if not actor or actor.get("isClient") or actor.get("tierId") != "tl" or not actor.get("department"):
        return []
    shared = {
        s.get("sharedTeam")
        for s in artifact.get("discussionShares") or []
        if s.get("toUserId") == actor["id"] and s.get("sharedTeam")
    }
    return [
        r for r in artifact.get("teamReports") or []
        if r.get("team") == actor["department"] or r.get("team") in shared
    ]


def _redact(artifact: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    out = dict(artifact)
    out["_id"] = str(artifact["_id"])

    can_join_discussion = bool(
        actor and not actor.get("isClient") and (
            (actor.get("tierId") == "tl" and actor.get("department")
             and any(r.get("team") == actor["department"] for r in artifact.get("teamReports") or []))
            or (actor.get("tierId") in ("md", "ceo", "vp") and (artifact.get("discussionMessages") or []))
        )
    )

    out["teamReports"] = _visible_team_reports(artifact, actor)
    out["teamReportEditHistory"] = (
        [e for e in artifact.get("teamReportEditHistory") or [] if e.get("department") == actor.get("department")]
        if actor.get("tierId") == "tl" and actor.get("department") else []
    )
    out["discussionMessages"] = artifact.get("discussionMessages") or [] if can_join_discussion else []
    return out


def _respond(artifact: dict[str, Any], actor: dict[str, Any], errors: dict | None = None) -> dict:
    payload: dict[str, Any] = {"artifact": _redact(artifact, actor)}
    for key, value in (errors or {}).items():
        if value:
            payload[key] = value
    return payload


# ── generation triggers ──────────────────────────────────────────────────────
def _maybe_generate_report_and_split(artifact: dict[str, Any], step_acted_on: dict | None) -> dict:
    """Generate the BRD once a gate clears, then the split once the chain finishes.

    Best-effort: a generation failure is returned to the caller but never
    undoes the approval that already saved, matching the JavaScript. Losing an
    approval because a model call timed out would be far worse than a missing
    document the user can regenerate.
    """
    errors: dict[str, str] = {}
    ready_for_report = _cleared_md_ceo_gate(step_acted_on) or (
        artifact.get("currentStage") == "approved"
        and _chain_never_has_md_ceo_gate(artifact.get("approvalChain") or [])
    )

    if not artifact.get("detailedReport") and ready_for_report:
        try:
            state = {
                "requirement": artifact.get("content") or {},
                "chat_history": [
                    {"role": m["role"], "content": m["content"]} for m in artifact.get("chatHistory") or []
                ],
            }
            artifact["detailedReport"] = brd_agent(state)["brd"]
            artifact["detailedReportGeneratedAt"] = _utcnow()
            # Every requirement goes through the FSD review loop, including a
            # self-originated MD/CEO one — that stage is the only place "Edit
            # FSD" is reachable, so skipping it would strand the originator
            # with a finished document and no way to revise it.
            artifact["currentStage"] = "fsd_review"
        except Exception as exc:  # noqa: BLE001 — surfaced, never fatal
            log.exception("detailed report generation failed")
            errors["detailedReportError"] = str(exc)

    if artifact.get("currentStage") == "approved" and not artifact.get("teamReports"):
        errors.update(_maybe_split(artifact))

    return errors


def _maybe_split(artifact: dict[str, Any]) -> dict:
    if not artifact.get("detailedReport"):
        return {}
    try:
        result = decomposition_agent({"brd": artifact["detailedReport"]})["work_items"]
        artifact["teamReports"] = result.get("packages", [])
        artifact["workItems"] = result.get("work_items", [])
        artifact["workItemIntegrity"] = result.get("integrity", {})
        artifact["teamReportsGeneratedAt"] = _utcnow()
        return {}
    except Exception as exc:  # noqa: BLE001
        log.exception("team split failed")
        return {"teamSplitError": str(exc)}


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _submit_and_maybe_self_approve(artifact: dict[str, Any], actor: dict[str, Any]) -> dict:
    _save(artifact)
    if not _is_self_origin_md_ceo(artifact) or artifact.get("currentStage") != "pending_approval":
        return {}
    step = (artifact.get("approvalChain") or [])[artifact.get("currentApprovalIndex", 0)]
    transition(
        artifact, "approve",
        {"user_id": actor["id"], "tier_id": (artifact["originator"] or {}).get("tierId")},
        comment="Self-approved on submission",
    )
    _save(artifact)
    errors = _maybe_generate_report_and_split(artifact, step)
    _save(artifact)
    return errors


# ── endpoints ────────────────────────────────────────────────────────────────
class CreateBody(BaseModel):
    title: str
    content: Any
    type: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED)
def create(body: CreateBody, actor: dict = Depends(current_user)):
    if not body.title or body.content is None:
        raise HTTPException(status_code=400, detail="title and content are required")

    originator_tier = None if actor.get("isClient") else actor.get("tierId")
    try:
        chain = resolve_approval_chain(originator_tier)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    artifact: dict[str, Any] = {
        "_id": ObjectId(),
        "type": "fsd" if body.type == "fsd" else "idea",
        "title": body.title,
        "content": body.content,
        "originator": {"userId": actor["id"], "tierId": originator_tier},
        "currentStage": "draft",
        "approvalChain": [
            {"approverTiers": s.approver_tiers, "mode": s.mode, "approvedBy": []} for s in chain
        ],
        "currentApprovalIndex": 0,
        "history": [], "chatHistory": [], "fsdChatHistory": [],
        "teamReports": [], "teamReportEditHistory": [], "teamRevisionRequests": [],
        "discussionMessages": [], "discussionRecipients": [], "discussionShares": [],
        "createdAt": _utcnow(), "updatedAt": _utcnow(),
    }

    try:
        transition(artifact, "submit", actor_from(actor), comment="Initial submission")
    except TransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    errors = _submit_and_maybe_self_approve(artifact, actor)
    return _respond(artifact, actor, errors)


@router.get("")
@router.get("/")
def list_artifacts(actor: dict = Depends(current_user)):
    """Mine, plus anything waiting on my tier, plus anything I previously acted on.

    The clauses below mirror the JavaScript exactly. Each exists for a reason:
    an MD who cleared gate 0 must not lose sight of the artifact once the
    chain moves to VP, and VP oversees every approved requirement's production
    even where VP was never the gating tier.
    """
    tier = actor.get("tierId")

    if actor.get("isClient"):
        query: dict[str, Any] = {"originator.userId": actor["id"]}
    else:
        clauses: list[dict[str, Any]] = [
            {"originator.userId": actor["id"]},
            {"currentStage": "pending_approval", "approvalChain.approverTiers": tier},
            {"history.actorId": actor["id"]},
            {"discussionRecipients": actor["id"]},
        ]
        if tier in ("md", "ceo", "vp"):
            clauses.append({
                "currentStage": {"$in": ["fsd_review", "fsd_final_approval"]},
                "approvalChain.0.approverTiers": tier,
            })
            clauses.append({"discussionMessages.0": {"$exists": True}})
        if tier == "vp":
            clauses.append({"currentStage": "team_revision_requested"})
            # VP oversees production for every approved requirement, not only
            # those VP gated — an MD can self-approve and never touch VP's queue.
            clauses.append({
                "currentStage": "approved",
                "teamReportsGeneratedAt": {"$exists": True, "$ne": None},
            })
        if tier in ("md", "ceo"):
            clauses.append({"originator.tierId": {"$in": ["md", "ceo"]}})
        if tier == "tl" and actor.get("department"):
            clauses.append({"teamReports.team": actor["department"]})
        query = {"$or": clauses}

    found = db.artifacts().find(query).sort("updatedAt", -1)
    return {"artifacts": [_redact(a, actor) for a in found]}


@router.post("/chat/start")
def chat_start(actor: dict = Depends(current_user)):
    """Open a guided intake. The first line is canned — no model call needed."""
    opener = OPENING_LINES["client" if actor.get("isClient") else "internal"]
    originator_tier = None if actor.get("isClient") else actor.get("tierId")

    artifact = {
        "_id": ObjectId(),
        "type": "idea",
        "title": "Untitled requirement",
        "content": {},
        "originator": {"userId": actor["id"], "tierId": originator_tier},
        "currentStage": "clarifying",
        "approvalChain": [
            {"approverTiers": s.approver_tiers, "mode": s.mode, "approvedBy": []}
            for s in resolve_approval_chain(originator_tier)
        ],
        "currentApprovalIndex": 0,
        "history": [],
        "chatHistory": [{"role": "assistant", "content": opener, "timestamp": _utcnow()}],
        "fsdChatHistory": [], "teamReports": [], "teamReportEditHistory": [],
        "teamRevisionRequests": [], "discussionMessages": [],
        "discussionRecipients": [], "discussionShares": [],
        "createdAt": _utcnow(), "updatedAt": _utcnow(),
    }
    db.artifacts().insert_one(artifact)
    return {"artifactId": str(artifact["_id"]), "message": opener}


@router.post("/chat/{artifact_id}/message")
def chat_message(artifact_id: str, message: str = Body(..., embed=True), actor: dict = Depends(current_user)):
    artifact = _load(artifact_id)
    if (artifact.get("originator") or {}).get("userId") != actor["id"]:
        raise HTTPException(status_code=403, detail="Only the originator can continue this conversation")
    if artifact.get("currentStage") != "clarifying":
        raise HTTPException(status_code=400, detail="This conversation has already been finalized")

    artifact.setdefault("chatHistory", []).append(
        {"role": "user", "content": message, "timestamp": _utcnow()}
    )

    history = [{"role": m["role"], "content": m["content"]} for m in artifact["chatHistory"]]
    label = originator_label(None if actor.get("isClient") else actor.get("tierId"))

    try:
        turn = run_chat_turn(history, label)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))

    if turn["type"] == "reply":
        artifact["chatHistory"].append(
            {"role": "assistant", "content": turn["text"], "timestamp": _utcnow()}
        )
        artifact["updatedAt"] = _utcnow()
        _save(artifact)
        return {"type": "reply", "message": turn["text"]}

    try:
        requirement = finalize_requirement(history)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))

    artifact["title"] = requirement.title
    artifact["content"] = requirement.model_dump()
    artifact["updatedAt"] = _utcnow()
    _save(artifact)
    return {"type": "ready", "artifact": _redact(artifact, actor)}


class ActionBody(BaseModel):
    comment: str | None = None
    finalApproverTier: str | None = None


@router.post("/{artifact_id}/{action}")
def act(artifact_id: str, action: str, body: ActionBody | None = None, actor: dict = Depends(current_user)):
    if action not in KNOWN_ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown action")

    artifact = _load(artifact_id)
    body = body or ActionBody()

    if action in ("regenerateFsd", "regenerateTeamSplit"):
        errors = _regenerate(artifact, action)
        artifact["updatedAt"] = _utcnow()
        _save(artifact)
        return _respond(artifact, actor, errors)

    step_acted_on = (artifact.get("approvalChain") or [None])[artifact.get("currentApprovalIndex", 0)] \
        if artifact.get("currentApprovalIndex", 0) < len(artifact.get("approvalChain") or []) else None

    try:
        transition(
            artifact, action, actor_from(actor),
            comment=body.comment or "",
            final_approver_tier=body.finalApproverTier,
        )
    except TransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    _save(artifact)
    errors = _maybe_generate_report_and_split(artifact, step_acted_on)
    artifact["updatedAt"] = _utcnow()
    _save(artifact)
    return _respond(artifact, actor, errors)


def _regenerate(artifact: dict[str, Any], action: str) -> dict:
    if action == "regenerateFsd":
        if not artifact.get("content"):
            raise HTTPException(status_code=400, detail="No approved requirement to regenerate from")
        try:
            state = {
                "requirement": artifact["content"],
                "chat_history": [
                    {"role": m["role"], "content": m["content"]} for m in artifact.get("chatHistory") or []
                ],
            }
            artifact["detailedReport"] = brd_agent(state)["brd"]
            artifact["detailedReportGeneratedAt"] = _utcnow()
            # A regenerated FSD invalidates the split derived from the old one.
            artifact["teamReports"] = []
            artifact["teamReportsGeneratedAt"] = None
            return {}
        except Exception as exc:  # noqa: BLE001
            return {"detailedReportError": str(exc)}

    artifact["teamReports"] = []
    return _maybe_split(artifact)
