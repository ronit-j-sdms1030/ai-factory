"""Phase 1 side-effect service used directly or from Temporal activities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import intake_skill
import requirement as R
import workflow_templates
from attestation import ContextInputs
from phase1 import brd, rails, webhook
from phase1.adapters import ModelCompletion, RuntimeAdapters
from phase1.adapters.signer import LocalHMACSigner

PROVENANCE_INTAKE = dict(
    model="deterministic/intake",
    model_version="phase1-1",
    prompt_version="intake.skill.md",
)
PROVENANCE_BRD = dict(
    model="deterministic/brd",
    model_version="phase1-1",
    prompt_version="brd.template.md",
)
BASE_SLA_HOURS = 48


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _due(now: str, hours: float) -> str:
    moment = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (moment + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Phase1:
    def __init__(
        self,
        root: Path,
        *,
        signing_secret: bytes = b"dev-signing-key",
        webhook_secret: bytes = b"dev-webhook-secret",
        llm: Any | None = None,
        adapters: RuntimeAdapters | None = None,
        clock=_now,
    ):
        self.root = Path(root)
        self.signing_secret = signing_secret
        self.webhook_secret = webhook_secret
        self.adapters = adapters or RuntimeAdapters.from_env(self.root)
        if llm is not None:
            self.adapters.model = llm
        if adapters is None and signing_secret != b"dev-signing-key":
            self.adapters.signer = LocalHMACSigner(signing_secret)
        self.llm = self.adapters.model
        self.clock = clock
        self.git = self.adapters.repository
        self.git.init()
        self.store = self.adapters.store

    # ── submit ───────────────────────────────────────────────────────────────

    def submit(
        self,
        originator: dict[str, Any],
        template: str,
        request_text: str,
        *,
        requirement_id: str | None = None,
    ) -> dict[str, Any]:
        definition = workflow_templates.for_name(template)
        if 1 not in definition.gates or 2 not in definition.gates:
            raise R.TransitionRefused(
                f"template '{template}' does not run Phase 1 (gates 1 and 2)"
            )
        rid = requirement_id or self.store.next_id()
        req = R.open_requirement(rid, template, originator)
        skill = self.git.skill()
        now = self.clock()
        screened_request = self.adapters.privacy.screen(request_text)
        precedents = self.adapters.precedent.search(screened_request, limit=5)
        body = {
            "requirement": req.dump(),
            "messages": [],
            "budget": {"asked": 0, "minimum": 4, "maximum": 10},
            "named_approvers": self.adapters.identity.freeze_chain(template),
            "request_text": screened_request,
            "retrieval_document_ids": [item.document_id for item in precedents],
            "precedent_enabled": bool(precedents),
            "skill_version": skill.version,
            "sla_due": None,
            "sla_gate": None,
            "escalated": False,
        }
        self._save(body, event="submitted", at=now)
        self.adapters.telemetry.event(
            "phase1.submitted", {"requirement_id": rid, "template": template}
        )
        first = self.turn(rid, body["request_text"], as_originator=True)
        return first

    def get(self, requirement_id: str) -> dict[str, Any]:
        body = self.store.get(requirement_id)
        if body is None:
            raise KeyError(requirement_id)
        req = R.Requirement.load(body["requirement"])
        body = dict(body)
        body["phase"] = self._phase(req)
        body["awaiting"] = req.awaiting
        body["state"] = req.state
        body["scope_text"] = self._artefact_text(req, "scope")
        body["brd_text"] = self._artefact_text(req, "brd")
        return body

    def list_runs(self) -> list[dict[str, Any]]:
        return [self.get(row["requirement"]["id"]) for row in self.store.all()]

    # ── intake conversation ──────────────────────────────────────────────────

    def turn(
        self,
        requirement_id: str,
        message: str,
        *,
        as_originator: bool = False,
    ) -> dict[str, Any]:
        body = self._load(requirement_id)
        req = R.Requirement.load(body["requirement"])
        if self._phase(req) not in {"intake"}:
            raise R.TransitionRefused(f"{requirement_id} is not in intake ({self._phase(req)})")

        text = message if as_originator else self.adapters.privacy.screen(message)
        if not as_originator:
            body["messages"].append({"role": "user", "content": text})
        else:
            body["messages"].append({"role": "user", "content": text})

        budget = intake_skill.QuestionBudget(
            minimum=int(body["budget"]["minimum"]),
            maximum=int(body["budget"]["maximum"]),
            asked=int(body["budget"]["asked"]),
        )
        skill = self.git.skill()
        prompt = [{"role": "system", "content": intake_skill.prompt_section(skill)}]
        if budget.must_close():
            prompt.append({"role": "system", "content": '{"type":"scope_report"} required now'})

        completion = self.llm.complete(prompt + body["messages"], skill=skill.content)
        if isinstance(completion, ModelCompletion):
            raw = completion.text
            provenance = {
                "model": completion.model,
                "model_version": completion.model_version,
                "prompt_version": completion.prompt_version,
            }
            body["model_metadata"] = completion.metadata
        else:
            raw = completion
            provenance = PROVENANCE_INTAKE
        parsed = rails.parse_agent_output(raw)

        if parsed["type"] == "question":
            budget.record_question()
            body["budget"]["asked"] = budget.asked
            body["messages"].append(
                {"role": "assistant", "content": json_question(parsed["text"])}
            )
            self._save(body, event="intake_question", at=self.clock())
            result = self.get(requirement_id)
            result["question"] = parsed["text"]
            return result

        budget.require_closeable()
        body["budget"]["asked"] = budget.asked
        scope_md = brd.render_scope(requirement_id, parsed)
        sha = self._commit_scope(req, body, scope_md, skill)
        req.record_artefact("scope", sha, **provenance)
        body["requirement"] = req.dump()
        body["messages"].append({"role": "assistant", "content": json_dumps(parsed)})
        self._arm_sla(body, req, 1)
        self._save(body, event="scope_written", at=self.clock())
        result = self.get(requirement_id)
        result["scope_sha"] = sha
        return result

    # ── BRD edit ─────────────────────────────────────────────────────────────

    def edit_brd(self, requirement_id: str, editor: dict[str, Any], content: str) -> dict[str, Any]:
        body = self._load(requirement_id)
        req = R.Requirement.load(body["requirement"])
        if req.awaiting != 2:
            raise R.TransitionRefused(f"{requirement_id} is not at Gate 2")
        sha = self._commit_brd(req, content, editor)
        req.record_artefact("brd", sha, **PROVENANCE_BRD)
        body["requirement"] = req.dump()
        self._save(body, event="brd_edited", at=self.clock())
        return self.get(requirement_id)

    # ── gates ────────────────────────────────────────────────────────────────

    def decide(
        self,
        requirement_id: str,
        actor: dict[str, Any],
        outcome: str,
        *,
        gate: int | None = None,
        channel: str = "workspace",
    ) -> dict[str, Any]:
        body = self._load(requirement_id)
        req = R.Requirement.load(body["requirement"])
        gate = gate if gate is not None else req.awaiting
        if gate is None:
            raise R.TransitionRefused(f"{requirement_id} has no open gate")
        ctx = ContextInputs(
            skill_file_versions={"intake": body.get("skill_version") or "shipped"},
            retrieval_document_ids=list(body.get("retrieval_document_ids") or []),
            precedent_enabled=bool(body.get("precedent_enabled")),
            connectors=[],
        )
        verdict = self.adapters.policy.evaluate(
            gate,
            outcome,
            actor,
            req._as_gate_input(gate),
            expected_gate=req.awaiting,
        )
        if not verdict.allowed:
            import gate_engine

            raise gate_engine.GateRefused(verdict.reason or "gate policy refused decision")
        decision = req.decide(gate, outcome, actor, context=ctx, timestamp=self.clock())
        statement = req.attestations[-1]
        path = req.path_for_attestation(statement)
        envelope = self.adapters.signer.sign(statement)
        branch = f"scope/{requirement_id}" if gate == 1 else f"brd/{requirement_id}"
        self.git.commit_files(
            branch,
            {path: json_dumps(envelope)},
            f"attest gate {gate} {outcome}",
            author="platform",
            email="platform@local",
        )
        body["requirement"] = req.dump()

        if outcome == "approve" and decision.satisfied:
            self.git.merge_to_main(branch, f"approve gate {gate} for {requirement_id}")
            if gate == 1:
                self._write_brd_from_main(req, body)
            self._arm_sla(body, req, req.awaiting)
            body["escalated"] = False
        elif outcome == "revise":
            if gate == 1:
                body["messages"] = []
                body["budget"]["asked"] = 0
                req.artefacts.pop("scope", None)
                body["requirement"] = req.dump()
            self._arm_sla(body, req, gate)
        elif outcome == "discard":
            body["sla_due"] = None
            body["sla_gate"] = None

        body["requirement"] = req.dump()
        self._save(
            body,
            event="decision",
            at=self.clock(),
            extra={"gate": gate, "outcome": outcome, "channel": channel, "satisfied": decision.satisfied},
        )
        self.adapters.telemetry.event(
            "phase1.decision",
            {
                "requirement_id": requirement_id,
                "gate": gate,
                "outcome": outcome,
                "satisfied": decision.satisfied,
                "channel": channel,
            },
        )
        if outcome == "revise" and gate == 1:
            restarted = self.turn(requirement_id, body["request_text"], as_originator=True)
            restarted["decision"] = {
                "gate": decision.gate,
                "outcome": decision.outcome,
                "satisfied": decision.satisfied,
                "awaiting_roles": decision.awaiting,
                "note": decision.note,
            }
            return restarted
        result = self.get(requirement_id)
        result["decision"] = {
            "gate": decision.gate,
            "outcome": decision.outcome,
            "satisfied": decision.satisfied,
            "awaiting_roles": decision.awaiting,
            "note": decision.note,
        }
        return result

    def ingest_webhook(self, raw: bytes, signature_header: str) -> dict[str, Any]:
        webhook.verify(raw, signature_header, self.webhook_secret)
        payload = webhook.parse_approval(raw)
        actor = self.adapters.identity.actor(payload["actor_id"])
        return self.decide(
            payload["requirement_id"],
            actor,
            payload["outcome"],
            gate=int(payload["gate"]),
            channel="github",
        )

    def inbox(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        roles = {r.lower() for r in (actor.get("roles") or [])}
        waiting = []
        for row in self.list_runs():
            req = R.Requirement.load(row["requirement"])
            if req.awaiting not in (1, 2):
                continue
            entitled = set(gate_roles(req.awaiting)) & roles
            if not entitled:
                continue
            if actor.get("id") == (req.originator or {}).get("id") and req.awaiting == 1:
                continue
            waiting.append(row)
        return waiting

    def tick(self, now: str | None = None) -> list[str]:
        """Compatibility poller for direct mode; Temporal mode owns the timer."""
        now = now or self.clock()
        escalated = []
        for row in self.store.all():
            rid = row["requirement"]["id"]
            if self.escalate(rid, now=now):
                escalated.append(rid)
        return escalated

    def escalate(
        self,
        requirement_id: str,
        *,
        gate: int | None = None,
        now: str | None = None,
    ) -> bool:
        """Apply one idempotent SLA escalation for a Temporal timer activity."""
        now = now or self.clock()
        body = self._load(requirement_id)
        due = body.get("sla_due")
        if (
            not due
            or body.get("escalated")
            or now < due
            or (gate is not None and body.get("sla_gate") != gate)
        ):
            return False
        body["escalated"] = True
        self._save(
            body,
            event="sla_escalated",
            at=now,
            extra={"gate": body.get("sla_gate")},
        )
        self.adapters.telemetry.event(
            "phase1.sla_escalated",
            {"requirement_id": requirement_id, "gate": body.get("sla_gate")},
        )
        return True

    # ── internals ────────────────────────────────────────────────────────────

    def _load(self, requirement_id: str) -> dict[str, Any]:
        body = self.store.get(requirement_id)
        if body is None:
            raise KeyError(requirement_id)
        return body

    def _save(self, body: dict[str, Any], *, event: str, at: str, extra: dict | None = None) -> None:
        rid = body["requirement"]["id"]
        self.store.put(rid, body, at=at)
        self.store.append_event(rid, event, extra or {}, at=at)

    def _phase(self, req: R.Requirement) -> str:
        if req.state == "discarded":
            return "discarded"
        if req.awaiting is None or req.awaiting > 2:
            return "phase_2"
        if req.awaiting == 1:
            return "intake" if "scope" not in req.artefacts else "awaiting_gate_1"
        if req.awaiting == 2:
            return "brd" if "brd" not in req.artefacts else "awaiting_gate_2"
        return "open"

    def _artefact_text(self, req: R.Requirement, stage: str) -> str:
        if stage not in req.artefacts:
            return ""
        name = "scope-report.md" if stage == "scope" else "brd.md"
        rel = f"requirements/{req.id}/{stage}/{name}"
        for ref in (f"{stage}/{req.id}", "main"):
            try:
                return self.git.read(rel, ref)
            except Exception:
                continue
        path = self.git.root / rel
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _arm_sla(self, body: dict[str, Any], req: R.Requirement, gate: int | None) -> None:
        if gate is None:
            body["sla_due"] = None
            body["sla_gate"] = None
            return
        hours = workflow_templates.sla_for(req.template, BASE_SLA_HOURS)
        body["sla_due"] = _due(self.clock(), hours)
        body["sla_gate"] = gate

    def _commit_scope(
        self,
        req: R.Requirement,
        body: dict[str, Any],
        scope_md: str,
        skill: intake_skill.SkillFile,
    ) -> str:
        rid = req.id
        files = {
            f"requirements/{rid}/scope/scope-report.md": scope_md,
            intake_skill.snapshot_path_for(rid, "scope"): skill.content,
        }
        return self.git.commit_files(
            f"scope/{rid}",
            files,
            f"scope report {rid}",
            author="intake-agent",
            email="intake@local",
        )

    def _commit_brd(self, req: R.Requirement, content: str, editor: dict[str, Any]) -> str:
        rid = req.id
        return self.git.commit_files(
            f"brd/{rid}",
            {f"requirements/{rid}/brd/brd.md": content},
            f"brd {rid}",
            author=str(editor.get("name") or editor.get("id")),
            email=str(editor.get("email") or f"{editor.get('id')}@entra.local"),
        )

    def _write_brd_from_main(self, req: R.Requirement, body: dict[str, Any]) -> None:
        rid = req.id
        scope_md = self.git.read(f"requirements/{rid}/scope/scope-report.md", "main")
        parsed = brd.parse_scope(scope_md)
        count = max(len(parsed["in_scope"]), 1)
        ids = req.allocate_traceability_ids(count)
        drafted = brd.draft_brd(rid, scope_md, self.git.brd_template(), ids)
        drafted = brd.append_findings(drafted, brd.critique(drafted))
        sha = self._commit_brd(
            req,
            drafted,
            {"id": "brd-agent", "name": "BRD Agent", "email": "brd@local"},
        )
        req.record_artefact("brd", sha, **PROVENANCE_BRD)
        body["requirement"] = req.dump()
        self._arm_sla(body, req, 2)


def gate_roles(gate: int) -> list[str]:
    import gate_engine

    return sorted(gate_engine.GATES[gate].approver_roles)


def json_dumps(data: Any) -> str:
    import json

    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def json_question(text: str) -> str:
    import json

    return json.dumps({"type": "question", "text": text})
