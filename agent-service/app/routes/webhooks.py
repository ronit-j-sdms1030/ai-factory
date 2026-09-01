"""GitHub webhook receiver.

A pull request review is a gate decision, so this endpoint carries real
authority. Two rules follow from that:

* **Verify before parsing.** The signature is checked against the raw body
  before anything in the payload is trusted, and a missing secret fails
  closed.
* **Confirm the reviewer's authority.** GitHub already restricts *who can
  approve* through CODEOWNERS, but approval chains are ordered and GitHub
  reviews are not. The reviewer's tier is therefore re-checked against the
  gate the requirement is actually waiting on, so an approval from a later
  gate's tier arriving early is recorded and ignored rather than acted on.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from .. import db, github_api
from ..state_machine import TransitionError, transition

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# Which tier owns each gate that is not chain-driven.
GATE_TIERS = {"ui": ("vp",), "workitems": ("vp",)}


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    config = github_api.load_config()
    if not config:
        raise HTTPException(status_code=503, detail="GitHub integration is not configured")

    raw = await request.body()
    if not github_api.verify_signature(raw, x_hub_signature_256, config.webhook_secret):
        # Deliberately terse: a detailed reason would help someone probe for a
        # valid signature.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    if x_github_event != "pull_request_review":
        return {"ignored": f"event {x_github_event}"}

    event = github_api.parse_review_event(await request.json())
    if event is None:
        return {"ignored": "not a decision"}  # a comment is not an approval
    if not event.artifact_id:
        return {"ignored": f"branch {event.branch} is not a requirement branch"}

    artifact = db.artifacts().find_one({"_id": _oid(event.artifact_id)})
    if not artifact:
        return {"ignored": f"unknown artifact {event.artifact_id}"}

    reviewer = db.users().find_one({"githubLogin": event.reviewer_login})
    if not reviewer:
        log.warning("review from unmapped GitHub login %s", event.reviewer_login)
        return {"ignored": f"no internal user mapped to {event.reviewer_login}"}

    actor = {"user_id": str(reviewer["_id"]), "tier_id": reviewer.get("tierId")}

    if not _may_act(artifact, event.stage, actor["tier_id"]):
        # Recorded, not acted on: an approval from the wrong gate is a fact
        # worth keeping, but it must not advance the chain.
        log.info(
            "ignoring %s from tier %s on stage %s — not the gate in progress",
            event.action, actor["tier_id"], event.stage,
        )
        return {"ignored": "reviewer is not the current gate"}

    action = _action_for(artifact.get("currentStage"), event.action)
    if action is None:
        return {"ignored": f"stage {artifact.get('currentStage')} accepts no {event.action} action"}

    try:
        transition(artifact, action, actor, comment=f"GitHub PR #{event.pr_number}: {event.body}".strip())
    except TransitionError as exc:
        log.warning("webhook transition refused: %s", exc)
        return {"ignored": str(exc)}

    db.artifacts().replace_one({"_id": artifact["_id"]}, artifact)
    return {"ok": True, "action": action, "stage": artifact.get("currentStage")}


# A pull-request approval means different things at different points in the
# workflow, so the action depends on the stage the artifact is actually in.
# Approving the BRD's pull request while the requirement sits in fsd_review is
# the reviewer saying "this FSD is done", which is sendFsdToClient — not
# approve, which that stage does not accept at all.
_APPROVE_ACTION_BY_STAGE = {
    "pending_approval": "approve",
    "fsd_review": "sendFsdToClient",
    "fsd_pending_client": "approveFsd",
    "fsd_final_approval": "giveFinalFsdApproval",
    "pending_client_review": "acceptChanges",
}

# Only pending_approval models rejection and revision; the FSD loop has no
# equivalent transition, so those decisions are recorded but cannot move it.
_OTHER_ACTIONS = {"revise": "requestRevision", "reject": "reject"}


def _action_for(current_stage: str | None, decision: str) -> str | None:
    if decision == "approve":
        return _APPROVE_ACTION_BY_STAGE.get(current_stage or "")
    if current_stage == "pending_approval":
        return _OTHER_ACTIONS.get(decision)
    return None


def _may_act(artifact: dict, stage: str | None, tier: str | None) -> bool:
    """Is this reviewer's tier the one this gate is waiting on?

    Mirrors the authorization in ``state_machine.transition`` rather than
    inventing a parallel rule: the FSD loop is not chain-indexed and is
    governed by the gate-0 reviewer pool, while pending_approval is governed
    by whichever step the chain currently sits on. A divergence here would let
    the webhook admit an approval the state machine would then refuse — or,
    worse, one it would wrongly accept.
    """
    current = artifact.get("currentStage")

    if current in ("fsd_review", "fsd_final_approval"):
        chain = artifact.get("approvalChain") or [{}]
        return tier in (chain[0].get("approverTiers") or [])

    if current == "fsd_pending_client":
        # Originator-only; a tier cannot stand in for them.
        return False

    if stage in GATE_TIERS:
        return tier in GATE_TIERS[stage]

    chain = artifact.get("approvalChain") or []
    index = artifact.get("currentApprovalIndex", 0)
    if index >= len(chain):
        return False
    return tier in (chain[index].get("approverTiers") or [])


def _oid(value: str):
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None
