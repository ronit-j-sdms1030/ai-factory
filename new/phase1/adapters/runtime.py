"""Environment-driven construction of all Phase 1 adapters."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib import request

from phase1.adapters.identity import DevDirectoryIdentity, OIDCIdentity
from phase1.adapters.model import DeterministicModelGateway, LiteLLMModelGateway
from phase1.adapters.policy import LocalGatePolicy, OPAGatePolicy
from phase1.adapters.precedent import DisabledPrecedentRetriever, PgvectorPrecedentRetriever
from phase1.adapters.privacy import LocalPrivacy, PresidioPrivacy
from phase1.adapters.repository import GitHubAppRepository, LocalGitRepository
from phase1.adapters.signer import CosignCLISigner, LocalHMACSigner
from phase1.adapters.store import PostgresStore, SQLiteStore
from phase1.adapters.telemetry import NoOpTelemetry, OpenTelemetryAdapter
from phase1.adapters.protocols import (
    GatePolicy,
    IdentityAdapter,
    ModelGateway,
    PrecedentRetriever,
    PrivacyAdapter,
    RepositoryAdapter,
    SignerAdapter,
    StoreAdapter,
    TelemetryAdapter,
)


@dataclass
class RuntimeAdapters:
    store: StoreAdapter
    model: ModelGateway
    policy: GatePolicy
    privacy: PrivacyAdapter
    precedent: PrecedentRetriever
    identity: IdentityAdapter
    repository: RepositoryAdapter
    signer: SignerAdapter
    telemetry: TelemetryAdapter

    @classmethod
    def from_env(
        cls,
        root: Path,
        env: Mapping[str, str] | None = None,
        *,
        embed=None,
    ) -> "RuntimeAdapters":
        values = dict(os.environ if env is None else env)
        root = Path(root)

        database_url = values.get("DATABASE_URL", "")
        store: StoreAdapter = (
            PostgresStore(database_url)
            if values.get("STORE_ADAPTER", "sqlite").lower() == "postgres"
            else SQLiteStore(root / "state.sqlite")
        )

        if values.get("MODEL_ADAPTER", "deterministic").lower() == "litellm":
            model: ModelGateway = LiteLLMModelGateway(
                values["LITELLM_API_BASE"],
                values["LITELLM_MASTER_KEY"],
                values["LITELLM_MODEL"],
        timeout=float(values.get("MODEL_TIMEOUT_SECONDS", "180")),
                root=root,
            )
        else:
            model = DeterministicModelGateway(root=root)

        policy: GatePolicy = (
            OPAGatePolicy(values["OPA_URL"])
            if values.get("POLICY_ADAPTER", "local").lower() == "opa"
            else LocalGatePolicy()
        )
        privacy: PrivacyAdapter = (
            PresidioPrivacy(
                values["PRESIDIO_ANALYZER_URL"],
                values["PRESIDIO_ANONYMIZER_URL"],
            )
            if values.get("PRIVACY_ADAPTER", "local").lower() == "presidio"
            else LocalPrivacy()
        )

        if values.get("PRECEDENT_ADAPTER", "disabled").lower() == "pgvector":
            embed = embed or _litellm_embedding(values)
            precedent: PrecedentRetriever = PgvectorPrecedentRetriever(
                database_url, embed, table=values.get("PRECEDENT_TABLE", "approved_brd_precedents")
            )
        else:
            precedent = DisabledPrecedentRetriever()

        identity: IdentityAdapter = (
            OIDCIdentity(
                values["OIDC_ISSUER"],
                values["OIDC_AUDIENCE"],
                values["OIDC_JWKS_URL"],
                role_claim=values.get("OIDC_ROLE_CLAIM", "roles"),
                team_claim=values.get("OIDC_TEAM_CLAIM", "team"),
            )
            if values.get("IDENTITY_ADAPTER", "dev").lower() == "oidc"
            else DevDirectoryIdentity()
        )

        if values.get("REPOSITORY_ADAPTER", "local").lower() == "github":
            token = values.get("GITHUB_TOKEN") or _github_app_token(values)
            repository: RepositoryAdapter = GitHubAppRepository(
                root / "governance",
                owner=values["GITHUB_OWNER"],
                repo=values["GITHUB_REPO"],
                token=token,
                api_url=values.get("GITHUB_API_URL", "https://api.github.com"),
                per_requirement=str(values.get("GITHUB_REPO_PER_REQUIREMENT", "")).lower()
                in {"1", "true", "yes"},
            )
        else:
            repository = LocalGitRepository(root / "governance")

        signer: SignerAdapter = (
            CosignCLISigner(
                key=values.get("COSIGN_KEY", ""),
                public_key=values.get("COSIGN_PUBLIC_KEY", ""),
                certificate_identity=values.get("COSIGN_CERTIFICATE_IDENTITY", ""),
                certificate_oidc_issuer=values.get("COSIGN_CERTIFICATE_OIDC_ISSUER", ""),
            )
            if values.get("SIGNER_ADAPTER", "hmac").lower() == "cosign"
            else LocalHMACSigner(
                values.get("SIGNING_SECRET", "dev-signing-key").encode()
            )
        )
        telemetry: TelemetryAdapter = (
            OpenTelemetryAdapter(values.get("OTEL_SERVICE_NAME", "governed-ai-factory"))
            if values.get("TELEMETRY_ADAPTER", "noop").lower() == "otel"
            else NoOpTelemetry()
        )
        return cls(store, model, policy, privacy, precedent, identity, repository, signer, telemetry)


def _litellm_embedding(env: Mapping[str, str]):
    base_url = env["LITELLM_API_BASE"].rstrip("/")
    api_key = env["LITELLM_MASTER_KEY"]
    model = env["EMBEDDING_MODEL"]

    def embed(text: str) -> list[float]:
        req = request.Request(
            f"{base_url}/v1/embeddings",
            data=json.dumps({"model": model, "input": text}).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=30) as response:
            payload = json.load(response)
        return [float(value) for value in payload["data"][0]["embedding"]]

    return embed


def _github_app_token(env: Mapping[str, str]) -> str:
    try:
        import jwt
    except ImportError as exc:
        raise RuntimeError("GitHub App authentication requires the 'identity' extra") from exc
    now = int(time.time())
    app_jwt = jwt.encode(
        {"iat": now - 60, "exp": now + 540, "iss": env["GITHUB_APP_ID"]},
        env["GITHUB_APP_PRIVATE_KEY"].replace("\\n", "\n"),
        algorithm="RS256",
    )
    url = (
        env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        + f"/app/installations/{env['GITHUB_INSTALLATION_ID']}/access_tokens"
    )
    req = request.Request(
        url,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=15) as response:
        return str(json.load(response)["token"])
