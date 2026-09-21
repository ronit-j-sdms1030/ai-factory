# Governed AI Factory — Phase 1

This directory implements the Intake & Requirements phase from
`docs/governed-sdlc-architecture.md`. The existing `../frontend` application is
the canonical product frontend. `phase1/workspace` is only a credential-free
runtime test harness; do not build product UI there.

## Current architecture

- `phase1/platform.py`: intake through Gate 4. Gate 3 approval writes the plan
  artefact — Sprint 0, department tickets with path allow-lists, QA tests
  before code, and an overview that gates nothing.
- `skill_registry.py` plus `skills/` and `skills/vendor/`: every Phase 1–3
  agent is compiled against a SkillFile bundle (factory-owned + pinned BMAD /
  addyosmani / wshobson SKILL.md). Bundles are snapshotted beside artefacts.
- Unique Gate 3 preview URL: `/preview/{requirement-id}` (local stand-in for
  an isolated preview VM). Sprint 0 writes CODEOWNERS, profile allow-list CI,
  OpenTofu, an Argo Application manifest, and a local/Checkov scan before it
  is marked green.
- `phase1/temporal_*`: durable workflow, updates, activities, and restart-safe
  gate timers.
- `phase1/adapters`: local and production adapters for PostgreSQL, LiteLLM,
  OPA, Presidio, Entra-compatible OIDC, GitHub App repositories, Cosign, and
  OpenTelemetry.
- `phase1/server.py`: bearer-authenticated HTTP API plus SSE. `/healthz` and
  `/api/tooling` are unauthenticated operational endpoints.
- `deploy/docker-compose.yml`: PostgreSQL/pgvector, Temporal, OPA, Presidio,
  API, worker, and local Cosign key initialization. LiteLLM and observability
  are opt-in profiles.

Git is authoritative for artefacts and attestations. PostgreSQL owns process
snapshots and events. Temporal owns durable execution and waiting.

## Local direct mode

Requires Python 3.12, Git, and the locked packages:

```bash
cd new
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.lock
PHASE1_DEV_MODE=1 .venv/bin/python -m phase1
```

Then in another terminal:

```bash
cd frontend
PHASE1_API_URL=http://127.0.0.1:8787 npm run dev
```

Open `http://localhost:5173`. Demo fills use `password123` with every gate
role in the login picker. Seed a finished G1–G7 run:

```bash
cd new
RUNTIME_ROOT=.runtime python3 scripts/demo_seed.py
```

Then sign in as Product Owner through Release Manager and walk Approvals.
Catalog: `/catalog.html`. Change request: `/change-request.html`.

The API and test harness listen on `http://127.0.0.1:8787`. Direct mode uses
SQLite, the deterministic model, local policy/privacy, and local Git. It is for
development and tests, not production identity or signing.

## Credential-free Compose

```bash
cd new
docker compose -f deploy/docker-compose.yml config
docker compose -f deploy/docker-compose.yml up --build
```

The default model is deterministic and requires no model credential. Compose
initializes a passwordless local-development Cosign key pair in the
`signing-data` volume and both API and worker sign with that key. HMAC is not
the Compose signing default. Containers run as UID/GID 10001; the image includes
Git, CA certificates, Cosign, and all locked Python runtime dependencies.

Use LiteLLM only when explicitly selected:

```bash
MODEL_ADAPTER=litellm \
LITELLM_MODEL=openai/gpt-4.1-mini \
OPENAI_API_KEY=... \
docker compose -f deploy/docker-compose.yml --profile litellm up --build
```

Azure OpenAI uses `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_API_VERSION`, and an
appropriate `LITELLM_MODEL`. A local Ollama gateway may use `OLLAMA_API_BASE`.

Free substitutes (scanners, DAST, UAT preview, local Cosign, demo login) vs
tools that need a tenant key: `docs/free-substitutes-and-tenant-keys.md`.
Operator inventory: `GET /api/tooling`.

Enable the Langfuse/OpenTelemetry support services with:

```bash
docker compose -f deploy/docker-compose.yml --profile observability up --build
```

## Client integration requirements

Production requires:

- Microsoft Entra ID issuer, audience, and JWKS URL; role/team claims must map
  to the gate roles. Set `IDENTITY_ADAPTER=oidc`, `OIDC_ISSUER`,
  `OIDC_AUDIENCE`, and `OIDC_JWKS_URL`.
- A GitHub Enterprise App installation with repository contents, pull request,
  review, and webhook access. Set `REPOSITORY_ADAPTER=github`, `GITHUB_OWNER`,
  `GITHUB_REPO`, `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID`, and
  `GITHUB_APP_PRIVATE_KEY`. Use `WEBHOOK_SECRET` for delivery verification.
- The canonical `../frontend` proxies `/api` and SSE through its Node server to
  this service. Set `PHASE1_API_URL` on the frontend server when the API is not
  at `http://127.0.0.1:8787`.
- The compatibility routes translate frontend auth, guided-intake, artifact,
  Gate 1/Gate 2, and BRD-edit contracts onto Phase 1 runs. Cookie/password
  sessions exist only with `PHASE1_DEV_MODE=1`; production uses Bearer/OIDC.
- `phase1/workspace` remains a runtime test harness, not a product UI.

## Verification

```bash
cd new
python scripts/check_phase1.py
```

The verifier runs static acceptance checks, Python compilation, Ruff when
installed, the full pytest suite (including adapters, HTTP, Temporal time-skip
and worker restart, PostgreSQL mock semantics, direct end-to-end, and
desktop/mobile frontend checks), Compose config validation, and OPA syntax when
the `opa` executable is installed.
