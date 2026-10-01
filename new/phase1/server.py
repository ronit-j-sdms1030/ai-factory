"""HTTP workspace for Phase 1 — intake form, conversation, BRD editor, inbox.

Backstage is the production shell. This is the same three surfaces so the
runtime can be exercised without standing up the portal.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from phase1 import directory, webhook
from phase1 import codegen as codegen_jobs
from phase1.compat_api import CompatibilityAPI
from phase1.service import service_from_env
from phase4 import runtime_db

STATIC = Path(__file__).resolve().parent / "workspace"


class AuthenticationError(Exception):
    pass


class EventHub:
    """Process-local run notifications; durable state remains in the service."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._versions: dict[str, int] = {}
        self._payloads: dict[str, dict[str, Any]] = {}

    def publish(self, run: dict[str, Any]) -> None:
        rid = str(run["id"])
        with self._condition:
            self._versions[rid] = self._versions.get(rid, 0) + 1
            self._payloads[rid] = run
            self._condition.notify_all()

    def version(self, requirement_id: str) -> int:
        with self._condition:
            return self._versions.get(requirement_id, 0)

    def wait(
        self, requirement_id: str, version: int, timeout: float
    ) -> tuple[int, dict[str, Any] | None]:
        with self._condition:
            self._condition.wait_for(
                lambda: self._versions.get(requirement_id, 0) > version,
                timeout=timeout,
            )
            current = self._versions.get(requirement_id, 0)
            payload = self._payloads.get(requirement_id) if current > version else None
            return current, payload


def make_handler(platform, *, dev_mode: bool = False, event_hub: EventHub | None = None):
    hub = event_hub or EventHub()
    compatibility = CompatibilityAPI(platform, dev_mode=dev_mode)
    configured_origins = {
        value.strip()
        for value in os.getenv(
            "PHASE1_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if value.strip()
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # quieter tests
            pass

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/healthz":
                return self._json(200, {"status": "ok"})
            if path == "/api/tooling":
                from phase1 import tooling

                return self._json(200, tooling.report())
            if path in {"/", "/index.html"}:
                return self._file(STATIC / "index.html", "text/html")
            if path.endswith(".css"):
                return self._file(STATIC / path.lstrip("/"), "text/css")
            if path.endswith(".js"):
                return self._file(STATIC / path.lstrip("/"), "application/javascript")
            if path.startswith("/api/settings/"):
                try:
                    actor = compatibility.authenticate(self.headers)
                    if actor.get("isClient"):
                        return self._json(403, {"error": "Not available"})
                    from phase1 import settings_pages

                    edit = settings_pages.may_edit(actor)
                    if path == "/api/settings/models":
                        body = settings_pages.models(platform.root)
                        body["canEdit"] = edit
                        return self._json(200, body)
                    if path == "/api/settings/design-system":
                        body = settings_pages.design_system_doc(platform.git)
                        body["canEdit"] = edit
                        return self._json(200, body)
                    if path == "/api/settings/intake-skill":
                        body = settings_pages.intake_skill_doc(platform.git)
                        body["canEdit"] = edit
                        return self._json(200, body)
                    if path == "/api/settings/prompts":
                        return self._json(
                            200,
                            {
                                "prompts": settings_pages.prompts(platform.root),
                                "canEdit": edit,
                            },
                        )
                    if path == "/api/settings/lessons":
                        return self._json(200, {"lessons": [], "canEdit": edit})
                    return self._json(404, {"error": "not found"})
                except AuthenticationError as exc:
                    return self._json(401, {"error": str(exc)})
                except PermissionError as exc:
                    return self._json(401, {"error": str(exc)})
                except Exception:
                    from phase1 import settings_pages

                    if path == "/api/settings/intake-skill":
                        body = settings_pages.intake_skill_doc(None)
                        body["canEdit"] = False
                        return self._json(200, body)
                    if path == "/api/settings/design-system":
                        body = settings_pages.design_system_doc(None)
                        body["canEdit"] = False
                        return self._json(200, body)
                    if path == "/api/settings/prompts":
                        return self._json(
                            200,
                            {"prompts": settings_pages.prompts(None), "canEdit": False},
                        )
                    if path == "/api/settings/lessons":
                        return self._json(200, {"lessons": [], "canEdit": False})
                    body = settings_pages.models(Path("."))
                    body["canEdit"] = False
                    return self._json(200, body)
            try:
                if path == "/api/auth/me":
                    actor = compatibility.authenticate(self.headers)
                    return self._json(200, compatibility.user_payload(actor))
                if path == "/api/codegen/models":
                    compatibility.authenticate(self.headers)
                    return self._json(200, codegen_jobs.models())
                if path.startswith("/api/codegen/by-artifact/"):
                    compatibility.authenticate(self.headers)
                    artifact_id = path.rsplit("/", 1)[-1]
                    row = {}
                    try:
                        row = platform.store.get(artifact_id) or {}
                    except Exception:
                        row = {}
                    return self._json(200, codegen_jobs.by_artifact(artifact_id, row=row))
                if path.startswith("/api/codegen/") and path.endswith("/status"):
                    compatibility.authenticate(self.headers)
                    code_gen_id = path.split("/")[3]
                    payload = codegen_jobs.status(code_gen_id)
                    if payload is None:
                        return self._json(404, {"error": "code generation job not found"})
                    return self._json(200, payload)
                if path.startswith("/api/codegen/") and path.rstrip("/").endswith("/file"):
                    compatibility.authenticate(self.headers)
                    code_gen_id = path.split("/")[3]
                    query = parse_qs(parsed.query)
                    file_path = (query.get("path") or [""])[0]
                    payload = codegen_jobs.get_file(code_gen_id, file_path)
                    if payload is None:
                        return self._json(404, {"error": "file not found"})
                    return self._json(200, payload)
                if path == "/api/usage":
                    actor = compatibility.authenticate(self.headers)
                    if actor.get("isClient"):
                        return self._json(403, {"error": "Not available"})
                    from phase1 import usage_ledger

                    try:
                        board = usage_ledger.dashboard(
                            platform.root, store=getattr(platform, "store", None)
                        )
                    except Exception as exc:
                        board = {
                            "recorded": {
                                "calls": 0,
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "total_tokens": 0,
                                "usd": 0.0,
                                "currency": "USD",
                            },
                            "byModel": [],
                            "byAgent": [],
                            "byRequirement": [],
                            "recent": [],
                            "history": [],
                            "cycleEstimate": {
                                "usd": 0,
                                "agents": [],
                                "assumption": "usage log failed: " + str(exc),
                            },
                            "governance": {},
                        }
                    return self._json(200, board)
                if path == "/api/artifacts":
                    actor = compatibility.authenticate(self.headers)
                    return self._json(
                        200, {"artifacts": compatibility.list_artifacts(actor)}
                    )
                parts = path.strip("/").split("/")
                if len(parts) == 3 and parts[0] == "api" and parts[1] == "artifacts":
                    actor = compatibility.authenticate(self.headers)
                    run = platform.get(parts[2])
                    return self._json(
                        200, {"artifact": compatibility.artifact(run, viewer=actor)}
                    )
                if (
                    path.startswith("/api/artifacts/")
                    and path.endswith("/merged-code")
                ):
                    compatibility.authenticate(self.headers)
                    rid = path.split("/")[3]
                    run = platform.get(rid)
                    git = getattr(platform, "git", None)
                    if git is None and hasattr(platform, "_direct"):
                        git = platform._direct().git
                    payload = codegen_jobs.ensure_merged_preview(
                        rid, git=git, row=run
                    )
                    return self._json(200, payload)
                if path.startswith("/api/artifacts/") and path.endswith("/brd"):
                    compatibility.authenticate(self.headers)
                    rid = path.split("/")[3]
                    run = platform.get(rid)
                    return self._json(
                        200,
                        {
                            "artifact": compatibility.artifact(run),
                            "content": run.get("brd_text") or "",
                        },
                    )
                if path.startswith("/preview/") and "/api/" in path:
                    return self._preview_api("GET", path)
                if path.startswith("/preview/"):
                    rid = path[len("/preview/") :].strip("/")
                    html = platform.preview_document(rid).encode("utf-8")
                    return self._bytes(200, html, "text/html; charset=utf-8")
                if path.startswith("/api/artifacts/") and path.endswith("/ui/preview"):
                    compatibility.authenticate(self.headers)
                    rid = path.split("/")[3]
                    html = platform.preview_document(rid).encode("utf-8")
                    return self._bytes(200, html, "text/html; charset=utf-8")
                if path.startswith("/api/artifacts/") and path.endswith("/ui/screens"):
                    compatibility.authenticate(self.headers)
                    rid = path.split("/")[3]
                    run = platform.get(rid)
                    from phase2.jsx_gate import dedupe_host_functions, neutralize_placeholder_tags

                    screens = []
                    for screen in list(run.get("screens") or []):
                        item = dict(screen)
                        if item.get("source"):
                            item["source"] = dedupe_host_functions(
                                neutralize_placeholder_tags(str(item["source"]))
                            )
                        screens.append(item)
                    return self._json(200, {"screens": screens})
                if path == "/api/me":
                    return self._json(200, self._actor(parsed=parsed))
                if path == "/api/directory":
                    actor = self._actor(parsed=parsed)
                    actors = list(directory.DIRECTORY.values()) if dev_mode else [actor]
                    return self._json(200, {"actors": actors})
                if path == "/api/runs":
                    self._actor(parsed=parsed)
                    return self._json(
                        200, {"runs": [public_run(row) for row in platform.list_runs()]}
                    )
                if path.startswith("/api/runs/") and path.endswith("/inbox"):
                    actor = self._actor(parsed=parsed)
                    return self._json(
                        200,
                        {"inbox": [public_run(row) for row in platform.inbox(actor)]},
                    )
                if path == "/api/inbox":
                    actor = self._actor(parsed=parsed)
                    return self._json(
                        200,
                        {"inbox": [public_run(row) for row in platform.inbox(actor)]},
                    )
                if path == "/api/catalog":
                    return self._json(200, {"items": platform.catalog()})
                if path == "/api/change-requests":
                    self._actor(parsed=parsed)
                    return self._json(200, {"items": platform.list_change_requests()})
                if path.startswith("/api/runs/") and path.endswith("/events"):
                    self._actor(parsed=parsed)
                    rid = path.split("/")[3]
                    return self._events(rid)
                if path.startswith("/api/runs/"):
                    self._actor(parsed=parsed)
                    rid = path.split("/")[3]
                    return self._json(200, public_run(platform.get(rid)))
            except AuthenticationError as exc:
                return self._json(401, {"error": str(exc)})
            except PermissionError as exc:
                return self._json(401, {"error": str(exc)})
            except KeyError:
                return self._json(404, {"error": "not found"})
            except Exception as exc:
                return self._json(500, {"error": str(exc)})
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            raw = self._body()
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                data = {}
                if not (path.startswith("/preview/") and "/api/" in path):
                    return self._json(400, {"error": "invalid json"})
            try:
                if path.startswith("/preview/") and "/api/" in path:
                    return self._preview_api("POST", path, data if isinstance(data, dict) else {})
                if path == "/api/auth/login":
                    token, payload = compatibility.login(
                        data.get("email") or "", data.get("password") or ""
                    )
                    return self._json(
                        200,
                        payload,
                        headers={"Set-Cookie": compatibility.session_cookie(token)},
                    )
                if path == "/api/auth/client/login":
                    token, payload = compatibility.login(
                        data.get("email") or "",
                        data.get("password") or "",
                        client=True,
                    )
                    return self._json(
                        200,
                        payload,
                        headers={"Set-Cookie": compatibility.session_cookie(token)},
                    )
                if path == "/api/auth/client/register":
                    token, payload = compatibility.register_client(
                        data.get("name") or "",
                        data.get("email") or "",
                        data.get("password") or "",
                    )
                    return self._json(
                        201,
                        payload,
                        headers={"Set-Cookie": compatibility.session_cookie(token)},
                    )
                if path == "/api/auth/logout":
                    compatibility.logout(self.headers)
                    return self._json(
                        200,
                        {"ok": True},
                        headers={"Set-Cookie": compatibility.clear_cookie()},
                    )
                if path.startswith("/api/codegen/") and path.endswith("/start"):
                    actor = compatibility.authenticate(self.headers)
                    artifact_id = path.split("/")[3]
                    try:
                        row = platform.get(artifact_id)
                    except KeyError:
                        return self._json(404, {"error": "Artifact not found"})
                    if actor.get("isClient"):
                        return self._json(
                            403, {"error": "Only internal roles can generate code"}
                        )
                    result = codegen_jobs.start(
                        artifact_id,
                        row=row,
                        actor=actor,
                        model=str((data or {}).get("model") or ""),
                    )
                    return self._json(202, result)
                if path == "/api/artifacts/chat/start":
                    actor = compatibility.authenticate(self.headers)
                    compatibility.ensure_can_start_intake(actor)
                    result = platform.submit(
                        actor,
                        data.get("template") or "full_governance",
                        data.get("request")
                        or "Start a guided requirement intake conversation.",
                    )
                    hub.publish(public_run(result))
                    return self._json(
                        201,
                        {
                            "artifactId": result["requirement"]["id"],
                            "reply": result.get("question") or "Tell me what you need.",
                        },
                    )
                if path.startswith("/api/artifacts/chat/") and path.endswith("/message"):
                    actor = compatibility.authenticate(self.headers)
                    rid = path.split("/")[4]
                    compatibility.ensure_owner(platform.get(rid), actor)
                    result = platform.turn(rid, data.get("message") or "")
                    hub.publish(public_run(result))
                    payload = {
                        "reply": result.get("question") or "",
                        "reviewReady": result.get("phase") == "awaiting_gate_1",
                    }
                    if payload["reviewReady"]:
                        payload["artifact"] = compatibility.artifact(
                            result, viewer=actor
                        )
                    return self._json(200, payload)
                if path.startswith("/api/artifacts/"):
                    actor = compatibility.authenticate(self.headers)
                    parts = path.split("/")
                    if path.endswith("/ui/screen/agent"):
                        rid = parts[3]
                        result = platform.apply_screen_instruction(
                            rid,
                            actor,
                            str(data.get("name") or ""),
                            str(data.get("instruction") or ""),
                            source=str(data.get("source") or ""),
                            theme=str(data.get("theme") or ""),
                        )
                        return self._json(200, result)
                    if len(parts) == 5:
                        rid, action = parts[3], parts[4]
                        current = platform.get(rid)
                        if action == "scope-revise":
                            compatibility.ensure_owner(current, actor)
                            return self._json(
                                200,
                                platform.revise_scope_draft(
                                    rid,
                                    actor,
                                    str(data.get("instruction") or ""),
                                    str(data.get("title") or ""),
                                    dict(data.get("content") or {}),
                                ),
                            )
                        if action == "brd-revise":
                            compatibility.ensure_brd_editor(current, actor)
                            return self._json(
                                200,
                                platform.revise_brd_draft(
                                    rid,
                                    actor,
                                    str(data.get("instruction") or ""),
                                    str(data.get("content") or ""),
                                ),
                            )
                        if action == "submit":
                            compatibility.ensure_owner(current, actor)
                            current = platform.edit_scope(
                                rid,
                                actor,
                                data.get("title") or "",
                                dict(data.get("content") or {}),
                            )
                            hub.publish(public_run(current))
                            return self._json(
                                200,
                                {
                                    "artifact": compatibility.artifact(
                                        current, viewer=actor
                                    )
                                },
                            )
                        outcomes = {
                            "approve": "approve",
                            "approveFsd": "approve",
                            "approveUi": "approve",
                            "approvePlan": "approve",
                            "giveFinalFsdApproval": "approve",
                            "requestRevision": "revise",
                            "requestFsdChanges": "revise",
                            "requestUiChanges": "revise",
                            "reject": "discard",
                            "discard": "discard",
                        }
                        if action in outcomes:
                            outcome = outcomes[action]
                            if outcome == "discard" and current.get("awaiting") in (3, 4, 5):
                                outcome = "revise"
                            if current.get("awaiting") == 6 and outcome == "discard":
                                outcome = "reject"
                            if current.get("awaiting") == 7 and outcome in {"discard", "revise"}:
                                outcome = "hold"
                            side = ""
                            if action == "requestUiChanges":
                                side = "ui"
                            elif action == "requestFsdChanges":
                                side = "architecture"
                            result = platform.decide(
                                rid,
                                actor,
                                outcome,
                                reason=data.get("comment") or data.get("reason") or "",
                                revise_side=side,
                            )
                            hub.publish(public_run(result))
                            return self._json(
                                200,
                                {
                                    "artifact": compatibility.artifact(
                                        result, viewer=actor
                                    )
                                },
                            )
                if path == "/api/runs/clear":
                    self._actor(data=data, parsed=parsed)
                    result = platform.clear_runs()
                    return self._json(200, result)
                if path == "/api/runs":
                    originator = self._actor(data=data, parsed=parsed, field="originator_id")
                    compatibility.ensure_can_start_intake(originator)
                    result = platform.submit(
                        originator,
                        data.get("template") or "full_governance",
                        data.get("request") or "",
                    )
                    return self._result(201, result)
                if path.startswith("/api/runs/") and path.endswith("/turn"):
                    self._actor(data=data, parsed=parsed)
                    rid = path.split("/")[3]
                    result = platform.turn(rid, data.get("message") or "")
                    return self._result(200, result)
                if path.startswith("/api/runs/") and path.endswith("/decide"):
                    rid = path.split("/")[3]
                    actor = self._actor(data=data, parsed=parsed)
                    result = platform.decide(
                        rid, actor, data.get("outcome") or "approve",
                        gate=data.get("gate"),
                        channel=data.get("channel") or "workspace",
                        reason=data.get("reason") or "",
                    )
                    return self._result(200, result)
                if path.startswith("/api/runs/") and path.endswith("/brd"):
                    rid = path.split("/")[3]
                    actor = self._actor(data=data, parsed=parsed)
                    result = platform.edit_brd(rid, actor, data.get("content") or "")
                    return self._result(200, result)
                if path in {"/api/webhook", "/api/webhooks/github"}:
                    sig = self.headers.get("X-Hub-Signature-256") or ""
                    event = self.headers.get("X-GitHub-Event") or ""
                    if event and event != "pull_request_review":
                        webhook.verify(raw, sig, platform.webhook_secret)
                        return self._json(200, {"ignored": event})
                    result = platform.ingest_webhook(raw, sig)
                    return self._result(200, result)
                if path == "/api/tick":
                    self._actor(data=data, parsed=parsed)
                    return self._json(200, {"applied": platform.tick()})
                if path == "/api/change-requests":
                    actor = self._actor(data=data, parsed=parsed)
                    result = platform.submit_change_request(
                        actor,
                        str(data.get("requirement_id") or ""),
                        str(data.get("justification") or ""),
                        str(data.get("impact") or ""),
                    )
                    return self._json(200, result)
            except AuthenticationError as exc:
                return self._json(401, {"error": str(exc)})
            except PermissionError as exc:
                return self._json(403, {"error": str(exc)})
            except Exception as exc:
                return self._json(400, {"error": str(exc)})
            self._json(404, {"error": "not found"})

        def do_PUT(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            raw = self._body()
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "invalid json"})
            try:
                if path.startswith("/api/codegen/") and path.rstrip("/").endswith("/file"):
                    actor = compatibility.authenticate(self.headers)
                    code_gen_id = path.split("/")[3]
                    payload = codegen_jobs.put_file(
                        code_gen_id,
                        str((data or {}).get("path") or ""),
                        str((data or {}).get("content") or ""),
                    )
                    if payload is None:
                        return self._json(404, {"error": "file not found"})
                    return self._json(200, payload)
                if path.startswith("/api/artifacts/") and path.endswith("/brd"):
                    actor = compatibility.authenticate(self.headers)
                    rid = path.split("/")[3]
                    compatibility.ensure_brd_editor(platform.get(rid), actor)
                    result = platform.edit_brd(rid, actor, data.get("content") or "")
                    hub.publish(public_run(result))
                    return self._json(
                        200,
                        {"artifact": compatibility.artifact(result, viewer=actor)},
                    )
                if path.startswith("/api/artifacts/") and path.endswith("/ui/screen"):
                    actor = compatibility.authenticate(self.headers)
                    rid = path.split("/")[3]
                    result = platform.edit_screen(
                        rid,
                        actor,
                        str(data.get("name") or ""),
                        str(data.get("source") or ""),
                    )
                    hub.publish(public_run(result))
                    return self._json(
                        200,
                        {"artifact": compatibility.artifact(result, viewer=actor)},
                    )
                if path == "/api/settings/models":
                    actor = compatibility.authenticate(self.headers)
                    if actor.get("isClient"):
                        return self._json(403, {"error": "Not available"})
                    from phase1 import agent_settings, settings_pages

                    if not settings_pages.may_edit(actor):
                        return self._json(403, {"error": "only Product Owner or Business Owner can change models"})
                    from phase1 import agent_settings

                    stored = agent_settings.set_models(
                        platform.root,
                        dict(data.get("models") or {}),
                        str(actor.get("id") or ""),
                    )
                    ui_context = agent_settings.ui_context(platform.root)
                    if "uiContext" in data:
                        ui_context = agent_settings.set_ui_context(
                            platform.root,
                            str(data.get("uiContext") or ""),
                            str(actor.get("id") or ""),
                        )
                    return self._json(
                        200,
                        {
                            "models": stored,
                            "roles": agent_settings.roles_view(platform.root),
                            "cycleCost": agent_settings.cycle_cost(platform.root),
                            "uiContext": ui_context,
                        },
                    )
                if path == "/api/settings/design-system":
                    compatibility.authenticate(self.headers)
                    return self._json(
                        200, {"content": data.get("content") or "", "canEdit": True}
                    )
                if path == "/api/settings/intake-skill":
                    compatibility.authenticate(self.headers)
                    return self._json(
                        200, {"content": data.get("content") or "", "canEdit": True}
                    )
            except PermissionError as exc:
                return self._json(403, {"error": str(exc)})
            except KeyError:
                return self._json(404, {"error": "not found"})
            except Exception as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(404, {"error": "not found"})

        def do_DELETE(self) -> None:
            path = urlparse(self.path).path
            if path.startswith("/preview/") and "/api/" in path:
                return self._preview_api("DELETE", path)
            return self._json(404, {"error": "not found"})

        def _preview_api(self, method: str, path: str, body: dict[str, Any] | None = None):
            parsed_api = runtime_db.parse_preview_api(path)
            if parsed_api is None:
                return self._json(404, {"error": "not found"})
            rid, resource, item_id = parsed_api
            run = platform.get(rid)
            status, payload = runtime_db.handle(
                platform.root,
                rid,
                run.get("brd_text") or "",
                method,
                resource,
                item_id,
                body=body,
            )
            return self._json(status, payload)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._cors_headers()
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _actor(
            self,
            *,
            data: dict[str, Any] | None = None,
            parsed=None,
            field: str = "actor_id",
        ) -> dict[str, Any]:
            authorization = self.headers.get("Authorization") or ""
            if authorization.startswith("Bearer "):
                token = authorization[7:].strip()
                if not token:
                    raise AuthenticationError("empty bearer token")
                identity = getattr(getattr(platform, "adapters", None), "identity", None)
                identity = identity or getattr(platform, "identity", None)
                if identity is None:
                    raise AuthenticationError("identity adapter is unavailable")
                try:
                    return identity.validate_token(token)
                except Exception as exc:
                    raise AuthenticationError("invalid bearer token") from exc
            if not dev_mode:
                raise AuthenticationError("Authorization Bearer token required")
            query = parse_qs(parsed.query) if parsed is not None else {}
            actor_id = (
                (data or {}).get(field)
                or (query.get("id") or [None])[0]
                or "u-requester"
            )
            return directory.actor(str(actor_id))

        def _result(self, status: int, result: dict[str, Any]) -> None:
            run = public_run(result)
            hub.publish(run)
            return self._json(status, run)

        def _events(self, requirement_id: str) -> None:
            version = hub.version(requirement_id)
            initial = public_run(platform.get(requirement_id))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._cors_headers()
            self.end_headers()
            latest = initial
            try:
                self._sse("status", initial)
                while True:
                    version, payload = hub.wait(requirement_id, version, 15.0)
                    if payload is None:
                        current = public_run(platform.get(requirement_id))
                        if current != latest:
                            latest = current
                            self._sse("status", current)
                        else:
                            self.wfile.write(b": heartbeat\n\n")
                    else:
                        latest = payload
                        self._sse("status", payload)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        def _sse(self, event: str, payload: dict[str, Any]) -> None:
            blob = json.dumps(payload, separators=(",", ":"))
            self.wfile.write(f"event: {event}\ndata: {blob}\n\n".encode("utf-8"))
            self.wfile.flush()

        def _json(
            self,
            status: int,
            payload: Any,
            *,
            headers: dict[str, str] | None = None,
        ) -> None:
            blob = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self._cors_headers()
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(blob)

        def _cors_headers(self) -> None:
            origin = self.headers.get("Origin") or ""
            if origin and origin in configured_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Vary", "Origin")
            self.send_header(
                "Access-Control-Allow-Headers", "Authorization, Content-Type, X-Hub-Signature-256"
            )
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")

        def _bytes(self, status: int, blob: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(blob)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(blob)

        def _file(self, path: Path, content_type: str) -> None:
            if not path.exists():
                return self._json(404, {"error": "not found"})
            blob = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

    return Handler


def public_run(row: dict) -> dict:
    req = row.get("requirement") or {}
    artefacts = req.get("artefacts") or {}
    current_shas = {
        int(gate): (artefacts.get(stage) or {}).get("sha")
        for gate, stage in ((1, "scope"), (2, "brd"), (3, "design"), (4, "plan"))
    }
    signatures = []
    for signature in req.get("signatures") or []:
        item = dict(signature)
        item["stale"] = item.get("artefact_sha") != current_shas.get(item.get("gate"))
        signatures.append(item)
    return {
        "id": req.get("id"),
        "requirement": req,
        "phase": row.get("phase"),
        "state": row.get("state"),
        "awaiting": row.get("awaiting"),
        "template": req.get("template"),
        "originator": req.get("originator"),
        "named_approvers": row.get("named_approvers"),
        "messages": row.get("messages") or [],
        "budget": row.get("budget"),
        "question": row.get("question") or row.get("design_question"),
        "scope_sha": (artefacts.get("scope") or {}).get("sha"),
        "brd_sha": (artefacts.get("brd") or {}).get("sha"),
        "design_sha": (artefacts.get("design") or {}).get("sha"),
        "attestations": req.get("attestations") or [],
        "signatures": signatures,
        "cleared": req.get("cleared") or [],
        "sla_due": row.get("sla_due"),
        "escalated": row.get("escalated"),
        "decision": row.get("decision"),
        "discarded_reason": req.get("discarded_reason"),
        "scope_text": row.get("scope_text") or "",
        "brd_text": row.get("brd_text") or "",
        "architecture_text": row.get("architecture_text") or "",
        "plan_text": row.get("plan_text") or "",
        "screens": list(row.get("screens") or []),
        "stack_profile": row.get("stack_profile"),
        "coverage": row.get("coverage") or {},
        "tickets": list(row.get("tickets") or []),
        "tests": list(row.get("tests") or []),
        "sprint0": row.get("sprint0") or {},
        "build": row.get("build") or {},
        "uat": row.get("uat") or {},
        "release": row.get("release") or {},
        "team_reports": list(row.get("team_reports") or []),
        "preview_url": row.get("preview_url") or "",
        "design_progress": row.get("design_progress"),
    }


def serve(host: str = "127.0.0.1", port: int = 8787, root: Path | None = None) -> None:
    runtime = Path(root) if root else Path(__file__).resolve().parent.parent / ".runtime"
    platform = service_from_env(runtime)
    from phase1 import agent_settings

    agent_settings.ensure_cheap_defaults(platform.root)
    from phase1.platform import Phase1

    homes = Phase1(platform.root)
    try:
        homes.sync_requirement_homes()
    except Exception as exc:
        print(f"[github] requirement homes were not synced: {exc}")
    try:
        homes.sync_specifications()
    except Exception as exc:
        print(f"[spec] specifications were not filled: {exc}")
    try:
        homes.sync_design_accuracy()
    except Exception as exc:
        print(f"[design] architectures were not aligned: {exc}")
    dev_mode = os.getenv("PHASE1_DEV_MODE", "").lower() in {"1", "true", "yes"}
    httpd = ThreadingHTTPServer(
        (host, port),
        make_handler(platform, dev_mode=dev_mode),
    )
    print(f"Phase 1 workspace http://{host}:{port}")
    httpd.serve_forever()
