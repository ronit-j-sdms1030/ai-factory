"""Adapter contracts and local/remote parity without external services."""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest

import gate_engine
from phase1.adapters.identity import DevDirectoryIdentity
from phase1.adapters.model import DeterministicModelGateway, LiteLLMModelGateway
from phase1.adapters.policy import LocalGatePolicy, OPAGatePolicy
from phase1.adapters.precedent import DisabledPrecedentRetriever
from phase1.adapters.privacy import LocalPrivacy, PresidioPrivacy
from phase1.adapters.repository import LocalGitRepository
from phase1.adapters.runtime import RuntimeAdapters
from phase1.adapters.signer import CosignCLISigner, LocalHMACSigner
from phase1.adapters.store import PostgresStore, SQLiteStore
from phase1.adapters.telemetry import safe_attributes


class Response:
    def __init__(self, payload: object, headers: dict | None = None):
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, *args):
        return json.dumps(self.payload).encode()


def test_runtime_factory_defaults_are_local(tmp_path):
    adapters = RuntimeAdapters.from_env(tmp_path, {})
    assert isinstance(adapters.store, SQLiteStore)
    assert isinstance(adapters.model, DeterministicModelGateway)
    assert isinstance(adapters.policy, LocalGatePolicy)
    assert isinstance(adapters.privacy, LocalPrivacy)
    assert isinstance(adapters.precedent, DisabledPrecedentRetriever)
    assert isinstance(adapters.identity, DevDirectoryIdentity)
    assert isinstance(adapters.repository, LocalGitRepository)
    assert isinstance(adapters.signer, LocalHMACSigner)


def test_store_and_signer_contracts(tmp_path):
    store = SQLiteStore(tmp_path / "state.sqlite")
    store.put("REQ-0001", {"value": 1}, at="2026-01-01T00:00:00Z")
    store.append_event("REQ-0001", "created", {}, at="2026-01-01T00:00:00Z")
    assert store.get("REQ-0001") == {"value": 1}
    assert store.next_id() == "REQ-0002"

    statement = {"subject": "test"}
    signer = LocalHMACSigner(b"secret")
    assert signer.verify(signer.sign(statement)) == statement


def test_postgres_store_contract_with_driver_mock(monkeypatch):
    rows = {}
    events = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            self.sql, self.params = " ".join(sql.split()), params
            if self.sql.startswith("INSERT INTO runs"):
                rows[params[0]] = json.loads(params[1])
            elif self.sql.startswith("INSERT INTO events"):
                events.append(params)

        def fetchone(self):
            if self.sql.startswith("SELECT id"):
                return (sorted(rows)[-1],) if rows else None
            if self.sql.startswith("SELECT body"):
                value = rows.get(self.params[0])
                return (value,) if value is not None else None
            raise AssertionError(self.sql)

        def fetchall(self):
            return [(rows[key],) for key in sorted(rows)]

    class Connection:
        def __init__(self):
            self.commits = 0

        def cursor(self):
            return Cursor()

        def commit(self):
            self.commits += 1

    connection = Connection()
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        types.SimpleNamespace(connect=lambda dsn: connection),
    )
    store = PostgresStore("postgresql://test")
    store.put("REQ-0001", {"value": 1}, at="2026-01-01T00:00:00Z")
    store.append_event("REQ-0001", "created", {"ok": True}, at="2026-01-01T00:00:00Z")
    assert store.get("REQ-0001") == {"value": 1}
    assert store.all() == [{"value": 1}]
    assert store.next_id() == "REQ-0002"
    assert len(events) == 1
    assert connection.commits == 3


def test_cosign_sign_and_verify_use_local_key_pair(tmp_path, monkeypatch):
    private_key = tmp_path / "cosign.key"
    public_key = tmp_path / "cosign.pub"
    private_key.write_text("private", encoding="utf-8")
    public_key.write_text("public", encoding="utf-8")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if "sign-blob" in command:
            bundle = Path(command[command.index("--bundle") + 1])
            bundle.write_text('{"verificationMaterial": {}}', encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("subprocess.run", run)
    signer = CosignCLISigner(key=str(private_key), public_key=str(public_key))
    envelope = signer.sign({"subject": "test"})
    assert signer.verify(envelope) == {"subject": "test"}
    assert "--tlog-upload=false" in commands[0]
    assert ["--key", str(private_key)] == commands[0][-3:-1]
    verify_key = commands[1][commands[1].index("--key") + 1]
    assert verify_key == str(public_key)


def test_model_gateway_captures_provenance(monkeypatch):
    payload = {
        "id": "call-1",
        "model": "provider/model-v2",
        "choices": [{"message": {"content": '{"type":"question","text":"Q?"}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
    }
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: Response(payload))
    result = LiteLLMModelGateway("http://litellm", "key", "alias").complete(
        [{"role": "user", "content": "hello"}], skill="rules"
    )
    assert result.model == "provider/model-v2"
    assert result.metadata["request_id"] == "call-1"
    assert result.metadata["usage"]["total_tokens"] == 7


def test_presidio_contract_uses_anonymized_result(monkeypatch):
    replies = iter(
        [
            [{"entity_type": "PERSON", "start": 0, "end": 3, "score": 0.9}],
            {"text": "<PERSON>"},
        ]
    )
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout: Response(next(replies))
    )
    assert PresidioPrivacy("http://analyzer", "http://anonymizer").screen("Ada") == "<PERSON>"


@pytest.mark.parametrize(
    ("gate", "outcome", "actor", "expected"),
    [
        (1, "approve", {"id": "po", "roles": ["product_owner"], "team": "p"}, True),
        (1, "approve", {"id": "author", "roles": ["product_owner"]}, False),
        (2, "approve", {"id": "x", "roles": ["developer"]}, False),
        (6, "reject", {"id": "author", "roles": ["business_stakeholder"]}, True),
        (7, "discard", {"id": "rm", "roles": ["release_manager"]}, False),
    ],
)
def test_local_policy_matches_gate_engine(gate, outcome, actor, expected):
    requirement = {"originator": {"id": "author"}, "gates": {}}
    verdict = LocalGatePolicy().evaluate(
        gate, outcome, actor, requirement, expected_gate=gate
    )
    try:
        gate_engine.evaluate(gate, outcome, actor, requirement, expected_gate=gate)
        reference = True
    except gate_engine.GateRefused:
        reference = False
    assert verdict.allowed is reference is expected


def test_opa_http_contract(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: Response({"result": {"allowed": False, "reason": "originator"}}),
    )
    verdict = OPAGatePolicy("http://opa").evaluate(
        1,
        "approve",
        {"id": "author", "roles": ["product_owner"]},
        {"originator": {"id": "author"}, "gates": {}},
        expected_gate=1,
    )
    assert verdict.allowed is False
    assert verdict.reason == "originator"


def test_rego_declares_every_gate_role_and_outcome():
    policy = (
        Path(__file__).resolve().parent.parent / "deploy/opa/governance.rego"
    ).read_text(encoding="utf-8")
    for number, definition in gate_engine.GATES.items():
        assert f"{number}:" in policy
        for value in definition.approver_roles | definition.outcomes:
            assert f'"{value}"' in policy
    for required_rule in (
        "input.gate != input.expected_gate",
        "count(entitled) == 0",
        "definition.excludes_originator",
        "actor has already signed gate",
        "count(denials) == 0",
    ):
        assert required_rule in policy


def test_rego_gate_definitions_are_exactly_in_python_parity():
    policy = (
        Path(__file__).resolve().parent.parent / "deploy/opa/governance.rego"
    ).read_text(encoding="utf-8")
    rows = re.findall(
        r'^\s*(\d+): \{"name": "[^"]+", "roles": \{([^}]*)\}, '
        r'"outcomes": \{([^}]*)\}, "excludes_originator": (true|false)\},$',
        policy,
        flags=re.MULTILINE,
    )

    def values(raw: str) -> set[str]:
        return set(re.findall(r'"([^"]+)"', raw))

    parsed = {
        int(number): (values(roles), values(outcomes), excludes == "true")
        for number, roles, outcomes, excludes in rows
    }
    expected = {
        number: (
            definition.approver_roles,
            definition.outcomes,
            definition.excludes_originator,
        )
        for number, definition in gate_engine.GATES.items()
    }
    assert parsed == expected


def test_telemetry_attributes_never_contain_raw_pii():
    safe = safe_attributes(
        {
            "request_text": "Ada at ada@example.com",
            "detail": "Call +1 212 555 0100",
            "requirement_id": "REQ-0001",
        }
    )
    serialized = json.dumps(safe)
    assert "Ada" not in serialized
    assert "ada@example.com" not in serialized
    assert "212 555 0100" not in serialized
    assert safe["requirement_id"] == "REQ-0001"


def test_github_commit_files_mirrors_the_remote(tmp_path, monkeypatch):
    from phase1.adapters.repository import GitHubAppRepository

    calls = []

    def urlopen(req, timeout=15):
        calls.append((req.get_method(), req.full_url))
        if req.get_method() == "GET" and req.full_url.endswith("/repos/acme/gov"):
            return Response({"default_branch": "main"})
        if req.get_method() == "GET" and req.full_url.endswith("/git/ref/heads/main"):
            return Response({"object": {"sha": "abc"}})
        if req.get_method() == "GET" and "/contents/" in req.full_url:
            raise RuntimeError("GitHub API GET /contents: 404 missing")
        if req.get_method() == "POST" and req.full_url.endswith("/git/refs"):
            return Response({"ref": "refs/heads/scope/REQ-0001"})
        return Response({"commit": {"sha": "def"}})

    monkeypatch.setattr("phase1.adapters.repository.request.urlopen", urlopen)
    repo = GitHubAppRepository(
        tmp_path, owner="acme", repo="gov", token="token", api_url="https://api.github.com"
    )
    repo.init()
    sha = repo.commit_files(
        "scope/REQ-0001",
        {"requirements/REQ-0001/scope/scope-report.md": "# Scope\n"},
        "scope report REQ-0001",
        author="intake",
        email="intake@local",
    )
    assert sha
    joined = " ".join(url for _, url in calls)
    assert "scope/REQ-0001" in joined or any("/contents/" in url for _, url in calls)


def test_github_promotes_non_main_default_branch(tmp_path, monkeypatch):
    from phase1.adapters.repository import GitHubAppRepository

    patched = []

    def urlopen(req, timeout=15):
        if req.get_method() == "GET" and req.full_url.endswith("/repos/acme/gov"):
            return Response({"default_branch": "req/old/requirement"})
        if req.get_method() == "GET" and "git/ref/heads/main" in req.full_url:
            raise RuntimeError("GitHub API GET /git/ref/heads/main: 404 missing")
        if req.get_method() == "GET" and "git/ref/heads/req/old/requirement" in req.full_url:
            return Response({"object": {"sha": "abc"}})
        if req.get_method() == "POST" and req.full_url.endswith("/git/refs"):
            return Response({"ref": "refs/heads/main"})
        if req.get_method() == "PATCH":
            patched.append(json.loads(req.data.decode()))
            return Response({"default_branch": "main"})
        return Response({"commit": {"sha": "def"}})

    monkeypatch.setattr("phase1.adapters.repository.request.urlopen", urlopen)
    repo = GitHubAppRepository(
        tmp_path, owner="acme", repo="gov", token="token", api_url="https://api.github.com"
    )
    repo._ensure_base()
    assert patched and patched[0]["default_branch"] == "main"
    assert repo._base_branch() == "main"


def test_github_targets_main_when_default_branch_cannot_be_patched(tmp_path, monkeypatch):
    from phase1.adapters.repository import GitHubAppRepository

    def urlopen(req, timeout=15):
        if req.get_method() == "GET" and "git/ref/heads/main" in req.full_url:
            raise RuntimeError("GitHub API GET /git/ref/heads/main: 404 missing")
        if req.get_method() == "GET" and req.full_url.endswith("/repos/acme/gov"):
            return Response({"default_branch": "req/old/requirement"})
        if req.get_method() == "GET" and "git/ref/heads/req/old/requirement" in req.full_url:
            return Response({"object": {"sha": "abc"}})
        if req.get_method() == "POST" and req.full_url.endswith("/git/refs"):
            return Response({"ref": "refs/heads/main"})
        if req.get_method() == "PATCH":
            raise RuntimeError("GitHub API PATCH : 403 Resource not accessible")
        return Response({"commit": {"sha": "def"}})

    monkeypatch.setattr("phase1.adapters.repository.request.urlopen", urlopen)
    repo = GitHubAppRepository(
        tmp_path, owner="acme", repo="gov", token="token", api_url="https://api.github.com"
    )
    repo._ensure_base()
    assert repo._base_branch() == "main"

