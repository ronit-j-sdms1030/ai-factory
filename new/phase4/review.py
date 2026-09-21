"""Review Bench — correctness, security, architecture, quality."""

from __future__ import annotations

from typing import Any


def review(files: dict[str, str], allow: list[str]) -> dict[str, Any]:
    lanes = {
        "correctness": _correctness(files),
        "security": _security(files),
        "architecture": _architecture(files, allow),
        "quality": _quality(files),
    }
    findings = [item for lane in lanes.values() for item in lane]
    return {"lanes": lanes, "findings": findings, "ok": not findings}


def _correctness(files: dict[str, str]) -> list[str]:
    return [f"{path} is empty" for path, content in files.items() if not content.strip()]


def _security(files: dict[str, str]) -> list[str]:
    hits = []
    for path, content in files.items():
        lowered = content.lower()
        if "eval(" in lowered or "innerhtml" in lowered:
            hits.append(f"{path} uses a dangerous sink")
    return hits


def _architecture(files: dict[str, str], allow: list[str]) -> list[str]:
    from phase4.sandbox import _matches

    return [f"{path} is outside {allow}" for path in files if not _matches(path, allow)]


def _quality(files: dict[str, str]) -> list[str]:
    hits = []
    for path, content in files.items():
        if path.endswith((".ts", ".js", ".tsx")) and "export" not in content:
            hits.append(f"{path} has no export")
    return hits
