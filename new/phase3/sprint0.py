"""Sprint 0 — ground the pipeline before any application code.

Brownfield against already-provisioned infrastructure is a no-op. Green is
earned by local policy (and Checkov when the binary is present), not by writing
a STATUS.md that says green.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from stack_profiles import StackProfile


_SECRET_MARKERS = (
    "password =",
    "secret =",
    "aws_secret_access_key",
    "begin private key",
    "akia",
)


def skip_sprint0(template: str) -> bool:
    return template == "brownfield_change"


def allowed_packages(profile: StackProfile) -> list[str]:
    if profile.id == "python":
        return ["fastapi", "sqlalchemy", "alembic", "pytest", "ruff", "psycopg"]
    return ["react", "express", "prisma", "vitest", "eslint", "prettier"]


def _ci(requirement_id: str, profile: StackProfile) -> str:
    test_cmd = "npx vitest run" if profile.tests == "Vitest" else "pytest -q"
    lint_cmd = "npx eslint ." if "ESLint" in profile.lint else "ruff check ."
    packages = " ".join(allowed_packages(profile))
    return "\n".join(
        [
            f"# Sprint 0 CI for {requirement_id} — {profile.id} profile",
            "name: governed-ci",
            "on: [push, pull_request]",
            "jobs:",
            "  allowlist:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v4",
            "      - run: test -s plan/sprint0/allowlist.txt",
            f"      - run: echo allowed {packages}",
            "  iac-scan:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v4",
            "      - run: if command -v checkov >/dev/null; then checkov -f plan/sprint0/main.tf; fi",
            "  verify:",
            "    needs: [allowlist, iac-scan]",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v4",
            f"      - run: {lint_cmd}",
            f"      - run: {test_cmd}",
            "",
        ]
    )


def _tofu(requirement_id: str) -> str:
    return "\n".join(
        [
            f"# OpenTofu — {requirement_id} single environment",
            'terraform { required_version = ">= 1.6.0" }',
            "variable \"uat_name\" { type = string }",
            'resource "null_resource" "uat" {',
            "  triggers = { name = var.uat_name }",
            "}",
            "",
        ]
    )


def _argo(requirement_id: str) -> str:
    return "\n".join(
        [
            "apiVersion: argoproj.io/v1alpha1",
            "kind: Application",
            "metadata:",
            f"  name: {requirement_id.lower()}-uat",
            "spec:",
            "  project: default",
            "  source:",
            "    repoURL: https://github.example/governed.git",
            "    path: deploy/uat",
            "    targetRevision: main",
            "  destination:",
            "    server: https://kubernetes.default.svc",
            "    namespace: uat",
            "  syncPolicy: {}",
            "",
        ]
    )


def _codeowners(reviewers: list[str]) -> str:
    owners = " ".join(f"@{person}" for person in reviewers) or "@tech-lead @stream-lead"
    return f"* {owners}\n"


def files(
    requirement_id: str,
    profile: StackProfile,
    *,
    reviewers: list[str] | None = None,
) -> dict[str, str]:
    prefix = f"requirements/{requirement_id}/plan/sprint0"
    allow = "\n".join(allowed_packages(profile)) + "\n"
    produced = {
        f"{prefix}/ci.yml": _ci(requirement_id, profile),
        f"{prefix}/allowlist.txt": allow,
        f"{prefix}/main.tf": _tofu(requirement_id),
        f"{prefix}/argo-application.yaml": _argo(requirement_id),
        f"{prefix}/CODEOWNERS": _codeowners(reviewers or []),
        f"{prefix}/BRANCH-PROTECTION.md": (
            "# Branch protection\n\n"
            "- `main` requires the `governed-ci` checks (allowlist, iac-scan, verify).\n"
            "- Force-push is refused.\n"
            "- Gate 4 reviewers are listed in CODEOWNERS.\n"
        ),
    }
    scan = scan_files(produced)
    status = "green" if not scan["blocking"] else "ungreen"
    produced[f"{prefix}/STATUS.md"] = _status(requirement_id, profile, status, scan)
    return produced


def scan_files(produced: dict[str, str]) -> dict[str, Any]:
    findings: list[str] = []
    tf = next((c for p, c in produced.items() if p.endswith("main.tf")), "")
    blob = "\n".join(produced.values()).lower()
    for marker in _SECRET_MARKERS:
        if marker in blob:
            findings.append(f"secret marker present: {marker}")
    if "required_version" not in tf:
        findings.append("OpenTofu file missing required_version")
    if "null_resource" in tf and "password" in tf.lower():
        findings.append("credentials inlined in OpenTofu")
    checkov = _checkov(produced)
    findings.extend(checkov)
    return {
        "tool": "checkov" if shutil.which("checkov") else "local-policy",
        "blocking": findings,
        "ok": not findings,
    }


def _checkov(produced: dict[str, str]) -> list[str]:
    binary = shutil.which("checkov")
    if not binary:
        return []
    tf = next((c for p, c in produced.items() if p.endswith(".tf")), "")
    if not tf:
        return []
    completed = subprocess.run(
        [binary, "-f", "-", "--quiet"],
        input=tf,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        text = (completed.stdout or completed.stderr or "checkov failed").strip()
        return [f"checkov: {text.splitlines()[0][:200]}"]
    return []


def _status(requirement_id: str, profile: StackProfile, status: str, scan: dict[str, Any]) -> str:
    blocked = "\n".join(f"- {item}" for item in scan["blocking"]) or "- none"
    return "\n".join(
        [
            f"# Sprint 0 — {requirement_id}",
            "",
            f"Status: **{status}**",
            f"Profile: `{profile.id}`",
            f"Scan: {scan['tool']}",
            "",
            "Pipeline, CODEOWNERS, allow-list CI, OpenTofu (UAT), Argo Application",
            "manifest, and branch-protection notes are in this folder. Secrets stay",
            "in the vault. The Argo file is not synced onto a cluster from here.",
            "",
            "## Blocking findings",
            "",
            blocked,
            "",
        ]
    )


def summary(profile: StackProfile, skipped: bool, *, scan: dict[str, Any] | None = None) -> dict[str, Any]:
    if skipped:
        return {"status": "skipped", "reason": "brownfield against provisioned infrastructure"}
    scan = scan or {"tool": "local-policy", "blocking": [], "ok": True}
    return {
        "status": "green" if scan.get("ok") else "ungreen",
        "profile": profile.id,
        "pipeline": "github-actions",
        "iac": "opentofu",
        "scan": scan.get("tool"),
        "allowlist": allowed_packages(profile),
        "preview": "local",
    }
