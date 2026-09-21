"""Phase 1 side-effect service used directly or from Temporal activities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import intake_skill
import requirement as R
import skill_registry
import workflow_templates
from attestation import ContextInputs
from phase1 import agent_settings, brd, catalog, rails, reconcile, webhook
from phase1.adapters import ModelCompletion, RuntimeAdapters
from phase1.adapters.signer import LocalHMACSigner
from phase2 import design as design_agent
from phase2 import jsx_gate, preview as preview_agent, ui as ui_agent
from phase3 import plan as plan_agent
from phase4 import build as build_agent
from phase5 import pack_release, pack_uat

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
PROVENANCE_DESIGN = dict(
    model="deterministic/design",
    model_version="phase2-1",
    prompt_version="design-system.skill.md",
)
PROVENANCE_PLAN = dict(
    model="deterministic/plan",
    model_version="phase3-1",
    prompt_version="department-routing",
)
PROVENANCE_BUILD = dict(
    model="deterministic/build",
    model_version="phase4-1",
    prompt_version="path-allowlist",
)
PROVENANCE_UAT = dict(
    model="local/uat",
    model_version="phase5-1",
    prompt_version="qa-dast-locust-argo",
)
PROVENANCE_RELEASE = dict(
    model="local/release",
    model_version="phase5-1",
    prompt_version="canary-kyverno-rollback-monitor",
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
        intake_bundle = self.git.bundle("intake")
        now = self.clock()
        screened_request = self.adapters.privacy.screen(request_text)
        precedents = self.adapters.precedent.search(screened_request, limit=5)
        body = {
            "requirement": req.dump(),
            "messages": [],
            "budget": {"asked": 0, "minimum": 4, "maximum": 10},
            "named_approvers": self.adapters.identity.freeze_chain(
                template, originator_id=originator.get("id")
            ),
            "request_text": screened_request,
            "retrieval_document_ids": [item.document_id for item in precedents],
            "precedent_enabled": bool(precedents),
            "skill_version": skill.version,
            "skill_versions": skill_registry.versions(intake_bundle),
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
        body["architecture_text"] = self._artefact_text(req, "design")
        body["plan_text"] = self._artefact_text(req, "plan")
        body["stack_profile"] = body.get("stack_profile")
        body["screens"] = list(body.get("screens") or [])
        body["coverage"] = dict(body.get("coverage") or {})
        body["tickets"] = list(body.get("tickets") or [])
        body["tests"] = list(body.get("tests") or [])
        body["team_reports"] = list(body.get("team_reports") or [])
        body["sprint0"] = dict(body.get("sprint0") or {})
        body["build"] = dict(body.get("build") or {})
        body["uat"] = dict(body.get("uat") or {})
        body["release"] = dict(body.get("release") or {})
        body["preview_url"] = body.get("preview_url") or (
            preview_agent.url(requirement_id) if body.get("screens") else ""
        )
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
        body["messages"].append({"role": "user", "content": text})
        if not as_originator and rails.is_bootstrap(str(body.get("request_text") or "")):
            body["request_text"] = text

        budget = intake_skill.QuestionBudget(
            minimum=int(body["budget"]["minimum"]),
            maximum=int(body["budget"]["maximum"]),
            asked=int(body["budget"]["asked"]),
        )
        skill = self.git.skill()
        bundle = self.git.bundle("intake")
        skill_registry.require(bundle.content, "intake")
        prompt = [
            {
                "role": "system",
                "content": (
                    "Reply with one JSON object only. No markdown. "
                    'Either {"type":"question","text":"..."} or a scope_report. '
                    "scope_report fields: title (short product name), "
                    "users, current_state (what happens today, in their words), "
                    "in_scope (6-10 testable capabilities drawn from the answers — "
                    "never the phrase about starting a guided intake conversation, "
                    "never paste the whole transcript as one bullet), "
                    "out_of_scope (exclusions only; do not list the browser itself as out of scope), "
                    "success (the afterwards picture, not a feature list), "
                    "non_functional (channel and constraints), assumptions, open_questions.\n\n"
                    + intake_skill.prompt_section(skill)
                ),
            }
        ]
        if budget.must_close():
            prompt.append({"role": "system", "content": '{"type":"scope_report"} required now'})

        completion = self.llm.complete(
            prompt + body["messages"],
            skill=bundle.content,
            model=agent_settings.model_for(self.root, "intake"),
        )
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
        if parsed["type"] == "scope_report":
            parsed = rails.shape_scope_report(
                parsed, request_text=str(body.get("request_text") or "")
            )
            overlay = rails.scope_from_conversation(
                body["messages"], request_text=str(body.get("request_text") or "")
            )
            thin = (
                any(rails.is_bootstrap(item) for item in parsed.get("in_scope") or [])
                or len(parsed.get("in_scope") or []) < 3
            )
            if thin:
                parsed = overlay
            else:
                parsed.setdefault("users", overlay.get("users"))
                parsed.setdefault("current_state", overlay.get("current_state"))
                if not parsed.get("non_functional"):
                    parsed["non_functional"] = overlay.get("non_functional") or []

        if parsed["type"] != "question" and not budget.may_close():
            parsed = {
                "type": "question",
                "text": "What else must be true for this to succeed in production?",
            }

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
        body["display_title"] = str(parsed.get("title") or "").strip()
        if rails.is_bootstrap(body["display_title"]):
            body["display_title"] = rails.title_from_request(str(body.get("request_text") or ""))
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

    def edit_scope(
        self,
        requirement_id: str,
        editor: dict[str, Any],
        title: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        body = self._load(requirement_id)
        req = R.Requirement.load(body["requirement"])
        if req.awaiting != 1 or "scope" not in req.artefacts:
            raise R.TransitionRefused(f"{requirement_id} is not at Gate 1")
        normalized = {
            "in_scope": list(report.get("inScope") or []),
            "out_of_scope": list(report.get("outOfScope") or []),
            "success": str(report.get("summary") or "").strip(),
            "assumptions": list(report.get("assumptions") or []),
            "open_questions": list(report.get("openQuestions") or []),
            "users": str(report.get("users") or "").strip(),
            "current_state": str(report.get("currentState") or "").strip(),
            "non_functional": list(report.get("nonFunctionalRequirements") or []),
        }
        skill = self.git.skill()
        content = brd.render_scope(requirement_id, normalized)
        sha = self._commit_scope(req, body, content, skill, editor=editor)
        req.record_artefact("scope", sha, **PROVENANCE_INTAKE)
        body["requirement"] = req.dump()
        body["display_title"] = title.strip()
        self._save(body, event="scope_edited", at=self.clock())
        return self.get(requirement_id)

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
        reason: str = "",
    ) -> dict[str, Any]:
        body = self._load(requirement_id)
        req = R.Requirement.load(body["requirement"])
        gate = gate if gate is not None else req.awaiting
        if gate is None:
            raise R.TransitionRefused(f"{requirement_id} has no open gate")
        if gate == 5 and outcome == "revise":
            outcome = "request_changes"
        if gate == 6 and outcome in {"revise", "discard"}:
            outcome = "reject"
        if gate == 7 and outcome in {"revise", "discard"}:
            outcome = "hold"
        if req.awaiting == 6 and "uat_deploy" not in req.artefacts:
            self._write_uat(req, body)
            req = R.Requirement.load(body["requirement"])
        if req.awaiting == 7 and "release" not in req.artefacts:
            self._write_release(req, body)
            req = R.Requirement.load(body["requirement"])
        ctx = ContextInputs(
            skill_file_versions={
                "intake": body.get("skill_version") or "shipped",
                "design_system": "shipped",
                **dict(body.get("skill_versions") or {}),
            },
            retrieval_document_ids=list(body.get("retrieval_document_ids") or []),
            precedent_enabled=False if (gate or 0) >= 3 else bool(body.get("precedent_enabled")),
            connectors=[],
        )
        named = body.get("named_approvers") or {}
        required = [
            person.get("id")
            for person in (named.get(str(gate)) or [])
            if person.get("id")
        ]
        verdict = self.adapters.policy.evaluate(
            gate,
            outcome,
            actor,
            req._as_gate_input(gate),
            expected_gate=req.awaiting,
            required_signers=required or None,
        )
        if not verdict.allowed:
            import gate_engine

            raise gate_engine.GateRefused(verdict.reason or "gate policy refused decision")
        decision = req.decide(
            gate,
            outcome,
            actor,
            context=ctx,
            timestamp=self.clock(),
            reason=reason,
            required_signers=required or None,
        )
        statement = req.attestations[-1]
        path = req.path_for_attestation(statement)
        envelope = self.adapters.signer.sign(statement)
        if gate == 1:
            branch = f"scope/{requirement_id}"
        elif gate == 2:
            branch = f"brd/{requirement_id}"
        elif gate == 3:
            branch = f"design/{requirement_id}"
        elif gate == 4:
            branch = f"plan/{requirement_id}"
        elif gate == 5:
            branch = f"build/{requirement_id}"
        elif gate == 6:
            branch = f"uat/{requirement_id}"
        else:
            branch = f"release/{requirement_id}"
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
            elif gate == 2:
                self._write_design(req, body)
            elif gate == 3:
                self._write_plan(req, body)
            elif gate == 4:
                self._write_build(req, body)
            elif gate == 5:
                self._merge_build(req, body)
                self._write_uat(req, body)
            elif gate == 6:
                self._write_release(req, body)
            self._arm_sla(body, req, req.awaiting)
            body["escalated"] = False
        elif outcome in {"revise", "request_changes", "reject"}:
            if gate == 1:
                body["messages"] = []
                body["budget"]["asked"] = 0
                req.artefacts.pop("scope", None)
                body["requirement"] = req.dump()
            elif gate == 2:
                self._write_brd_from_main(req, body, revision=True)
            elif gate == 3:
                req.artefacts.pop("design", None)
                body["requirement"] = req.dump()
                self._write_design(req, body)
            elif gate == 4:
                req.artefacts.pop("plan", None)
                body["requirement"] = req.dump()
                self._write_plan(req, body)
            elif gate == 5:
                req.artefacts.pop("build", None)
                body["requirement"] = req.dump()
                self._write_build(req, body)
            elif gate == 6:
                req.artefacts.pop("uat_deploy", None)
                body["requirement"] = req.dump()
                self._write_uat(req, body)
            self._arm_sla(body, req, gate)
        elif outcome == "discard":
            body["sla_due"] = None
            body["sla_gate"] = None

        body["requirement"] = req.dump()
        self._save(
            body,
            event="decision",
            at=self.clock(),
            extra={
                "gate": gate,
                "outcome": outcome,
                "channel": channel,
                "satisfied": decision.satisfied,
                "reason": reason,
            },
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
            if req.awaiting not in (1, 2, 3, 4, 5, 6, 7):
                continue
            entitled = set(gate_roles(req.awaiting)) & roles
            if not entitled:
                continue
            if actor.get("id") == (req.originator or {}).get("id") and req.awaiting == 1:
                continue
            waiting.append(row)
        return waiting

    def tick(self, now: str | None = None) -> list[str]:
        """SLA poller plus GitHub missed-webhook reconciliation."""
        now = now or self.clock()
        escalated = []
        for row in self.store.all():
            rid = row["requirement"]["id"]
            if self.escalate(rid, now=now):
                escalated.append(rid)
        for rid in reconcile.apply(self):
            if rid not in escalated:
                escalated.append(rid)
        return escalated

    def catalog(self) -> list[dict[str, Any]]:
        return catalog.entities(self.list_runs())

    def list_change_requests(self) -> list[dict[str, Any]]:
        return list(self._crs())

    def submit_change_request(
        self,
        actor: dict[str, Any],
        requirement_id: str,
        justification: str,
        impact: str,
    ) -> dict[str, Any]:
        from phase5.change_request import draft

        body = self._load(requirement_id)
        req = R.Requirement.load(body["requirement"])
        artefact = req.artefacts.get("brd")
        brd_sha = artefact.sha if artefact else ""
        rows = self._crs()
        change_id = f"CR-{len(rows) + 1:04d}"
        item = draft(
            requirement_id=requirement_id,
            brd_sha=brd_sha,
            justification=justification,
            impact=impact,
            actor=actor,
            change_id=change_id,
        )
        follow = self.submit(
            actor,
            req.template,
            f"Change request {change_id}: {justification}",
        )
        item["follow_on"] = follow["requirement"]["id"]
        item["status"] = "opened_cycle"
        rows.append(item)
        self._write_crs(rows)
        return item

    def _crs(self) -> list[dict[str, Any]]:
        import json

        path = self.root / "change-requests.json"
        if not path.exists():
            return []
        try:
            return list(json.loads(path.read_text()))
        except json.JSONDecodeError:
            return []

    def _write_crs(self, rows: list[dict[str, Any]]) -> None:
        import json

        (self.root / "change-requests.json").write_text(json.dumps(rows, indent=2))

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
        if req.awaiting is None:
            return "complete"
        if req.awaiting == 7:
            return "awaiting_gate_7" if "release" in req.artefacts else "releasing"
        if req.awaiting == 6:
            return "awaiting_gate_6" if "uat_deploy" in req.artefacts else "uat"
        if req.awaiting == 5:
            return "awaiting_gate_5" if "build" in req.artefacts else "building"
        if req.awaiting == 4:
            return "awaiting_gate_4" if "plan" in req.artefacts else "planning"
        if req.awaiting == 3:
            return "awaiting_gate_3" if "design" in req.artefacts else "designing"
        if req.awaiting == 1:
            return "intake" if "scope" not in req.artefacts else "awaiting_gate_1"
        if req.awaiting == 2:
            return "brd" if "brd" not in req.artefacts else "awaiting_gate_2"
        return "open"

    def _artefact_text(self, req: R.Requirement, stage: str) -> str:
        if stage not in req.artefacts:
            return ""
        names = {
            "scope": "scope-report.md",
            "brd": "brd.md",
            "design": "architecture.md",
            "plan": "plan.md",
            "build": "build.md",
            "uat_deploy": "STATUS.md",
            "release": "STATUS.md",
        }
        name = names.get(stage, f"{stage}.md")
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
        *,
        editor: dict[str, Any] | None = None,
    ) -> str:
        rid = req.id
        files = {
            f"requirements/{rid}/scope/scope-report.md": scope_md,
            intake_skill.snapshot_path_for(rid, "scope"): skill.content,
        }
        files.update(skill_registry.snapshot_paths(rid, "scope", self.git.bundle("intake")))
        return self.git.commit_files(
            f"scope/{rid}",
            files,
            f"scope report {rid}",
            author=str((editor or {}).get("name") or (editor or {}).get("id") or "intake-agent"),
            email=str((editor or {}).get("email") or "intake@local"),
        )

    def _commit_brd(
        self,
        req: R.Requirement,
        content: str,
        editor: dict[str, Any],
        extra: dict[str, str] | None = None,
    ) -> str:
        rid = req.id
        files = {f"requirements/{rid}/brd/brd.md": content}
        files.update(extra or {})
        return self.git.commit_files(
            f"brd/{rid}",
            files,
            f"brd {rid}",
            author=str(editor.get("name") or editor.get("id")),
            email=str(editor.get("email") or f"{editor.get('id')}@entra.local"),
        )

    def _write_brd_from_main(
        self,
        req: R.Requirement,
        body: dict[str, Any],
        *,
        revision: bool = False,
    ) -> None:
        rid = req.id
        scope_md = self.git.read(f"requirements/{rid}/scope/scope-report.md", "main")
        parsed = brd.parse_scope(scope_md)
        count = max(len(parsed["in_scope"]), 1)
        if revision:
            import re

            previous = body.get("brd_text") or self._artefact_text(req, "brd")
            req.retire_traceability_ids(
                sorted(set(re.findall(rf"{re.escape(rid)}-R\d{{2}}", previous)))
            )
        ids = req.allocate_traceability_ids(count)
        bundle = self.git.bundle("brd")
        skill_registry.require(bundle.content, "brd")
        drafted = brd.draft_brd(rid, scope_md, self.git.brd_template(), ids, skill=bundle.content)
        drafted = brd.append_findings(drafted, brd.critique(drafted, skill=bundle.content))
        sha = self._commit_brd(
            req,
            drafted,
            {"id": "brd-agent", "name": "BRD Agent", "email": "brd@local"},
            extra=skill_registry.snapshot_paths(rid, "brd", bundle),
        )
        req.record_artefact("brd", sha, **PROVENANCE_BRD)
        body["requirement"] = req.dump()
        self._arm_sla(body, req, 2)

    def _write_design(self, req: R.Requirement, body: dict[str, Any]) -> None:
        rid = req.id
        brd_text = body.get("brd_text") or self._artefact_text(req, "brd")
        skill_text = self.git.design_system()
        built = design_agent.build(rid, brd_text, skill_text, root=self.git.root)
        if built.get("clarification"):
            body["design_question"] = built["clarification"]
            body["screens"] = []
            body["stack_profile"] = None
            return
        sha = self.git.commit_files(
            f"design/{rid}",
            built["files"],
            f"design {rid}",
            author="design-agent",
            email="design@local",
        )
        req.record_artefact("design", sha, **PROVENANCE_DESIGN)
        body["requirement"] = req.dump()
        body["stack_profile"] = built["profile"]
        body["screens"] = built["screens"]
        body["coverage"] = built["coverage"]
        body["design_report"] = built["report"]
        body["design_question"] = None
        body["preview_url"] = built.get("preview_url") or preview_agent.url(rid)
        self._arm_sla(body, req, 3)

    def _write_plan(self, req: R.Requirement, body: dict[str, Any]) -> None:
        rid = req.id
        named = body.get("named_approvers") or {}
        reviewers = []
        seen: set[str] = set()
        for key in ("4", 4):
            for person in named.get(key) or []:
                ident = person.get("id")
                if ident and ident not in seen:
                    seen.add(ident)
                    reviewers.append(ident)
        built = plan_agent.build(
            rid,
            template=req.template,
            brd_text=body.get("brd_text") or self._artefact_text(req, "brd"),
            architecture_text=body.get("architecture_text") or self._artefact_text(req, "design"),
            screens=list(body.get("screens") or []),
            stack_profile=body.get("stack_profile") or {"id": "node"},
            reviewers=reviewers,
            root=self.git.root,
        )
        sha = self.git.commit_files(
            f"plan/{rid}",
            built["files"],
            f"plan {rid}",
            author="plan-agent",
            email="plan@local",
        )
        req.record_artefact("plan", sha, **PROVENANCE_PLAN)
        body["requirement"] = req.dump()
        body["tickets"] = built["tickets"]
        body["tests"] = built["tests"]
        body["overview"] = built["overview"]
        body["sprint0"] = built["sprint0"]
        body["team_reports"] = built["team_reports"]
        body["entities"] = built["entities"]
        self._arm_sla(body, req, 4)

    def _write_build(self, req: R.Requirement, body: dict[str, Any]) -> None:
        rid = req.id
        built = build_agent.materialise(
            rid, list(body.get("tickets") or []), tests=list(body.get("tests") or [])
        )
        files: dict[str, str] = dict(built.get("extra") or {})
        for ticket_files in built["branches"].values():
            files.update(ticket_files)
        sha = self.git.commit_files(
            f"build/{rid}",
            files,
            f"build {rid}",
            author="build-agent",
            email="build@local",
        )
        for branch, ticket_files in built["branches"].items():
            self.git.commit_files(
                branch,
                ticket_files,
                f"build {branch}",
                author="build-agent",
                email="build@local",
            )
        req.record_artefact("build", sha, **PROVENANCE_BUILD)
        body["requirement"] = req.dump()
        body["build"] = {
            "sandbox": built["sandbox"],
            "scans": built["scans"],
            "engine": built["engine"],
            "engines": built.get("engines") or [],
            "ok": built.get("ok", True),
            "blocking": built.get("blocking") or [],
            "branches": list(built["branches"]),
        }
        self._arm_sla(body, req, 5)

    def _merge_build(self, req: R.Requirement, body: dict[str, Any]) -> None:
        for branch in (body.get("build") or {}).get("branches") or []:
            self.git.merge_to_main(str(branch), f"merge {branch}")

    def _write_uat(self, req: R.Requirement, body: dict[str, Any]) -> None:
        rid = req.id
        packed = pack_uat(
            rid,
            list(body.get("tests") or []),
            preview_url=str(body.get("preview_url") or f"/preview/{rid}"),
            html=preview_agent.document(rid, list(body.get("screens") or [])),
        )
        sha = self.git.commit_files(
            f"uat/{rid}",
            packed["files"],
            f"uat {rid}",
            author="uat-agent",
            email="uat@local",
        )
        req.record_artefact("uat_deploy", sha, **PROVENANCE_UAT)
        body["requirement"] = req.dump()
        body["uat"] = packed["uat"]
        self._arm_sla(body, req, 6)

    def _write_release(self, req: R.Requirement, body: dict[str, Any]) -> None:
        rid = req.id
        packed = pack_release(rid)
        sha = self.git.commit_files(
            f"release/{rid}",
            packed["files"],
            f"release {rid}",
            author="release-agent",
            email="release@local",
        )
        req.record_artefact("release", sha, **PROVENANCE_RELEASE)
        body["requirement"] = req.dump()
        body["release"] = packed["release"]
        self._arm_sla(body, req, 7)

    def preview_document(self, requirement_id: str) -> str:
        body = self.get(requirement_id)
        return preview_agent.document(requirement_id, list(body.get("screens") or []))

    def edit_screen(
        self,
        requirement_id: str,
        editor: dict[str, Any],
        name: str,
        source: str,
    ) -> dict[str, Any]:
        body = self._load(requirement_id)
        req = R.Requirement.load(body["requirement"])
        if req.awaiting != 3:
            raise R.TransitionRefused(f"{requirement_id} is not at Gate 3")
        roles = set(editor.get("roles") or [])
        if not roles & {"architect", "ui_ux"}:
            raise PermissionError("screen edits require the architect or UI/UX role")
        jsx_gate.compile_jsx(name, source)
        failures = jsx_gate.conform(source, self.git.design_system())
        if failures:
            raise jsx_gate.CompileFailed(failures[0])
        screens = list(body.get("screens") or [])
        found = False
        for screen in screens:
            if screen.get("name") == name:
                screen["source"] = source
                found = True
        if not found:
            raise KeyError(name)
        files = {f"requirements/{requirement_id}/design/ui/{name}.jsx": source}
        sha = self.git.commit_files(
            f"design/{requirement_id}",
            files,
            f"edit screen {name}",
            author=str(editor.get("name") or editor.get("id")),
            email=str(editor.get("email") or f"{editor.get('id')}@entra.local"),
        )
        req.record_artefact("design", sha, **PROVENANCE_DESIGN)
        body["requirement"] = req.dump()
        body["screens"] = screens
        self._save(body, event="screen_edited", at=self.clock(), extra={"name": name})
        return self.get(requirement_id)

    def apply_screen_instruction(
        self,
        requirement_id: str,
        editor: dict[str, Any],
        name: str,
        instruction: str,
    ) -> dict[str, Any]:
        body = self._load(requirement_id)
        screens = list(body.get("screens") or [])
        current = next((s for s in screens if s.get("name") == name), None)
        if current is None:
            raise KeyError(name)
        source = ui_agent.apply_instruction(
            name, current["source"], instruction, self.git.design_system()
        )
        return {"name": name, "source": source, "summary": "Applied locally — save to commit"}

    def answer_design(
        self, requirement_id: str, actor: dict[str, Any], message: str
    ) -> dict[str, Any]:
        body = self._load(requirement_id)
        req = R.Requirement.load(body["requirement"])
        originator = req.originator or {}
        if actor.get("id") != originator.get("id"):
            raise PermissionError("only the originator can answer a design clarification")
        if not body.get("design_question"):
            raise R.TransitionRefused(f"{requirement_id} has no open design question")
        appendix = f"\n\n## Clarification\n\n**Q:** {body['design_question']}\n\n**A:** {message}\n"
        brd_text = (body.get("brd_text") or self._artefact_text(req, "brd")) + appendix
        sha = self._commit_brd(req, brd_text, actor)
        req.record_artefact("brd", sha, **PROVENANCE_BRD)
        body["requirement"] = req.dump()
        body["brd_text"] = brd_text
        body["design_question"] = None
        self._write_design(req, body)
        self._save(body, event="design_clarified", at=self.clock())
        return self.get(requirement_id)


def gate_roles(gate: int) -> list[str]:
    import gate_engine

    return sorted(gate_engine.GATES[gate].approver_roles)


def json_dumps(data: Any) -> str:
    import json

    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def json_question(text: str) -> str:
    import json

    return json.dumps({"type": "question", "text": text})
