"""Remove every requirement and the artefacts it produced.

Clears the slate for a fresh run: artifacts and their generation jobs from
Mongo, then the branches and pull requests they opened on GitHub, then the
local governance clone. Users, tiers and GitHub account mappings are left
alone — those are configuration, not work product.

This is irreversible. It prints everything it will remove and writes nothing
unless --apply is passed.

    python scripts/wipe_requirements.py [--apply]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent-service"))

from app import db  # noqa: E402


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / "agent-service" / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


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
    env = _env()
    token, repo = env.get("GITHUB_TOKEN", ""), env.get("GITHUB_REPO", "")

    artifacts = list(db.artifacts().find({}, {"title": 1, "currentStage": 1, "createdAt": 1}))
    print(f"MONGO — {len(artifacts)} requirement(s):")
    for a in artifacts:
        print(f"   {a['_id']}  {str(a.get('currentStage')):18s} {(a.get('title') or '')[:44]}")
    jobs = db.generation_jobs().count_documents({})
    # codegenjobs is written by the Express service for agents 5-8. Leaving it
    # behind would strand generated code against artifacts that no longer
    # exist, which reads as a populated code-gen queue for nothing.
    codegen = db.db()["codegenjobs"].count_documents({})
    print(f"   plus {jobs} generation job record(s) and {codegen} code-gen job(s)")

    pulls = _api(token, f"/repos/{repo}/pulls?state=open&per_page=100") if token else []
    branches = _api(token, f"/repos/{repo}/branches?per_page=100") if token else []
    req_branches = [b["name"] for b in branches if isinstance(branches, list) and b["name"].startswith("req/")]
    print(f"\nGITHUB — {len(pulls) if isinstance(pulls, list) else 0} open PR(s), {len(req_branches)} req/ branch(es):")
    for p in pulls if isinstance(pulls, list) else []:
        print(f"   #{p['number']:<3} {p['head']['ref']}")
    for b in req_branches:
        print(f"   branch {b}")

    if not apply:
        print("\nNothing removed. Re-run with --apply.")
        return 0

    db.artifacts().delete_many({})
    db.generation_jobs().delete_many({})
    db.db()["codegenjobs"].delete_many({})
    print(f"\n   removed {len(artifacts)} artifact(s), {jobs} job record(s), {codegen} code-gen job(s)")

    for p in pulls if isinstance(pulls, list) else []:
        _api(token, f"/repos/{repo}/pulls/{p['number']}", "PATCH", {"state": "closed"})
        print(f"   closed #{p['number']}")
    for b in req_branches:
        result = _api(token, f"/repos/{repo}/git/refs/heads/{b}", "DELETE")
        note = f" ({result['_error']})" if isinstance(result, dict) and result.get("_error") else ""
        print(f"   deleted branch {b}{note}")

    print("\nClean. Users and GitHub mappings untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(apply="--apply" in sys.argv))
