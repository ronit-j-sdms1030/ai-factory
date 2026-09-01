"""Background generation jobs.

Generating a BRD, a set of screens or a decomposition takes minutes of model
time. Running that inside the approving HTTP request produced two distinct
failures, both observed on a real requirement:

* **The response was lost but the work was not.** The proxy gives up at 300s
  and returns 502 while the server carries on and finishes. The approver sees
  an error over work that succeeded, and the obvious reaction — approve again —
  is exactly wrong.
* **The failure was lost too.** Generation errors were returned in the
  response body and nowhere else, so when the response was discarded the
  artifact was left at ``approved`` with no BRD, no screens, no packages and
  no record of why. A requirement that silently produces nothing is worse than
  one that visibly fails.

So the transition commits and answers immediately, the generation runs here,
and this collection is the durable record of what happened. A job is a fact in
the database, not a value in a response.

**Mongo-first, deliberately.** The JavaScript code-generation pipeline started
with an in-process dictionary and lost generated code whenever the backend
restarted; the fix was to write through to Mongo and treat the cache as
disposable (see the note atop ``codegen.routes.js``). Starting there rather
than earning that lesson twice.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from . import db

log = logging.getLogger(__name__)

# Identifies this process's jobs. A job still marked "running" but stamped
# with a different process cannot be running any more — nothing survives a
# restart — so it is provably dead and can be reported as such.
_PROCESS_ID = uuid.uuid4().hex

# What a job is generating, and how to say so in a UI.
LABELS = {
    "brd": "Generating the BRD",
    "ui": "Generating the interface",
    "workitems": "Splitting into work items",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def start(artifact_id: str, kind: str, work: Callable[[Callable[[str], None]], dict[str, str]]) -> str:
    """Record a job and run ``work`` on a background thread.

    ``work`` is handed a ``progress`` callable it uses to say which phase it
    has reached, because one transition can trigger several generations in
    sequence — approving the final gate produces the screens and then the
    decomposition. Reporting "generating" for eight minutes with no further
    detail is not much better than reporting nothing.

    It returns the error dictionary the generation produced — empty when
    everything succeeded. That is stored on the job, so a partial failure (the
    BRD generated but its pull request did not open) is visible rather than
    being flattened into "done".
    """
    job_id = uuid.uuid4().hex
    db.generation_jobs().insert_one(
        {
            "_id": job_id,
            "artifactId": artifact_id,
            "kind": kind,
            "label": LABELS.get(kind, kind),
            "status": "running",
            "process": _PROCESS_ID,
            "startedAt": _utcnow(),
            "finishedAt": None,
            "errors": {},
        }
    )

    # Daemon, so shutdown is not held hostage by a model call. The job dies
    # with the process and `reap_stale` reports it as interrupted on the next
    # boot — which is true, and better than a job that claims to be running
    # forever.
    threading.Thread(target=_run, args=(job_id, kind, work), daemon=True).start()
    return job_id


def _run(job_id: str, kind: str, work: Callable[[Callable[[str], None]], dict[str, str]]) -> None:
    def progress(phase: str) -> None:
        db.generation_jobs().update_one(
            {"_id": job_id},
            {"$set": {"kind": phase, "label": LABELS.get(phase, phase)}},
        )

    try:
        errors = work(progress) or {}
        _finish(job_id, "failed" if errors else "done", errors)
    except Exception as exc:  # noqa: BLE001 — the job's whole purpose is to record this
        log.exception("generation job %s (%s) failed", job_id, kind)
        _finish(job_id, "failed", {kind: str(exc)})


def _finish(job_id: str, status: str, errors: dict[str, str]) -> None:
    db.generation_jobs().update_one(
        {"_id": job_id},
        {"$set": {"status": status, "errors": errors, "finishedAt": _utcnow()}},
    )


def for_artifact(artifact_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Jobs for one artifact, newest first."""
    found = db.generation_jobs().find({"artifactId": artifact_id}).sort("startedAt", -1).limit(limit)
    return [_public(job) for job in found]


def active_kinds(artifact_id: str) -> set[str]:
    """What is being generated right now — used to avoid starting it twice."""
    running = db.generation_jobs().find(
        {"artifactId": artifact_id, "status": "running", "process": _PROCESS_ID},
        {"kind": 1},
    )
    return {job["kind"] for job in running}


def reap_stale() -> int:
    """Fail jobs abandoned by a previous process. Called once at startup.

    Without this, a job interrupted by a restart stays "running" forever and
    the UI waits on something that will never finish.
    """
    result = db.generation_jobs().update_many(
        {"status": "running", "process": {"$ne": _PROCESS_ID}},
        {
            "$set": {
                "status": "failed",
                "errors": {"interrupted": "the agent service restarted while this was generating"},
                "finishedAt": _utcnow(),
            }
        },
    )
    if result.modified_count:
        log.warning("marked %d interrupted generation job(s) as failed", result.modified_count)
    return result.modified_count


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "jobId": job.get("_id"),
        "kind": job.get("kind"),
        "label": job.get("label"),
        "status": job.get("status"),
        "errors": job.get("errors") or {},
        "startedAt": job.get("startedAt"),
        "finishedAt": job.get("finishedAt"),
    }
