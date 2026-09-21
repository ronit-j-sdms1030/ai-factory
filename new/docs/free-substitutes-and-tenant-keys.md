# Free substitutes vs tenant keys

Substitutes **run real checks** and write artefacts. They label `status: substitute`. They do **not** claim Entra, AKS, E2B cloud, Argo sync, cluster Kyverno, or Rekor ran.

Live inventory: `GET /api/tooling`.

## Free substitutes (in the pipeline now)

| Architecture tool | Substitute that still moves the run |
|---|---|
| E2B microVM | Temp-dir sandbox + path allow-list |
| OpenHands / SWE-agent | Agentless stub (engine choice still logged) |
| Gitleaks | Secret-marker scan |
| Bandit | Python sink scan (`exec`, `pickle.loads`, …) |
| eslint-plugin-security | JS sink scan (`eval`, `innerHTML`, …) |
| Opengrep | SQL-concat scan |
| Trivy | Unpinned `*` dependency scan |
| Checkov | IaC public-ACL + migration down-script scan |
| Syft | Path list SBOM |
| ScanCode | AGPL / GPL-3 / Commons Clause string scan |
| GitHub Actions | Recorded `governed-build` workflow; runs when you push |
| OWASP ZAP | Preview HTML sink + CSP scan |
| Locust | Sequential HTTP samples + recorded locustfile |
| Argo CD UAT URL | `/preview/{id}` + committed `preview.html` |
| OpenFeature + flagd | Python evaluator on `flagd.json` (default **off**) |
| Kyverno | Local admission JSON: needs an attestation artefact |
| Cosign + Rekor | Local Cosign key, `tlog-upload=false` |
| Entra directory | Demo `directory.py` + `PHASE1_DEV_MODE` |
| Backstage + Entra | `GET /api/catalog` + compose `--profile backstage` |
| GitHub App | Personal `GITHUB_TOKEN` |
| Ollama | Point LiteLLM at local Ollama (`OLLAMA_API_BASE`) |

Critical/high substitute findings still **block** Gate 5. Missing a vendor binary no longer means “skip and pass”.

## Requires a tenant / vendor key (no honest substitute)

These stay recorded-only until the client (or you) provide the credential. The run still completes on substitutes above.

1. **Microsoft Entra ID** — SSO, groups, SCIM. Env: `IDENTITY_ADAPTER=oidc`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`.
2. **Azure Kubernetes Service (client cluster)** — production / UAT hostnames. Kube credentials.
3. **Argo CD on that cluster** — sync the Sprint 0 Application. `ARGO_SERVER`.
4. **Kyverno on that cluster** — enforce signed images at admit time. Cluster apply.
5. **E2B cloud** — hosted sandboxes. `E2B_API_KEY`.
6. **Sigstore Rekor / Fulcio** — public transparency log and OIDC certs. `COSIGN_CERTIFICATE_IDENTITY` + issuer.
7. **Backstage with Entra SSO** — production portal. Entra app registration.
8. **GitHub App org install** — installation tokens + org hooks. App ID, installation ID, private key. (PAT is the free stand-in.)
9. **Hosted OpenHands / SWE-agent** — `OPENHANDS_URL` / `SWE_AGENT`.
10. **Paid model APIs** — Azure OpenAI or paid OpenRouter models. `AZURE_API_KEY` or billed OpenRouter.

Keycloak can stand in for (1) on a laptop. kind/k3s can stand in for (2–4) on a laptop. Those are still *your* cluster, not the client tenant.
