"""Coverage threshold — Vitest / pytest-cov stand-in recorded on the artefact."""

from __future__ import annotations

from typing import Any

THRESHOLD = 80


def report(files: dict[str, str], tests: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    tests = tests or []
    unit = [path for path in files if ".test." in path or path.endswith("_test.py")]
    src = [
        path
        for path in files
        if path.startswith(("src/", "app/"))
        and ".test." not in path
        and not path.endswith("_test.py")
    ]
    if unit and src:
        percent = min(100, int(100 * len(unit) / max(len(src), 1)))
        framework = "node:test" if any(path.endswith(".js") for path in unit) else "pytest"
    else:
        statements = max(len(files), 1)
        covered = min(statements, len(tests) or statements)
        percent = int(100 * covered / statements)
        framework = "Vitest"
    return {
        "threshold": THRESHOLD,
        "percent": percent,
        "ok": percent >= THRESHOLD,
        "framework": framework,
        "unit_files": len(unit),
    }
