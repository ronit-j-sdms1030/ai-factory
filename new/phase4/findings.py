"""DefectDojo substitute — de-dup findings and attach SLAs. Blocks on critical/high."""

from __future__ import annotations

from typing import Any

SLA_DAYS = {"critical": 1, "high": 7, "medium": 30, "low": 90}


def ingest(findings: list[dict[str, Any]], *, loc: int = 0) -> dict[str, Any]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in findings or []:
        key = (
            str(row.get("tool") or ""),
            str(row.get("rule") or row.get("title") or row.get("message") or ""),
            str(row.get("path") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        severity = str(row.get("severity") or "low").lower()
        unique.append(
            {
                **row,
                "severity": severity,
                "sla_days": SLA_DAYS.get(severity, 90),
            }
        )
    blocking = [row for row in unique if row["severity"] in {"critical", "high"}]
    lines = max(int(loc or 0), 1)
    return {
        "tool": "defectdojo",
        "status": "substitute",
        "findings": unique,
        "blocking": blocking,
        "duplicates_dropped": max(0, len(findings or []) - len(unique)),
        "ok": not blocking,
        "density_per_kloc": round(1000 * len(unique) / lines, 2),
        "loc": lines,
    }
