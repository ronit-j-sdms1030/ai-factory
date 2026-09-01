"""Artifact endpoints — creation, visibility, guided intake, and approvals.

Port of ``backend/src/routes/artifact.routes.js``, scoped to intake through
the FSD split. Code generation is out of scope and lives in the Express
backend.

Paths and response shapes match the JavaScript so the existing frontend works
against either service.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from .. import db, jobs
from ..agents.brd import brd_agent
from ..agents.decomposition import decomposition_agent
from ..agents.edits import run_fsd_chat_edit, run_team_report_chat_edit
from ..agents.intake import finalize_requirement, run_chat_turn
from ..agents.ui import ui_agent
from ..auth import actor_from, current_user
from ..config import TIERS, originator_label, resolve_approval_chain
from ..edit_ops import EditPathError, apply_edit_operation, normalize_operation
from ..publish import publish
from ..ui_preview import CSP as PREVIEW_CSP
from ..ui_preview import build_preview
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
    "regenerateTeamSplit", "regenerateUi", "requestTeamRevision",
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
    # Upsert, because ``create`` builds its artifact in memory and never
    # inserts it separately: a plain replace matched nothing, so the endpoint
    # returned 201 with an _id that was never written. Every other caller
    # passes a document already in the collection, where this is a no-op.
    db.artifacts().replace_one({"_id": artifact["_id"]}, artifact, upsert=True)


def _ready_for_report(artifact: dict[str, Any], step_acted_on: dict | None) -> bool:
    """Has a gate cleared that entitles this requirement to a BRD?"""
    return _cleared_md_ceo_gate(step_acted_on) or (
        artifact.get("currentStage") == "approved"
        and _chain_never_has_md_ceo_gate(artifact.get("approvalChain") or [])
    )


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


def _ui_summary(ui: dict[str, Any] | None) -> dict[str, Any] | None:
    """The screens without their source.

    A generated design is around 300KB of React, and the list endpoint returns
    every artifact the viewer can see — so sending the source meant a few
    requirements made the workspace's own page load slower than the pipeline
    that produced them. Nothing in the list needs the code; it needs to know
    the screens exist and what they are called. The source is served by the
    preview endpoint, once, when somebody actually looks at it.
    """
    if not ui:
        return None
    return {
        "screens": [
            {"name": s.get("name"), "route": s.get("route"), "purpose": s.get("purpose")}
            for s in ui.get("screens") or []
        ],
        "clarifications": ui.get("clarifications") or [],
    }


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

    out["ui"] = _ui_summary(artifact.get("ui"))
    out["teamReports"] = _visible_team_reports(artifact, actor)
    out["teamReportEditHistory"] = (
        [e for e in artifact.get("teamReportEditHistory") or [] if e.get("department") == actor.get("department")]
        if actor.get("tierId") == "tl" and actor.get("department") else []
    )
    out["discussionMessages"] = artifact.get("discussionMessages") or [] if can_join_discussion else []
    return out


def _respond(
    artifact: dict[str, Any],
    actor: dict[str, Any],
    errors: dict | None = None,
    job_id: str | None = None,
) -> dict:
    payload: dict[str, Any] = {"artifact": _redact(artifact, actor)}
    for key, value in (errors or {}).items():
        if value:
            payload[key] = value
    if job_id:
        # The transition is committed; the document it produces is not written
        # yet. Poll ``GET /{id}/jobs`` for that.
        payload["jobId"] = job_id
        payload["generating"] = True
    return payload


# ── generation triggers ──────────────────────────────────────────────────────
def _maybe_generate_report_and_split(
    artifact: dict[str, Any],
    step_acted_on: dict | None,
    progress: Callable[[str], None] = lambda _: None,
) -> dict:
    """Generate the BRD once a gate clears, then the split once the chain finishes.

    Best-effort: a generation failure is returned to the caller but never
    undoes the approval that already saved, matching the JavaScript. Losing an
    approval because a model call timed out would be far worse than a missing
    document the user can regenerate.

    Runs on a job thread (see ``jobs``), so ``progress`` is how it reports
    which phase it is in — the caller's HTTP request finished long before.
    """
    errors: dict[str, str] = {}

    if not artifact.get("detailedReport") and _ready_for_report(artifact, step_acted_on):
        progress("brd")
        try:
            state = {
                "requirement": artifact.get("content") or {},
                "chat_history": [
                    {"role": m["role"], "content": m["content"]} for m in artifact.get("chatHistory") or []
                ],
            }
            brd = brd_agent(state)["brd"]
            artifact["detailedReport"] = brd
            artifact["detailedReportGeneratedAt"] = _utcnow()
            _record_publish(artifact, errors, "brd",
                            publish(artifact=artifact, stage="brd",
                                    build_files=lambda store: store.brd_files(brd)))
            # Every requirement goes through the FSD review loop, including a
            # self-originated MD/CEO one — that stage is the only place "Edit
            # FSD" is reachable, so skipping it would strand the originator
            # with a finished document and no way to revise it.
            artifact["currentStage"] = "fsd_review"
        except Exception as exc:  # noqa: BLE001 — surfaced, never fatal
            log.exception("detailed report generation failed")
            errors["detailedReportError"] = str(exc)

    if artifact.get("currentStage") == "approved":
        # Screens before work items: the decomposition is scoped against the
        # approved interface, per architecture.md §2.3. SoW 12.0 additionally
        # requires UI approval to *block* code generation — that gate does not
        # exist in this state machine yet, so the UI is produced and published
        # for review but does not currently hold anything back.
        if not artifact.get("ui"):
            progress("ui")
            errors.update(_maybe_generate_ui(artifact))
        if not artifact.get("teamReports"):
            progress("workitems")
            errors.update(_maybe_split(artifact))

    return errors


def _maybe_generate_ui(artifact: dict[str, Any]) -> dict:
    """Generate the application's screens from the approved BRD."""
    if not artifact.get("detailedReport"):
        return {}
    try:
        ui = ui_agent({"brd": artifact["detailedReport"]})["ui"]
        artifact["ui"] = ui
        artifact["uiGeneratedAt"] = _utcnow()
        errors: dict[str, str] = {}
        _record_publish(artifact, errors, "ui",
                        publish(artifact=artifact, stage="ui",
                                build_files=lambda store: store.ui_files(ui, artifact.get("title") or "Untitled"),
                                body_extra=_clarifications_summary(ui)))
        return errors
    except Exception as exc:  # noqa: BLE001
        log.exception("UI generation failed")
        return {"uiError": str(exc)}


def _clarifications_summary(ui: dict[str, Any]) -> str:
    """Put the agent's open questions in the pull request, where a reviewer sees them.

    SoW 11.0 requires the UI agent to raise clarifications where the BRD is
    ambiguous rather than guessing silently; surfacing them at the point of
    approval is what makes that useful.
    """
    screens = ui.get("screens") or []
    lines = [f"**Screens** ({len(screens)}): " + ", ".join(s.get("name", "?") for s in screens)]
    clarifications = ui.get("clarifications") or []
    if clarifications:
        lines += ["", "**Clarifications needed**", *(f"- {c}" for c in clarifications)]
    return "\n".join(lines)


def _maybe_split(artifact: dict[str, Any]) -> dict:
    if not artifact.get("detailedReport"):
        return {}
    try:
        result = decomposition_agent({"brd": artifact["detailedReport"]})["work_items"]
        artifact["teamReports"] = result.get("packages", [])
        artifact["workItems"] = result.get("workItems", [])
        artifact["workItemIntegrity"] = result.get("integrity", {})
        artifact["teamReportsGeneratedAt"] = _utcnow()
        errors: dict[str, str] = {}
        _record_publish(artifact, errors, "workitems",
                        publish(artifact=artifact, stage="workitems",
                                build_files=lambda store: store.workitem_files(result),
                                body_extra=_integrity_summary(result.get("integrity") or {})))
        return errors
    except Exception as exc:  # noqa: BLE001
        log.exception("team split failed")
        return {"teamSplitError": str(exc)}


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _pending_phase(artifact: dict[str, Any], step_acted_on: dict | None) -> str | None:
    """The first thing generation would do, or None if it would do nothing.

    Shares ``_ready_for_report`` with the generator itself so the two cannot
    disagree about whether there is work — a job that claims to be generating
    a BRD and then generates nothing is a worse lie than no job at all.
    """
    if not artifact.get("detailedReport") and _ready_for_report(artifact, step_acted_on):
        return "brd"
    if artifact.get("currentStage") == "approved":
        if not artifact.get("ui"):
            return "ui"
        if not artifact.get("teamReports"):
            return "workitems"
    return None


def _start_generation(artifact: dict[str, Any], step_acted_on: dict | None) -> str | None:
    """Run the model work on a background job, returning its id.

    The artifact passed in has already been saved. The job works on a private
    copy and writes its own results back, because by the time it finishes the
    request that started it is long gone and this dictionary is stale.
    """
    phase = _pending_phase(artifact, step_acted_on)
    if phase is None:
        return None

    snapshot = copy.deepcopy(artifact)

    def work(progress: Callable[[str], None]) -> dict[str, str]:
        working = copy.deepcopy(snapshot)
        errors = _maybe_generate_report_and_split(working, step_acted_on, progress)
        _apply_generated(snapshot, working)
        return errors

    return jobs.start(str(artifact["_id"]), phase, work)


def _apply_generated(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Persist only what generation actually changed.

    A whole-document replace would undo anything that happened while the model
    was working — a comment, a revision request, another gate — because this
    job's copy of the artifact predates it. Writing just the changed keys
    keeps the blast radius to the fields generation owns.
    """
    changed = {k: v for k, v in after.items() if k != "_id" and before.get(k) != v}
    if not changed:
        return
    changed["updatedAt"] = _utcnow()

    # The stage is the one field where a late write has governance
    # consequences, so it is only applied if nobody moved the requirement
    # while we were generating. Everything else is content and safe to land.
    stage = changed.pop("currentStage", None)
    if stage is not None:
        result = db.artifacts().update_one(
            {"_id": before["_id"], "currentStage": before.get("currentStage")},
            {"$set": {**changed, "currentStage": stage}},
        )
        if result.matched_count:
            return
        log.warning(
            "artifact %s moved on from %s while generating; keeping the new stage",
            before["_id"], before.get("currentStage"),
        )

    db.artifacts().update_one({"_id": before["_id"]}, {"$set": changed})


def _submit_and_maybe_self_approve(artifact: dict[str, Any], actor: dict[str, Any]) -> str | None:
    _save(artifact)
    if not _is_self_origin_md_ceo(artifact) or artifact.get("currentStage") != "pending_approval":
        return None
    step = (artifact.get("approvalChain") or [])[artifact.get("currentApprovalIndex", 0)]
    transition(
        artifact, "approve",
        {"user_id": actor["id"], "tier_id": (artifact["originator"] or {}).get("tierId")},
        comment="Self-approved on submission",
    )
    _save(artifact)
    return _start_generation(artifact, step)



def _record_publish(artifact: dict[str, Any], errors: dict[str, str], stage: str, result) -> None:
    """Attach the pull request to the artifact, or surface why there isn't one.

    Publishing is best-effort — the artefact is already saved in Mongo, and the
    approval flow must not stall because GitHub was unreachable. A failure is
    reported to the caller rather than raised.
    """
    if result.pull_request_url:
        artifact.setdefault("pullRequests", {})[stage] = result.pull_request_url
    if result.branch:
        artifact.setdefault("gitBranches", {})[stage] = result.branch
    if result.error:
        errors[f"{stage}PublishError"] = result.error
        log.warning("publish of %s failed: %s", stage, result.error)


def _integrity_summary(integrity: dict[str, Any]) -> str:
    """Surface decomposition defects in the pull request body, where a reviewer sees them."""
    problems = []
    if integrity.get("unowned"):
        problems.append(f"- Entities owned by nobody: {', '.join(integrity['unowned'])}")
    if integrity.get("multiply_owned"):
        problems.append(f"- Entities with multiple owners: {', '.join(integrity['multiply_owned'])}")
    if integrity.get("inconsistent_spelling"):
        problems.append(f"- Inconsistent entity spelling: {'; '.join(integrity['inconsistent_spelling'])}")
    if integrity.get("cycles"):
        problems.append(f"- Dependency cycles: {integrity['cycles']}")
    if integrity.get("dangling"):
        problems.append(f"- Dangling dependencies: {', '.join(integrity['dangling'])}")
    return "**Integrity checks**\n" + ("\n".join(problems) if problems else "- All checks passed.")


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

    job_id = _submit_and_maybe_self_approve(artifact, actor)
    return _respond(artifact, actor, job_id=job_id)


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
    # "reply", not "message": the frontend reads `body.reply`, and assigning
    # its undefined result to textContent renders an empty bubble rather than
    # an error — so a renamed key here is silently invisible.
    return {"artifactId": str(artifact["_id"]), "reply": opener}


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
        return {"done": False, "reply": turn["text"]}

    try:
        requirement = finalize_requirement(history)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))

    artifact["title"] = requirement.title
    artifact["content"] = requirement.model_dump(by_alias=True)
    # Recorded in the transcript as well as shown, so reopening the session
    # does not lose the closing message. Stays in 'clarifying': the originator
    # reviews this summary and sends it explicitly via /submit.
    artifact["chatHistory"].append(
        {
            "role": "assistant",
            "content": "Here's a summary of what I've captured. Review it below and send it when you're ready.",
            "timestamp": _utcnow(),
        }
    )
    artifact["updatedAt"] = _utcnow()
    _save(artifact)

    published: dict[str, str] = {}
    _record_publish(artifact, published, "requirement",
                    publish(artifact=artifact, stage="requirement",
                            build_files=lambda store: store.requirement_files(
                                artifact["content"],
                                "\n".join(f"{m['role']}: {m['content']}" for m in artifact.get("chatHistory") or []),
                            )))
    # _record_publish writes the branch and pull request onto the artifact, so
    # it has to be saved again. Without this the requirement was committed,
    # pushed and opened as a pull request, and the artifact still showed no
    # Git links at all — the record existed everywhere except the one place
    # anyone looks.
    _save(artifact)
    return {"reviewReady": True, "artifact": _redact(artifact, actor), **published}


class ActionBody(BaseModel):
    comment: str | None = None
    finalApproverTier: str | None = None


@router.get("/{artifact_id}/ui/preview")
def ui_preview(artifact_id: str, actor: dict = Depends(current_user)):
    """The generated screens, assembled into one runnable page.

    Served as HTML rather than JSON because the point is to look at it. The
    Content-Security-Policy sandboxes the generated code the same way the
    JavaScript project demo does: it may render and run, but it cannot reach
    the network or the parent page.
    """
    artifact = _load(artifact_id)
    return Response(
        content=build_preview(artifact.get("ui") or {}, artifact.get("title") or "Untitled"),
        media_type="text/html; charset=utf-8",
        headers={"Content-Security-Policy": PREVIEW_CSP, "Cache-Control": "no-store"},
    )


@router.get("/{artifact_id}/jobs")
def artifact_jobs(artifact_id: str, actor: dict = Depends(current_user)):
    """What is being generated for this requirement, and what failed.

    Declared before the ``/{artifact_id}/{action}`` catch-all so "jobs" is not
    swallowed as an action name.
    """
    _load(artifact_id)  # 404 for an artifact that does not exist
    found = jobs.for_artifact(artifact_id)
    return {"jobs": found, "generating": any(j["status"] == "running" for j in found)}


_REGENERATION_KINDS = {"regenerateFsd": "brd", "regenerateUi": "ui", "regenerateTeamSplit": "workitems"}


def _start_regeneration(artifact: dict[str, Any], action: str) -> str:
    """Regeneration is the same model work as a first pass, so it runs the same way."""
    if action == "regenerateFsd" and not artifact.get("content"):
        raise HTTPException(status_code=400, detail="No approved requirement to regenerate from")
    if action in ("regenerateUi", "regenerateTeamSplit") and not artifact.get("detailedReport"):
        raise HTTPException(status_code=400, detail="No approved FSD to regenerate from")

    snapshot = copy.deepcopy(artifact)

    def work(progress: Callable[[str], None]) -> dict[str, str]:
        working = copy.deepcopy(snapshot)
        errors = _regenerate(working, action)
        _apply_generated(snapshot, working)
        return errors

    return jobs.start(str(artifact["_id"]), _REGENERATION_KINDS[action], work)


def _regenerate(artifact: dict[str, Any], action: str) -> dict:
    if action == "regenerateFsd":
        errors: dict[str, str] = {}
        try:
            state = {
                "requirement": artifact["content"],
                "chat_history": [
                    {"role": m["role"], "content": m["content"]} for m in artifact.get("chatHistory") or []
                ],
            }
            brd = brd_agent(state)["brd"]
            artifact["detailedReport"] = brd
            artifact["detailedReportGeneratedAt"] = _utcnow()
            _record_publish(artifact, errors, "brd",
                            publish(artifact=artifact, stage="brd",
                                    build_files=lambda store: store.brd_files(brd)))
            # A regenerated FSD invalidates the split derived from the old one.
            artifact["teamReports"] = []
            artifact["teamReportsGeneratedAt"] = None
            return errors
        except Exception as exc:  # noqa: BLE001
            return {"detailedReportError": str(exc)}

    if action == "regenerateUi":
        # Cleared first so a failed regeneration leaves nothing behind
        # pretending to be current — the previous screens were the reason for
        # rerunning, and keeping them on a failure is how a stale design gets
        # approved by mistake.
        artifact["ui"] = None
        return _maybe_generate_ui(artifact)

    artifact["teamReports"] = []
    return _maybe_split(artifact)


# ── conversational editing and discussion ────────────────────────────────────
def _has_discussion_access(artifact: dict[str, Any], actor: dict[str, Any]) -> bool:
    """Mirrors the visibility rules in the list query.

    Shared by the discussion endpoints so access can never drift out of sync
    with what the list endpoint actually shows someone.
    """
    chain = artifact.get("approvalChain") or [{}]
    return (
        (artifact.get("originator") or {}).get("userId") == actor["id"]
        or actor.get("tierId") in (chain[0].get("approverTiers") or [])
        or (actor.get("tierId") in ("md", "ceo")
            and (artifact.get("originator") or {}).get("tierId") in ("md", "ceo"))
        or any(r.get("team") == actor.get("department") for r in artifact.get("teamReports") or [])
        or actor["id"] in (artifact.get("discussionRecipients") or [])
    )


def _fsd_actor_role(artifact: dict[str, Any], actor: dict[str, Any]) -> str | None:
    """Who is editing, and in what capacity — or None if the FSD is not open to them."""
    stage = artifact.get("currentStage")
    originator = artifact.get("originator") or {}
    chain = artifact.get("approvalChain") or [{}]
    tier = actor.get("tierId")

    if originator.get("userId") == actor["id"] and stage == "fsd_pending_client":
        return "the client" if actor.get("isClient") else "the originator"
    if not actor.get("isClient") and stage == "fsd_review" and tier in (chain[0].get("approverTiers") or []):
        return f"the {TIERS.get(tier, tier)} ({str(tier).upper()})"
    if not actor.get("isClient") and tier == "vp" and stage == "team_revision_requested":
        return "the Vice President (VP), revising the FSD after Team Lead feedback"
    # A VP holding a reviewed FSD at gate 1 may edit before releasing it.
    if (not actor.get("isClient") and tier == "vp" and stage == "pending_approval"
            and artifact.get("currentApprovalIndex", 0) > 0
            and originator.get("tierId") in ("md", "ceo", "tl")
            and artifact.get("detailedReport")):
        return "the Vice President (VP), editing the FSD before sending it to Team Leads"
    return None


@router.post("/{artifact_id}/fsdChat")
def fsd_chat(artifact_id: str, message: str = Body(..., embed=True), actor: dict = Depends(current_user)):
    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    artifact = _load(artifact_id)
    actor_role = _fsd_actor_role(artifact, actor)
    if not actor_role:
        raise HTTPException(status_code=403, detail="The detailed report is not open for your edits right now")

    artifact.setdefault("fsdChatHistory", []).append({"role": "user", "content": message.strip()})

    try:
        result = run_fsd_chat_edit(
            originator_label=originator_label((artifact.get("originator") or {}).get("tierId")),
            actor_role=actor_role,
            title=artifact.get("title", ""),
            requirement=artifact.get("content") or {},
            detailed_report=artifact.get("detailedReport"),
            history=[{"role": h["role"], "content": h["content"]} for h in artifact["fsdChatHistory"]],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI edit service error: {exc}")

    if not result.operations:
        raise HTTPException(status_code=502, detail="AI edit service returned no report changes")

    # Apply to locals first; a rejected operation must not leave the stored
    # document half-edited.
    next_title = artifact.get("title")
    next_requirement = artifact.get("content")
    next_report = artifact.get("detailedReport")
    try:
        for raw in result.operations:
            op = normalize_operation(raw.model_dump(by_alias=True))
            if op["target"] == "title":
                if op["path"] or not isinstance(op["value"], str) or not op["value"].strip():
                    raise EditPathError("A title edit requires a non-empty string and an empty path")
                next_title = op["value"].strip()
            elif op["target"] == "requirement":
                next_requirement = apply_edit_operation(next_requirement, op["path"], op["value"])
            elif op["target"] == "detailedReport":
                next_report = apply_edit_operation(next_report, op["path"], op["value"])
            else:
                raise EditPathError(f'Unknown edit target: "{op["target"]}"')
    except (EditPathError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"AI edit service produced an invalid change: {exc}")

    artifact["title"] = next_title
    artifact["content"] = next_requirement
    artifact["detailedReport"] = next_report
    artifact["fsdChatHistory"].append({"role": "assistant", "content": result.change_summary})
    artifact["updatedAt"] = _utcnow()
    _save(artifact)
    return {"reply": result.change_summary, "artifact": _redact(artifact, actor)}


@router.post("/{artifact_id}/teamReportChat")
def team_report_chat(artifact_id: str, message: str = Body(..., embed=True), actor: dict = Depends(current_user)):
    text = (message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="message is required")
    if actor.get("isClient") or actor.get("tierId") != "tl" or not actor.get("department"):
        raise HTTPException(status_code=403, detail="Only an assigned Team Lead can edit a team package")

    artifact = _load(artifact_id)
    if artifact.get("currentStage") != "approved":
        raise HTTPException(status_code=400, detail="This package is not currently open for Team Lead edits")

    reports = artifact.get("teamReports") or []
    index = next((i for i, r in enumerate(reports) if r.get("team") == actor["department"]), -1)
    if index < 0:
        raise HTTPException(status_code=403, detail="No package is assigned to your department")

    try:
        result = run_team_report_chat_edit(
            department=actor["department"], package=reports[index], message=text
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI package edit service error: {exc}")

    # A team lead may revise their own package, never reassign the work.
    if result.updated_package.team != actor["department"]:
        raise HTTPException(status_code=502, detail="AI package edit attempted to change the assigned department")

    reports[index] = result.updated_package.model_dump(by_alias=True)
    artifact["teamReports"] = reports
    artifact.setdefault("teamReportEditHistory", []).extend([
        {"department": actor["department"], "role": "user", "content": text, "timestamp": _utcnow()},
        {"department": actor["department"], "role": "assistant", "content": result.change_summary, "timestamp": _utcnow()},
    ])
    artifact["updatedAt"] = _utcnow()
    _save(artifact)
    return {"reply": result.change_summary, "artifact": _redact(artifact, actor)}


@router.post("/{artifact_id}/shareForDiscussion")
def share_for_discussion(
    artifact_id: str,
    toUserId: str = Body(...),
    note: str = Body(default=""),
    actor: dict = Depends(current_user),
):
    """Share a team package with another Team Lead.

    Both ends must be a TL. Team packages are TL-only material, so allowing a
    share to any internal colleague would hand other roles a side channel into
    content the list endpoint deliberately withholds from them.
    """
    artifact = _load(artifact_id)
    if not _has_discussion_access(artifact, actor):
        raise HTTPException(status_code=403, detail="You do not have access to this report")

    recipient = db.users().find_one({"_id": _oid(toUserId)})
    if not recipient or recipient.get("isClient") or recipient.get("tierId") != "tl":
        raise HTTPException(status_code=400, detail="Recipient must be a Team Lead")
    if str(recipient["_id"]) == actor["id"]:
        raise HTTPException(status_code=400, detail="Cannot share a report with yourself")

    recipients = artifact.setdefault("discussionRecipients", [])
    if toUserId not in recipients:
        recipients.append(toUserId)

    artifact.setdefault("discussionShares", []).append({
        "fromUserId": actor["id"], "fromName": actor.get("name"),
        "toUserId": toUserId, "toName": recipient.get("name"),
        "sharedTeam": actor.get("department"), "note": (note or "").strip(),
        "timestamp": _utcnow(),
    })
    artifact["updatedAt"] = _utcnow()
    _save(artifact)
    return {"artifact": _redact(artifact, actor)}


@router.post("/{artifact_id}/discussionMessage")
def discussion_message(artifact_id: str, message: str = Body(..., embed=True), actor: dict = Depends(current_user)):
    """Requirement-level group discussion.

    Discussion never changes workflow state — governed changes still go
    through Request revision to VP.
    """
    text = (message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="message is required")
    if actor.get("isClient") or actor.get("tierId") not in ("tl", "vp", "ceo", "md"):
        raise HTTPException(status_code=403, detail="Only assigned Team Leads and leadership can join requirement discussions")

    artifact = _load(artifact_id)
    is_assigned_tl = (
        actor.get("tierId") == "tl" and actor.get("department")
        and any(r.get("team") == actor["department"] for r in artifact.get("teamReports") or [])
    )
    leadership_joining_open_thread = (
        actor.get("tierId") in ("md", "ceo", "vp") and bool(artifact.get("discussionMessages"))
    )
    if not is_assigned_tl and not leadership_joining_open_thread:
        raise HTTPException(status_code=403, detail="An assigned Team Lead must open this discussion first")

    artifact.setdefault("discussionMessages", []).append({
        "userId": actor["id"], "name": actor.get("name"),
        "department": actor.get("department") or str(actor.get("tierId")).upper(),
        "message": text, "timestamp": _utcnow(),
    })
    artifact["updatedAt"] = _utcnow()
    _save(artifact)
    return {"artifact": _redact(artifact, actor)}


# ── the generic action route ─────────────────────────────────────────────────
# Declared last, deliberately. "/{artifact_id}/{action}" matches the same URLs
# as /fsdChat, /teamReportChat, /shareForDiscussion and /discussionMessage, and
# FastAPI resolves in declaration order — so registering it earlier swallowed
# all four, which returned 400 "Unknown action" because none of them is in
# KNOWN_ACTIONS. The JavaScript carried the same warning: "Registered ahead of
# the generic /:id/:action route since both patterns would otherwise match the
# same URL."
@router.post("/{artifact_id}/{action}")
def act(artifact_id: str, action: str, body: ActionBody | None = None, actor: dict = Depends(current_user)):
    if action not in KNOWN_ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown action")

    artifact = _load(artifact_id)
    body = body or ActionBody()

    if action in ("regenerateFsd", "regenerateTeamSplit", "regenerateUi"):
        return _respond(artifact, actor, job_id=_start_regeneration(artifact, action))

    # Only an approval clears a gate. Without this guard every action was
    # treated as having cleared whatever step the chain happened to be sitting
    # on, so *submitting* generated and published the BRD before anyone had
    # approved it — the JavaScript this ports from gated the same call on
    # `action === 'approve'`. Actions that finish the chain still generate,
    # via the `approved` branch rather than via a cleared step.
    step_acted_on = (
        (artifact.get("approvalChain") or [None])[artifact.get("currentApprovalIndex", 0)]
        if action == "approve"
        and artifact.get("currentApprovalIndex", 0) < len(artifact.get("approvalChain") or [])
        else None
    )

    try:
        transition(
            artifact, action, actor_from(actor),
            comment=body.comment or "",
            final_approver_tier=body.finalApproverTier,
        )
    except TransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Saved before the job starts, never after: the job writes its own results
    # and a later full-document save from this request would overwrite them
    # with the pre-generation copy held here.
    artifact["updatedAt"] = _utcnow()
    _save(artifact)
    return _respond(artifact, actor, job_id=_start_generation(artifact, step_acted_on))
