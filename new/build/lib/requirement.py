"""The requirement record — the state the four Phase 1 components share.

The gate engine decides, the attestation is evidence, the ledger is the graph
and the skill file is the boundary. Each works alone. Nothing until now has
carried state *between* them, which meant nothing had ever checked that the
output of one is the input of the next.

This is that seam, and it is the Postgres half of §2's split: the requirement
record, the resolved approval chain and the position in the run. **It stores
references, never content.** A commit SHA rather than a scope report, because
Git holds the artefacts and *"if Postgres and Git disagree about an artefact,
Git wins"* — an authority the record can contradict is not an authority.

**One approval produces four effects or none.** A signature recorded, an
attestation built, a ledger edge added, the gate cleared. Everything that can
fail is computed before anything mutates, so the state this record cannot reach
is the dangerous one: a gate marked approved with no evidence behind it. That
ordering is the reason this module exists rather than being four calls at a call
site, where the second could fail after the first had already been written.

**A signature is on an artefact, not on a gate.** Every signature carries the
SHA it was given for, and a gate counts only the signatures matching the artefact
currently in front of it. So a revision automatically vacates the approvals of
the document it replaced — a reviewer who approved the first draft did not
approve the second, and Gate 2's two signatures must not be collected across two
different BRDs. The stale signatures are kept rather than deleted: *who approved
the superseded version* is an audit question, and this record is append-only for
the same reason the ledger is.

**Phase 1 artefacts are not ledger nodes.** The ledger traces delivery —
requirement to ticket to test to commit. A scope report is not delivery; it is
the requirement being written down, and it has no ticket behind it because no
work has been decomposed yet. Adding it would manufacture a commit that
``orphans("commit")`` then reports as a finding, which would be a false one. The
binding from decision to artefact already exists where it belongs: the
attestation's subject digest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import attestation
import gate_engine
import run_ledger as rl
import workflow_templates
from attestation import ContextInputs
from gate_engine import Decision

# Which stage produces the artefact each gate reviews. Inverted from the
# templates' map rather than restated, because a second spelling of it is a
# second thing that can disagree with the first.
GATE_STAGES: dict[int, str] = {g: s for s, g in workflow_templates.STAGE_GATES.items()}


class TransitionRefused(Exception):
    """The requirement is not in a state where this step is meaningful.

    Distinct from ``GateRefused`` (the actor may not do this) and
    ``IncompleteAttestation`` (this could not be evidenced), so a caller can
    tell a lifecycle problem from a policy one. All three refuse; only the
    middle one is about the person.
    """


@dataclass(frozen=True)
class Artefact:
    """One committed artefact, and what produced it.

    Provenance is recorded here rather than passed at approval time because it
    is a property of the artefact, not of the person reviewing it. Recording it
    at the gate would mean asking the approver's code for a fact about a
    document written hours earlier — and getting it wrong there is invisible.
    """

    stage: str
    sha: str
    model: str
    model_version: str
    prompt_version: str


@dataclass(frozen=True)
class Signature:
    """One role signed off on one artefact.

    ``artefact_sha`` is what makes the signature specific. Without it a
    signature says "this person approved gate 2", which stays true after the
    document it was given for has been rewritten.
    """

    gate: int
    role: str
    actor_id: str
    team: str
    artefact_sha: str


@dataclass
class Requirement:
    """One requirement, from submission to the last gate it fires."""

    id: str
    template: str
    originator: dict[str, Any]
    approval_chain: dict[str, list[str]]
    gates: tuple[int, ...]

    artefacts: dict[str, Artefact] = field(default_factory=dict)
    signatures: list[Signature] = field(default_factory=list)
    attestations: list[dict[str, Any]] = field(default_factory=list)
    cleared: set[int] = field(default_factory=set)
    ledger: rl.Ledger = field(default_factory=rl.Ledger)
    history: list[dict[str, Any]] = field(default_factory=list)
    discarded_reason: str = ""

    _allocated: int = 0
    _retired: set[str] = field(default_factory=set)

    # ── position ─────────────────────────────────────────────────────────────

    @property
    def awaiting(self) -> int | None:
        """The gate this requirement is parked on, or ``None`` when finished.

        Derived from which gates have cleared rather than held as a pointer.
        Two things that must agree can disagree, and a pointer that drifted from
        the signatures would let a gate be approved twice or skipped entirely.
        """
        for gate in self.gates:
            if gate not in self.cleared:
                return gate
        return None

    @property
    def state(self) -> str:
        """``open``, ``discarded`` or ``complete`` — derived, never set."""
        if self.discarded_reason:
            return "discarded"
        return "open" if self.awaiting is not None else "complete"

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    # ── artefacts ────────────────────────────────────────────────────────────

    def record_artefact(
        self,
        stage: str,
        sha: str,
        *,
        model: str,
        model_version: str,
        prompt_version: str | int,
    ) -> Artefact:
        """Record what was committed at a stage, and what produced it.

        A stage whose gate has already cleared is refused: rewriting an approved
        scope report would silently change what was approved, and the approval
        would still be sitting there pointing at it. Rework goes through
        ``revise``, which reopens the gate first.
        """
        self._require_open()

        if stage not in workflow_templates.for_name(self.template).stages:
            raise TransitionRefused(
                f"template '{self.template}' does not run the {stage} stage"
            )

        gate = workflow_templates.STAGE_GATES.get(stage)
        if gate is not None and gate in self.cleared:
            raise TransitionRefused(
                f"gate {gate} has already cleared on the {stage} artefact; changing it "
                f"now would alter what was approved without reopening the approval"
            )

        missing = [
            n
            for n, v in (
                ("sha", sha),
                ("model", model),
                ("model_version", model_version),
                ("prompt_version", prompt_version),
            )
            if not str(v or "").strip()
        ]
        if missing:
            raise TransitionRefused(
                f"the {stage} artefact is missing " + ", ".join(missing) + " — refused "
                "here rather than at the gate, where the document is already written "
                "and a reviewer is waiting on an approval that cannot be attested"
            )

        artefact = Artefact(
            stage=stage,
            sha=sha,
            model=model,
            model_version=model_version,
            prompt_version=str(prompt_version),
        )
        self.artefacts[stage] = artefact
        self.history.append({"event": "artefact", "stage": stage, "sha": sha})
        return artefact

    def artefact_for(self, gate: int) -> Artefact | None:
        """The artefact a gate is reviewing, or ``None`` if nothing is written."""
        stage = GATE_STAGES.get(gate)
        return self.artefacts.get(stage) if stage else None

    def live_signatures(self, gate: int) -> list[Signature]:
        """Signatures on the artefact currently in front of this gate.

        Signatures given for a superseded artefact are retained on the record
        and excluded here. They are history, not consent.
        """
        artefact = self.artefact_for(gate)
        if artefact is None:
            return []
        return [
            s for s in self.signatures if s.gate == gate and s.artefact_sha == artefact.sha
        ]

    # ── the transition ───────────────────────────────────────────────────────

    def decide(
        self,
        gate: int,
        outcome: str,
        actor: dict[str, Any],
        *,
        context: ContextInputs | None = None,
        timestamp: str | None = None,
    ) -> Decision:
        """Apply one gate decision, or apply none of it.

        The order is the substance of this method. ``evaluate`` may refuse on
        policy and ``build`` may refuse for want of evidence; both run to
        completion before a single field is written. A caller doing this by hand
        would record the signature first, because that is the obvious order, and
        would eventually leave a gate approved with no attestation behind it —
        the one inconsistency the whole trail exists to make impossible.
        """
        self._require_open()

        if gate not in self.gates:
            raise TransitionRefused(
                f"template '{self.template}' does not fire gate {gate}; "
                f"it fires {', '.join(str(g) for g in self.gates)}"
            )

        artefact = self.artefact_for(gate)
        if artefact is None:
            raise TransitionRefused(
                f"nothing is recorded at the {GATE_STAGES.get(gate, '?')} stage — "
                f"gate {gate} has no artefact to approve"
            )

        # Policy. Raises GateRefused: wrong gate, wrong role, the originator, or
        # a second signature from someone who has already signed.
        decision = gate_engine.evaluate(
            gate, outcome, actor, self._as_gate_input(gate), expected_gate=self.awaiting
        )

        # Evidence. Raises IncompleteAttestation. A refusal is attested exactly
        # as an approval is: who tried to approve what, and why it was rejected,
        # is part of the trail rather than an error to swallow.
        statement = attestation.build(
            requirement_id=self.id,
            gate=gate,
            decision=outcome,
            actor=actor.get("id", ""),
            artefact_sha=artefact.sha,
            model=artefact.model,
            model_version=artefact.model_version,
            prompt_version=artefact.prompt_version,
            template=self.template,
            context=context,
            timestamp=timestamp,
        )

        # Nothing above has mutated anything. Everything below cannot fail.
        self.attestations.append(statement)
        self.ledger.link(rl.requirement(self.id), rl.approval(gate, self.id))
        self.history.append(
            {
                "event": "decision",
                "gate": gate,
                "outcome": outcome,
                "actor": actor.get("id", ""),
                "artefact_sha": artefact.sha,
                "satisfied": decision.satisfied,
            }
        )

        if outcome == "approve":
            for role in sorted(
                {r.strip().lower() for r in (actor.get("roles") or [])}
                & gate_engine.GATES[gate].approver_roles
            ):
                self.signatures.append(
                    Signature(
                        gate=gate,
                        role=role,
                        actor_id=actor.get("id", ""),
                        team=actor.get("team", ""),
                        artefact_sha=artefact.sha,
                    )
                )
            if decision.satisfied:
                self.cleared.add(gate)
        elif outcome == "discard":
            self.discarded_reason = decision.note or f"discarded at gate {gate}"

        # 'revise' clears nothing. The requirement stays at this gate, and the
        # signatures already collected go stale on their own the moment the
        # reworked artefact is recorded under a new SHA.
        return decision

    def _as_gate_input(self, gate: int) -> dict[str, Any]:
        """The record in the shape ``gate_engine.evaluate`` reads.

        Only live signatures are presented. The gate engine is deliberately
        ignorant of revisions — it evaluates what it is given — so deciding
        which signatures still stand belongs here, where the artefact history
        is.
        """
        return {
            "originator": self.originator,
            "gates": {
                str(gate): {
                    "signatures": [
                        {"role": s.role, "actorId": s.actor_id, "team": s.team}
                        for s in self.live_signatures(gate)
                    ]
                }
            },
        }

    def _require_open(self) -> None:
        if self.state == "discarded":
            raise TransitionRefused(
                f"{self.id} was discarded ({self.discarded_reason}); the trail is "
                f"retained but the request is closed"
            )
        if self.state == "complete":
            raise TransitionRefused(f"{self.id} has cleared every gate its template fires")

    # ── traceability ids ─────────────────────────────────────────────────────

    def allocate_traceability_ids(self, count: int) -> list[str]:
        """Ids for BRD requirement items, carried to ticket, test and commit.

        The counter only ever climbs. An id belonging to an item a later
        revision drops is retired rather than returned to the pool: reissuing it
        would silently repoint every ticket, test and commit that already cited
        it at a different requirement, and nothing in the trail would look
        wrong.
        """
        if count < 1:
            raise TransitionRefused("cannot allocate fewer than one traceability id")
        issued = [
            f"{self.id}-R{n:02d}"
            for n in range(self._allocated + 1, self._allocated + count + 1)
        ]
        self._allocated += count
        return issued

    def retire_traceability_ids(self, ids: list[str]) -> None:
        """Mark ids as belonging to items no longer in the BRD."""
        self._retired.update(ids)

    @property
    def retired_traceability_ids(self) -> set[str]:
        return set(self._retired)

    def path_for_attestation(self, statement: dict[str, Any]) -> str:
        """Filename for a statement already appended to this record.

        Counted after the append, so the first decision at a gate is ``.01``.
        """
        gate = int(statement["predicate"]["gate"])
        idx = None
        for i, existing in enumerate(self.attestations):
            if existing is statement or existing == statement:
                idx = i
                break
        if idx is None:
            raise ValueError("statement is not on this requirement")
        sequence = sum(
            1
            for a in self.attestations[: idx + 1]
            if int((a.get("predicate") or {}).get("gate") or 0) == gate
        )
        return attestation.path_for(self.id, gate, sequence)

    def dump(self) -> dict[str, Any]:
        """Process state for Postgres. Artefact *content* is not here — Git holds it."""
        edges = [
            {
                "parent": {"kind": parent.kind, "id": parent.id},
                "child": {"kind": child.kind, "id": child.id},
            }
            for parent, children in self.ledger._forward.items()
            for child in children
        ]
        return {
            "id": self.id,
            "template": self.template,
            "originator": self.originator,
            "approval_chain": self.approval_chain,
            "gates": list(self.gates),
            "artefacts": {k: asdict(v) for k, v in self.artefacts.items()},
            "signatures": [asdict(s) for s in self.signatures],
            "attestations": self.attestations,
            "cleared": sorted(self.cleared),
            "history": self.history,
            "discarded_reason": self.discarded_reason,
            "allocated": self._allocated,
            "retired": sorted(self._retired),
            "ledger_edges": edges,
        }

    @classmethod
    def load(cls, data: dict[str, Any]) -> Requirement:
        req = cls(
            id=data["id"],
            template=data["template"],
            originator=dict(data.get("originator") or {}),
            approval_chain=dict(data.get("approval_chain") or {}),
            gates=tuple(data.get("gates") or ()),
            artefacts={
                k: Artefact(**v) for k, v in (data.get("artefacts") or {}).items()
            },
            signatures=[Signature(**s) for s in (data.get("signatures") or [])],
            attestations=list(data.get("attestations") or []),
            cleared=set(data.get("cleared") or []),
            history=list(data.get("history") or []),
            discarded_reason=data.get("discarded_reason") or "",
            _allocated=int(data.get("allocated") or 0),
            _retired=set(data.get("retired") or []),
        )
        for edge in data.get("ledger_edges") or []:
            parent = edge["parent"]
            child = edge["child"]
            req.ledger.link(
                rl.Node(parent["kind"], parent["id"]),
                rl.Node(child["kind"], child["id"]),
            )
        return req


def open_requirement(
    requirement_id: str, template: str, originator: dict[str, Any]
) -> Requirement:
    """Create a requirement and resolve its approval chain once.

    §2 step 2: the template is chosen and the chain resolved at intake, then
    written onto the requirement. Resolved once rather than per gate, because a
    mid-flight change to a group would otherwise silently rewrite who was
    allowed to approve a decision already in progress — the chain would describe
    the organisation as it is now rather than as it was when the requirement
    entered.
    """
    if not str(requirement_id or "").strip():
        raise TransitionRefused("a requirement needs an id before it has a workflow")
    if not (originator or {}).get("id"):
        raise TransitionRefused(
            "a requirement needs a named originator — segregation of duties at gate 1 "
            "is unenforceable against an anonymous one"
        )

    definition = workflow_templates.for_name(template)  # raises UnknownTemplate
    return Requirement(
        id=requirement_id,
        template=definition.name,
        originator=dict(originator),
        approval_chain=workflow_templates.approval_chain(definition.name),
        gates=definition.gates,
        history=[{"event": "opened", "template": definition.name}],
    )
