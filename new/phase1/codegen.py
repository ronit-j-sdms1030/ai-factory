"""Demo IDE code-generation jobs for the canonical frontend.

Progressively reveals assembler output (Gate 3 screens → API modules → DB)
so the old `/api/codegen/*` IDE can stream files without OpenHands.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from phase4 import product

_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_BY_ARTIFACT: dict[str, dict[str, str]] = {}  # artifactId -> {department: codeGenId}

MODELS = [
    {
        "id": "factory/assembler",
        "label": "Factory assembler (demo)",
        "isDefault": True,
    },
    {
        "id": "deepseek/deepseek-v3.2:nitro",
        "label": "DeepSeek V3.2 (demo label)",
        "isDefault": False,
    },
]

# Reveal one file every CHUNK_MS; stream partials every PARTIAL_MS within a file.
CHUNK_MS = 900
PARTIAL_MS = 280
CHARS_PER_PARTIAL = 180


def models() -> dict[str, Any]:
    return {"models": list(MODELS)}


def _model_label(model_id: str) -> str:
    for item in MODELS:
        if item["id"] == model_id:
            return str(item["label"])
    return model_id or "Factory assembler (demo)"


def _order_key(path: str) -> tuple[int, str]:
    """UI modules first, then API/AI, then DB/schema last (as the user expects)."""
    p = path.replace("\\", "/")
    if "/src/ui/" in p or p.endswith(".jsx"):
        return (0, p)
    if "/src/ai/" in p:
        return (1, p)
    if "/src/api/" in p:
        return (2, p)
    if p.endswith("package.json") or p.endswith("README.md"):
        return (3, p)
    if p.endswith("server.js") or p.endswith("infer.js") or p.endswith("infer.ts"):
        return (4, p)
    if "prisma/schema" in p:
        return (5, p)
    if "migration" in p or p.endswith(".sql"):
        return (6, p)
    if "/tests/" in p or p.endswith(".test.js"):
        return (7, p)
    return (4, p)


def _plan_files(row: dict[str, Any], department: str) -> list[dict[str, Any]]:
    rid = str((row.get("requirement") or {}).get("id") or row.get("requirement_id") or "")
    if not rid:
        rid = "REQ"
    screens = list(row.get("screens") or [])
    brd = str(row.get("brd_text") or "")
    profile = str((row.get("stack_profile") or {}).get("id") or "node")
    assembled = product.assemble(rid, brd_text=brd, screens=screens, profile_id=profile)

    # Strip app/{rid}/ prefix for IDE tree (matches old job paths).
    prefix = f"app/{rid}/"
    planned: list[dict[str, Any]] = []
    for full, content in assembled.items():
        rel = full[len(prefix) :] if full.startswith(prefix) else full
        # Filter by department stream when possible.
        if department == "ai" and "/src/ai/" not in rel and not rel.startswith("src/ai/"):
            if "infer" not in rel:
                continue
        if department == "development" and "/src/ai/" in rel:
            continue
        planned.append(
            {
                "path": rel,
                "description": _describe(rel),
                "content": content if isinstance(content, str) else str(content),
                "done": False,
            }
        )

    # Guarantee at least screens for development if assemble filtered oddly.
    if department in {"", "development", "delivery"} and not any(
        "/src/ui/" in f["path"] or f["path"].startswith("src/ui/") for f in planned
    ):
        for screen in screens:
            name = str(screen.get("name") or "Screen")
            source = product.neutralize_placeholder_tags(str(screen.get("source") or "").strip())
            if not source:
                continue
            planned.append(
                {
                    "path": f"src/ui/{name}.jsx",
                    "description": f"Gate 3 screen {name}",
                    "content": product.screen_jsx(name, source),
                    "done": False,
                }
            )

    planned.sort(key=lambda item: _order_key(item["path"]))
    # Deduplicate paths keeping first.
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in planned:
        if item["path"] in seen:
            continue
        seen.add(item["path"])
        out.append(item)
    return out


def _describe(path: str) -> str:
    if path.endswith(".jsx"):
        return "Gate 3 approved screen"
    if "migration" in path or path.endswith(".sql"):
        return "Database migration"
    if "schema.prisma" in path:
        return "Data model schema"
    if path.endswith("server.js"):
        return "API server"
    if "infer" in path:
        return "AI adapter"
    if path.endswith("package.json"):
        return "Package manifest"
    if path.endswith("README.md"):
        return "App readme"
    if ".test." in path:
        return "Unit test"
    return "Module"


def start(
    artifact_id: str,
    *,
    row: dict[str, Any],
    actor: dict[str, Any],
    model: str | None = None,
) -> dict[str, Any]:
    department = str(actor.get("department") or actor.get("team") or "development")
    model_id = model if model else MODELS[0]["id"]
    label = _model_label(model_id)

    with _LOCK:
        existing_map = _BY_ARTIFACT.setdefault(artifact_id, {})
        existing_id = existing_map.get(department)
        if existing_id and existing_id in _JOBS:
            job = _JOBS[existing_id]
            if job.get("status") != "error":
                return {
                    "codeGenId": existing_id,
                    "model": job.get("model"),
                    "modelLabel": job.get("modelLabel"),
                    "resumed": True,
                }

        files = _plan_files(row, department)
        code_gen_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        job = {
            "codeGenId": code_gen_id,
            "artifactId": artifact_id,
            "department": department,
            "model": model_id,
            "modelLabel": label,
            "status": "generating",
            "files": files,
            "log": [
                {
                    "ts": now,
                    "msg": f"Planning modules from Gate 3 screens + tickets ({label})…",
                }
            ],
            "totalFiles": len(files),
            "createdAt": now,
            "startedAt": now,
            "security": None,
            "reviewedAt": None,
            "ciAdded": False,
            "ciExecution": None,
            "tlApproved": False,
            "tlApprovedAt": None,
            "tlApprovedBy": None,
            "codeReview": None,
            "codeReviewedAt": None,
            "testExecution": None,
            "securityFixRunning": False,
            "_cursor": 0,
            "_partial": 0,
        }
        _JOBS[code_gen_id] = job
        existing_map[department] = code_gen_id
        job["log"].append(
            {
                "ts": now,
                "msg": f"Queued {len(files)} files — UI screens first, database last.",
            }
        )
        return {
            "codeGenId": code_gen_id,
            "model": model_id,
            "modelLabel": label,
        }


def _departments_for(row: dict[str, Any]) -> list[str]:
    depts: list[str] = []
    for report in row.get("team_reports") or row.get("teamReports") or []:
        team = str(report.get("team") or "").strip()
        if team and team not in depts:
            depts.append(team)
    for ticket in row.get("tickets") or []:
        team = str(ticket.get("department") or "").strip() or "development"
        if team not in depts:
            depts.append(team)
    return depts or ["development"]


def _awaiting(row: dict[str, Any]) -> int:
    raw = row.get("awaiting")
    if raw is None:
        raw = (row.get("requirement") or {}).get("awaiting")
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _scan_payload(row: dict[str, Any]) -> dict[str, Any]:
    build = row.get("build") or {}
    pipeline = dict(build.get("pipeline") or {})
    ok = bool(build.get("ok", True)) and not (build.get("blocking") or [])
    scans_raw = build.get("scans")
    inventory: list[dict[str, Any]] = []
    # Prefer live PATH so UI updates after binaries are installed into the image.
    try:
        from phase4 import scanners

        inventory = scanners.inventory()
    except Exception:
        inventory = []
    if not inventory:
        if isinstance(scans_raw, list):
            inventory = [item for item in scans_raw if isinstance(item, dict)]
        elif isinstance(scans_raw, dict):
            inventory = list(scans_raw.get("inventory") or [])
    if not inventory:
        inventory = list(pipeline.get("inventory") or [])
    if not inventory:
        inventory = [
            {"name": name, "status": "substitute"}
            for name in (
                "gitleaks",
                "bandit",
                "eslint-plugin-security",
                "opengrep",
                "trivy",
                "checkov",
                "syft",
                "scancode",
            )
        ]

    findings_board = build.get("findings") or {}
    if isinstance(findings_board, dict):
        raw_findings = list(findings_board.get("findings") or findings_board.get("blocking") or [])
    else:
        raw_findings = []
    if isinstance(scans_raw, dict) and not raw_findings:
        raw_findings = list(scans_raw.get("findings") or [])

    findings = []
    for item in raw_findings[:20]:
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                "severity": item.get("severity") or "medium",
                "tool": item.get("tool") or item.get("scanner") or item.get("engine") or "",
                "file": item.get("file") or item.get("path") or "",
                "line": item.get("line"),
                "issue": item.get("issue")
                or item.get("message")
                or item.get("detail")
                or item.get("rule")
                or "",
            }
        )

    missing = [t for t in inventory if str(t.get("status") or "") != "available"]
    if not inventory or len(missing) == len(inventory):
        scan_mode = "substitute"
        mode_label = "substitute (binaries missing)"
        scan_note = "Vendor binaries absent → substitutes."
    elif missing:
        scan_mode = "mixed"
        mode_label = "mixed (some substitutes)"
        names = ", ".join(str(t.get("name") or "?") for t in missing)
        scan_note = f"Vendor binaries for most scanners; substitutes for: {names}."
    else:
        scan_mode = "vendor"
        mode_label = "vendor"
        scan_note = "Vendor scanner binaries present."

    pipeline_summary = (
        "Phase 4 build loop (max 5): agents → local CI → eight scanners → "
        f"Review Bench ×4 → adversary → coverage. {scan_note}"
    )
    pipeline = {
        **pipeline,
        "inventory": inventory,
        "mode": scan_mode,
        "summary": pipeline_summary,
    }

    scanner_stages = [
        {
            "name": str(tool.get("name") or tool),
            "passed": ok,
            "skipped": False,
            "findingsCount": sum(
                1
                for f in findings
                if str(f.get("tool") or "").lower() == str(tool.get("name") or tool).lower()
            ),
            "mode": str(tool.get("status") or scan_mode),
        }
        for tool in inventory
    ]

    flowchart = []
    for ticket in pipeline.get("tickets") or []:
        for key in ("agents", "ci", "scans", "review", "adversary", "coverage"):
            stage = (ticket.get("stages") or {}).get(key) or {}
            if key == "scans":
                stage_mode = scan_mode
            elif key == "ci":
                stage_mode = "local CI"
            else:
                stage_mode = stage.get("mode") or stage.get("status") or stage.get("engine") or ""
            flowchart.append(
                {
                    "name": f"{key}",
                    "passed": bool(stage.get("ok")),
                    "skipped": False,
                    "findingsCount": 0 if stage.get("ok") else 1,
                    "mode": stage_mode,
                }
            )
        break

    tool_names = " · ".join(str(t.get("name") or t) for t in inventory)
    ci_checks = []
    for ticket in pipeline.get("tickets") or []:
        ci_stage = (ticket.get("stages") or {}).get("ci") or {}
        for check in ci_stage.get("checks") or []:
            ci_checks.append(check)
        for key, stage in (ticket.get("stages") or {}).items():
            if key == "ci":
                continue
            ci_checks.append(
                {
                    "name": f"pipeline:{key}",
                    "ok": bool(stage.get("ok")),
                    "stdout": stage.get("summary")
                    or stage.get("verdict")
                    or ("pass" if stage.get("ok") else "fail"),
                }
            )
        break
    if not ci_checks:
        ci_checks = [
            {
                "name": str(tool.get("name") or tool),
                "ok": ok,
                "stdout": str(tool.get("status") or tool.get("reason") or mode_label),
            }
            for tool in inventory
        ]

    return {
        "security": {
            "verdict": "pass" if ok else "needs_fixes",
            "findings": findings,
            "stages": flowchart or scanner_stages,
            "inventory": inventory,
            "mode": mode_label,
            "summary": (
                f"Phase 4 eight scanners ({mode_label}): {tool_names}. {scan_note}"
            ).strip(),
            "scanners": scans_raw if isinstance(scans_raw, dict) else {"inventory": inventory},
            "pipeline": pipeline,
        },
        "ciExecution": {
            "verdict": "pass" if ok else "needs_fixes",
            "summary": (
                f"Local CI + Review Bench x4 + adversary ({mode_label})"
            ),
            "checks": ci_checks,
            "mode": mode_label,
        },
        "ciAdded": True,
        "codeReview": {
            "verdict": "pass" if ok else "needs_fixes",
            "summary": "Review Bench lanes attached at Gate 4 build clearance",
        },
        "pipeline": pipeline,
    }



def ensure_merged_preview(
    artifact_id: str,
    *,
    git: Any,
    row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One IDE job for the assembled ``build/{id}`` tree — Gate 5 SE review.

    Ticket streams stay separate; this is the product Senior merges as one.
    """
    rid = str(artifact_id)
    job_id = f"merged-{rid}"
    branch = f"build/{rid}"
    with _LOCK:
        existing = _JOBS.get(job_id)
        if existing and existing.get("status") == "done" and existing.get("files"):
            # RLock allows status() while we hold _LOCK; still release before return.
            cached = status(job_id)
            if cached:
                return cached
            return {"codeGenId": job_id, "status": "done", "files": [], "totalFiles": 0}

    paths = list(git.list_files(branch)) if git is not None else []
    app_prefix = f"app/{rid}/"
    preferred = [
        path
        for path in paths
        if path.startswith(app_prefix)
        or path.startswith("src/")
        or path.startswith("prisma/")
        or path.startswith("public/")
    ]
    if not preferred:
        preferred = [
            path
            for path in paths
            if "/attestations/" not in path
            and (
                path.startswith(f"app/{rid}/")
                or path.startswith(f"requirements/{rid}/build/")
                or path.startswith("src/")
            )
        ]
    files: list[dict[str, Any]] = []
    for path in sorted(preferred, key=_order_key):
        try:
            content = git.read(path, branch)
        except Exception:
            continue
        if len(content) > 400_000:
            content = content[:400_000] + "\n/* truncated for Gate 5 review */\n"
        files.append(
            {
                "path": path,
                "description": "Merged build (Gate 5)",
                "content": content,
                "done": True,
            }
        )

    pipeline = ((row or {}).get("build") or {}).get("pipeline") or {}
    # Avoid full live scanner inventory here — that path is for status pages;
    # Gate 5 IDE only needs the assembled file tree.
    build = (row or {}).get("build") or {}
    scan = {
        "security": {
            "verdict": "pass" if build.get("ok", True) else "needs_fixes",
            "findings": list((build.get("findings") or {}).get("findings") or [])[:20]
            if isinstance(build.get("findings"), dict)
            else [],
            "stages": [],
            "mode": str(pipeline.get("mode") or "vendor"),
            "summary": str(pipeline.get("summary") or "Merged build preview"),
            "pipeline": pipeline,
        },
        "ciExecution": {
            "verdict": "pass" if build.get("ok", True) else "needs_fixes",
            "summary": "Gate 5 merged preview",
            "checks": [],
            "mode": str(pipeline.get("mode") or "vendor"),
        },
        "ciAdded": True,
        "pipeline": pipeline,
    }
    now = int(time.time() * 1000)
    job: dict[str, Any] = {
        "codeGenId": job_id,
        "artifactId": rid,
        "department": "merged",
        "status": "done" if files else "error",
        "model": "factory/merged-build",
        "modelLabel": f"{branch} — merged product",
        "files": files,
        "totalFiles": len(files),
        "createdAt": now,
        "startedAt": now,
        "log": [
            {
                "ts": now,
                "msg": (
                    f"Merged build on {branch}: {len(files)} files "
                    "(assembled app + ticket sources)."
                    if files
                    else f"No files on {branch} yet — Gate 4 build may still be writing."
                ),
            }
        ],
        "tlApproved": True,
        "tlApprovedAt": now,
        "tlApprovedBy": "gate5-preview",
        "ciAdded": True,
        "_gate4_scan": scan,
        "pipeline": pipeline,
    }
    job.update(scan)
    with _LOCK:
        _JOBS[job_id] = job
        _BY_ARTIFACT.setdefault(rid, {})["merged"] = job_id
    return status(job_id) or {
        "codeGenId": job_id,
        "status": job["status"],
        "files": [{"path": f["path"], "done": True} for f in files],
        "totalFiles": len(files),
        "modelLabel": job["modelLabel"],
        "department": "merged",
    }


def start_after_gate4(artifact_id: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    """Kick IDE stream jobs for every stream once Gate 4 build lands."""
    started: list[dict[str, Any]] = []
    scan = _scan_payload(row)
    for department in _departments_for(row):
        actor = {"department": department, "team": department, "id": "build-agent"}
        result = start(artifact_id, row=row, actor=actor)
        with _LOCK:
            job = _JOBS.get(result["codeGenId"])
            if job:
                job["_gate4_scan"] = scan
                if job.get("status") == "done":
                    job.update(scan)
                    job["tlApproved"] = True
                    job["tlApprovedAt"] = job.get("createdAt")
                    job["tlApprovedBy"] = "gate4-build"
        started.append(result)
    return started


def _advance(job: dict[str, Any]) -> None:
    if job.get("status") != "generating":
        return
    files: list[dict[str, Any]] = list(job.get("files") or [])
    if not files:
        job["status"] = "done"
        job["log"].append({"ts": int(time.time() * 1000), "msg": "No files to generate."})
        return

    started = job.get("startedAt")
    if started is None:
        started = job.get("createdAt") or 0
    elapsed = max(0, int(time.time() * 1000) - int(started))
    # How many partial ticks should have happened.
    ticks = elapsed // PARTIAL_MS
    cursor = 0
    partial = 0
    remaining = ticks
    for idx, item in enumerate(files):
        content = item.get("content") or ""
        need = max(1, (len(content) + CHARS_PER_PARTIAL - 1) // CHARS_PER_PARTIAL)
        if remaining >= need:
            remaining -= need
            item["done"] = True
            item.pop("partialContent", None)
            cursor = idx + 1
            partial = 0
            continue
        # Currently writing this file.
        cursor = idx
        partial = int(remaining)
        written = min(len(content), (partial + 1) * CHARS_PER_PARTIAL)
        item["done"] = False
        item["partialContent"] = content[:written]
        for later in files[idx + 1 :]:
            later["done"] = False
            later.pop("partialContent", None)
        break
    else:
        cursor = len(files)
        partial = 0

    job["_cursor"] = cursor
    job["_partial"] = partial

    done_count = sum(1 for item in files if item.get("done"))
    if done_count >= len(files):
        job["status"] = "done"
        job["log"].append(
            {
                "ts": int(time.time() * 1000),
                "msg": f"Done — {len(files)} modules generated (DB schema last).",
            }
        )
        scan = job.get("_gate4_scan")
        if isinstance(scan, dict):
            job.update(scan)
            job.setdefault("tlApproved", True)
            job.setdefault("tlApprovedAt", int(time.time() * 1000))
            job.setdefault("tlApprovedBy", "gate4-build")
        return

    # Keep log fresh without flooding.
    current = files[min(cursor, len(files) - 1)]
    msg = f"Generating {current['path']} ({done_count + 1}/{len(files)})…"
    log = list(job.get("log") or [])
    if not log or log[-1].get("msg") != msg:
        log.append({"ts": int(time.time() * 1000), "msg": msg})
        job["log"] = log[-40:]


def status(code_gen_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(code_gen_id)
        if not job:
            return None
        _advance(job)
        files_out = []
        for item in job.get("files") or []:
            entry = {
                "path": item["path"],
                "description": item.get("description") or "",
                "done": bool(item.get("done")),
                "findingsCount": 0,
            }
            if not item.get("done") and item.get("partialContent") is not None:
                entry["partialContent"] = item["partialContent"]
            files_out.append(entry)
        return {
            "codeGenId": code_gen_id,
            "status": job.get("status"),
            "model": job.get("model"),
            "modelLabel": job.get("modelLabel"),
            "totalFiles": job.get("totalFiles") or len(files_out),
            "files": files_out,
            "log": list(job.get("log") or []),
            "security": job.get("security"),
            "reviewedAt": job.get("reviewedAt"),
            "ciAdded": bool(job.get("ciAdded")),
            "ciExecution": job.get("ciExecution"),
            "tlApproved": bool(job.get("tlApproved")),
            "tlApprovedAt": job.get("tlApprovedAt"),
            "tlApprovedBy": job.get("tlApprovedBy"),
            "codeReview": job.get("codeReview"),
            "codeReviewedAt": job.get("codeReviewedAt"),
            "testExecution": job.get("testExecution"),
            "securityFixRunning": bool(job.get("securityFixRunning")),
            "department": job.get("department"),
            "artifactId": job.get("artifactId"),
            "createdAt": job.get("createdAt"),
        }


def get_file(code_gen_id: str, path: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(code_gen_id)
        if not job:
            return None
        _advance(job)
        for item in job.get("files") or []:
            if item.get("path") == path:
                return {
                    "path": path,
                    "content": item.get("content") or "",
                    "done": bool(item.get("done")),
                    "findings": [],
                }
        return None


def put_file(code_gen_id: str, path: str, content: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(code_gen_id)
        if not job:
            return None
        for item in job.get("files") or []:
            if item.get("path") == path:
                item["content"] = content
                item["done"] = True
                item.pop("partialContent", None)
                return {"path": path, "content": content, "done": True, "findings": []}
        return None


def by_artifact(artifact_id: str, *, row: dict[str, Any] | None = None) -> dict[str, Any]:
    # After Gate 4 build, IDE jobs should already be running. If the status
    # page polls before/without that kick (e.g. process restart), start now.
    if row and (row.get("build") or _awaiting(row) >= 5):
        existing = _BY_ARTIFACT.get(artifact_id) or {}
        if not existing:
            start_after_gate4(artifact_id, row)

    with _LOCK:
        modules = []
        for department, code_gen_id in (_BY_ARTIFACT.get(artifact_id) or {}).items():
            job = _JOBS.get(code_gen_id)
            if not job:
                continue
            _advance(job)
            files = list(job.get("files") or [])
            done = sum(1 for item in files if item.get("done"))
            total = len(files) or int(job.get("totalFiles") or 0) or 1
            status_name = str(job.get("status") or "not_started")
            pct = 100 if status_name == "done" else int(done * 100 / total)
            modules.append(
                {
                    "department": department,
                    "codeGenId": code_gen_id,
                    "status": status_name,
                    "model": job.get("model"),
                    "modelLabel": job.get("modelLabel"),
                    "totalFiles": total,
                    "filesDone": done,
                    "progressPct": pct,
                    "createdAt": job.get("createdAt"),
                    "security": job.get("security"),
                    "ciAdded": bool(job.get("ciAdded")),
                    "ciExecution": job.get("ciExecution"),
                    "tlApproved": bool(job.get("tlApproved")),
                    "tlApprovedAt": job.get("tlApprovedAt"),
                    "tlApprovedBy": job.get("tlApprovedBy"),
                    "codeReview": job.get("codeReview"),
                    "testExecution": job.get("testExecution"),
                    "securityFixRunning": bool(job.get("securityFixRunning")),
                    "hasFrontend": any(
                        str(item.get("path") or "").endswith(".jsx") for item in files
                    ),
                }
            )
        return {
            "modules": modules,
            "allDone": bool(modules) and all(m["status"] == "done" for m in modules),
            "projectDemoReady": False,
            "projectDemoGeneratedAt": None,
        }


def enrich_team_reports(
    reports: list[dict[str, Any]],
    *,
    tickets: list[dict[str, Any]] | None = None,
    stack_profile: dict[str, Any] | None = None,
    screens: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Shape team reports so the overview modal finds plan + techStack."""
    profile = stack_profile or {}
    tech_stack = [
        {
            "layer": "frontend",
            "choice": profile.get("frontend") or "React",
            "rationale": profile.get("lint") or "Gate 3 locked profile",
        },
        {
            "layer": "api",
            "choice": profile.get("api") or "Express",
            "rationale": profile.get("data_access") or "Same process as UI",
        },
        {
            "layer": "database",
            "choice": profile.get("database") or "PostgreSQL",
            "rationale": profile.get("migrations") or "Migrations in profile",
        },
    ]
    screen_names = [str(s.get("name") or "") for s in (screens or []) if s.get("name")]
    by_team: dict[str, list[dict[str, Any]]] = {}
    for ticket in tickets or []:
        dept = str(ticket.get("department") or "development")
        by_team.setdefault(dept, []).append(ticket)

    def _plan_from_tickets(team_tickets: list[dict[str, Any]], fallback: str) -> list[dict[str, Any]]:
        if not team_tickets:
            return [{"phase": "Awaiting plan", "description": fallback}]
        return [
            {
                "phase": str(t.get("id") or f"W{i+1}"),
                "description": str(t.get("title") or ""),
                "tasks": [
                    f"paths: {', '.join(t.get('paths') or []) or 'n/a'}",
                    f"depends: {', '.join(t.get('depends_on') or []) or 'none'}",
                ],
            }
            for i, t in enumerate(team_tickets[:20])
        ]

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for report in reports or []:
        team = str(report.get("team") or "development")
        seen.add(team)
        item = dict(report)
        item.setdefault("objective", item.get("summary") or f"{team} stream package")
        item.setdefault("architecture", item.get("contract") or "")
        # Always refresh stack shape so rationale is present for the markdown renderer.
        item["techStack"] = tech_stack
        team_tickets = by_team.get(team) or []
        if not item.get("plan"):
            item["plan"] = _plan_from_tickets(
                team_tickets,
                str(item.get("summary") or "Build modules after Gate 4 plan lands"),
            )
        if team == "development" and screen_names:
            item.setdefault("screens", screen_names)
        if team_tickets and not item.get("tickets"):
            item["tickets"] = [str(t.get("id") or "") for t in team_tickets]
        out.append(item)

    for team, team_tickets in by_team.items():
        if team in seen:
            continue
        out.append(
            {
                "team": team,
                "objective": f"{len(team_tickets)} tickets for {team} in one assembled app",
                "architecture": (
                    "Streams own folders in one deploy — not separate products."
                ),
                "techStack": tech_stack,
                "plan": _plan_from_tickets(team_tickets, f"{team} work"),
                "tickets": [str(t.get("id") or "") for t in team_tickets],
                "screens": screen_names if team == "development" else [],
            }
        )
    # No inventing a fake package before the plan exists — empty means empty.
    return out
