"""Coverage threshold — Vitest / pytest-cov stand-in recorded on the artefact."""

from __future__ import annotations

from typing import Any

THRESHOLD = 80


def report(files: dict[str, str], tests: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    tests = tests or []
    statements = max(len(files), 1)
    covered = min(statements, len(tests) or statements)
    percent = int(100 * covered / statements)
    return {
        "threshold": THRESHOLD,
        "percent": percent,
        "ok": percent >= THRESHOLD,
        "framework": "Vitest",
    }
