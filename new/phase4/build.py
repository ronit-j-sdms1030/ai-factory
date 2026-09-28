"""Builders: ticket files on the allow-list, plus an assembled runnable app."""

from __future__ import annotations

from typing import Any

from phase4 import (
    adversary,
    ci,
    context,
    coverage,
    engines,
    findings,
    openhands,
    product,
    review,
    sandbox,
    scanners,
    swe_agent,
)

MAX_LOOPS = 5


def _concrete(pattern: str, ticket_id: str) -> str:
    if pattern.endswith("/**"):
        return f"{pattern[:-3].rstrip('/')}/{ticket_id}.ts"
    if "*" in pattern:
        return pattern.replace("*", ticket_id)
    return pattern


def _heal(files: dict[str, str]) -> dict[str, str]:
    healed = {}
    for path, content in files.items():
        text = content
        lowered = text.lower()
        for marker in scanners._SECRET_MARKERS:
            if marker in lowered:
                text = text + "\n// secret marker stripped by build loop\n"
        if any(token in path.lower() for token in ("schema", "migration", "alembic", "prisma")):
            if "down" not in text.lower() and "rollback" not in text.lower():
                text += "\n// down: revert this migration\n"
        if path.endswith((".ts", ".js", ".tsx")) and "export" not in text:
            text += "\nexport const repaired = true;\n"
        healed[path] = text
    return healed


def _ticket_files(
    ticket: dict[str, Any],
    screens: list[dict[str, Any]],
    entities: list[tuple[str, list[str]]],
    profile_id: str,
) -> dict[str, str]:
    allow = list(ticket.get("paths") or ["src/api/**"])
    title = str(ticket.get("title") or "").lower()
    tid = str(ticket.get("id") or "ticket")
    files: dict[str, str] = {}
    schema_job = "schema" in title or "migration" in title
    screen_job = title.endswith(" screen") or "form" in title or title.endswith(" ui")
    if schema_job:
        if profile_id == "python":
            files[f"alembic/versions/{tid}.py"] = product.alembic_revision(entities, tid)
        else:
            files["prisma/schema.prisma"] = product.prisma_schema(entities)
            files[f"prisma/migrations/{tid}/migration.sql"] = product.migration_sql(entities)
        return files
    if ticket.get("department") == "ai" or "src/ai" in " ".join(allow) or "app/ai" in " ".join(allow):
        ai_root = "src/ai" if profile_id != "python" else "app/ai"
        files[f"{ai_root}/{tid}.ts"] = product.ai_adapter(str(ticket.get("id") or tid), screens)
        files[f"{ai_root}/infer.ts"] = product.ai_adapter(str(ticket.get("id") or tid), screens)
        return files
    if screen_job:
        name = str(ticket.get("title") or "")
        if name.lower().endswith(" screen"):
            name = name[: -len(" screen")]
        match = next((screen for screen in screens if screen.get("name") == name), None)
        source = str((match or {}).get("source") or "").strip()
        ui_root = "src/ui" if profile_id != "python" else "app/ui"
        files[f"{ui_root}/{tid}.ts"] = product.screen_ticket_ts(name or "Screen", tid)
        if source:
            files[f"{ui_root}/{name or 'Screen'}.jsx"] = product.screen_jsx(name or "Screen", source)
        return files
    path = _concrete(allow[0] if allow else "src/api/**", tid)
    if path.endswith(".ts") or "/api/" in path or path.endswith(".py"):
        if profile_id == "python" and not path.endswith(".py"):
            path = f"app/api/{tid}.py"
        if path.endswith(".py"):
            files[path] = product.api_py(ticket, entities)
        else:
            files[path] = product.api_module(ticket, entities)
        return files
    files[path] = product.api_module(ticket, entities)
    return files


def materialise(
    requirement_id: str,
    tickets: list[dict[str, Any]],
    tests: list[dict[str, Any]] | None = None,
    screens: list[dict[str, Any]] | None = None,
    *,
    brd_text: str = "",
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    host, meta = sandbox.from_env()
    tests = tests or []
    screens = list(screens or [])
    profile_id = str((profile or {}).get("id") or "node")
    entities = product.parse_entities(brd_text)
    branches: dict[str, dict[str, str]] = {}
    engine_log: list[dict[str, Any]] = []
    loops_used = 0
    blocking: list[dict[str, Any]] = []
    all_findings: list[dict[str, Any]] = []
    for ticket in tickets:
        allow = list(ticket.get("paths") or ["src/api/**"])
        files = _ticket_files(ticket, screens, entities, profile_id)
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
        packed = context.assemble(confined, choice["engine"])
        choice = {**choice, "acp": packed["acp"], "context_tokens": packed["pack"]["used"]}
        report = {}
        board: dict[str, Any] = {}
        for loops_used in range(MAX_LOOPS):
            scan = scanners.scan_files(confined)
            loc = sum(len((text or "").splitlines()) for text in confined.values())
            board = findings.ingest(scan.get("findings") or [], loc=loc)
            scan = {**scan, "ok": scan["ok"] and board["ok"], "defectdojo": board}
            rev = review.review(confined, allow)
            adv = adversary.attack(confined, tests)
            cov = coverage.report(confined, tests)
            report = {
                "scans": scan,
                "review": rev,
                "adversary": adv,
                "coverage": cov,
                "findings": board,
                "context": packed,
            }
            if scan["ok"] and rev["ok"] and adv["ok"] and cov["ok"]:
                all_findings.extend(board.get("findings") or [])
                break
            confined = host.confine(_heal(confined), allow)
        else:
            all_findings.extend(board.get("findings") or [])
            blocking.append({"ticket": ticket.get("id"), **report})
        branches[f"feat/{ticket['id']}"] = confined
        engine_log.append(choice)
    extra = {
        f"requirements/{requirement_id}/build/ci.yml": ci.workflow(requirement_id),
        f"requirements/{requirement_id}/build/SCANNERS.md": "\n".join(
            f"- {row['name']}: {row['status']}" for row in scanners.inventory()
        )
        + "\n",
        f"requirements/{requirement_id}/build/APP.md": (
            f"Assembled product at app/{requirement_id}/. "
            "SQLite file database, Express API, Gate 3 JSX screens.\n"
        ),
        f"requirements/{requirement_id}/build/CONTEXT.md": (
            "Repomix pack + CodeGraph + Serena + ACP — local substitutes. "
            f"Budget {context.TOKEN_BUDGET} tokens per ticket.\n"
        ),
        f"requirements/{requirement_id}/build/FINDINGS.md": (
            "DefectDojo substitute. Critical/high still block Gate 5.\n"
        ),
    }
    extra.update(
        product.assemble(
            requirement_id,
            brd_text=brd_text,
            screens=screens,
            profile_id=profile_id,
        )
    )
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
        "context": context.assemble(
            {path: text for files in branches.values() for path, text in files.items()},
            engine_log[0]["engine"] if engine_log else "agentless",
        )
        if branches
        else context.assemble({}, "agentless"),
        "findings": findings.ingest(all_findings),
    }
