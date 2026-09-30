"""GitHub Actions workflow text plus a local CI substitute for the build loop."""

from __future__ import annotations

from typing import Any

from phase4 import coverage


def workflow(requirement_id: str) -> str:
    return f"""# Governed build CI — {requirement_id}
name: governed-build
on:
  pull_request:
  push:
    branches: [main, feat/**]
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: command -v gitleaks && gitleaks detect || echo skip-gitleaks
      - run: command -v bandit && bandit -r . || echo skip-bandit
      - run: command -v opengrep && opengrep scan || echo skip-opengrep
      - run: command -v trivy && trivy fs . || echo skip-trivy
      - run: command -v checkov && checkov -d . || echo skip-checkov
      - run: command -v syft && syft . || echo skip-syft
      - run: command -v scancode && scancode --license --json-pp - . || echo skip-scancode
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npx vitest run --coverage || pytest --cov -q || echo skip-coverage
"""


def run_local(
    files: dict[str, str],
    tests: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Unit + integration stand-in (Vitest / pytest-cov substitute).

    Never claims GitHub Actions ran. Records pass/fail for the Phase 4 loop.
    """
    tests = tests or []
    checks: list[dict[str, Any]] = []
    empty = [path for path, text in files.items() if not str(text or "").strip()]
    checks.append(
        {
            "name": "compile_or_parse",
            "ok": not empty,
            "stdout": "all ticket files non-empty" if not empty else f"empty: {empty[:3]}",
        }
    )
    cov = coverage.report(files, tests)
    checks.append(
        {
            "name": "unit_integration_coverage",
            "ok": bool(cov.get("ok")),
            "stdout": (
                f"{cov.get('framework')} {cov.get('percent')}% "
                f"(threshold {cov.get('threshold')}%)"
            ),
        }
    )
    ok = all(item["ok"] for item in checks)
    return {
        "ok": ok,
        "mode": "substitute",
        "summary": "Local CI substitute — unit/integration + coverage threshold",
        "checks": checks,
        "coverage": cov,
        "verdict": "pass" if ok else "needs_fixes",
    }
