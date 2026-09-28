"""Direct end-to-end and canonical-frontend acceptance smoke checks."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from phase1.platform import Phase1
from phase1.server import make_handler


def call(server, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    connection = HTTPConnection(*server.server_address, timeout=5)
    payload = json.dumps(body) if body is not None else None
    headers = {"Content-Type": "application/json"} if payload else {}
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    result = json.loads(response.read())
    connection.close()
    return response.status, result


def test_full_direct_http_flow_reaches_phase_2(tmp_path):
    platform = Phase1(tmp_path / "runtime")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(platform, dev_mode=True),
    )
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, run = call(
            server,
            "POST",
            "/api/runs",
            {
                "originator_id": "u-requester",
                "template": "full_governance",
                "request": "Create a governed room-booking web application.",
            },
        )
        assert status == 201
        rid = run["id"]
        for answer in (
            "Facilities coordinators use it.",
            "A spreadsheet causes duplicate bookings.",
            "Success means no duplicate bookings.",
            "Responsive browser only; no native mobile application.",
        ):
            status, run = call(
                server,
                "POST",
                f"/api/runs/{rid}/turn",
                {"actor_id": "u-requester", "message": answer},
            )
            assert status == 200
        assert run["phase"] == "awaiting_gate_1"

        _, run = call(
            server,
            "POST",
            f"/api/runs/{rid}/decide",
            {"actor_id": "u-po", "outcome": "approve", "gate": 1},
        )
        assert run["phase"] == "awaiting_gate_2"
        _, run = call(
            server,
            "POST",
            f"/api/runs/{rid}/decide",
            {"actor_id": "u-bo", "outcome": "approve", "gate": 2},
        )
        assert run["decision"]["satisfied"] is False
        _, run = call(
            server,
            "POST",
            f"/api/runs/{rid}/decide",
            {"actor_id": "u-ctl", "outcome": "approve", "gate": 2},
        )
        assert run["phase"] == "awaiting_gate_3"
        assert run["awaiting"] == 3
        assert run["screens"]
        assert len(run["attestations"]) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_canonical_frontend_has_desktop_and_mobile_static_contracts():
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    app = (frontend / "public/app.html").read_text(encoding="utf-8")
    config = (frontend / "public/config.js").read_text(encoding="utf-8")
    assert '<meta name="viewport"' in app
    assert "@media (max-width:" in app
    assert "window.API_BASE" in app
    assert "window.API_BASE" in config
    assert (frontend / "server.js").is_file()
    login = (frontend / "public/login.html").read_text(encoding="utf-8")
    for email in (
        "requester@client.example",
        "po@client.example",
        "bo@client.example",
        "ctl@client.example",
        "architect@client.example",
        "ux@client.example",
        "ba@client.example",
        "techlead@client.example",
        "dev-lead@client.example",
        "ai-lead@client.example",
        "qa-lead@client.example",
        "senior@client.example",
        "release@client.example",
    ):
        assert email in login
    assert "password123" in login
    assert "openBrdEditorModal" in app
    assert "Approve merge" in app
    assert "Approve UAT" in app
    assert "Approve release" in app
    compose = Path(__file__).resolve().parents[1] / "deploy/docker-compose.yml"
    assert "PHASE1_DEV_MODE" in compose.read_text(encoding="utf-8")
