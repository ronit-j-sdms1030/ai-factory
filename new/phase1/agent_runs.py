"""Parse model output for later-gate agents. Invalid JSON keeps the template."""

from __future__ import annotations

from typing import Any

import department_routing as routing
from phase3 import decomposer
from stack_profiles import StackProfile


def tickets_from_model(
    data: dict[str, Any],
    requirement_id: str,
    profile: StackProfile,
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items = data.get("tickets")
    if not isinstance(items, list) or len(items) < 5:
        return fallback
    out: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not item.get("title"):
            return fallback
        title = str(item["title"])[:160]
        department = routing.department_for(title)
        paths = [str(p) for p in (item.get("paths") or []) if str(p).strip()]
        if not paths:
            paths = decomposer._paths(department, title, profile)
        if not paths:
            return fallback
        out.append(
            {
                "id": str(item.get("id") or f"{requirement_id}-W{index}"),
                "title": title,
                "depends_on": [str(x) for x in (item.get("depends_on") or [])][:8],
                "trace": [str(x) for x in (item.get("trace") or [])][:12],
                "department": department,
                "paths": paths,
            }
        )
    fallback_blob = " ".join(str(row.get("title") or "") for row in fallback).lower()
    for row in out:
        title = row["title"].lower()
        if "availab" in title and "availab" not in fallback_blob:
            return fallback
        if "booking" in title and "book" not in fallback_blob:
            return fallback
    return out


def tests_from_model(
    data: dict[str, Any],
    fallback: list[dict[str, Any]],
    framework: str,
) -> list[dict[str, Any]]:
    items = data.get("tests") or data.get("cases")
    if not isinstance(items, list) or len(items) < 4:
        return fallback
    out: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            return fallback
        row = {
            "id": str(item.get("id") or f"T{index:02d}"),
            "name": str(item["name"])[:160],
            "framework": framework,
            "criterion": str(item.get("criterion") or "scope"),
        }
        if item.get("critical"):
            row["critical"] = True
        out.append(row)
    if not any(case.get("critical") for case in out):
        out[2]["critical"] = True
    return out

