"""Backstage Component entities — one per requirement."""

from __future__ import annotations

import os
import re
from typing import Any

_REQ = re.compile(r"REQ-\d+", re.I)


def github_repo_name(requirement_id: str, *, base: str | None = None) -> str:
    """Private product repo is always named after the requirement id."""
    rid = str(requirement_id or "").strip()
    found = _REQ.search(rid)
    slug = found.group(0).lower() if found else rid.lower().replace(" ", "-")
    root = (base or os.environ.get("GITHUB_REPO") or "ai-factory-governance").strip()
    if os.environ.get("GITHUB_REPO_PER_REQUIREMENT", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return f"{root}-{slug}" if slug and slug not in root else (slug or root)
    return root


def github_html_url(requirement_id: str) -> str:
    owner = (os.environ.get("GITHUB_OWNER") or "").strip()
    if not owner:
        return ""
    return f"https://github.com/{owner}/{github_repo_name(requirement_id)}"


def entities(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for row in runs:
        req = row.get("requirement") or {}
        rid = str(req.get("id") or "")
        if not rid:
            continue
        title = (
            str(row.get("display_title") or "").strip()
            or str(req.get("title") or "").strip()
            or rid
        )
        preview = f"/preview/{rid}"
        git_url = github_html_url(rid)
        annotations = {
            "governed.io/requirement": rid,
            "governed.io/phase": str(row.get("phase") or ""),
            "governed.io/preview": preview,
        }
        if git_url:
            annotations["github.com/project-slug"] = git_url.replace(
                "https://github.com/", "", 1
            )
            annotations["governed.io/github"] = git_url
        links = {
            "links": [
                {"url": preview, "title": "Live app", "icon": "catalog"},
                *([{ "url": git_url, "title": "GitHub", "icon": "github" }] if git_url else []),
            ]
        }
        items.append(
            {
                "apiVersion": "backstage.io/v1alpha1",
                "kind": "Component",
                "metadata": {
                    "name": rid.lower(),
                    "title": title,
                    "annotations": annotations,
                    **links,
                },
                "spec": {
                    "type": "service",
                    "lifecycle": str(row.get("phase") or "unknown"),
                    "owner": "group:default/platform",
                    "system": "governed-factory",
                },
            }
        )
    return items
