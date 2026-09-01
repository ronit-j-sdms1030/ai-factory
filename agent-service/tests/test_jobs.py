"""Background generation jobs.

These exist because the behaviour they cover is invisible from the response:
the whole point of a job is that the request has already returned, so a bug
here shows up as a requirement that silently produced nothing — the exact
failure the job store was introduced to stop.
"""

from __future__ import annotations

import time

import mongomock
import pytest

from app import db, jobs


@pytest.fixture
def collection(monkeypatch):
    fake = mongomock.MongoClient().db.generationJobs
    monkeypatch.setattr(db, "generation_jobs", lambda: fake)
    return fake


def _settle(job_id: str, timeout: float = 3.0) -> dict:
    """Wait for the worker thread to finish and return the job."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = db.generation_jobs().find_one({"_id": job_id})
        if job and job["status"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never finished")


class TestOutcomes:
    def test_work_that_succeeds_is_marked_done(self, collection):
        job_id = jobs.start("art1", "brd", lambda progress: {})
        assert _settle(job_id)["status"] == "done"

    def test_errors_returned_by_work_fail_the_job(self, collection):
        """A generation that half-worked must not read as success.

        The BRD can generate while its pull request fails to open; flattening
        that to "done" is how a missing artefact goes unnoticed.
        """
        job = _settle(jobs.start("art1", "brd", lambda progress: {"brdError": "github down"}))
        assert job["status"] == "failed"
        assert job["errors"] == {"brdError": "github down"}

    def test_an_exception_is_recorded_rather_than_lost(self, collection):
        def explode(progress):
            raise RuntimeError("model refused")

        job = _settle(jobs.start("art1", "ui", explode))
        assert job["status"] == "failed"
        assert "model refused" in job["errors"]["ui"]

    def test_progress_updates_the_reported_phase(self, collection):
        def work(progress):
            progress("workitems")
            return {}

        job = _settle(jobs.start("art1", "ui", work))
        assert job["kind"] == "workitems"
        assert job["label"] == jobs.LABELS["workitems"]


class TestReaping:
    def test_a_job_from_a_dead_process_is_failed(self, collection):
        collection.insert_one(
            {"_id": "old", "artifactId": "art1", "kind": "brd",
             "status": "running", "process": "a-process-that-no-longer-exists"}
        )

        assert jobs.reap_stale() == 1
        reaped = collection.find_one({"_id": "old"})
        assert reaped["status"] == "failed"
        assert "restarted" in reaped["errors"]["interrupted"]

    def test_this_process_own_running_jobs_are_left_alone(self, collection):
        """Otherwise a restart-time sweep would kill work that is still going."""
        started = threading_safe_job(collection)
        assert jobs.reap_stale() == 0
        assert collection.find_one({"_id": started})["status"] == "running"


def threading_safe_job(collection) -> str:
    """A running job owned by this process, without starting a real thread."""
    collection.insert_one(
        {"_id": "mine", "artifactId": "art1", "kind": "brd",
         "status": "running", "process": jobs._PROCESS_ID}
    )
    return "mine"


class TestListing:
    def test_jobs_are_returned_newest_first(self, collection):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        for i, when in enumerate([now - timedelta(minutes=5), now, now - timedelta(minutes=1)]):
            collection.insert_one(
                {"_id": f"j{i}", "artifactId": "art1", "kind": "brd",
                 "status": "done", "startedAt": when, "errors": {}}
            )

        assert [j["jobId"] for j in jobs.for_artifact("art1")] == ["j1", "j2", "j0"]

    def test_only_this_artifact_is_returned(self, collection):
        collection.insert_one({"_id": "a", "artifactId": "art1", "status": "done", "startedAt": 1})
        collection.insert_one({"_id": "b", "artifactId": "art2", "status": "done", "startedAt": 2})

        assert [j["jobId"] for j in jobs.for_artifact("art1")] == ["a"]

    def test_active_kinds_reports_what_is_running(self, collection):
        threading_safe_job(collection)
        assert jobs.active_kinds("art1") == {"brd"}
