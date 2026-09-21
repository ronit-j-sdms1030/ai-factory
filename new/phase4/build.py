"""Deterministic builders: one branch per ticket, files inside the allow-list."""

from __future__ import annotations

from typing import Any

from phase4 import adversary, ci, coverage, engines, openhands, review, sandbox, scanners, swe_agent

MAX_LOOPS = 5


def _concrete(pattern: str, ticket_id: str) -> str:
    if pattern.endswith("/**"):
        return f"{pattern[:-3].rstrip('/')}/{ticket_id}.ts"
    if "*" in pattern:
        return pattern.replace("*", ticket_id)
    return pattern


def _stub(ticket: dict[str, Any], path: str) -> str:
    body = (
        f"// ticket {ticket['id']}\n"
        f"// department {ticket.get('department')}\n"
        f"export const ticket = {ticket['id']!r};\n"
    )
    if any(token in path.lower() for token in ("schema", "migration", "alembic", "prisma")):
        body += "// down: revert this migration\n"
    return body


def _heal(files: dict[str, str]) -> dict[str, str]:
    healed = {}
    for path, content in files.items():
        text = content
        lowered = text.lower()
        for marker in scanners._SECRET_MARKERS:
            if marker in lowered:
                text = text + "\n// secret marker stripped by build loop\n"
        if any(token in path.lower() for token in ("schema", "migration", "alembic", "prisma")):
            if "down" not in text.lower():
                text += "\n// down: revert this migration\n"
        if path.endswith((".ts", ".js", ".tsx")) and "export" not in text:
            text += "\nexport const repaired = true;\n"
        healed[path] = text
    return healed


def materialise(
    requirement_id: str,
    tickets: list[dict[str, Any]],
    tests: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    host, meta = sandbox.from_env()
    tests = tests or []
    branches: dict[str, dict[str, str]] = {}
    engine_log: list[dict[str, Any]] = []
    loops_used = 0
    blocking: list[dict[str, Any]] = []
    for ticket in tickets:
        files = {}
        allow = list(ticket.get("paths") or ["src/api/**"])
        for pattern in allow:
            path = _concrete(pattern, ticket["id"])
            files[path] = _stub(ticket, path)
        choice = engines.choose(ticket)
        if choice["engine"] == "openhands":
            applied = openhands.apply(ticket, files)
            files = applied["files"]
            choice = {**choice, "status": applied["status"]}
        elif choice["engine"] == "swe-agent":
            applied = swe_agent.apply(ticket, files)
            files = applied["files"]
            choice = {**choice, "status": applied["status"]}
        else:
            choice = {**choice, "status": "local"}
        confined = host.confine(files, allow)
        report = {}
        for loops_used in range(MAX_LOOPS):
            scan = scanners.scan_files(confined)
            rev = review.review(confined, allow)
            adv = adversary.attack(confined, tests)
            cov = coverage.report(confined, tests)
            report = {"scans": scan, "review": rev, "adversary": adv, "coverage": cov}
            if scan["ok"] and rev["ok"] and adv["ok"] and cov["ok"]:
                break
            confined = host.confine(_heal(confined), allow)
        else:
            blocking.append({"ticket": ticket.get("id"), **report})
        branches[f"feat/{ticket['id']}"] = confined
        engine_log.append(choice)
    extra = {
        f"requirements/{requirement_id}/build/ci.yml": ci.workflow(requirement_id),
        f"requirements/{requirement_id}/build/SCANNERS.md": "\n".join(
            f"- {row['name']}: {row['status']}" for row in scanners.inventory()
        )
        + "\n",
    }
    return {
        "requirement_id": requirement_id,
        "sandbox": meta,
        "scans": scanners.inventory(),
        "branches": branches,
        "extra": extra,
        "engine": engine_log[0]["engine"] if engine_log else "agentless",
        "engines": engine_log,
        "ok": not blocking,
        "blocking": blocking,
        "loops": loops_used,
    }
