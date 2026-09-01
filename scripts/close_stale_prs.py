"""Close finished/orphaned governance pull requests and delete their branches.

Keeps only requirements still moving through the pipeline. Everything else is
either an orphan from a test run (its artifact no longer exists in Mongo) or a
requirement that already reached `approved`, whose pull requests are a record
rather than a gate.

Requirements named in KEEP are never touched, and the base branch is never
deleted.

    python scripts/close_stale_prs.py [--apply]

Without --apply it lists what it would close and writes nothing.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Artifact ids whose pull requests must stay open — anything mid-pipeline.
KEEP = {
    "6a968da2ee951e14aa09ae1a",  # StarkLogix — at fsd_review
}


def _env() -> tuple[str, str]:
    env_path = Path(__file__).resolve().parent.parent / "agent-service" / ".env"
    values = {}
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    token = values.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    repo = values.get("GITHUB_REPO") or os.environ.get("GITHUB_REPO", "")
    if not token or not repo:
        raise SystemExit("GITHUB_TOKEN and GITHUB_REPO must be set in agent-service/.env")
    return token, repo


def _api(token: str, path: str, method: str = "GET", body: dict | None = None):
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        return {"_error": f"{exc.code} {exc.reason}"}


def main(apply: bool) -> int:
    token, repo = _env()
    pulls = _api(token, f"/repos/{repo}/pulls?state=open&per_page=100")
    if isinstance(pulls, dict):
        raise SystemExit(f"could not list pull requests: {pulls.get('_error')}")

    closed = 0
    for pull in sorted(pulls, key=lambda p: p["number"]):
        branch = pull["head"]["ref"]
        if any(artifact_id in branch for artifact_id in KEEP):
            print(f"  keep   #{pull['number']:<3} {branch}")
            continue

        print(f"  close  #{pull['number']:<3} {branch}")
        closed += 1
        if not apply:
            continue

        result = _api(token, f"/repos/{repo}/pulls/{pull['number']}", "PATCH", {"state": "closed"})
        if isinstance(result, dict) and result.get("_error"):
            print(f"         ! close failed: {result['_error']}")
            continue
        # Deleting the branch is what actually clears the repo; the closed pull
        # request stays as a record either way.
        deleted = _api(token, f"/repos/{repo}/git/refs/heads/{branch}", "DELETE")
        if isinstance(deleted, dict) and deleted.get("_error"):
            print(f"         ! branch delete failed: {deleted['_error']}")

    print(f"\n{closed} pull request(s) {'closed' if apply else 'would be closed'}")
    if not apply and closed:
        print("Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(apply="--apply" in sys.argv))
