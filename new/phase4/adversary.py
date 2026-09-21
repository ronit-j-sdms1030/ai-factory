"""Adversary Agent — assumes the artefact is wrong and tries to prove it."""

from __future__ import annotations

from typing import Any


def attack(files: dict[str, str], tests: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    defects = []
    tests = tests or []
    if tests and not any(case.get("critical") for case in tests):
        defects.append("no critical test case covers this change")
    for path, content in files.items():
        if "migration" in path.lower() or "schema" in path.lower():
            if "down" not in content.lower() and "rollback" not in content.lower():
                defects.append(f"{path} can be applied but not reversed")
        if "TODO" in content or "FIXME" in content:
            defects.append(f"{path} ships a known hole")
    return {"defects": defects, "ok": not defects}
