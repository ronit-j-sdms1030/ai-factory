"""Free local stand-ins. They find real defects; they never claim the vendor tool ran."""

from __future__ import annotations

import re
import shutil
from typing import Any

_SECRET = (
    "password =",
    "secret =",
    "begin private key",
    "aws_secret_access_key",
    "akia",
    "ghp_",
    "sk-or-v1-",
)
_PYTHON_SINKS = ("exec(", "eval(", "pickle.loads", "yaml.load(", "os.system(")
_JS_SINKS = ("eval(", "innerhtml", "document.write(", "new function(")
_SQL = re.compile(r"(select|insert|delete).*\+|f[\"'].*(select|insert)", re.I)
_COPYLEFT = ("agpl-3", "gpl-3", "commons clause", "sspl")
_IAC_BAD = ("0.0.0.0/0", "acl = \"public-read\"", "disable_rollback")


def binary(name: str) -> str | None:
    if name == "eslint-plugin-security":
        return shutil.which("eslint")
    aliases = {
        "zap": ("zap-cli", "zap.sh", "zap"),
        "locust": ("locust",),
    }
    for candidate in aliases.get(name, (name,)):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def tool_row(name: str, substitute: str) -> dict[str, Any]:
    found = binary(name)
    if found:
        return {"name": name, "status": "available", "reason": "", "binary": found}
    return {
        "name": name,
        "status": "substitute",
        "reason": f"{name} missing; {substitute} runs instead",
        "binary": None,
    }


def scan_tree(files: dict[str, str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    blob = "\n".join(files.values())
    lowered = blob.lower()
    for marker in _SECRET:
        if marker in lowered:
            findings.append(
                {"severity": "critical", "tool": "gitleaks", "engine": "substitute", "detail": marker}
            )
    for path, content in files.items():
        text = content.lower()
        if path.endswith((".py", ".pyi")):
            for sink in _PYTHON_SINKS:
                if sink in text:
                    findings.append(
                        {
                            "severity": "high",
                            "tool": "bandit",
                            "engine": "substitute",
                            "detail": f"{path} {sink}",
                        }
                    )
        if path.endswith((".js", ".ts", ".tsx", ".jsx")):
            for sink in _JS_SINKS:
                if sink in text:
                    findings.append(
                        {
                            "severity": "high",
                            "tool": "eslint-plugin-security",
                            "engine": "substitute",
                            "detail": f"{path} {sink}",
                        }
                    )
        if _SQL.search(content):
            findings.append(
                {
                    "severity": "high",
                    "tool": "opengrep",
                    "engine": "substitute",
                    "detail": f"{path} concatenated SQL",
                }
            )
        if any(token in path.lower() for token in ("schema", "migration", "alembic", "prisma")):
            if "down" not in text and "rollback" not in text:
                findings.append(
                    {
                        "severity": "high",
                        "tool": "checkov",
                        "engine": "substitute",
                        "detail": f"{path} migration has no down-script",
                    }
                )
        for marker in _IAC_BAD:
            if marker in text:
                findings.append(
                    {
                        "severity": "high",
                        "tool": "checkov",
                        "engine": "substitute",
                        "detail": f"{path} {marker}",
                    }
                )
        if re.search(r"==\s*\*|version\s*=\s*\"\*\"", content):
            findings.append(
                {
                    "severity": "medium",
                    "tool": "trivy",
                    "engine": "substitute",
                    "detail": f"{path} unpinned dependency",
                }
            )
        for license_name in _COPYLEFT:
            if license_name in text:
                findings.append(
                    {
                        "severity": "high",
                        "tool": "scancode",
                        "engine": "substitute",
                        "detail": f"{path} {license_name}",
                    }
                )
    return findings


def sbom(files: dict[str, str]) -> dict[str, Any]:
    return {
        "tool": "syft",
        "engine": "substitute",
        "packages": sorted(files),
    }


def dast_html(html: str, preview_url: str) -> list[dict[str, Any]]:
    findings = []
    lowered = html.lower()
    if "eval(" in lowered or "innerhtml" in lowered:
        findings.append(
            {
                "severity": "high",
                "tool": "owasp-zap",
                "engine": "substitute",
                "detail": "dangerous sink in preview HTML",
            }
        )
    if html and "content-security-policy" not in lowered:
        findings.append(
            {
                "severity": "medium",
                "tool": "owasp-zap",
                "engine": "substitute",
                "detail": f"{preview_url} has no CSP in the snapshot",
            }
        )
    return findings


def evaluate_flag(document: dict[str, Any], flag: str) -> str:
    spec = ((document.get("flags") or {}).get(flag) or {})
    return str(spec.get("defaultVariant") or "off")
