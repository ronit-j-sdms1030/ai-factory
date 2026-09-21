"""Typed ports used by the Phase 1 runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ContextManager, Protocol

import intake_skill


@dataclass(frozen=True)
class ModelCompletion:
    text: str
    model: str
    model_version: str
    prompt_version: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyVerdict:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class Precedent:
    document_id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class StoreAdapter(Protocol):
    def next_id(self) -> str: ...
    def put(self, requirement_id: str, body: dict[str, Any], *, at: str) -> None: ...
    def get(self, requirement_id: str) -> dict[str, Any] | None: ...
    def all(self) -> list[dict[str, Any]]: ...
    def append_event(
        self, requirement_id: str, kind: str, body: dict[str, Any], *, at: str
    ) -> None: ...


class ModelGateway(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        skill: str,
        model: str | None = None,
    ) -> ModelCompletion | str: ...


class GatePolicy(Protocol):
    def evaluate(
        self,
        gate: int,
        outcome: str,
        actor: dict[str, Any],
        requirement: dict[str, Any],
        *,
        expected_gate: int | None,
        required_signers: list[str] | None = None,
    ) -> PolicyVerdict: ...


class PrivacyAdapter(Protocol):
    def screen(self, text: str) -> str: ...


class PrecedentRetriever(Protocol):
    def search(
        self, query: str, *, limit: int = 5, filters: dict[str, Any] | None = None
    ) -> list[Precedent]: ...


class IdentityAdapter(Protocol):
    def actor(self, actor_id: str) -> dict[str, Any]: ...
    def freeze_chain(self, template: str) -> dict[str, list[dict[str, Any]]]: ...
    def validate_token(self, token: str) -> dict[str, Any]: ...


class RepositoryAdapter(Protocol):
    root: Any
    def init(self) -> None: ...
    def commit_files(
        self,
        branch: str,
        files: dict[str, str],
        message: str,
        *,
        author: str,
        email: str,
    ) -> str: ...
    def merge_to_main(self, branch: str, message: str) -> str: ...
    def read(self, rel: str, ref: str = "main") -> str: ...
    def exists(self, rel: str, ref: str = "main") -> bool: ...
    def skill(self) -> intake_skill.SkillFile: ...
    def brd_template(self) -> str: ...
    def create_branch(self, branch: str, *, base: str = "main") -> str: ...
    def commit_remote_file(
        self, branch: str, path: str, content: str, message: str
    ) -> str: ...
    def open_pull_request(self, branch: str, title: str, body: str = "") -> int: ...
    def merge_pull_request(self, number: int, message: str = "") -> str: ...


class SignerAdapter(Protocol):
    def sign(self, statement: dict[str, Any]) -> dict[str, Any]: ...
    def verify(self, envelope: dict[str, Any]) -> dict[str, Any]: ...


class TelemetryAdapter(Protocol):
    def span(
        self, name: str, attributes: dict[str, Any] | None = None
    ) -> ContextManager[Any]: ...
    def event(self, name: str, attributes: dict[str, Any] | None = None) -> None: ...
