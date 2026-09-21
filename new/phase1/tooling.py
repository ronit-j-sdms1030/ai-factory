"""Operator view of vendor tools vs free substitutes."""

from __future__ import annotations

from typing import Any

from phase4 import sandbox, scanners
from phase5 import dast, load

TENANT_KEYS = [
    {
        "tool": "Microsoft Entra ID",
        "env": "OIDC_ISSUER, OIDC_AUDIENCE, OIDC_JWKS_URL, IDENTITY_ADAPTER=oidc",
        "why": "Production SSO, groups, SCIM directory freeze",
    },
    {
        "tool": "Azure Kubernetes / client AKS",
        "env": "kube credentials, ARGO_SERVER",
        "why": "Real UAT + production canary sync",
    },
    {
        "tool": "Argo CD on that cluster",
        "env": "ARGO_SERVER",
        "why": "GitOps Application sync to a unique UAT hostname",
    },
    {
        "tool": "Kyverno on that cluster",
        "env": "cluster admin apply",
        "why": "Admission control that blocks unsigned images",
    },
    {
        "tool": "E2B cloud",
        "env": "E2B_API_KEY",
        "why": "Hosted microVMs with egress allow-list",
    },
    {
        "tool": "Sigstore Rekor / Fulcio",
        "env": "COSIGN_CERTIFICATE_IDENTITY, COSIGN_CERTIFICATE_OIDC_ISSUER",
        "why": "Public transparency log and OIDC-issued signing certs",
    },
    {
        "tool": "Backstage + Entra",
        "env": "Entra app registration",
        "why": "Production portal shell with SSO",
    },
    {
        "tool": "GitHub App (org install)",
        "env": "GITHUB_APP_ID, GITHUB_INSTALLATION_ID, GITHUB_APP_PRIVATE_KEY",
        "why": "Org webhooks and installation tokens; a PAT is the free stand-in",
    },
    {
        "tool": "OpenHands / SWE-agent hosted",
        "env": "OPENHANDS_URL, SWE_AGENT",
        "why": "Remote coding engines; agentless + local stub is the free stand-in",
    },
    {
        "tool": "Paid OpenRouter / Azure OpenAI models",
        "env": "OPENAI_API_KEY / AZURE_API_KEY",
        "why": "Billed tokens. Ollama or OpenRouter free-tier models stay local/cheap",
    },
]


def report() -> dict[str, Any]:
    _, sandbox_meta = sandbox.from_env()
    return {
        "sandbox": sandbox_meta,
        "scanners": scanners.inventory(),
        "dast": dast.scan("/preview/_"),
        "load": load.inventory(),
        "tenant_keys": TENANT_KEYS,
    }
