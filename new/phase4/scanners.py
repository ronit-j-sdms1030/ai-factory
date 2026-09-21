"""Eight scanners. Vendor binary if present; otherwise the free local engine."""

from __future__ import annotations

from typing import Any

import substitutes

# Architecture §5: Gitleaks, Bandit, eslint-plugin-security, Opengrep,
# Trivy, Checkov, Syft, ScanCode.
TOOLS = (
    "gitleaks",
    "bandit",
    "eslint-plugin-security",
    "opengrep",
    "trivy",
    "checkov",
    "syft",
    "scancode",
)

_SUBSTITUTE = {
    "gitleaks": "secret-marker scan",
    "bandit": "Python sink scan",
    "eslint-plugin-security": "JS sink scan",
    "opengrep": "SQL concat scan",
    "trivy": "unpinned-dependency scan",
    "checkov": "IaC + migration down-script scan",
    "syft": "path SBOM",
    "scancode": "copyleft string scan",
}

_SECRET_MARKERS = substitutes._SECRET


def inventory() -> list[dict[str, Any]]:
    return [substitutes.tool_row(name, _SUBSTITUTE[name]) for name in TOOLS]


def scan_files(files: dict[str, str]) -> dict[str, Any]:
    findings = substitutes.scan_tree(files)
    blocking = [row for row in findings if row["severity"] in {"critical", "high"}]
    return {
        "inventory": inventory(),
        "findings": findings,
        "blocking": blocking,
        "sbom": substitutes.sbom(files),
        "ok": not blocking,
    }
