"""HTTP workspace for Phase 1 — intake form, conversation, BRD editor, inbox.

Backstage is the production shell. This is the same three surfaces so the
runtime can be exercised without standing up the portal.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from phase1 import directory
from phase1.service import service_from_env

STATIC = Path(__file__).resolve().parent / "workspace"


def make_handler(platform):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # quieter tests
            pass

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path in {"/", "/index.html"}:
                return self._file(STATIC / "index.html", "text/html")
            if path.endswith(".css"):
                return self._file(STATIC / path.lstrip("/"), "text/css")
            if path.endswith(".js"):
                return self._file(STATIC / path.lstrip("/"), "application/javascript")
            if path == "/api/me":
                q = parse_qs(parsed.query)
                actor_id = (q.get("id") or ["u-requester"])[0]
                return self._json(200, directory.actor(actor_id))
            if path == "/api/directory":
                return self._json(200, {"actors": list(directory.DIRECTORY.values())})
            if path == "/api/runs":
                return self._json(200, {"runs": platform.list_runs()})
            if path.startswith("/api/runs/") and path.endswith("/inbox"):
                actor_id = parse_qs(parsed.query).get("id", ["u-po"])[0]
                return self._json(200, {"inbox": platform.inbox(directory.actor(actor_id))})
            if path == "/api/inbox":
                actor_id = parse_qs(parsed.query).get("id", ["u-po"])[0]
                return self._json(200, {"inbox": platform.inbox(directory.actor(actor_id))})
            if path.startswith("/api/runs/"):
                rid = path.split("/")[3]
                try:
                    return self._json(200, public_run(platform.get(rid)))
                except KeyError:
                    return self._json(404, {"error": "not found"})
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            raw = self._body()
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "invalid json"})
            try:
                if path == "/api/runs":
                    originator = directory.actor(data.get("originator_id") or "u-requester")
                    result = platform.submit(
                        originator,
                        data.get("template") or "full_governance",
                        data.get("request") or "",
                    )
                    return self._json(201, public_run(result))
                if path.startswith("/api/runs/") and path.endswith("/turn"):
                    rid = path.split("/")[3]
                    result = platform.turn(rid, data.get("message") or "")
                    return self._json(200, public_run(result))
                if path.startswith("/api/runs/") and path.endswith("/decide"):
                    rid = path.split("/")[3]
                    actor = directory.actor(data["actor_id"])
                    result = platform.decide(
                        rid, actor, data.get("outcome") or "approve",
                        gate=data.get("gate"),
                        channel=data.get("channel") or "workspace",
                    )
                    return self._json(200, public_run(result))
                if path.startswith("/api/runs/") and path.endswith("/brd"):
                    rid = path.split("/")[3]
                    actor = directory.actor(data.get("actor_id") or "u-bo")
                    result = platform.edit_brd(rid, actor, data.get("content") or "")
                    return self._json(200, public_run(result))
                if path == "/api/webhook":
                    sig = self.headers.get("X-Hub-Signature-256") or ""
                    result = platform.ingest_webhook(raw, sig)
                    return self._json(200, public_run(result))
            except Exception as exc:
                return self._json(400, {"error": str(exc)})
            self._json(404, {"error": "not found"})

        def do_PUT(self) -> None:
            self.do_POST()

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _json(self, status: int, payload: dict) -> None:
            blob = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Access-Control-Allow-Origin", "*")
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
    return {
        "id": req.get("id"),
        "phase": row.get("phase"),
        "state": row.get("state"),
        "awaiting": row.get("awaiting"),
        "template": req.get("template"),
        "originator": req.get("originator"),
        "named_approvers": row.get("named_approvers"),
        "messages": row.get("messages") or [],
        "budget": row.get("budget"),
        "question": row.get("question"),
        "scope_sha": (artefacts.get("scope") or {}).get("sha"),
        "brd_sha": (artefacts.get("brd") or {}).get("sha"),
        "attestations": req.get("attestations") or [],
        "cleared": req.get("cleared") or [],
        "sla_due": row.get("sla_due"),
        "escalated": row.get("escalated"),
        "decision": row.get("decision"),
        "discarded_reason": req.get("discarded_reason"),
        "scope_text": row.get("scope_text") or "",
        "brd_text": row.get("brd_text") or "",
    }


def serve(host: str = "127.0.0.1", port: int = 8787, root: Path | None = None) -> None:
    runtime = Path(root) if root else Path(__file__).resolve().parent.parent / ".runtime"
    platform = service_from_env(runtime)
    httpd = ThreadingHTTPServer((host, port), make_handler(platform))
    print(f"Phase 1 workspace http://{host}:{port}")
    httpd.serve_forever()
