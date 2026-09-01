"""Approval state machine.

Port of ``backend/src/services/stateMachine.service.js``. Pure logic — no
database access; callers pass a plain dict and receive it mutated.

This is governance code, so it is a faithful port rather than a redesign. The
authorization rules and stage transitions decide who may approve what, and a
well-intentioned "improvement" here changes the audit story. Where the
JavaScript has a special case, that special case is reproduced along with the
reason for it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

Artifact = dict[str, Any]
Actor = dict[str, Any]  # {"user_id": str, "tier_id": str | None}

TRANSITIONS: dict[str, dict[str, str]] = {
    "draft": {"submit": "pending_approval"},
    "clarifying": {"submit": "pending_approval"},
    "revision_requested": {"submit": "pending_approval"},
    # The gate-0 reviewer proposes their own edit rather than asking the
    # originator to redo it; the originator only has to accept, which sends
    # it back to the same gate for signoff.
    "pending_client_review": {"acceptChanges": "pending_approval"},
    "pending_approval": {
        "approve": "approved",
        "reject": "rejected",
        "requestRevision": "revision_requested",
        "proposeChanges": "pending_client_review",
    },
    "fsd_review": {"sendFsdToClient": "fsd_pending_client"},
    "fsd_pending_client": {"approveFsd": "fsd_final_approval"},
    # Target is computed dynamically below — it depends on whether the chain
    # has a real next gate (client: yes, VP) or is already exhausted.
    "fsd_final_approval": {"giveFinalFsdApproval": "approved"},
}


class TransitionError(Exception):
    """Raised when an action is not permitted from the current stage or actor."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def push_history(artifact: Artifact, actor: Actor, action: str, comment: str, stage: str) -> None:
    artifact.setdefault("history", []).append(
        {
            "stage": stage,
            "actorId": actor["user_id"],
            "actorTier": actor.get("tier_id"),
            "action": action,
            "comment": comment,
            "timestamp": _now(),
        }
    )


def _current_step(artifact: Artifact) -> dict[str, Any] | None:
    chain = artifact.get("approvalChain") or []
    index = artifact.get("currentApprovalIndex", 0)
    return chain[index] if 0 <= index < len(chain) else None


def _is_self_origin_md_ceo(artifact: Artifact) -> bool:
    return (artifact.get("originator") or {}).get("tierId") in ("md", "ceo")


def _apply_approval(artifact: Artifact, actor: Actor, step: dict[str, Any], comment: str) -> None:
    step.setdefault("approvedBy", []).append(
        {"userId": actor["user_id"], "tierId": actor.get("tier_id"), "timestamp": _now()}
    )

    if step.get("mode") == "all":
        satisfied = all(
            any(a.get("tierId") == tier for a in step["approvedBy"]) for tier in step["approverTiers"]
        )
    else:
        satisfied = True  # 'any' — the approval that just landed is enough

    if satisfied:
        artifact["currentApprovalIndex"] = artifact.get("currentApprovalIndex", 0) + 1
        artifact["currentStage"] = (
            "approved"
            if artifact["currentApprovalIndex"] >= len(artifact.get("approvalChain") or [])
            else "pending_approval"
        )

    push_history(artifact, actor, "approve", comment, artifact["currentStage"])


def transition(
    artifact: Artifact,
    action: str,
    actor: Actor,
    *,
    comment: str = "",
    final_approver_tier: str | None = None,
) -> Artifact:
    from_stage = artifact.get("currentStage")
    stage_map = TRANSITIONS.get(from_stage or "")

    if not stage_map or action not in stage_map:
        raise TransitionError(f'Cannot perform "{action}" from stage "{from_stage}"')

    if from_stage == "pending_approval":
        step = _current_step(artifact)
        if step is None:
            raise TransitionError("No pending approval step found on this artifact.")

        if actor.get("tier_id") not in step["approverTiers"]:
            expected = ", ".join(step["approverTiers"])
            raise TransitionError(
                f'Unauthorized approver: "{actor.get("tier_id")}" cannot act on this step '
                f"(expected one of: {expected})."
            )

        # A VP reaching this gate is reviewing an FSD that already cleared
        # MD/CEO. Rejecting outright would discard that review, so the VP
        # must either edit it or pass it on.
        if (
            action == "reject"
            and actor.get("tier_id") == "vp"
            and artifact.get("currentApprovalIndex", 0) > 0
            and (artifact.get("originator") or {}).get("tierId") in ("md", "ceo", "tl")
        ):
            raise TransitionError(
                "VP cannot reject this reviewed FSD; edit it or send it to TL for production."
            )

        if action == "approve":
            _apply_approval(artifact, actor, step, comment)
            return artifact

    # The FSD loop's stages are not chain-indexed, so they need their own
    # authorization — still driven by the artifact's own chain rather than a
    # hardcoded tier. Whoever could approve gate 0 is the reviewer pool for
    # the whole loop, which makes the loop work identically for a client
    # (gate 0: md/ceo) and for a PM or TL without special-casing each.
    if from_stage in ("fsd_review", "fsd_final_approval"):
        reviewer_pool = (artifact.get("approvalChain") or [{}])[0].get("approverTiers", [])
        vp_originator_finishing = (
            from_stage == "fsd_final_approval"
            and (artifact.get("originator") or {}).get("tierId") == "vp"
            and (artifact.get("originator") or {}).get("userId") == actor["user_id"]
            and action == "giveFinalFsdApproval"
        )
        if actor.get("tier_id") not in reviewer_pool and not vp_originator_finishing:
            raise TransitionError(
                f'Unauthorized: "{actor.get("tier_id")}" is not part of this requirement\'s '
                f"reviewer pool ({', '.join(reviewer_pool)})."
            )

    if from_stage in ("fsd_pending_client", "pending_client_review"):
        if actor["user_id"] != (artifact.get("originator") or {}).get("userId"):
            raise TransitionError("Unauthorized: only the originator can act on this step.")

    if action == "submit":
        if not artifact.get("approvalChain"):
            raise TransitionError(
                "Artifact has no approval chain configured; resolve one before submitting."
            )
        artifact["currentApprovalIndex"] = 0

    # VP-originated work follows VP -> MD/CEO -> VP -> TL. Once the VP accepts
    # the FSD it goes straight to team splitting; it must not bounce back to
    # MD/CEO for a redundant final approval.
    if action == "approveFsd" and (artifact.get("originator") or {}).get("tierId") == "vp":
        artifact["currentStage"] = "approved"
        push_history(artifact, actor, action, comment, artifact["currentStage"])
        return artifact

    # A self-originated MD/CEO requirement has the same person on both ends of
    # the FSD client-review loop, so "send it to the client" has nobody to send
    # to. It goes to the next real gate instead — VP, who signs off before the
    # team split. VP is the only permitted next gate: allowing an MD/CEO to
    # nominate a co-equal peer would let them pick a rubber stamp and skip the
    # one genuinely independent review the requirement is supposed to get.
    if action == "sendFsdToClient" and _is_self_origin_md_ceo(artifact):
        if final_approver_tier:
            raise TransitionError(
                "Self-originated MD/CEO requirements always route to VP next — "
                "a final approver cannot be chosen."
            )
        artifact["currentStage"] = (
            "approved"
            if artifact.get("currentApprovalIndex", 0) >= len(artifact.get("approvalChain") or [])
            else "pending_approval"
        )
        push_history(artifact, actor, action, comment, artifact["currentStage"])
        return artifact

    # TL requirements move from the MD/CEO FSD review to the existing VP gate.
    # They do not return to the originating TL until VP releases the generated
    # production packages.
    if action == "sendFsdToClient" and (artifact.get("originator") or {}).get("tierId") == "tl":
        artifact["currentStage"] = "pending_approval"
        push_history(artifact, actor, action, comment, artifact["currentStage"])
        return artifact

    if action == "giveFinalFsdApproval":
        artifact["currentStage"] = (
            "approved"
            if artifact.get("currentApprovalIndex", 0) >= len(artifact.get("approvalChain") or [])
            else "pending_approval"
        )
        push_history(artifact, actor, action, comment, artifact["currentStage"])
        return artifact

    artifact["currentStage"] = stage_map[action]
    push_history(artifact, actor, action, comment, artifact["currentStage"])
    return artifact
