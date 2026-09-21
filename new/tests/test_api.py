"""HTTP contract, identity boundary, and live-event tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from phase1.platform import Phase1
from phase1.server import EventHub, make_handler


@pytest.fixture
def api_server(tmp_path):
    platform = Phase1(tmp_path / "runtime")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(platform, dev_mode=False),
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


def request(server, method: str, path: str, body=None, token: str | None = None):
    connection = HTTPConnection(*server.server_address, timeout=3)
    headers = {}
    if body is not None:
        body = json.dumps(body)
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    return response.status, payload


def test_non_dev_api_requires_bearer_and_ignores_body_impersonation(api_server):
    status, payload = request(
        api_server,
        "POST",
        "/api/runs",
        {
            "originator_id": "u-po",
            "template": "full_governance",
            "request": "Book rooms.",
        },
    )
    assert status == 401
    assert "Bearer" in payload["error"]

    status, run = request(
        api_server,
        "POST",
        "/api/runs",
        {
            "originator_id": "u-po",
            "template": "full_governance",
            "request": "Book rooms.",
        },
        token="u-requester",
    )
    assert status == 201
    assert run["originator"]["id"] == "u-requester"


def test_health_and_cors_preflight_are_credential_free(api_server):
    status, payload = request(api_server, "GET", "/healthz")
    assert status == 200
    assert payload == {"status": "ok"}

    connection = HTTPConnection(*api_server.server_address, timeout=3)
    connection.request("OPTIONS", "/api/runs")
    response = connection.getresponse()
    assert response.status == 204
    assert "Authorization" in response.getheader("Access-Control-Allow-Headers")
    assert "POST" in response.getheader("Access-Control-Allow-Methods")
    response.read()
    connection.close()


def test_run_list_uses_the_same_public_item_contract(api_server):
    _, created = request(
        api_server,
        "POST",
        "/api/runs",
        {"template": "full_governance", "request": "Book rooms."},
        token="u-requester",
    )
    status, listing = request(api_server, "GET", "/api/runs", token="u-requester")
    assert status == 200
    listed = listing["runs"][0]
    assert set(listed) == set(created)
    assert listed["id"] == created["id"]
    assert "request_text" not in listed


def test_dev_mode_explicitly_allows_actor_selection(tmp_path):
    platform = Phase1(tmp_path / "runtime")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(platform, dev_mode=True),
    )
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, run = request(
            server,
            "POST",
            "/api/runs",
            {
                "originator_id": "u-po",
                "template": "full_governance",
                "request": "Book rooms.",
            },
        )
        assert status == 201
        assert run["originator"]["id"] == "u-po"
    finally:
        server.shutdown()
        server.server_close()


def test_event_hub_wakes_subscribers_and_times_out_with_heartbeat_signal():
    hub = EventHub()
    version, payload = hub.wait("REQ-1", 0, 0.001)
    assert (version, payload) == (0, None)
    hub.publish({"id": "REQ-1", "phase": "intake"})
    version, payload = hub.wait("REQ-1", 0, 0.001)
    assert version == 1
    assert payload["phase"] == "intake"
    assert hub.wait("REQ-1", version, 0.001) == (version, None)


def test_sse_endpoint_starts_with_current_public_run(api_server):
    _, created = request(
        api_server,
        "POST",
        "/api/runs",
        {"template": "full_governance", "request": "Book rooms."},
        token="u-requester",
    )
    connection = HTTPConnection(*api_server.server_address, timeout=3)
    connection.request(
        "GET",
        f"/api/runs/{created['id']}/events",
        headers={"Authorization": "Bearer u-requester"},
    )
    response = connection.getresponse()
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/event-stream"
    assert response.readline() == b"event: status\n"
    payload = json.loads(response.readline().removeprefix(b"data: "))
    assert payload["id"] == created["id"]
    assert set(payload) == set(created)
    connection.close()


def test_old_github_webhook_path_accepts_a_signed_ping(api_server):
    body = b'{"zen":"Check once."}'
    secret = b"dev-webhook-secret"
    sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    connection = HTTPConnection(*api_server.server_address, timeout=3)
    connection.request(
        "POST",
        "/api/webhooks/github",
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "ping",
        },
    )
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    assert response.status == 200
    assert payload["ignored"] == "ping"
