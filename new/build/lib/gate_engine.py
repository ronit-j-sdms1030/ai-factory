"""The Gate Engine — who may approve what, and when it counts.

A gate is not a button. The architecture defines it as a durable wait carrying
an SLA timer, escalation when nobody responds, segregation-of-duties
enforcement, and a resumable verdict that later stages branch on. Nothing
open-source ships that as a reusable primitive, which is why it is the one
layer built rather than assembled.

**This module is the decision half, and only that.** Given a gate, an actor and
an outcome, it decides whether the approval counts and what the gate's state
becomes. The durable wait itself belongs to the workflow engine: a timer that
survives a redeploy is Temporal's problem, not a function's. Splitting them
this way keeps the part that encodes policy pure and fully testable — no clock,
no network, no database — which matters because this is the code that decides
whether a governance claim is true.

**The three questions.** The architecture has the policy engine ask exactly
three things of every approval: is this identity in the approver set, is it
*not* the originator, and is this the gate we are actually waiting on. Each
exists because of a different failure:

  * *approver set* — the ordinary authorisation question
  * *not the originator* — the one GitHub cannot answer. It blocks a pull
    request author approving their own PR, but has no idea the person
    approving a BRD is the one who raised the requirement three branches ago
  * *expected gate* — an approval arriving out of sequence. Reviews are
    unordered; approval chains are not, and an approval for a gate that has
    already passed, or has not yet opened, is not an approval

An approval that fails any of the three is refused with the reason recorded.
Refusals are evidence: "who tried to approve what, and why it was rejected" is
part of the audit trail, not an error to swallow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Outcome = Literal["approve", "revise", "discard", "request_changes", "reject", "hold"]


class GateRefused(Exception):
    """An approval that does not count, with the reason it did not.

    Raised rather than returned because a refused approval must not be
    mistaken for a pending one by a caller that forgets to check a boolean.
    """


@dataclass(frozen=True)
class Gate:
    """One gate, as the architecture defines it.

    ``approver_roles`` is the set of roles entitled to act. ``all_must_sign``
    distinguishes Gate 4 — where every stream lead signs, because a dependency
    one department accepts and another has not seen is the defect the gate
    exists to catch — from the gates a single qualified approver can clear.

    ``distinct_teams`` covers Gate 2, where two reviewers are required *and*
    they must come from different teams. Two signatures from one team is one
    perspective twice.
    """

    number: int
    name: str
    approver_roles: frozenset[str]
    outcomes: frozenset[str]
    all_must_sign: bool = False
    distinct_teams: int = 1
    excludes_originator: bool = True


# The gate set, exactly as §9 of the architecture defines it. Discard appears
# only at gates 1 and 2: after the design is approved, work is corrected rather
# than abandoned, and a rejection at gate 6 produces a corrective ticket in the
# existing build loop rather than closing the request.
GATES: dict[int, Gate] = {
    1: Gate(
        number=1,
        name="Scope",
        approver_roles=frozenset({"product_owner"}),
        outcomes=frozenset({"approve", "revise", "discard"}),
    ),
    2: Gate(
        number=2,
        name="BRD",
        approver_roles=frozenset({"business_owner", "client_tech_lead"}),
        outcomes=frozenset({"approve", "revise", "discard"}),
        all_must_sign=True,
        distinct_teams=2,
    ),
    3: Gate(
        number=3,
        name="Design",
        approver_roles=frozenset({"architect", "ui_ux", "business_analyst"}),
        outcomes=frozenset({"approve", "revise"}),
        all_must_sign=True,
    ),
    4: Gate(
        number=4,
        name="Plan",
        approver_roles=frozenset({"tech_lead", "stream_lead"}),
        outcomes=frozenset({"approve", "revise"}),
        all_must_sign=True,
    ),
    5: Gate(
        number=5,
        name="Merge",
        approver_roles=frozenset({"senior_engineer"}),
        outcomes=frozenset({"approve", "request_changes"}),
    ),
    6: Gate(
        number=6,
        name="UAT",
        approver_roles=frozenset({"business_stakeholder"}),
        outcomes=frozenset({"approve", "reject"}),
        # A UAT reviewer is often the person who raised the requirement, and
        # should be: they are the one who knows whether it does the job. The
        # separation that matters here was enforced upstream, at scope and at
        # merge.
        excludes_originator=False,
    ),
    7: Gate(
        number=7,
        name="Release",
        approver_roles=frozenset({"release_manager"}),
        outcomes=frozenset({"approve", "hold"}),
    ),
}


@dataclass
class Decision:
    """What an accepted approval did to the gate.

    ``satisfied`` is the property later stages branch on. A gate needing three
    signatures is not satisfied by the first, and the workflow must not advance
    on it — which is why this is a computed verdict rather than a flag the
    caller sets.
    """

    gate: int
    outcome: str
    actor_id: str
    satisfied: bool
    awaiting: list[str] = field(default_factory=list)
    note: str = ""


def _actor_roles(actor: dict) -> set[str]:
    return {r.strip().lower() for r in (actor.get("roles") or []) if r and r.strip()}


def _signed_roles(requirement: dict, gate: int) -> dict[str, str]:
    """Roles already signed at this gate, mapped to who signed them."""
    record = (requirement.get("gates") or {}).get(str(gate)) or {}
    return {s["role"]: s["actorId"] for s in record.get("signatures", [])}


def _signed_teams(requirement: dict, gate: int) -> set[str]:
    record = (requirement.get("gates") or {}).get(str(gate)) or {}
    return {s.get("team", "") for s in record.get("signatures", []) if s.get("team")}


def evaluate(
    gate: int,
    outcome: str,
    actor: dict,
    requirement: dict,
    *,
    expected_gate: int | None = None,
) -> Decision:
    """Decide whether this approval counts, and what it leaves outstanding.

    ``actor`` is ``{"id", "roles", "team"}``. ``requirement`` carries
    ``originator`` and the signatures recorded so far. ``expected_gate`` is the
    gate the workflow is actually parked on — passed in rather than inferred,
    because the workflow engine is the only thing that knows, and a gate engine
    that guessed would be a second source of truth.

    Raises ``GateRefused`` on anything that does not count.
    """
    definition = GATES.get(gate)
    if definition is None:
        raise GateRefused(f"there is no gate {gate}")

    if outcome not in definition.outcomes:
        raise GateRefused(
            f"'{outcome}' is not an outcome of gate {gate} ({definition.name}); "
            f"permitted: {', '.join(sorted(definition.outcomes))}"
        )

    # Sequence. Checked before identity so an out-of-order approval is refused
    # for the honest reason, rather than appearing to be an authorisation
    # problem the approver could fix by finding someone more senior.
    if expected_gate is not None and gate != expected_gate:
        raise GateRefused(
            f"gate {gate} is not open — this requirement is waiting at gate {expected_gate}"
        )

    roles = _actor_roles(actor)
    entitled = roles & definition.approver_roles
    if not entitled:
        raise GateRefused(
            f"{actor.get('id', 'actor')} holds no role entitled to act at gate {gate} "
            f"({definition.name}); expected one of: {', '.join(sorted(definition.approver_roles))}"
        )

    # Segregation of duties. The check GitHub cannot make: it blocks a pull
    # request author approving their own PR, and knows nothing about who raised
    # the requirement the PR belongs to.
    originator_id = (requirement.get("originator") or {}).get("id")
    if definition.excludes_originator and originator_id and actor.get("id") == originator_id:
        raise GateRefused(
            f"{actor.get('id')} raised this requirement and cannot approve it at gate {gate}"
        )

    already = _signed_roles(requirement, gate)
    if actor.get("id") in already.values():
        raise GateRefused(f"{actor.get('id')} has already signed gate {gate}")

    # A non-approving outcome resolves the gate immediately — there is nothing
    # to countersign about a revision or a discard.
    if outcome != "approve":
        return Decision(
            gate=gate,
            outcome=outcome,
            actor_id=actor.get("id", ""),
            satisfied=True,
            note=f"gate {gate} returned '{outcome}'",
        )

    signed_roles = set(already) | entitled
    outstanding = sorted(definition.approver_roles - signed_roles) if definition.all_must_sign else []

    teams = _signed_teams(requirement, gate) | ({actor.get("team")} if actor.get("team") else set())
    short_of_teams = definition.distinct_teams > len(teams)

    if outstanding or short_of_teams:
        note = ""
        if short_of_teams and not outstanding:
            note = (
                f"gate {gate} needs {definition.distinct_teams} signatures from distinct teams; "
                f"{len(teams)} so far"
            )
        return Decision(
            gate=gate,
            outcome=outcome,
            actor_id=actor.get("id", ""),
            satisfied=False,
            awaiting=outstanding,
            note=note,
        )

    return Decision(
        gate=gate,
        outcome=outcome,
        actor_id=actor.get("id", ""),
        satisfied=True,
        note=f"gate {gate} ({definition.name}) satisfied",
    )


def resolve_approval_chain(originator: dict, gates: list[int]) -> dict[str, list[str]]:
    """The roles entitled to act at each gate this run will fire.

    Resolved once, at intake, and written onto the requirement. Resolving per
    gate instead would mean a mid-flight change to a group silently rewriting
    who was allowed to approve a decision already in progress — the approval
    chain would then describe the organisation as it is now rather than as it
    was when the requirement entered.
    """
    return {str(g): sorted(GATES[g].approver_roles) for g in gates if g in GATES}
