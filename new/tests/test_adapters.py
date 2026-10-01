"""Adapter contracts and local/remote parity without external services."""

from __future__ import annotations

import base64
import io
import json
import re
import sys
import types
from pathlib import Path
from urllib import error

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


def test_model_gateway_fetches_skill_then_returns_jsx(monkeypatch):
    replies = [
        {
            "id": "call-tool",
            "model": "provider/model-v2",
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "t1",
                                "function": {
                                    "name": "read_skill",
                                    "arguments": '{"name":"typography"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        },
        {
            "id": "call-jsx",
            "model": "provider/model-v2",
            "choices": [{"message": {"content": "function Page(){}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52},
        },
    ]

    def fake_open(req, timeout):
        return Response(replies.pop(0))

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    seen = []
    result = LiteLLMModelGateway("http://litellm", "key", "alias").complete(
        [{"role": "user", "content": "Login"}],
        skill="core rules",
        tools=[{"type": "function", "function": {"name": "read_skill"}}],
        execute_tool=lambda name, args: seen.append((name, args)) or "type scale",
    )
    assert result.text == "function Page(){}"
    assert result.metadata["tool_hops"] == 1
    assert seen == [("read_skill", {"name": "typography"})]


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


def test_model_gateway_surfaces_provider_http_error(monkeypatch):
    def boom(req, timeout):
        raise error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"Invalid model name"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", boom)
    try:
        LiteLLMModelGateway("http://litellm", "key", "alias").complete(
            [{"role": "user", "content": "hello"}],
            skill="rules",
            model="anthropic/claude-haiku-4.5",
        )
    except RuntimeError as exc:
        assert "anthropic/claude-haiku-4.5" in str(exc)
        assert "Invalid model name" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


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


def test_presidio_keeps_the_word_today(monkeypatch):
    replies = iter(
        [
            [{"entity_type": "DATE_TIME", "start": 0, "end": 5, "score": 0.8}],
        ]
    )
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout: Response(next(replies))
    )
    assert PresidioPrivacy("http://analyzer", "http://anonymizer").screen("today") == "today"


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


def _per_requirement_urlopen(calls, *, can_create=True):
    def urlopen(req, timeout=15):
        method, url = req.get_method(), req.full_url
        calls.append((method, url))
        if method == "GET" and url.endswith("/repos/acme/gov-req-0007"):
            raise RuntimeError("GitHub API GET : 404 missing")
        if method == "GET" and url.endswith("/user"):
            return Response({"login": "acme"})
        if method == "POST" and url.endswith("/user/repos"):
            if not can_create:
                raise RuntimeError("GitHub API POST /user/repos: 403 Resource not accessible")
            return Response({"name": "gov-req-0007"})
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return Response({"object": {"sha": "abc"}})
        if method == "GET" and "/contents/" in url:
            raise RuntimeError("GitHub API GET /contents: 404 missing")
        if method == "POST" and url.endswith("/git/refs"):
            return Response({"ref": "refs/heads/brd/REQ-0007"})
        return Response({"commit": {"sha": "def"}})

    return urlopen


def test_github_gives_each_requirement_its_own_private_repo(tmp_path, monkeypatch):
    from phase1.adapters.repository import GitHubAppRepository

    calls = []
    monkeypatch.setattr(
        "phase1.adapters.repository.request.urlopen", _per_requirement_urlopen(calls)
    )
    repo = GitHubAppRepository(
        tmp_path, owner="acme", repo="gov", token="t", api_url="https://api.github.com",
        per_requirement=True,
    )
    repo.init()
    repo.commit_files("brd/REQ-0007", {"brd.md": "# BRD\n"}, "brd", author="a", email="a@x")
    created = [c for c in calls if c == ("POST", "https://api.github.com/user/repos")]
    assert created
    puts = [url for method, url in calls if method == "PUT"]
    assert puts and all("/repos/acme/gov-req-0007/" in url for url in puts)
    assert repo.repo_for("skills/main") == "gov"


def test_github_keeps_the_commit_local_when_create_is_refused(tmp_path, monkeypatch):
    from phase1.adapters.repository import GitHubAppRepository

    calls = []
    monkeypatch.setattr(
        "phase1.adapters.repository.request.urlopen",
        _per_requirement_urlopen(calls, can_create=False),
    )
    repo = GitHubAppRepository(
        tmp_path, owner="acme", repo="gov", token="t", api_url="https://api.github.com",
        per_requirement=True,
    )
    repo.init()
    sha = repo.commit_files("brd/REQ-0007", {"brd.md": "# BRD\n"}, "brd", author="a", email="a@x")
    assert sha == repo.rev_parse("brd/REQ-0007")
    assert not [url for method, url in calls if method == "PUT"]
    assert not [url for _method, url in calls if "/repos/acme/gov/" in url or url.endswith("/repos/acme/gov")]


def test_per_requirement_mode_never_calls_the_shared_repo(tmp_path, monkeypatch):
    from phase1.adapters.repository import GitHubAppRepository

    calls = []
    monkeypatch.setattr(
        "phase1.adapters.repository.request.urlopen", _per_requirement_urlopen(calls)
    )
    repo = GitHubAppRepository(
        tmp_path, owner="acme", repo="gov", token="t", api_url="https://api.github.com",
        per_requirement=True,
    )
    repo.init()
    repo.commit_files("skills/main", {"skill.md": "x\n"}, "skill", author="a", email="a@x")
    repo.merge_to_main("skills/main", "merge skill")
    repo.delete_branch("skills/main")
    repo.commit_files("main", {"notes.md": "x\n"}, "note", author="a", email="a@x")
    repo.list_open_pull_requests()
    assert not [url for _method, url in calls if "/repos/acme/gov/" in url or url.endswith("/repos/acme/gov")]


def test_repo_name_includes_the_product_title(monkeypatch):
    monkeypatch.setenv("GITHUB_REPO", "ai-factory-governance")
    monkeypatch.setenv("GITHUB_REPO_PER_REQUIREMENT", "true")
    from phase1.catalog import github_repo_name

    assert github_repo_name("REQ-0001", "Travel Company") == "travel-company-req-0001"
    assert github_repo_name("REQ-0001", "Community Library Loan Manager") == (
        "community-library-loan-manager-req-0001"
    )
    assert github_repo_name("REQ-0001", "") == "ai-factory-governance-req-0001"
    assert github_repo_name("REQ-0005", "Requirement REQ-0005") == "requirement-req-0005"


def test_github_names_the_repo_after_the_product(tmp_path, monkeypatch):
    from phase1.adapters.repository import GitHubAppRepository

    calls = []

    def urlopen(req, timeout=15):
        method, url = req.get_method(), req.full_url
        calls.append((method, url, req.data))
        if method == "GET" and (
            url.endswith("/repos/acme/book-meeting-rooms-req-0007")
            or url.endswith("/repos/acme/gov-req-0007")
        ):
            raise RuntimeError("GitHub API GET : 404 missing")
        if method == "GET" and url.endswith("/user"):
            return Response({"login": "acme"})
        if method == "POST" and url.endswith("/user/repos"):
            return Response({"name": "book-meeting-rooms-req-0007"})
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return Response({"object": {"sha": "abc"}})
        if method == "GET" and "/contents/" in url:
            raise RuntimeError("GitHub API GET /contents: 404 missing")
        if method == "POST" and url.endswith("/git/refs"):
            return Response({"ref": "refs/heads/brd/REQ-0007"})
        return Response({"commit": {"sha": "def"}})

    monkeypatch.setattr("phase1.adapters.repository.request.urlopen", urlopen)
    repo = GitHubAppRepository(
        tmp_path, owner="acme", repo="gov", token="t", api_url="https://api.github.com",
        per_requirement=True,
    )
    repo.init()
    repo.note_title("REQ-0007", "Book meeting rooms")
    repo.commit_files("brd/REQ-0007", {"brd.md": "# BRD\n"}, "brd", author="a", email="a@x")
    created = [data for method, url, data in calls if method == "POST" and url.endswith("/user/repos")]
    assert created and b"book-meeting-rooms-req-0007" in created[0]
    puts = [url for method, url, _data in calls if method == "PUT"]
    assert puts and all("/repos/acme/book-meeting-rooms-req-0007/" in url for url in puts)


def test_github_renames_an_id_only_repo_once_the_title_is_known(tmp_path, monkeypatch):
    from phase1.adapters.repository import GitHubAppRepository

    patched = {}

    def urlopen(req, timeout=15):
        method, url = req.get_method(), req.full_url
        if method == "GET" and url.endswith("/repos/acme/book-meeting-rooms-req-0007"):
            raise RuntimeError("GitHub API GET : 404 missing")
        if method == "GET" and url.endswith("/repos/acme/gov-req-0007"):
            return Response({"name": "gov-req-0007"})
        if method == "PATCH" and url.endswith("/repos/acme/gov-req-0007"):
            patched["body"] = req.data
            return Response({"name": "book-meeting-rooms-req-0007"})
        return Response({})

    monkeypatch.setattr("phase1.adapters.repository.request.urlopen", urlopen)
    repo = GitHubAppRepository(
        tmp_path, owner="acme", repo="gov", token="t", api_url="https://api.github.com",
        per_requirement=True,
    )
    repo.note_title("REQ-0007", "Book meeting rooms")
    repo._ensure_repo(repo.repo_for("scope/REQ-0007"))
    assert patched and b"book-meeting-rooms-req-0007" in patched["body"]


def test_github_renames_a_generic_repo_once_the_website_name_is_known(tmp_path, monkeypatch):
    from phase1.adapters.repository import GitHubAppRepository

    patched = {}

    def urlopen(req, timeout=15):
        method, url = req.get_method(), req.full_url
        if method == "GET" and url.endswith("/repos/acme/book-meeting-rooms-req-0007"):
            raise RuntimeError("GitHub API GET : 404 missing")
        if method == "GET" and url.endswith("/repos/acme/gov-req-0007"):
            raise RuntimeError("GitHub API GET : 404 missing")
        if method == "GET" and url.endswith("/repos/acme/requirement-req-0007"):
            return Response({"name": "requirement-req-0007"})
        if method == "GET" and url.endswith("/user"):
            return Response({"login": "acme"})
        if method == "GET" and "/user/repos" in url:
            return Response([])
        if method == "PATCH" and url.endswith("/repos/acme/requirement-req-0007"):
            patched["body"] = req.data
            return Response({"name": "book-meeting-rooms-req-0007"})
        return Response({})

    monkeypatch.setattr("phase1.adapters.repository.request.urlopen", urlopen)
    repo = GitHubAppRepository(
        tmp_path, owner="acme", repo="gov", token="t", api_url="https://api.github.com",
        per_requirement=True,
    )
    repo.note_title("REQ-0007", "Book meeting rooms")
    repo._ensure_repo(repo.repo_for("scope/REQ-0007"))
    assert patched and b"book-meeting-rooms-req-0007" in patched["body"]


def test_github_readme_lands_on_the_named_repo_from_a_main_commit(tmp_path, monkeypatch):
    from phase1.adapters.repository import GitHubAppRepository

    puts = []

    def urlopen(req, timeout=15):
        method, url = req.get_method(), req.full_url
        if method == "PUT" and "/contents/" in url:
            puts.append((url, req.data))
            return Response({"commit": {"sha": "def"}})
        if method == "GET" and url.endswith("/repos/acme/library-req-0001"):
            return Response({"name": "library-req-0001", "default_branch": "main"})
        if method == "GET" and "git/ref/heads/main" in url:
            return Response({"object": {"sha": "abc"}})
        if method == "GET" and "/contents/" in url:
            raise RuntimeError("GitHub API GET /contents: 404 missing")
        if method == "GET" and url.endswith("/repos/acme/gov"):
            return Response({"default_branch": "main"})
        return Response({"commit": {"sha": "def"}})

    monkeypatch.setattr("phase1.adapters.repository.request.urlopen", urlopen)
    repo = GitHubAppRepository(
        tmp_path, owner="acme", repo="gov", token="t", api_url="https://api.github.com",
        per_requirement=True,
    )
    repo.init()
    repo.note_title("REQ-0001", "Library")
    readme = "# Library\n\nGate 3 — Design\n"
    repo.commit_files(
        "main",
        {"requirements/REQ-0001/README.md": readme},
        "status",
        author="a",
        email="a@x",
    )
    home = [item for item in puts if item[0].endswith("/repos/acme/library-req-0001/contents/README.md")]
    assert home
    assert not [url for url, _data in puts if "/repos/acme/gov/" in url]
    body = json.loads(home[-1][1].decode())
    assert "Gate 3" in base64.b64decode(body["content"]).decode()

