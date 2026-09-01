"""Trust boundary for the Express proxy.

During migration Express authenticates and forwards the user. That identity is
only trustworthy if the caller proves it is Express, so these cover the
rejection paths — the ones that would otherwise let anyone who can reach the
port act as an MD.
"""

import json

import pytest
from fastapi import HTTPException

from app.auth import _forwarded_user

MD = {"id": "u1", "tierId": "md", "isClient": False}


class Req:
    def __init__(self, **headers):
        self.headers = {k.lower().replace("_", "-"): v for k, v in headers.items()}


def test_no_header_defers_to_the_session(monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "secret")
    assert _forwarded_user(Req()) is None


def test_valid_token_is_accepted(monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "secret")
    req = Req(**{"x-actor": json.dumps(MD), "x-agent-service-token": "secret"})
    assert _forwarded_user(req)["tierId"] == "md"


def test_missing_token_is_rejected(monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "secret")
    with pytest.raises(HTTPException) as exc:
        _forwarded_user(Req(**{"x-actor": json.dumps(MD)}))
    assert exc.value.status_code == 401


def test_wrong_token_is_rejected(monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "secret")
    with pytest.raises(HTTPException) as exc:
        _forwarded_user(Req(**{"x-actor": json.dumps(MD), "x-agent-service-token": "guessed"}))
    assert exc.value.status_code == 401


def test_unset_token_fails_closed(monkeypatch):
    """An unconfigured secret must not become an open door."""
    monkeypatch.delenv("AGENT_SERVICE_TOKEN", raising=False)
    with pytest.raises(HTTPException) as exc:
        _forwarded_user(Req(**{"x-actor": json.dumps(MD), "x-agent-service-token": "anything"}))
    assert exc.value.status_code == 401


@pytest.mark.parametrize("payload", ["not-json", "[]", '"a string"', "{}", '{"tierId":"md"}'])
def test_malformed_identities_are_rejected(monkeypatch, payload):
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "secret")
    with pytest.raises(HTTPException) as exc:
        _forwarded_user(Req(**{"x-actor": payload, "x-agent-service-token": "secret"}))
    assert exc.value.status_code == 400
