"""Compatibility shapes for the canonical static frontend.

The Phase 1 service remains the source of truth.  This module only translates
its runs and identity model into the legacy frontend's wire format.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

from phase1 import brd, directory, rails
import gate_engine


SESSION_COOKIE = "phase1_session"
DEV_PASSWORD = "password123"

_ROLE_WAITING = {
    "architect": "Architect",
    "ui_ux": "UI/UX",
    "business_analyst": "Business Analyst",
    "business_owner": "Business Owner",
    "client_tech_lead": "Client Tech Lead",
    "tech_lead": "Tech Lead",
    "stream_lead": "Stream Lead",
    "product_owner": "Product Owner",
    "senior_engineer": "Senior Engineer",
    "release_manager": "Release Manager",
    "business_stakeholder": "Requester",
}
_TIER_BY_ROLE = {
    "product_owner": "po",
    "business_owner": "bo",
    "client_tech_lead": "ctl",
    "business_analyst": "ba",
    "architect": "architect",
    "ui_ux": "ux",
    "tech_lead": "tech",
    "stream_lead": "sl",
    "senior_engineer": "se",
    "release_manager": "rm",
    "business_stakeholder": "stakeholder",
}
_TIER_BY_ID = {
    "u-requester": "requester",
    "u-po": "po",
    "u-bo": "bo",
    "u-ctl": "ctl",
    "u-ba": "ba",
    "u-arch": "architect",
    "u-ux": "ux",
    "u-tl": "tech",
    "u-sl-dev": "sl",
    "u-sl-ai": "sl",
    "u-sl-qa": "sl",
    "u-se": "se",
    "u-rm": "rm",
}

_GATE_CHAIN = [
    {"gate": 1, "approverTiers": ["po"], "mode": "single"},
    {"gate": 2, "approverTiers": ["bo", "ctl"], "mode": "all"},
    {"gate": 3, "approverTiers": ["architect", "ux", "ba"], "mode": "all"},
    {"gate": 4, "approverTiers": ["tech", "sl"], "mode": "all"},
    {"gate": 5, "approverTiers": ["se"], "mode": "single"},
    {"gate": 6, "approverTiers": ["requester", "stakeholder"], "mode": "single"},
    {"gate": 7, "approverTiers": ["rm"], "mode": "single"},
]


def _account_tier(actor_id: str, actor: dict[str, Any]) -> str:
    if actor_id in _TIER_BY_ID:
        return _TIER_BY_ID[actor_id]
    for role in actor.get("roles") or []:
        if role in _TIER_BY_ROLE:
            return _TIER_BY_ROLE[role]
    return "requester"


_DEV_ACCOUNTS = {
    actor["email"].lower(): (actor_id, _account_tier(actor_id, actor))
    for actor_id, actor in directory.DIRECTORY.items()
}


class CompatibilityAPI:
    """Session and shape adapter used by ``phase1.server``."""

    def __init__(self, platform: Any, *, dev_mode: bool = False) -> None:
        self.platform = platform
        self.dev_mode = dev_mode
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._clients: dict[str, dict[str, Any]] = {}
        root = os.getenv("RUNTIME_ROOT")
        self._session_file = Path(root) / "dev-sessions.json" if dev_mode and root else None
        self._load_sessions()

    def _load_sessions(self) -> None:
        if self._session_file is None or not self._session_file.exists():
            return
        try:
            saved = json.loads(self._session_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._sessions.update(saved.get("sessions") or {})
        self._clients.update(saved.get("clients") or {})

    def _save_sessions(self) -> None:
        if self._session_file is None:
            return
        with self._lock:
            payload = json.dumps({"sessions": self._sessions, "clients": self._clients})
        try:
            self._session_file.write_text(payload, encoding="utf-8")
        except OSError:
            pass

    def login(self, email: str, password: str, *, client: bool = False) -> tuple[str, dict]:
        if not self.dev_mode:
            raise PermissionError("password login is available only in explicit dev mode")
        normalized = email.strip().lower()
        if client:
            actor = self._clients.get(normalized)
            if actor is None or actor.get("_password") != password:
                raise PermissionError("invalid email or password")
        else:
            account = _DEV_ACCOUNTS.get(normalized)
            if account is None or password != DEV_PASSWORD:
                raise PermissionError("invalid email or password")
            actor_id, tier = account
            actor = self._with_frontend_identity(directory.actor(actor_id), tier=tier)
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = dict(actor)
        self._save_sessions()
        return token, self.user_payload(actor)

    def register_client(self, name: str, email: str, password: str) -> tuple[str, dict]:
        if not self.dev_mode:
            raise PermissionError("client registration is available only in explicit dev mode")
        normalized = email.strip().lower()
        if not name.strip() or not normalized or len(password) < 8:
            raise ValueError("name, email, and a password of at least 8 characters are required")
        with self._lock:
            if normalized in self._clients or normalized in _DEV_ACCOUNTS:
                raise ValueError("an account with that email already exists")
            actor = {
                "id": f"client-{secrets.token_hex(8)}",
                "name": name.strip(),
                "email": normalized,
                "roles": ["business_analyst"],
                "team": "client",
                "tierId": "requester",
                "department": "",
                "isClient": True,
                "_password": password,
            }
            self._clients[normalized] = actor
        return self.login(normalized, password, client=True)

    def authenticate(self, headers: Any) -> dict[str, Any]:
        authorization = headers.get("Authorization") or ""
        if authorization.startswith("Bearer "):
            token = authorization[7:].strip()
            if not token:
                raise PermissionError("empty bearer token")
            identity = getattr(getattr(self.platform, "adapters", None), "identity", None)
            identity = identity or getattr(self.platform, "identity", None)
            if identity is None:
                raise PermissionError("identity adapter is unavailable")
            try:
                return self._with_frontend_identity(identity.validate_token(token))
            except Exception as exc:
                raise PermissionError("invalid bearer token") from exc
        if self.dev_mode:
            token = self._cookie(headers.get("Cookie") or "", SESSION_COOKIE)
            with self._lock:
                actor = self._sessions.get(token or "")
            if actor:
                return dict(actor)
        raise PermissionError("Authorization Bearer token required")

    def logout(self, headers: Any) -> None:
        token = self._cookie(headers.get("Cookie") or "", SESSION_COOKIE)
        if token:
            with self._lock:
                self._sessions.pop(token, None)
            self._save_sessions()

    def user_payload(self, actor: dict[str, Any]) -> dict[str, Any]:
        return {
            "user": {
                "id": actor["id"],
                "name": actor.get("name") or actor["id"],
                "email": actor.get("email") or "",
                "tierId": actor.get("tierId") or self._tier(actor),
                "department": actor.get("department") or actor.get("team") or "",
                "isClient": bool(actor.get("isClient")),
            }
        }

    def list_artifacts(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        # Visibility remains deliberately broad in Phase 1: the frontend
        # derives its inbox and submissions from this one list.
        return [self.artifact(row, viewer=actor) for row in self.platform.list_runs()]

    def artifact(
        self, row: dict[str, Any], *, viewer: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        requirement = row.get("requirement") or {}
        originator = requirement.get("originator") or row.get("originator") or {}
        scope = brd.parse_scope(row.get("scope_text") or "")
        phase = row.get("phase") or ""
        awaiting = row.get("awaiting")
        stage = {
            "intake": "clarifying",
            "awaiting_gate_1": "pending_approval",
            "brd": "pending_approval",
            "awaiting_gate_2": "pending_approval",
            "designing": "pending_approval",
            "awaiting_gate_3": "pending_approval",
            "planning": "pending_approval",
            "awaiting_gate_4": "pending_approval",
            "building": "pending_approval",
            "awaiting_gate_5": "pending_approval",
            "uat": "pending_approval",
            "awaiting_gate_6": "pending_approval",
            "releasing": "pending_approval",
            "awaiting_gate_7": "pending_approval",
            "complete": "approved",
            "phase_4": "pending_approval",
            "discarded": "rejected",
        }.get(phase, phase or "clarifying")
        if awaiting in (1, 2, 3, 4, 5, 6, 7):
            current_index = int(awaiting) - 1
        elif phase == "complete":
            current_index = 7
        else:
            current_index = 0
        request_text = str(row.get("request_text") or "").strip()
        if "guided requirement intake" in request_text.lower():
            request_text = ""
        title = rails.card_title(
            str(row.get("display_title") or ""),
            list(scope.get("in_scope") or []),
            str(scope.get("success") or ""),
        )
        today = str(scope.get("current_state") or "").strip()
        if not today:
            for item in scope.get("assumptions") or []:
                if str(item).lower().startswith("today"):
                    today = str(item).split(":", 1)[-1].strip() if ":" in str(item) else str(item)
                    break
        users = str(scope.get("users") or "").strip()
        if not users:
            for item in scope.get("in_scope") or []:
                if str(item).lower().startswith("used by"):
                    users = str(item)
                    break
        success = str(scope.get("success") or "").strip()
        summary_parts = []
        if title and title.lower() not in success.lower():
            summary_parts.append(title.rstrip(".") + ".")
        if success:
            summary_parts.append(success)
        content = {
            "summary": " ".join(summary_parts).strip() or request_text,
            "users": users,
            "currentState": today,
            "inScope": scope.get("in_scope") or [],
            "outOfScope": scope.get("out_of_scope") or [],
            "functionalRequirements": [],
            "nonFunctionalRequirements": scope.get("non_functional") or [],
            "openQuestions": scope.get("open_questions") or [],
            "assumptions": scope.get("assumptions") or [],
        }
        history = []
        for item in requirement.get("history") or []:
            if item.get("event") != "decision":
                continue
            history.append(
                {
                    "action": {
                        "approve": "approve",
                        "revise": "requestRevision",
                        "discard": "reject",
                    }.get(item.get("outcome"), item.get("outcome")),
                    "actorId": item.get("actor"),
                    "comment": item.get("reason") or "",
                }
            )
        timestamp = self._timestamp(row)
        brd_text = row.get("brd_text") or ""
        architecture = row.get("architecture_text") or ""
        screens = list(row.get("screens") or [])
        viewer_roles = set((viewer or {}).get("roles") or [])
        gate_progress = self._gate_progress(row, viewer)
        signed = gate_progress["viewerHasSigned"]
        gate_2_roles = {"business_owner", "client_tech_lead"}
        gate_3_roles = {"architect", "ui_ux", "business_analyst"}
        gate_4_roles = {"tech_lead", "stream_lead"}
        can_gate_2 = awaiting == 2 and bool(viewer_roles & gate_2_roles) and not signed
        can_gate_3 = awaiting == 3 and bool(viewer_roles & gate_3_roles) and not signed
        can_gate_4 = awaiting == 4 and bool(viewer_roles & gate_4_roles) and not signed
        can_gate_5 = awaiting == 5 and bool(viewer_roles & {"senior_engineer"})
        can_gate_6 = awaiting == 6 and (
            bool(viewer_roles & {"business_stakeholder"})
            or (viewer or {}).get("tierId") == "requester"
            or (originator.get("id") and originator.get("id") == (viewer or {}).get("id"))
        )
        can_gate_7 = awaiting == 7 and bool(viewer_roles & {"release_manager"})
        governed = 1
        if awaiting == 3 or phase in {"designing", "awaiting_gate_3"}:
            governed = 2
        if awaiting == 4 or phase in {"planning", "awaiting_gate_4"}:
            governed = 3
        if awaiting == 5 or phase in {"building", "awaiting_gate_5", "phase_4"}:
            governed = 4
        if awaiting == 6 or phase in {"uat", "awaiting_gate_6"}:
            governed = 5
        if awaiting == 7 or phase in {"releasing", "awaiting_gate_7"}:
            governed = 6
        if phase == "complete":
            governed = 7
        report = row.get("design_report")
        if row.get("plan_text"):
            report = dict(report or {})
            report["plan"] = [
                {"phase": "Sprint 0", "description": str((row.get("sprint0") or {}).get("status") or "")},
                {"phase": "Tickets", "description": f"{len(row.get('tickets') or [])} work items"},
                {"phase": "Tests", "description": f"{len(row.get('tests') or [])} cases before code"},
            ]
        if not report and architecture:
            report = {"objective": architecture, "architecture": architecture}
        elif not report and brd_text:
            report = None
        return {
            "_id": requirement.get("id"),
            "title": title,
            "content": content,
            "currentStage": stage,
            "currentApprovalIndex": current_index,
            "approvalChain": [dict(step) for step in _GATE_CHAIN],
            "originator": {
                "userId": originator.get("id"),
                "name": originator.get("name") or originator.get("id"),
                "tierId": originator.get("tierId") or self._tier(originator),
            },
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "history": history,
            "chatHistory": list(row.get("messages") or []),
            "detailedReport": report,
            "brdText": brd_text,
            "architectureText": architecture,
            "phase1": True,
            "phase2": governed >= 2,
            "governedPhase": governed,
            "teamReports": list(row.get("team_reports") or []),
            "discussionMessages": [],
            "ui": {
                "screens": screens,
                "previewUrl": f"/preview/{requirement.get('id')}",
            }
            if screens
            else None,
            "uiGate": {
                "blocksSplit": awaiting == 3,
                "canEdit": can_gate_2 or (can_gate_3 and bool(viewer_roles & {"architect", "ui_ux"})),
                "canApprove": can_gate_3 or can_gate_4 or can_gate_5 or can_gate_6 or can_gate_7,
            },
            "viewerHasSigned": signed,
            "outstandingLabels": gate_progress["outstandingLabels"],
            "teamReportsGeneratedAt": (row.get("tickets") or None) and row.get("sla_due"),
            "stackProfile": row.get("stack_profile"),
            "coverage": row.get("coverage") or {},
            "tickets": list(row.get("tickets") or []),
            "planText": row.get("plan_text") or "",
            "awaitingGate": awaiting,
            "designProgress": row.get("design_progress"),
            "build": row.get("build") or {},
            "uat": row.get("uat") or {},
            "release": row.get("release") or {},
        }

    def _gate_progress(self, row: dict[str, Any], viewer: dict[str, Any] | None) -> dict[str, Any]:
        awaiting = row.get("awaiting")
        live = []
        for signature in (row.get("requirement") or {}).get("signatures") or []:
            if signature.get("gate") != awaiting:
                continue
            if signature.get("stale"):
                continue
            live.append(signature)
        viewer_id = str((viewer or {}).get("id") or "")
        signed_ids = {
            str(item.get("actor_id") or item.get("actorId") or "")
            for item in live
        }
        signed_roles = {str(item.get("role") or "") for item in live}
        outstanding: list[str] = []
        try:
            gate_no = int(awaiting or 0)
        except (TypeError, ValueError):
            gate_no = 0
        definition = gate_engine.GATES.get(gate_no)
        if definition and definition.all_must_sign:
            outstanding = [
                _ROLE_WAITING.get(role, role)
                for role in sorted(definition.approver_roles - signed_roles)
            ]
        return {
            "viewerHasSigned": bool(viewer_id and viewer_id in signed_ids),
            "outstandingLabels": outstanding,
        }

    def ensure_owner(self, row: dict[str, Any], actor: dict[str, Any]) -> None:
        originator = (row.get("requirement") or {}).get("originator") or {}
        if originator.get("id") != actor.get("id"):
            raise PermissionError("only the requirement originator can continue intake")

    @staticmethod
    def ensure_can_start_intake(actor: dict[str, Any]) -> None:
        if actor.get("isClient"):
            return
        if actor.get("tierId") in {"requester", "stakeholder", "pm"}:
            return
        if "business_stakeholder" in set(actor.get("roles") or []):
            return
        raise PermissionError("only the requester can start a requirement")

    @staticmethod
    def ensure_brd_editor(row: dict[str, Any], actor: dict[str, Any]) -> None:
        if row.get("awaiting") != 2:
            raise PermissionError("the BRD can be edited only while Gate 2 is open")
        roles = set(actor.get("roles") or [])
        if not roles & {"business_owner", "client_tech_lead"}:
            raise PermissionError("the BRD editor requires a Gate 2 approver role")

    @staticmethod
    def session_cookie(token: str) -> str:
        return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax"

    @staticmethod
    def clear_cookie() -> str:
        return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"

    @staticmethod
    def _cookie(header: str, name: str) -> str | None:
        cookie = SimpleCookie()
        cookie.load(header)
        morsel = cookie.get(name)
        return morsel.value if morsel else None

    @staticmethod
    def _tier(actor: dict[str, Any]) -> str:
        actor_id = str(actor.get("id") or "")
        if actor_id in _TIER_BY_ID:
            return _TIER_BY_ID[actor_id]
        for role in actor.get("roles") or []:
            if role in _TIER_BY_ROLE:
                return _TIER_BY_ROLE[role]
        return "requester"

    def _with_frontend_identity(
        self, actor: dict[str, Any], *, tier: str | None = None
    ) -> dict[str, Any]:
        result = dict(actor)
        result["tierId"] = tier or self._tier(result)
        result["department"] = result.get("team") or ""
        result["isClient"] = bool(result.get("isClient"))
        return result

    @staticmethod
    def _timestamp(row: dict[str, Any]) -> str:
        attestations = (row.get("requirement") or {}).get("attestations") or []
        if attestations:
            predicate = attestations[-1].get("predicate") or {}
            if predicate.get("timestamp"):
                return str(predicate["timestamp"])
        due = row.get("sla_due")
        if due:
            return str(due)
        return datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat()
