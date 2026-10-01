"""Canonical frontend compatibility contract and Phase 1 flow."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from phase1.platform import Phase1
from phase1.server import make_handler


@pytest.fixture
def compat_server(tmp_path):
    platform = Phase1(tmp_path / "runtime")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(platform, dev_mode=True),
    )
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(server, method, path, body=None, *, cookie=None, token=None, origin=None, timeout=30):
    connection = HTTPConnection(*server.server_address, timeout=timeout)
    headers = {}
    encoded = None
    if body is not None:
        encoded = json.dumps(body)
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if origin:
        headers["Origin"] = origin
    connection.request(method, path, body=encoded, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    payload = json.loads(raw) if raw else None
    result = response.status, payload, dict(response.getheaders())
    connection.close()
    return result


def login(server, email):
    status, payload, headers = request(
        server,
        "POST",
        "/api/auth/login",
        {"email": email, "password": "password123"},
    )
    assert status == 200
    assert set(payload["user"]) >= {
        "id",
        "name",
        "email",
        "tierId",
        "department",
        "isClient",
    }
    return headers["Set-Cookie"].split(";", 1)[0]


def test_login_session_and_proxy_compatible_shapes(compat_server):
    cookie = login(compat_server, "requester@client.example")

    status, me, _ = request(compat_server, "GET", "/api/auth/me", cookie=cookie)
    assert status == 200
    assert me["user"]["id"] == "u-requester"
    assert me["user"]["tierId"] == "requester"

    status, listing, _ = request(
        compat_server, "GET", "/api/artifacts", cookie=cookie
    )
    assert status == 200
    assert listing == {"artifacts": []}

    status, _, headers = request(
        compat_server,
        "OPTIONS",
        "/api/artifacts",
        origin="http://localhost:5173",
    )
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert headers["Access-Control-Allow-Credentials"] == "true"
    assert "PUT" in headers["Access-Control-Allow-Methods"]

    status, _, headers = request(
        compat_server, "POST", "/api/auth/logout", {}, cookie=cookie
    )
    assert status == 200
    assert "Max-Age=0" in headers["Set-Cookie"]
    assert request(compat_server, "GET", "/api/auth/me", cookie=cookie)[0] == 401


def test_intake_gate1_gate2_and_brd_edit_flow(compat_server):
    requester = login(compat_server, "requester@client.example")
    status, started, _ = request(
        compat_server,
        "POST",
        "/api/artifacts/chat/start",
        {},
        cookie=requester,
    )
    assert status == 201
    assert started["artifactId"].startswith("REQ-")
    assert started["reply"]
    rid = started["artifactId"]

    answers = [
        "Facilities coordinators in Workplace Services.",
        "They use a spreadsheet and double-book rooms.",
        "People can book without collisions.",
        "Native mobile apps are out of scope.",
    ]
    final = None
    for answer in answers:
        status, final, _ = request(
            compat_server,
            "POST",
            f"/api/artifacts/chat/{rid}/message",
            {"message": answer},
            cookie=requester,
        )
        assert status == 200
    assert final["reviewReady"] is True
    assert final["artifact"]["currentStage"] == "pending_approval"
    assert final["artifact"]["currentApprovalIndex"] == 0
    assert all(
        "guided requirement intake" not in str(item).lower()
        for item in final["artifact"]["content"]["inScope"]
    )
    assert final["artifact"]["content"]["currentState"]

    status, submitted, _ = request(
        compat_server,
        "POST",
        f"/api/artifacts/{rid}/submit",
        {"title": "Room booking", "content": final["artifact"]["content"]},
        cookie=requester,
    )
    assert status == 200
    assert submitted["artifact"]["_id"] == rid
    assert submitted["artifact"]["title"] == "Room booking"
    assert submitted["artifact"]["content"]["inScope"] == final["artifact"]["content"]["inScope"]
    assert submitted["artifact"]["content"]["outOfScope"] == final["artifact"]["content"]["outOfScope"]
    assert submitted["artifact"]["approvalChain"][0]["approverTiers"] == ["po"]
    assert submitted["artifact"]["approvalChain"][1]["approverTiers"] == ["bo", "ctl"]
    assert submitted["artifact"]["approvalChain"][5]["approverTiers"] == [
        "requester",
        "stakeholder",
    ]
    assert submitted["artifact"]["approvalChain"][6]["approverTiers"] == ["rm"]

    product_owner = login(compat_server, "po@client.example")
    status, po_me, _ = request(compat_server, "GET", "/api/auth/me", cookie=product_owner)
    assert status == 200
    assert po_me["user"]["tierId"] == "po"
    assert po_me["user"]["tierId"] not in {"vp", "md", "ceo", "tl"}
    status, gate1, _ = request(
        compat_server,
        "POST",
        f"/api/artifacts/{rid}/approve",
        {},
        cookie=product_owner,
    )
    assert status == 200
    assert gate1["artifact"]["currentApprovalIndex"] == 1
    assert gate1["artifact"]["brdText"].startswith("#")

    business_owner = login(compat_server, "bo@client.example")
    edited = gate1["artifact"]["brdText"] + "\n\nEdited in the canonical frontend.\n"
    status, edit_result, _ = request(
        compat_server,
        "PUT",
        f"/api/artifacts/{rid}/brd",
        {"content": edited},
        cookie=business_owner,
    )
    assert status == 200
    assert edit_result["artifact"]["brdText"].endswith(
        "Edited in the canonical frontend.\n"
    )

    status, first_gate2, _ = request(
        compat_server,
        "POST",
        f"/api/artifacts/{rid}/approve",
        {},
        cookie=business_owner,
    )
    assert status == 200
    assert first_gate2["artifact"]["currentStage"] == "pending_approval"

    tech_lead = login(compat_server, "ctl@client.example")
    status, gate2, _ = request(
        compat_server,
        "POST",
        f"/api/artifacts/{rid}/approve",
        {},
        cookie=tech_lead,
    )
    assert status == 200
    assert gate2["artifact"]["currentStage"] == "pending_approval"
    assert gate2["artifact"]["currentApprovalIndex"] == 2
    assert gate2["artifact"]["phase2"] is True
    # Screens are written when the business analyst signs, not at the Gate 2 lock.
    assert not (gate2["artifact"].get("ui") or {}).get("screens")
    assert gate2["artifact"].get("architectureText") or (
        (gate2["artifact"].get("detailedReport") or {}).get("architecture")
    )

    architect = login(compat_server, "architect@client.example")
    status, arch_view, _ = request(
        compat_server,
        "GET",
        f"/api/artifacts/{rid}",
        cookie=architect,
    )
    assert status == 200
    assert arch_view["artifact"]["architectureText"] or (
        (arch_view["artifact"].get("detailedReport") or {}).get("architecture")
    )
    assert arch_view["artifact"]["awaitingGate"] == 3

    status, g3a, _ = request(
        compat_server,
        "POST",
        f"/api/artifacts/{rid}/approveUi",
        {},
        cookie=architect,
    )
    assert status == 200
    ba = login(compat_server, "ba@client.example")
    status, g3b, _ = request(
        compat_server,
        "POST",
        f"/api/artifacts/{rid}/approveUi",
        {},
        cookie=ba,
    )
    assert status == 200
    ux = login(compat_server, "ux@client.example")
    status, screens, _ = request(
        compat_server,
        "GET",
        f"/api/artifacts/{rid}/ui/screens",
        cookie=ux,
    )
    assert status == 200
    assert screens["screens"]
    status, edited_ui, _ = request(
        compat_server,
        "PUT",
        f"/api/artifacts/{rid}/ui/screen",
        {
            "name": screens["screens"][0]["name"],
            "source": screens["screens"][0]["source"],
        },
        cookie=ux,
    )
    assert status == 200
    status, g3, _ = request(
        compat_server,
        "POST",
        f"/api/artifacts/{rid}/approveUi",
        {},
        cookie=ux,
    )
    assert status == 200
    assert g3["artifact"]["currentStage"] == "pending_approval"
    assert g3["artifact"]["currentApprovalIndex"] == 3
    assert g3["artifact"]["governedPhase"] == 3
    assert g3["artifact"]["tickets"]

    tech = login(compat_server, "techlead@client.example")
    status, g4a, _ = request(
        compat_server,
        "POST",
        f"/api/artifacts/{rid}/approvePlan",
        {},
        cookie=tech,
    )
    assert status == 200
    for email in (
        "dev-lead@client.example",
        "ai-lead@client.example",
        "qa-lead@client.example",
    ):
        lead = login(compat_server, email)
        status, g4, _ = request(
            compat_server,
            "POST",
            f"/api/artifacts/{rid}/approvePlan",
            {},
            cookie=lead,
        )
        assert status == 200
    assert g4["artifact"]["currentStage"] == "pending_approval"
    assert g4["artifact"]["governedPhase"] == 4
    assert g4["artifact"]["currentApprovalIndex"] == 4
    assert g4["artifact"]["awaitingGate"] == 5

    senior = login(compat_server, "senior@client.example")
    status, g5, _ = request(
        compat_server,
        "POST",
        f"/api/artifacts/{rid}/approve",
        {},
        cookie=senior,
    )
    assert status == 200
    assert g5["artifact"]["awaitingGate"] == 6
    assert g5["artifact"]["uat"]

    uat = login(compat_server, "requester@client.example")
    status, g6, _ = request(
        compat_server,
        "POST",
        f"/api/artifacts/{rid}/approve",
        {},
        cookie=uat,
    )
    assert status == 200
    assert g6["artifact"]["awaitingGate"] == 7

    release = login(compat_server, "release@client.example")
    status, g7, _ = request(
        compat_server,
        "POST",
        f"/api/artifacts/{rid}/approve",
        {},
        cookie=release,
    )
    assert status == 200
    assert g7["artifact"]["currentStage"] == "approved"
    assert g7["artifact"]["governedPhase"] == 7
    assert g7["artifact"]["release"]

    status, listing, _ = request(
        compat_server, "GET", "/api/artifacts", cookie=requester
    )
    assert status == 200
    artifact = listing["artifacts"][0]
    assert artifact["_id"] == rid
    assert artifact["title"] == "Room booking"
    assert artifact["brdText"] == edited
    assert len(artifact["history"]) >= 8


def test_settings_readable_for_requester(compat_server):
    cookie = login(compat_server, "requester@client.example")
    status, payload, _ = request(compat_server, "GET", "/api/settings/models", cookie=cookie)
    assert status == 200
    assert payload["canEdit"] is False
    assert {row["role"] for row in payload["roles"]} >= {"intake", "brd", "ui"}
    assert payload["cycleCost"]["usd"] >= 0
    status, usage, _ = request(compat_server, "GET", "/api/usage", cookie=cookie)
    assert status == 200
    assert "recorded" in usage
    assert "cycleEstimate" in usage
    assert "byAgent" in usage
    assert "byRequirement" in usage
    assert "history" in usage
    status, denied, _ = request(
        compat_server,
        "PUT",
        "/api/settings/models",
        {"models": {"intake": "openai/gpt-4o"}},
        cookie=cookie,
    )
    assert status == 403
    status, skill, _ = request(
        compat_server, "GET", "/api/settings/intake-skill", cookie=cookie
    )
    assert status == 200
    assert skill["canEdit"] is False
    assert "capability boundary" in skill["content"].lower()
    status, design, _ = request(
        compat_server, "GET", "/api/settings/design-system", cookie=cookie
    )
    assert status == 200
    assert design["content"]
    status, prompts, _ = request(
        compat_server, "GET", "/api/settings/prompts", cookie=cookie
    )
    assert status == 200
    names = {row["name"] for row in prompts["prompts"]}
    assert names >= {"intake", "brd", "ui", "build", "review", "adversary", "monitor"}
    intake_prompt = next(row for row in prompts["prompts"] if row["name"] == "intake")
    assert "capability boundary" in intake_prompt["content"].lower()
    status, lessons, _ = request(
        compat_server, "GET", "/api/settings/lessons", cookie=cookie
    )
    assert status == 200
    assert lessons["lessons"] == []


def test_settings_models_list_openrouter_and_save(compat_server):
    cookie = login(compat_server, "po@client.example")
    status, payload, _ = request(compat_server, "GET", "/api/settings/models", cookie=cookie)
    assert status == 200
    roles = {row["role"] for row in payload["roles"]}
    assert "intake" in roles and "build" in roles and "adversary" in roles
    assert payload["canEdit"] is True
    assert payload["cycleCost"]["usd"] > 0
    status, saved, _ = request(
        compat_server,
        "PUT",
        "/api/settings/models",
        {"models": {"intake": "openai/gpt-4o-mini", "ui": "qwen/qwen3-coder"}},
        cookie=cookie,
    )
    assert status == 200
    assert saved["models"]["ui"] == "qwen/qwen3-coder"


def test_compatibility_routes_use_bearer_outside_dev(tmp_path):
    platform = Phase1(tmp_path / "runtime")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(platform, dev_mode=False),
    )
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert request(server, "GET", "/api/auth/me")[0] == 401
        status, payload, _ = request(
            server, "GET", "/api/auth/me", token="u-requester"
        )
        assert status == 200
        assert payload["user"]["id"] == "u-requester"
        assert request(
            server,
            "POST",
            "/api/auth/login",
            {"email": "requester@client.example", "password": "password123"},
        )[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_only_requester_can_start_intake(compat_server):
    product_owner = login(compat_server, "po@client.example")
    status, payload, _ = request(
        compat_server,
        "POST",
        "/api/artifacts/chat/start",
        {},
        cookie=product_owner,
    )
    assert status == 403
    assert "requester" in (payload or {}).get("error", "").lower()

    requester = login(compat_server, "requester@client.example")
    status, started, _ = request(
        compat_server,
        "POST",
        "/api/artifacts/chat/start",
        {},
        cookie=requester,
    )
    assert status == 201
    assert started["artifactId"].startswith("REQ-")
