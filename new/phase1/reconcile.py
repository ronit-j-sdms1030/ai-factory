"""GitHub missed-webhook reconciliation — reviews that never reached /api/webhook."""

from __future__ import annotations

import json
from typing import Any

from phase1 import webhook


def apply(platform) -> list[str]:
    git = platform.git
    list_prs = getattr(git, "list_open_pull_requests", None)
    list_reviews = getattr(git, "list_reviews", None)
    if not callable(list_prs) or not callable(list_reviews):
        return []
    seen = _seen(platform)
    applied: list[str] = []
    try:
        pulls = list_prs() or []
    except Exception:
        return []
    for pr in pulls:
        number = pr.get("number")
        if number is None:
            continue
        repo = ((pr.get("base") or {}).get("repo") or {}).get("name")
        other_repo = repo if repo and repo != getattr(git, "repo", repo) else None
        try:
            reviews = (
                list_reviews(int(number), repo=other_repo)
                if other_repo
                else list_reviews(int(number))
            ) or []
        except Exception:
            continue
        for review in reviews:
            key = f"{number}:{review.get('id')}"
            if other_repo:
                key = f"{other_repo}#{key}"
            if key in seen:
                continue
            payload = json.dumps({"review": review, "pull_request": pr}).encode()
            try:
                parsed = webhook.parse_approval(payload)
            except webhook.WebhookRefused:
                seen.add(key)
                continue
            try:
                actor = platform.adapters.identity.actor(parsed["actor_id"])
                platform.decide(
                    parsed["requirement_id"],
                    actor,
                    parsed["outcome"],
                    gate=int(parsed["gate"]),
                    channel="github-reconcile",
                )
                applied.append(parsed["requirement_id"])
            except Exception:
                continue
            seen.add(key)
    _store_seen(platform, seen)
    return applied


def _seen(platform) -> set[str]:
    path = platform.root / "reconciled-reviews.json"
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text()))
    except json.JSONDecodeError:
        return set()


def _store_seen(platform, seen: set[str]) -> None:
    path = platform.root / "reconciled-reviews.json"
    path.write_text(json.dumps(sorted(seen)))
