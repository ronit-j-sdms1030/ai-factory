"""Phase 1 side-effect service used directly or from Temporal activities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import json
import re

import connectors
import intake_skill
import requirement as R
import skill_registry
import workflow_templates
from attestation import ContextInputs
from phase1 import agent_runs, agent_settings, brd, catalog, rails, reconcile, webhook
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
            "budget": {"asked": 0, "minimum": 4, "maximum": 10, "calls": 0},
            "named_approvers": self.adapters.identity.freeze_chain(
                template, originator_id=originator.get("id")
            ),
            "request_text": screened_request,
            "retrieval_document_ids": [item.document_id for item in precedents],
            "precedent_enabled": bool(precedents),
            "skill_version": skill.version,
            "skill_versions": skill_registry.versions(intake_bundle),
            "connectors": connectors.resolve_all(
                str(originator.get("team") or "demo")
            ),
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
        body["architecture_text"] = body.get("architecture_text") or self._artefact_text(req, "design")
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

    def clear_runs(self) -> dict[str, Any]:
        ids = [row["requirement"]["id"] for row in self.store.all() if row.get("requirement")]
        self.store.delete_all()
        return {"deleted": ids, "count": len(ids)}

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
            calls=int(body["budget"].get("calls") or 0),
        )
        skill = self.git.skill()
        bundle = self.git.bundle("intake")
        skill_registry.require(bundle.content, "intake")
        intake_call = self._skill_call("intake", bundle.content)
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
            skill=intake_call["skill"],
            model=agent_settings.model_for(self.root, "intake"),
            agent="intake",
            requirement_id=requirement_id,
            max_tokens=4096,
            **(
                {"tools": intake_call["tools"], "execute_tool": intake_call["execute_tool"]}
                if intake_call.get("tools")
                else {}
            ),
        )
        budget.record_call()
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
        def _scope_from_chat() -> dict[str, Any]:
            return rails.scope_from_conversation(
                body["messages"],
                request_text=str(body.get("request_text") or ""),
            )

        try:
            parsed = rails.parse_agent_output(raw)
        except (TypeError, rails.OutputRefused):
            # Enough answers already + model dumped/truncated a scope_report → close
            # from the transcript instead of pasting JSON into the chat bubble.
            if (
                budget.at_call_cap()
                or budget.must_close()
                or (budget.may_close() and rails.looks_like_aborted_scope(str(raw or "")))
            ):
                parsed = _scope_from_chat()
            else:
                retry_kwargs: dict[str, Any] = {
                    "skill": intake_call["skill"],
                    "model": agent_settings.model_for(self.root, "intake"),
                    "agent": "intake",
                    "requirement_id": requirement_id,
                    "max_tokens": 4096,
                }
                if intake_call.get("tools"):
                    retry_kwargs["tools"] = intake_call["tools"]
                    retry_kwargs["execute_tool"] = intake_call["execute_tool"]
                try:
                    retry = self.llm.complete(
                        prompt
                        + body["messages"]
                        + [
                            {
                                "role": "system",
                                "content": 'JSON only. {"type":"question","text":"..."} or a scope_report.',
                            }
                        ],
                        **retry_kwargs,
                    )
                except TypeError:
                    retry_kwargs.pop("max_tokens", None)
                    retry_kwargs.pop("agent", None)
                    retry_kwargs.pop("requirement_id", None)
                    retry = self.llm.complete(
                        prompt + body["messages"],
                        **retry_kwargs,
                    )
                budget.record_call()
                raw = retry.text if isinstance(retry, ModelCompletion) else retry
                try:
                    parsed = rails.parse_agent_output(raw)
                except rails.OutputRefused:
                    if budget.may_close():
                        parsed = _scope_from_chat()
                    else:
                        parsed = {
                            "type": "question",
                            "text": "Say that in one short answer: who uses this, and what happens today without the system?",
                        }
        body["budget"]["calls"] = budget.calls
        if parsed.get("type") == "question" and rails.looks_like_aborted_scope(
            str(parsed.get("text") or "")
        ):
            if budget.may_close():
                parsed = _scope_from_chat()
            else:
                parsed = {
                    "type": "question",
                    "text": "What else must be true for this to succeed in production?",
                }
        if parsed.get("type") == "question" and budget.must_close():
            parsed = _scope_from_chat()
            extras = list(parsed.get("open_questions") or [])
            extras.append("Intake hit the 10-call cap; remaining gaps stay as open questions.")
            parsed["open_questions"] = extras[:6]
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

        if parsed["type"] != "question" and not budget.may_close() and not budget.at_call_cap():
            parsed = {
                "type": "question",
                "text": "What else must be true for this to succeed in production?",
            }

        if parsed["type"] == "question":
            budget.record_question()
            body["budget"]["asked"] = budget.asked
            body["budget"]["calls"] = budget.calls
            body["messages"].append(
                {"role": "assistant", "content": json_question(parsed["text"])}
            )
            self._save(body, event="intake_question", at=self.clock())
            result = self.get(requirement_id)
            result["question"] = parsed["text"]
            return result

        if budget.may_close():
            budget.require_closeable()
        body["budget"]["asked"] = budget.asked
        body["budget"]["calls"] = budget.calls
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

    _SCOPE_LIST_KEYS = (
        "inScope",
        "outOfScope",
        "functionalRequirements",
        "nonFunctionalRequirements",
        "assumptions",
        "openQuestions",
    )
    _SCOPE_TEXT_KEYS = ("summary", "users", "currentState")

    def revise_scope_draft(
        self,
        requirement_id: str,
        editor: dict[str, Any],
        instruction: str,
        title: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        """Model rewrite of the pre-send scope report. Nothing is committed; Send does that."""
        body = self._load(requirement_id)
        req = R.Requirement.load(body["requirement"])
        if req.awaiting != 1 or "scope" not in req.artefacts:
            raise R.TransitionRefused(f"{requirement_id} is not at Gate 1")
        if not instruction.strip():
            raise ValueError("instruction is required")
        keys = self._SCOPE_TEXT_KEYS + self._SCOPE_LIST_KEYS
        current = {"title": title, **{k: report.get(k) for k in keys}}
        transcript = "\n".join(
            f"{m.get('role')}: {m.get('content')}"
            for m in body.get("messages") or []
            if m.get("role") in {"user", "assistant"}
        )[-6000:]
        completion = self.llm.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You revise a requirement scope report. Apply the change the requester asks for, "
                        "keep everything else as it is, and never invent facts the conversation does not support. "
                        "Reply with one JSON object only, no markdown, with exactly these keys: title, "
                        + ", ".join(keys)
                        + ". List keys are arrays of short strings; the others are strings."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Intake conversation:\n{transcript}\n\n"
                        f"Current report:\n{json.dumps(current, indent=1)}\n\n"
                        f"Change requested:\n{instruction.strip()}"
                    ),
                },
            ],
            skill="",
            model=agent_settings.model_for(self.root, "intake"),
            agent="intake",
            requirement_id=requirement_id,
            max_tokens=2048,
        )
        raw = completion.text if isinstance(completion, ModelCompletion) else str(completion)
        text = raw.strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
        if fenced:
            text = fenced.group(1).strip()
        start = text.find("{")
        try:
            data, _ = json.JSONDecoder().raw_decode(text[start:] if start >= 0 else text)
        except json.JSONDecodeError as exc:
            raise ValueError("the model did not return a usable report, try rephrasing") from exc
        if not isinstance(data, dict):
            raise ValueError("the model did not return a usable report, try rephrasing")
        revised = dict(report)
        for key in self._SCOPE_TEXT_KEYS:
            if isinstance(data.get(key), str) and data[key].strip():
                revised[key] = data[key].strip()
        for key in self._SCOPE_LIST_KEYS:
            if isinstance(data.get(key), list):
                revised[key] = [str(item).strip() for item in data[key] if str(item).strip()]
        new_title = str(data.get("title") or "").strip() or title
        return {"title": new_title, "content": revised}

    def revise_brd_draft(
        self,
        requirement_id: str,
        editor: dict[str, Any],
        instruction: str,
        content: str,
    ) -> dict[str, Any]:
        """Model rewrite of the Gate 2 BRD markdown. Nothing is committed; Save does that."""
        body = self._load(requirement_id)
        req = R.Requirement.load(body["requirement"])
        if req.awaiting != 2 or "brd" not in req.artefacts:
            raise R.TransitionRefused(f"{requirement_id} is not at Gate 2")
        if not instruction.strip():
            raise ValueError("instruction is required")
        current = (content or body.get("brd_text") or self._artefact_text(req, "brd") or "").strip()
        if not current:
            raise ValueError("BRD content is empty")
        completion = self.llm.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You revise a Business Requirements Document in Markdown. "
                        "Apply only the change the editor asks for. Keep every other "
                        "section, heading, requirement id, and acceptance criterion as it is. "
                        "Do not invent facts. Reply with the full revised BRD markdown only — "
                        "no code fence, no preamble."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Current BRD:\n{current[:16000]}\n\n"
                        f"Change requested:\n{instruction.strip()}"
                    ),
                },
            ],
            skill="",
            model=agent_settings.model_for(self.root, "brd"),
            agent="brd",
            requirement_id=requirement_id,
            max_tokens=4096,
        )
        raw = completion.text if isinstance(completion, ModelCompletion) else str(completion)
        text = (raw or "").strip()
        if not text:
            raise ValueError("the model did not return a usable BRD, try rephrasing")
        # Only unwrap when the whole reply is one outer fence — never strip
        # mermaid/code fences that belong inside the BRD body.
        wrapped = re.match(r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*$", text, re.I)
        if wrapped:
            text = wrapped.group(1).strip()
        if len(text) < 40:
            raise ValueError("the model did not return a usable BRD, try rephrasing")
        return {"content": text}

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
        revise_side: str = "",
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
        # Flowchart: critical/high (or failed build loop) blocks Gate 5 merge.
        # Senior engineer may still request_changes to send work back into the loop.
        if gate == 5 and outcome == "approve":
            import gate_engine

            build = body.get("build") or {}
            findings_board = build.get("findings") or {}
            blocking = list(build.get("blocking") or []) or list(
                findings_board.get("blocking") or []
            )
            if build.get("ok") is False or blocking:
                raise gate_engine.GateRefused(
                    "Gate 5 blocked: build loop left critical/high findings "
                    "(or CI/scan/review/adversary failed). Request changes to rebuild."
                )
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
            try:
                if gate == 1:
                    self._write_brd_from_main(req, body)
                elif gate == 2:
                    # Architecture only — screens wait until BA signs Gate 3.
                    self._write_design(req, body, refresh="architecture")
                elif gate == 3:
                    self._write_plan(req, body)
                elif gate == 4:
                    self._write_build(req, body)
                elif gate == 5:
                    self._merge_build(req, body)
                    self._write_uat(req, body)
                elif gate == 6:
                    self._write_release(req, body)
            except Exception as exc:
                # Gate clearance is already on the requirement. Persist a visible
                # failure so Gate 3 does not look like "BRD only" with no architecture.
                body["design_progress"] = {
                    "status": "error",
                    "label": f"Post-gate generation failed: {type(exc).__name__}: {exc}"[:280],
                    "gate": gate,
                }
                body["requirement"] = req.dump()
                self._save(body, event="gate_write_failed", at=self.clock(), extra={"gate": gate})
                raise
            self._arm_sla(body, req, req.awaiting)
            body["escalated"] = False
        elif outcome == "approve" and gate == 3 and not decision.satisfied:
            # Architect → BA → UI. After BA signs, materialise screens for UI/UX.
            roles = {str(r).lower() for r in (actor.get("roles") or [])}
            if "business_analyst" in roles and not body.get("screens"):
                self._write_screens_for_ui_review(req, body)
        elif outcome in {"revise", "request_changes", "reject"}:
            if gate == 1:
                body["messages"] = []
                body["budget"]["asked"] = 0
                body["budget"]["calls"] = 0
                req.artefacts.pop("scope", None)
                body["requirement"] = req.dump()
            elif gate == 2:
                self._write_brd_from_main(req, body, revision=True)
            elif gate == 3:
                body["architecture_text"] = body.get("architecture_text") or self._artefact_text(
                    req, "design"
                )
                req.artefacts.pop("design", None)
                body["requirement"] = req.dump()
                self._write_design(req, body, refresh=_revise_side(actor, reason, revise_side))
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

    def _invoke_llm(
        self,
        messages: list[dict[str, str]],
        *,
        skill: str,
        agent: str,
        requirement_id: str,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        execute_tool: Any = None,
    ) -> str:
        if tools is None:
            packed = self._skill_call(agent, skill)
            if packed.get("tools"):
                skill = packed["skill"]
                tools = packed["tools"]
                execute_tool = packed["execute_tool"]
        kwargs: dict[str, Any] = {
            "skill": skill,
            "model": agent_settings.model_for(self.root, agent),
            "agent": agent,
            "requirement_id": requirement_id,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["execute_tool"] = execute_tool

        def _failed(exc: BaseException) -> str:
            from phase1 import usage_ledger

            usage_ledger.record_failure(
                self.root,
                agent=agent,
                requirement_id=requirement_id,
                model=str(kwargs.get("model") or ""),
                error=f"{type(exc).__name__}: {exc}",
            )
            return ""

        try:
            completion = self.llm.complete(messages, **kwargs)
        except TypeError:
            kwargs.pop("max_tokens", None)
            try:
                completion = self.llm.complete(messages, **kwargs)
            except TypeError:
                kwargs.pop("agent", None)
                kwargs.pop("requirement_id", None)
                try:
                    completion = self.llm.complete(messages, **kwargs)
                except Exception as exc:
                    return _failed(exc)
            except Exception as exc:
                return _failed(exc)
        except Exception as exc:
            return _failed(exc)
        if isinstance(completion, ModelCompletion):
            return str(completion.text or "")
        return str(completion or "")

    def _llm_json(self, text: str) -> dict[str, Any]:
        import json
        import re

        blob = (text or "").strip()
        if blob.startswith("```"):
            blob = re.sub(r"^```(?:json)?\s*", "", blob)
            blob = re.sub(r"\s*```$", "", blob)
        try:
            data = json.loads(blob)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            start, end = blob.find("{"), blob.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(blob[start : end + 1])
                    return data if isinstance(data, dict) else {}
                except json.JSONDecodeError:
                    return {}
        return {}

    def _llm_prose(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw or (raw.startswith("{") and '"type"' in raw):
            return ""
        return raw

    def _refine_brd(self, requirement_id: str, drafted: str, skill: str) -> str:
        raw = self._invoke_llm(
            [
                {
                    "role": "system",
                    "content": (
                        "Return the full BRD markdown only. Keep '## Page behaviour' as a "
                        "flat unique list of '- **ScreenName**: description' bullets — that "
                        "section is the only screen inventory. Keep '###' requirement "
                        "headings in Functional requirements for acceptance criteria only. "
                        "Never paste '- **Screen**: …' bullets under a ### requirement "
                        "(that duplicates Gate 3 screens)."
                    ),
                },
                {"role": "user", "content": drafted[:14000]},
            ],
            skill=skill,
            agent="brd",
            requirement_id=requirement_id,
            max_tokens=3500,
        )
        import re

        if raw and re.search(r"Page behaviour", raw, re.I) and "### " in raw:
            return brd.normalize_screen_inventory(raw)
        return brd.normalize_screen_inventory(drafted)

    def _critique_brd(self, requirement_id: str, drafted: str, skill: str) -> list[str]:
        raw = self._invoke_llm(
            [
                {
                    "role": "system",
                    "content": 'Reply with one JSON object: {"findings":["..."]}.',
                },
                {"role": "user", "content": drafted[:12000]},
            ],
            skill=skill,
            agent="brd",
            requirement_id=requirement_id,
            max_tokens=800,
        )
        data = self._llm_json(raw)
        findings = [str(item) for item in (data.get("findings") or []) if str(item).strip()]
        return findings or brd.critique(drafted, skill=skill)

    def _architect_note(self, requirement_id: str, brd_text: str, skill: str) -> str:
        raw = self._invoke_llm(
            [
                {
                    "role": "system",
                    "content": "Write a short architecture note (risks, modules, NFRs). Markdown, no code fence.",
                },
                {"role": "user", "content": brd_text[:10000]},
            ],
            skill=skill,
            agent="architect",
            requirement_id=requirement_id,
            max_tokens=1500,
        )
        return self._llm_prose(raw)[:4000]

    def _bundle_skill(self, agent: str, fallback: str = "") -> str:
        try:
            return self.git.bundle(agent).content
        except Exception:
            return fallback

    def _skill_call(self, agent: str, fallback: str = "") -> dict[str, Any]:
        if agent_settings.ui_context(self.root) == "fetch" and skill_registry.can_fetch(agent):
            return {
                "skill": skill_registry.fetch_prompt(agent, root=self.git.root),
                "tools": skill_registry.fetch_tools(agent),
                "execute_tool": lambda name, args: skill_registry.execute_fetch_tool(
                    agent, name, args, root=self.git.root
                ),
            }
        return {
            "skill": fallback or self._bundle_skill(agent, fallback),
            "tools": None,
            "execute_tool": None,
        }

    def _ui_model(
        self,
        requirement_id: str,
        brd_text: str,
        skill: str,
        *,
        on_progress: Any | None = None,
    ) -> dict[str, Any]:
        from phase2 import coverage

        sources: dict[str, str] = {}
        used: set[str] = set()
        pages = coverage.pages_from_brd(brd_text)
        total = len(pages)
        fetch = agent_settings.ui_context(self.root) == "fetch"
        if fetch:
            skill = skill_registry.ui_prompt(root=self.git.root)
            tools = skill_registry.ui_tools()
            execute_tool = skill_registry.execute_ui_tool
        else:
            tools = None
            execute_tool = None

        def report(index: int, name: str, *, done: bool = False) -> None:
            if on_progress is None:
                return
            finished = index if done else max(0, index - 1)
            on_progress(
                {
                    "status": "running",
                    "label": (
                        f"Generated {name} ({index} of {total})"
                        if done
                        else f"Generating screen {index} of {total}: {name}"
                    ),
                    "current": index,
                    "total": total,
                    "screen": name,
                    "pct": int((finished / max(total, 1)) * 100),
                }
            )

        planned: set[str] = set()
        roster = [coverage.component_name(page, planned) for page in pages]
        product_theme = ui_agent.pick_theme(brd_text)
        for index, page in enumerate(pages, start=1):
            name = coverage.component_name(page, used)
            role = ui_agent.screen_role(name, page)
            report(index, name)
            raw = self._invoke_llm(
                [
                    {
                        "role": "system",
                        "content": (
                            "JSX only. No markdown fence. Balanced braces. No raw hex colours. "
                            + _HOST_HELPERS_RULE
                            + "Write only function "
                            + name
                            + ". "
                            + name
                            + " returns ( <Page data-theme=\""
                            + product_theme
                            + "\"><Sidebar>brand + one nav Button per roster "
                            "screen</Sidebar><div>…main…</div></Page> ). "
                            "Screen role for THIS screen: "
                            + role
                            + ". "
                            + ui_agent.layout_contract(role)
                            + " "
                            "Screens are one connected app: each nav Button is "
                            "<Button onClick={() => navigate(\"ScreenName\")}>Short label</Button> "
                            "(navigate is a host global), mark the current screen with "
                            "fontWeight 700 + underline (never accent text on accent Button), "
                            "and in-page jumps call navigate too. "
                            "Only roster screens in the sidebar. "
                            "Use only "
                            + jsx_gate.TOKEN_HINT
                            + ". "
                            "Do NOT stamp the same form+table layout on every screen — "
                            "follow the role contract above. Concrete product microcopy "
                            "from the BRD. No lorem or emoji icons. "
                            + jsx_gate.PLACEHOLDER_RULE
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Screen: {name}\n"
                            f"Role: {role}\n"
                            f"Purpose: {page.get('description') or page.get('id')}\n"
                            "Roster (sidebar order): "
                            + ", ".join(roster)
                            + ".\n\n"
                            + brd_text[:4000]
                        ),
                    },
                ],
                skill=skill,
                agent="ui",
                requirement_id=requirement_id,
                max_tokens=3200,
                tools=tools,
                execute_tool=execute_tool,
            )
            source = _jsx_from_model(name, raw)
            if source:
                sources[name] = source
            report(index, name, done=True)
        if on_progress is not None:
            on_progress(
                {
                    "status": "running",
                    "label": "Assembling the design preview…",
                    "current": total,
                    "total": total,
                    "pct": 96,
                }
            )
        return {"pages": [], "sources": sources}

    def _plan_from_models(
        self,
        requirement_id: str,
        brd_text: str,
        screens: list[dict[str, Any]],
        profile: dict[str, Any],
        architecture_text: str = "",
    ) -> dict[str, Any]:
        from stack_profiles import get as get_profile

        locked = get_profile(str((profile or {}).get("id") or "node"))
        fallback_tickets = None
        fallback_tests = None
        decomp_raw = self._invoke_llm(
            [
                {
                    "role": "system",
                    "content": (
                        'JSON only: {"tickets":[{"id","title","depends_on":[],"trace":[],"paths":[]}]} '
                        "At least 5 tickets from THIS BRD and these screens. "
                        "Do not invent booking or availability work the BRD does not name. "
                        "Paths like src/api/** or src/ui/**."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        brd_text[:8000]
                        + "\n\nScreens: "
                        + ", ".join(str(s.get("name") or "") for s in screens)
                    ),
                },
            ],
            skill=self._bundle_skill("decomposer"),
            agent="decomposer",
            requirement_id=requirement_id,
            max_tokens=1600,
        )
        from phase3 import decomposer, qa

        fallback_tickets = decomposer.tickets(
            requirement_id,
            brd_text,
            screens,
            locked,
            architecture_text=architecture_text,
        )
        tickets = agent_runs.tickets_from_model(
            self._llm_json(decomp_raw), requirement_id, locked, fallback_tickets
        )
        qa_raw = self._invoke_llm(
            [
                {
                    "role": "system",
                    "content": (
                        'JSON only: {"tests":[{"id","name","criterion","critical":false}]} '
                        "Exactly 8 tests from THIS BRD and these screens. One must be critical. "
                        "Do not invent booking cases the BRD does not name."
                    ),
                },
                {"role": "user", "content": brd_text[:10000]},
            ],
            skill=self._bundle_skill("qa"),
            agent="qa",
            requirement_id=requirement_id,
            max_tokens=1200,
        )
        fallback_tests = qa.cases(brd_text, screens, locked)
        tests = agent_runs.tests_from_model(self._llm_json(qa_raw), fallback_tests, locked.tests)
        overview_raw = self._invoke_llm(
            [
                {
                    "role": "system",
                    "content": "Write a short stakeholder overview in markdown. Start with '# Product overview'.",
                },
                {"role": "user", "content": brd_text[:8000]},
            ],
            skill=self._bundle_skill("overview"),
            agent="overview",
            requirement_id=requirement_id,
            max_tokens=1200,
        )
        overview_md = self._llm_prose(overview_raw)
        if overview_md and not overview_md.lstrip().startswith("#"):
            overview_md = f"# Product overview — {requirement_id}\n\n" + overview_md
        devops_raw = self._invoke_llm(
            [
                {
                    "role": "system",
                    "content": "Write Sprint 0 notes: CI, allow-list, UAT URL. Markdown. No secrets.",
                },
                {"role": "user", "content": brd_text[:6000]},
            ],
            skill=self._bundle_skill("devops"),
            agent="devops",
            requirement_id=requirement_id,
            max_tokens=800,
        )
        return {
            "tickets": tickets,
            "tests": tests,
            "overview_md": overview_md or None,
            "devops_note": self._llm_prose(devops_raw),
        }

    def _build_from_models(self, requirement_id: str, tickets: list[dict[str, Any]], extra: dict[str, str]) -> dict[str, str]:
        listing = "\n".join(f"- {t.get('id')}: {t.get('title')}" for t in tickets[:20])
        build_raw = self._invoke_llm(
            [
                {
                    "role": "system",
                    "content": "Choose local/agentless vs OpenHands vs SWE-agent per ticket. Markdown.",
                },
                {"role": "user", "content": listing or requirement_id},
            ],
            skill=self._bundle_skill("build"),
            agent="build",
            requirement_id=requirement_id,
            max_tokens=800,
        )
        review_raw = self._invoke_llm(
            [
                {
                    "role": "system",
                    "content": 'JSON only: {"findings":["..."]}. Correctness, security, architecture, quality.',
                },
                {"role": "user", "content": listing or requirement_id},
            ],
            skill=self._bundle_skill("review"),
            agent="review",
            requirement_id=requirement_id,
            max_tokens=800,
        )
        adv_raw = self._invoke_llm(
            [
                {
                    "role": "system",
                    "content": 'JSON only: {"defects":["..."]}. Assume the build is wrong and try to prove it.',
                },
                {"role": "user", "content": listing or requirement_id},
            ],
            skill=self._bundle_skill("adversary"),
            agent="adversary",
            requirement_id=requirement_id,
            max_tokens=800,
        )
        extra[f"requirements/{requirement_id}/build/ROUTER.md"] = (
            self._llm_prose(build_raw) or "local/agentless"
        ) + "\n"
        findings = self._llm_json(review_raw).get("findings") or []
        extra[f"requirements/{requirement_id}/build/REVIEW.md"] = (
            "\n".join(f"- {item}" for item in findings) or "- no model findings\n"
        )
        extra[f"requirements/{requirement_id}/build/REVIEW.md"] = extra[
            f"requirements/{requirement_id}/build/REVIEW.md"
        ].rstrip() + "\n"
        defects = self._llm_json(adv_raw).get("defects") or []
        extra[f"requirements/{requirement_id}/build/ADVERSARY.md"] = (
            "\n".join(f"- {item}" for item in defects) or "- no model defects\n"
        )
        extra[f"requirements/{requirement_id}/build/ADVERSARY.md"] = extra[
            f"requirements/{requirement_id}/build/ADVERSARY.md"
        ].rstrip() + "\n"
        return extra

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
        drafted = self._refine_brd(rid, drafted, bundle.content)
        drafted = brd.normalize_screen_inventory(drafted)
        drafted = brd.append_findings(drafted, self._critique_brd(rid, drafted, bundle.content))
        drafted = brd.normalize_screen_inventory(drafted)
        sha = self._commit_brd(
            req,
            drafted,
            {"id": "brd-agent", "name": "BRD Agent", "email": "brd@local"},
            extra=skill_registry.snapshot_paths(rid, "brd", bundle),
        )
        req.record_artefact("brd", sha, **PROVENANCE_BRD)
        body["requirement"] = req.dump()
        self._arm_sla(body, req, 2)

    def _write_design(self, req: R.Requirement, body: dict[str, Any], *, refresh: str = "both") -> None:
        rid = req.id
        brd_text = body.get("brd_text") or self._artefact_text(req, "brd")
        skill_text = self.git.design_system()
        ui_skill = self._bundle_skill("ui", skill_text)

        def on_progress(progress: dict[str, Any]) -> None:
            body["design_progress"] = progress
            self._save(body, event="design_progress", at=self.clock(), extra=dict(progress))

        body["design_progress"] = {
            "status": "running",
            "label": "Preparing accurate screens from the approved BRD…",
            "current": 0,
            "total": 0,
            "pct": 0,
        }
        self._save(body, event="design_progress", at=self.clock(), extra=dict(body["design_progress"]))
        prior_screens = list(body.get("screens") or [])
        if refresh == "architecture":
            ui_model = {
                "pages": [],
                "sources": {
                    str(screen.get("name") or ""): str(screen.get("source") or "")
                    for screen in prior_screens
                    if screen.get("name") and screen.get("source")
                },
            }
            note = ""
        else:
            ui_model = self._ui_model(rid, brd_text, ui_skill, on_progress=on_progress)
            note = "" if refresh == "ui" else self._architect_note(
                rid, brd_text, self._bundle_skill("architect", skill_text)
            )
        built = design_agent.build(
            rid,
            brd_text,
            skill_text,
            root=self.git.root,
            extra_pages=ui_model["pages"],
            architect_note=note,
            screen_sources=ui_model["sources"],
            refresh=refresh,
            prior_architecture="" if refresh != "ui" else (
                body.get("architecture_text") or self._artefact_text(req, "design")
            ),
            prior_decision=body.get("architecture_decision") if refresh == "ui" else None,
        )
        if built.get("clarification"):
            body["design_question"] = built["clarification"]
            body["screens"] = []
            body["stack_profile"] = None
            body["design_progress"] = None
            return
        sha = self.git.commit_files(
            f"design/{rid}",
            built["files"],
            f"design {rid}",
            author="design-agent",
            email="design@local",
            delete=self._orphan_ui_paths(rid, built.get("screens") or []),
        )
        req.record_artefact("design", sha, **PROVENANCE_DESIGN)
        body["requirement"] = req.dump()
        body["stack_profile"] = built["profile"]
        body["architecture_text"] = built.get("architecture") or body.get("architecture_text")
        body["architecture_decision"] = built.get("decision") or body.get("architecture_decision")
        body["preview_host"] = built.get("preview_host") or preview_agent.host()
        body["screens"] = built["screens"]
        body["coverage"] = built["coverage"]
        body["design_report"] = built["report"]
        body["design_question"] = None
        body["preview_url"] = built.get("preview_url") or preview_agent.url(rid)
        body["design_progress"] = None
        self._arm_sla(body, req, 3)

    def _write_screens_for_ui_review(self, req: R.Requirement, body: dict[str, Any]) -> None:
        """Generate screens after BA signs — keep the design artefact SHA stable.

        Architect and BA already signed the architecture commit. Replacing that
        artefact would vacate their signatures; screens therefore update body +
        git files only.
        """
        rid = req.id
        brd_text = body.get("brd_text") or self._artefact_text(req, "brd")
        skill_text = self.git.design_system()
        ui_skill = self._bundle_skill("ui", skill_text)
        ui_model = self._ui_model(rid, brd_text, ui_skill)
        built = design_agent.build(
            rid,
            brd_text,
            skill_text,
            root=self.git.root,
            extra_pages=ui_model["pages"],
            architect_note="",
            screen_sources=ui_model["sources"],
            refresh="ui",
            prior_architecture=body.get("architecture_text") or self._artefact_text(req, "design"),
            prior_decision=body.get("architecture_decision"),
        )
        if built.get("clarification"):
            body["design_question"] = built["clarification"]
            return
        self.git.commit_files(
            f"design/{rid}",
            built["files"],
            f"screens {rid}",
            author="design-agent",
            email="design@local",
            delete=self._orphan_ui_paths(rid, built["screens"]),
        )
        body["screens"] = built["screens"]
        body["coverage"] = built["coverage"]
        body["preview_host"] = built.get("preview_host") or preview_agent.host()
        body["preview_url"] = built.get("preview_url") or preview_agent.url(rid)
        body["design_question"] = None
        body["design_progress"] = None
        if built.get("report"):
            body["design_report"] = built["report"]
        # Never call record_artefact here — that would mint a new design SHA and
        # vacate Architect/BA Gate 3 signatures (UX would see the card with no Approve).
        body["phase"] = self._phase(req)
        body["awaiting"] = req.awaiting

    def _orphan_ui_paths(self, requirement_id: str, screens: list[dict[str, Any]]) -> list[str]:
        """JSX files left from older duplicate screen mints (LoginScreen2, …)."""
        keep = {str(s.get("name") or "") for s in screens if s.get("name")}
        ui_dir = self.git.root / f"requirements/{requirement_id}/design/ui"
        if not ui_dir.is_dir():
            return []
        orphans: list[str] = []
        for path in ui_dir.glob("*.jsx"):
            if path.stem not in keep:
                orphans.append(f"requirements/{requirement_id}/design/ui/{path.name}")
        return orphans

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
        brd_text = body.get("brd_text") or self._artefact_text(req, "brd")
        screens = list(body.get("screens") or [])
        stack_profile = body.get("stack_profile") or {"id": "node"}
        planned = self._plan_from_models(
            rid,
            brd_text,
            screens,
            stack_profile,
            architecture_text=body.get("architecture_text") or self._artefact_text(req, "design"),
        )
        built = plan_agent.build(
            rid,
            template=req.template,
            brd_text=brd_text,
            architecture_text=body.get("architecture_text") or self._artefact_text(req, "design"),
            screens=screens,
            stack_profile=stack_profile,
            reviewers=reviewers,
            root=self.git.root,
            tickets=planned["tickets"],
            tests=planned["tests"],
            overview_md=planned["overview_md"],
            devops_note=planned["devops_note"],
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
            rid,
            list(body.get("tickets") or []),
            tests=list(body.get("tests") or []),
            screens=list(body.get("screens") or []),
            brd_text=body.get("brd_text") or self._artefact_text(req, "brd"),
            profile=body.get("stack_profile") or {"id": "node"},
        )
        files: dict[str, str] = dict(built.get("extra") or {})
        self._build_from_models(rid, list(body.get("tickets") or []), files)
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
            "pipeline": built.get("pipeline") or {},
            "engine": built["engine"],
            "engines": built.get("engines") or [],
            "ok": built.get("ok", True),
            "blocking": built.get("blocking") or [],
            "branches": list(built["branches"]),
            "context": built.get("context") or {},
            "findings": built.get("findings") or {},
        }
        # Demo IDE status page: stream jobs start here so TL sees progress
        # right after Gate 4 clears — Gate 5 then merges the scanned build.
        try:
            from phase1 import codegen as codegen_jobs

            body["codegen"] = codegen_jobs.start_after_gate4(rid, body)
        except Exception as exc:
            body["codegen"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
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
        watch_raw = self._invoke_llm(
            [
                {
                    "role": "system",
                    "content": "Report errors, latency, spend, and whether a change request is needed. Short markdown.",
                },
                {
                    "role": "user",
                    "content": f"{rid} build={body.get('build')} uat={body.get('uat')}",
                },
            ],
            skill=self._bundle_skill("monitor"),
            agent="monitor",
            requirement_id=rid,
            max_tokens=600,
        )
        packed = pack_release(rid, monitor_note=self._llm_prose(watch_raw))
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
        return preview_agent.document(
            requirement_id,
            list(body.get("screens") or []),
            brd_text=str(body.get("brd_text") or ""),
        )

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
        source = ui_agent.gated_source(name, source, self.git.design_system())
        screens = list(body.get("screens") or [])
        found = False
        for screen in screens:
            if screen.get("name") == name:
                screen["source"] = source
                found = True
        if not found:
            raise KeyError(name)
        files = {f"requirements/{requirement_id}/design/ui/{name}.jsx": source}
        # Keep the design artefact SHA stable — Gate 3 Arch/BA signatures bind to it.
        self.git.commit_files(
            f"design/{requirement_id}",
            files,
            f"edit screen {name}",
            author=str(editor.get("name") or editor.get("id")),
            email=str(editor.get("email") or f"{editor.get('id')}@entra.local"),
        )
        body["screens"] = screens
        body["phase"] = self._phase(req)
        body["awaiting"] = req.awaiting
        self._save(body, event="screen_edited", at=self.clock(), extra={"name": name})
        return self.get(requirement_id)

    def apply_screen_instruction(
        self,
        requirement_id: str,
        editor: dict[str, Any],
        name: str,
        instruction: str,
        source: str = "",
        theme: str = "",
    ) -> dict[str, Any]:
        body = self._load(requirement_id)
        req = R.Requirement.load(body["requirement"])
        roles = set(editor.get("roles") or [])
        if not roles & {"architect", "ui_ux"}:
            raise PermissionError("screen edits require the architect or UI/UX role")
        screens = list(body.get("screens") or [])
        roster = [str(s.get("name")) for s in screens if s.get("name")]
        current = next((s for s in screens if s.get("name") == name), None)
        if current is None:
            raise KeyError(name)
        skill_text = self.git.design_system()
        current_source = str(source or current.get("source") or "").strip()
        if not instruction.strip():
            raise ValueError("instruction is required")
        if not current_source:
            raise ValueError("screen has no source")
        rewritten = self._rewrite_screen(
            requirement_id,
            name,
            current_source,
            instruction.strip(),
            skill_text,
            roster=roster,
            theme=theme if theme in jsx_gate.ALLOWED_THEMES else "",
        )
        return {
            "name": name,
            "source": rewritten,
            "summary": "Rewrote the screen from your instruction — review it, then Save",
        }

    def _rewrite_screen(
        self,
        requirement_id: str,
        name: str,
        current_source: str,
        instruction: str,
        skill_text: str,
        *,
        roster: list[str] | None = None,
        theme: str = "",
    ) -> str:
        packed = self._skill_call("ui", skill_text)
        roster = roster or [name]
        theme_rule = (
            f'Current theme is data-theme="{theme}". Keep it exactly unless the '
            "instruction asks to reimagine, redesign, restyle or change the theme. "
            if theme
            else f"Pick a named theme ({jsx_gate.THEME_HINT}). "
        )
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are a world-class product designer rewriting one React screen. "
                    "JSX only. No markdown fence. "
                    + _HOST_HELPERS_RULE
                    + f"Return function {name} plus any small sub-components it calls. "
                    f"{name} returns ( <Page data-theme=\"…\"><Sidebar>…</Sidebar><div>…</div></Page> ). "
                    "One h1. Balanced braces. Prefer named themes; hex only as "
                    "--color-* overrides on Page style. "
                    f"Use {jsx_gate.TOKEN_HINT}. Themes: {jsx_gate.THEME_HINT}. "
                    + theme_rule
                    + "Design like Canva-grade marketing UI: clear hierarchy, generous "
                    "whitespace, one focal Hero or Image inset, Card surfaces, Badge chips, "
                    "Button variants (solid|ghost|outline|soft), and subtle motion "
                    "(.motion-rise|.motion-fade|.motion-slide or Framer Motion). "
                    "Pink/white → data-theme=\"blush\". Light pastel → blush|sunrise|paper. "
                    "Dark luxury → noir|plum|midnight. "
                    "Keep buttons legible (accent fill + bg text, or ghost/outline). "
                    "Screens are one connected app: the Sidebar has one nav Button per "
                    "roster screen, <Button onClick={() => navigate(\"ScreenName\")}>Short "
                    "label</Button> (navigate is a host global), current screen marked with "
                    "fontWeight 700 + underline (never accent-coloured text on the accent "
                    "Button — that hides the label). In-page actions that open another screen call "
                    "navigate. Only roster screens in the sidebar. "
                    "If the instruction says reimagine, redesign, restyle, change the scene, "
                    "or change the theme: pick a DIFFERENT data-theme than the current source, "
                    "change the layout (hero, split, cards, photo inset — not the same stacked form), "
                    "and rewrite the copy. A lookalike of the current screen is a failure. "
                    "React JSX only, no Figma. "
                    + jsx_gate.PLACEHOLDER_RULE
                    + f" The screen function MUST be named {name}."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Instruction: {instruction}\n\nRoster: {', '.join(roster)}\n\n"
                    f"Current source:\n{current_source[:12000]}"
                ),
            },
        ]
        last_error = "UI model returned nothing"
        last_raw = ""
        for _ in range(3):
            raw = self._invoke_llm(
                messages,
                skill=packed["skill"],
                agent="ui",
                requirement_id=requirement_id,
                max_tokens=4000,
                tools=packed.get("tools"),
                execute_tool=packed.get("execute_tool"),
            )
            last_raw = raw or last_raw
            extracted = _jsx_from_model(name, raw)
            if not extracted.strip() or not re.search(r"<[A-Za-z]", extracted):
                last_error = (
                    "UI model returned no JSX, screen unchanged. "
                    "Check MODEL_ADAPTER=litellm and Settings."
                )
                break
            candidate = ui_agent._adopt_model_source(name, extracted)
            try:
                return ui_agent.apply_model_rewrite(
                    name,
                    candidate,
                    skill_text,
                    roster=roster,
                    theme=theme,
                    instruction=instruction,
                )
            except jsx_gate.CompileFailed as exc:
                last_error = exc.reason
                if "skill file" in exc.reason:
                    break
                cut_off = any(
                    word in exc.reason.lower() for word in ("unbalanced", "unterminated", "unexpected end")
                )
                fix = (
                    f"Your answer was cut off. Return a shorter function {name} only, "
                    "under 120 lines, no helper components, no style tags."
                    if cut_off
                    else f"Fix this. Parser: {exc.reason}. JSX only for function {name}. "
                    "No hex. Design-system tokens only."
                )
                messages = messages[:2] + [
                    {"role": "assistant", "content": (raw or "")[:1500]},
                    {"role": "user", "content": fix},
                ]
        raise jsx_gate.CompileFailed(last_error)

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


_HOST_HELPERS_RULE = (
    "The host already defines Page (accepts data-theme), Sidebar, Button (accepts "
    "onClick), Field and Table, plus every theme's CSS variables. Do not define "
    "them, do not write <style> tags, CSS strings or theme maps; style with inline "
    "style={{...}} using var(--color-*) tokens. Keep it under 120 lines: long answers "
    "get cut off and replaced by a role-specific template. "
    "Loaded libraries (globals, or import them normally): LucideReact icons, "
    "Recharts charts, Motion (framer-motion: motion, AnimatePresence), dayjs. "
    "No other packages. "
)


def _revise_side(actor: dict[str, Any], reason: str, explicit: str = "") -> str:
    if explicit in {"ui", "architecture", "both"}:
        return explicit
    blob = (reason or "").lower()
    if any(token in blob for token in ("screen", "ui", "preview", "usable")):
        return "ui"
    if any(token in blob for token in ("architect", "adr", "stack", "buildable", "module")):
        return "architecture"
    roles = set(actor.get("roles") or [])
    if "ui_ux" in roles and "architect" not in roles:
        return "ui"
    if "architect" in roles and "ui_ux" not in roles:
        return "architecture"
    return "both"


def gate_roles(gate: int) -> list[str]:
    import gate_engine

    return sorted(gate_engine.GATES[gate].approver_roles)


def json_dumps(data: Any) -> str:
    import json

    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _jsx_from_model(name: str, raw: str) -> str:
    """Pull a screen function out of a model reply.

    The reply is often JSX, sometimes a JSON object whose source string is not
    valid JSON because of quotes inside the markup.
    """
    import re

    text = (raw or "").strip()
    fenced = re.search(r"```(?:jsx|javascript|js)?\s*([\s\S]*?)```", text, re.I)
    if fenced:
        text = fenced.group(1).strip()
    hits = []
    fn = text.find("function ")
    if fn >= 0:
        hits.append(fn)
    const_hit = re.search(r"(?:const|let|var|export\s+default\s+function)\s+", text)
    if const_hit:
        hits.append(const_hit.start())
    if not hits:
        return ""
    body = text[min(hits) :]
    if (
        f"function {name}" not in body
        and "function Page" not in body
        and name not in body
    ):
        return ""
    return body


def json_question(text: str) -> str:
    import json

    return json.dumps({"type": "question", "text": text})
