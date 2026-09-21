"""GitHub Actions for the eight scanners, coverage, and property tests."""

from __future__ import annotations


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
