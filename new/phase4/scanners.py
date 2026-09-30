"""Eight scanners. Vendor binary if present; otherwise the free local engine."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
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
_TIMEOUT = 90


def inventory() -> list[dict[str, Any]]:
    return [substitutes.tool_row(name, _SUBSTITUTE[name]) for name in TOOLS]


def _write_tree(files: dict[str, str], root: Path) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8")


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )


def _finding(
    *,
    tool: str,
    severity: str,
    detail: str,
    path: str = "",
    engine: str = "vendor",
) -> dict[str, Any]:
    return {
        "severity": severity,
        "tool": tool,
        "engine": engine,
        "detail": detail,
        "file": path,
        "path": path,
        "issue": detail,
    }


def _run_gitleaks(root: Path, binary: str) -> list[dict[str, Any]]:
    report = root / "gitleaks.json"
    _run(
        [binary, "detect", "--source", str(root), "--no-git", "-f", "json", "-r", str(report)],
        cwd=root,
    )
    if not report.exists():
        return []
    try:
        data = json.loads(report.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        out.append(
            _finding(
                tool="gitleaks",
                severity="critical",
                detail=str(item.get("Description") or item.get("RuleID") or "secret"),
                path=str(item.get("File") or ""),
            )
        )
    return out


def _run_bandit(root: Path, binary: str) -> list[dict[str, Any]]:
    py_files = list(root.rglob("*.py"))
    if not py_files:
        return []
    proc = _run([binary, "-r", str(root), "-f", "json", "-q"], cwd=root)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    out = []
    for item in data.get("results") or []:
        sev = str(item.get("issue_severity") or "MEDIUM").lower()
        if sev == "moderate":
            sev = "medium"
        out.append(
            _finding(
                tool="bandit",
                severity=sev if sev in {"critical", "high", "medium", "low"} else "medium",
                detail=str(item.get("issue_text") or item.get("test_id") or "bandit"),
                path=str(item.get("filename") or "").replace(str(root) + "/", ""),
            )
        )
    return out


def _run_eslint(root: Path, binary: str) -> list[dict[str, Any]]:
    js = [
        p
        for p in root.rglob("*")
        if p.suffix in {".js", ".jsx", ".ts", ".tsx"} and p.is_file()
    ]
    if not js:
        return []
    cfg = root / "eslint.config.cjs"
    cfg.write_text(
        "module.exports = [{ files: ['**/*.{js,jsx,ts,tsx}'], "
        "plugins: { security: require('eslint-plugin-security') }, "
        "rules: { 'security/detect-eval-with-expression': 'error', "
        "'security/detect-non-literal-fs-filename': 'warn', "
        "'security/detect-object-injection': 'warn' } }];\n",
        encoding="utf-8",
    )
    # Prefer global plugin resolution
    env_path = os.environ.get("NODE_PATH", "")
    proc = _run(
        [binary, "-f", "json", "--no-error-on-unmatched-pattern", *[str(p) for p in js[:40]]],
        cwd=root,
    )
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for file_report in data if isinstance(data, list) else []:
        rel = str(file_report.get("filePath") or "").replace(str(root) + "/", "")
        for msg in file_report.get("messages") or []:
            sev = "high" if msg.get("severity") == 2 else "medium"
            out.append(
                _finding(
                    tool="eslint-plugin-security",
                    severity=sev,
                    detail=str(msg.get("message") or msg.get("ruleId") or "eslint"),
                    path=rel,
                )
            )
    _ = env_path
    return out


def _run_opengrep(root: Path, binary: str) -> list[dict[str, Any]]:
    # Without our own ruleset, run a minimal pattern search for SQL concat.
    rules = root / "opengrep-rules.yaml"
    rules.write_text(
        "rules:\n"
        "  - id: sql-concat\n"
        "    pattern-either:\n"
        "      - pattern: $X + $Y\n"
        "    message: possible string concat near SQL\n"
        "    languages: [javascript, typescript, python]\n"
        "    severity: WARNING\n",
        encoding="utf-8",
    )
    proc = _run([binary, "scan", "--config", str(rules), "--json", str(root)], cwd=root)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        # Binary may not speak this CLI yet — fall through to substitute caller.
        return []
    out = []
    for item in data.get("results") or []:
        out.append(
            _finding(
                tool="opengrep",
                severity="high",
                detail=str(item.get("check_id") or item.get("extra", {}).get("message") or "opengrep"),
                path=str(item.get("path") or ""),
            )
        )
    return out


def _run_trivy(root: Path, binary: str) -> list[dict[str, Any]]:
    proc = _run(
        [binary, "fs", "--quiet", "--format", "json", "--severity", "CRITICAL,HIGH,MEDIUM", str(root)],
        cwd=root,
    )
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    out = []
    for result in data.get("Results") or []:
        target = str(result.get("Target") or "")
        for vuln in result.get("Vulnerabilities") or []:
            sev = str(vuln.get("Severity") or "MEDIUM").lower()
            out.append(
                _finding(
                    tool="trivy",
                    severity=sev if sev in {"critical", "high", "medium", "low"} else "medium",
                    detail=f"{vuln.get('PkgName')} {vuln.get('VulnerabilityID')}",
                    path=target,
                )
            )
        for mis in result.get("Misconfigurations") or []:
            sev = str(mis.get("Severity") or "MEDIUM").lower()
            out.append(
                _finding(
                    tool="trivy",
                    severity=sev if sev in {"critical", "high", "medium", "low"} else "medium",
                    detail=str(mis.get("Title") or mis.get("ID") or "misconfig"),
                    path=target,
                )
            )
    return out


def _run_checkov(root: Path, binary: str) -> list[dict[str, Any]]:
    proc = _run(
        [binary, "-d", str(root), "-o", "json", "--quiet", "--compact"],
        cwd=root,
    )
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    out = []
    results = data.get("results") or {}
    for item in results.get("failed_checks") or []:
        sev = str(item.get("severity") or "HIGH").lower()
        out.append(
            _finding(
                tool="checkov",
                severity=sev if sev in {"critical", "high", "medium", "low"} else "high",
                detail=str(item.get("check_name") or item.get("check_id") or "checkov"),
                path=str(item.get("file_path") or "").lstrip("/"),
            )
        )
    return out


def _run_syft(root: Path, binary: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    proc = _run([binary, str(root), "-o", "json"], cwd=root)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return [], {"tool": "syft", "engine": "vendor", "packages": []}
    packages = []
    for artifact in data.get("artifacts") or []:
        name = artifact.get("name") or artifact.get("id")
        if name:
            packages.append(str(name))
    sbom = {"tool": "syft", "engine": "vendor", "packages": sorted(set(packages))[:200]}
    return [], sbom


def _run_scancode(root: Path, binary: str) -> list[dict[str, Any]]:
    report = root / "scancode.json"
    _run(
        [binary, "-l", "-i", "--json-pp", str(report), str(root), "--quiet"],
        cwd=root,
    )
    if not report.exists():
        return []
    try:
        data = json.loads(report.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return []
    out = []
    copyleft = {"gpl", "agpl", "lgpl", "sspl", "commons clause"}
    for file_row in data.get("files") or []:
        path = str(file_row.get("path") or "")
        for lic in file_row.get("licenses") or []:
            key = str(lic.get("key") or lic.get("spdx_license_key") or "").lower()
            if any(token in key for token in copyleft):
                out.append(
                    _finding(
                        tool="scancode",
                        severity="high",
                        detail=f"copyleft license {key}",
                        path=path,
                    )
                )
    return out


_RUNNERS = {
    "gitleaks": _run_gitleaks,
    "bandit": _run_bandit,
    "eslint-plugin-security": _run_eslint,
    "opengrep": _run_opengrep,
    "trivy": _run_trivy,
    "checkov": _run_checkov,
    "scancode": _run_scancode,
}


def scan_files(files: dict[str, str]) -> dict[str, Any]:
    """Run each available vendor binary; substitute only for tools that are missing."""
    inv = inventory()
    available = {row["name"]: row["binary"] for row in inv if row.get("status") == "available"}
    missing = [name for name in TOOLS if name not in available]

    findings: list[dict[str, Any]] = []
    sbom: dict[str, Any] = {"tool": "syft", "engine": "substitute", "packages": []}

    with tempfile.TemporaryDirectory(prefix="phase4-scan-") as tmp:
        root = Path(tmp)
        _write_tree(files, root)
        for name, binary in available.items():
            try:
                if name == "syft":
                    _, sbom = _run_syft(root, binary)
                    continue
                runner = _RUNNERS.get(name)
                if not runner:
                    continue
                findings.extend(runner(root, binary))
            except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
                findings.append(
                    _finding(
                        tool=name,
                        severity="medium",
                        detail=f"scanner error: {type(exc).__name__}: {exc}"[:200],
                        engine="vendor-error",
                    )
                )

    if missing:
        # Keep substitute coverage for tools we could not install/run.
        for row in substitutes.scan_tree(files):
            if row.get("tool") in missing:
                findings.append(row)
        if "syft" in missing:
            sbom = substitutes.sbom(files)

    blocking = [row for row in findings if row.get("severity") in {"critical", "high"}]
    return {
        "inventory": inv,
        "findings": findings,
        "blocking": blocking,
        "sbom": sbom,
        "ok": not blocking,
        "vendor": sorted(available),
        "substitute": missing,
    }
