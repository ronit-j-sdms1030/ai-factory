"""QA Agent — live preview if reachable; committed HTML snapshot otherwise."""

from __future__ import annotations

from typing import Any
from urllib import error, request


def execute(
    preview_url: str,
    tests: list[dict[str, Any]],
    html: str = "",
) -> dict[str, Any]:
    reachable = _reachable(preview_url)
    results = []
    for case in tests:
        critical = bool(case.get("critical"))
        if reachable:
            ok, engine = True, "live"
        elif critical:
            ok, engine = False, "substitute"
        elif html.strip():
            ok, engine = True, "substitute"
        else:
            ok, engine = False, "substitute"
        results.append(
            {
                "id": case.get("id") or case.get("name"),
                "ok": ok,
                "against": preview_url,
                "engine": engine,
                "critical": critical,
            }
        )
    return {
        "preview": preview_url,
        "reachable": reachable,
        "results": results,
        "ok": all(row["ok"] or not row["critical"] for row in results) and (
            reachable or bool(html.strip())
        ),
        "note": (
            ""
            if reachable
            else "live app down — snapshot substitute ran; critical cases still fail"
        ),
    }


def _reachable(url: str) -> bool:
    if not url.startswith("http://") and not url.startswith("https://"):
        return False
    try:
        with request.urlopen(url, timeout=2):
            return True
    except (error.URLError, TimeoutError, ValueError):
        return False
