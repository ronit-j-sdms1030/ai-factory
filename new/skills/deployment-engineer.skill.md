# DevOps Sprint 0 — Deployment Engineer

Prepare the ground before any application code. This is a prerequisite, not a
work stream. Brownfield against already-provisioned infrastructure is a no-op.

## Must produce

- GitHub Actions workflow matching the locked stack profile
- Dependency allow-list check generated from that profile
- OpenTofu for one environment (UAT)
- CODEOWNERS for Gate 4 reviewers
- Argo CD Application manifest (sync is the client's cluster; the file is ours)
- Branch-protection notes (required checks, no force-push on `main`)
- Secrets named, never inlined — vault references only

## Must scan

- IaC is scanned before Sprint 0 is marked green (Checkov when installed;
  local policy otherwise). Critical/high findings keep the status ungreen.

## Must not

- Write application source
- Put credentials in Git
- Declare the pipeline green if the allow-list job or IaC scan failed
