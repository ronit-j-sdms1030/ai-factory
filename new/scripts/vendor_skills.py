#!/usr/bin/env python3
"""Fetch the approved Phase 1–3 skill files without importing or executing them."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "skills" / "vendor-manifest.json"
DEFAULT_DESTINATION = ROOT / "skills" / "vendor"
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
APPROVED_REPOSITORIES = {
    "bmad-code-org/BMAD-METHOD",
    "addyosmani/agent-skills",
    "wshobson/agents",
}
EXPECTED_NAMES = {
    "bmad-agent-analyst",
    "bmad-agent-pm",
    "bmad-prd",
    "bmad-advanced-elicitation",
    "spec-driven-development",
    "bmad-agent-architect",
    "bmad-agent-ux-designer",
    "bmad-architecture",
    "bmad-ux",
    "bmad-spec",
    "bmad-create-epics-and-stories",
    "bmad-sprint-planning",
    "bmad-qa-generate-e2e-tests",
    "bmad-product-brief",
    "bmad-prfaq",
    "deployment-pipeline-design",
}


def _safe_relative(value: str, field: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"{field} must be a safe relative path")
    return path


def load_manifest(path: Path) -> list[dict[str, str]]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported vendor manifest schema")
    skills = payload.get("skills")
    if not isinstance(skills, list) or len(skills) != len(EXPECTED_NAMES):
        raise ValueError("manifest must contain the approved Phase 1–3 skill set")
    names = {item.get("name") for item in skills if isinstance(item, dict)}
    if names != EXPECTED_NAMES:
        raise ValueError("manifest does not contain the approved skill set")
    return skills


def validate_entry(entry: dict[str, str]) -> tuple[str, Path]:
    repository = entry.get("repository", "")
    commit = entry.get("commit", "")
    checksum = entry.get("sha256", "")
    source = _safe_relative(entry.get("path", ""), "path")
    target = _safe_relative(entry.get("target", ""), "target")
    if repository not in APPROVED_REPOSITORIES:
        raise ValueError(f"repository is not approved: {repository}")
    if not COMMIT.fullmatch(commit):
        raise ValueError(f"commit is not a full SHA: {commit}")
    if not SHA256.fullmatch(checksum):
        raise ValueError(f"invalid SHA-256 for {entry.get('name')}")
    if source.name != "SKILL.md" or target.name != "SKILL.md":
        raise ValueError("only SKILL.md files may be fetched")
    url = f"https://raw.githubusercontent.com/{repository}/{commit}/{source}"
    return url, Path(*target.parts)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "governed-ai-factory-vendor/1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.geturl() != url:
            raise ValueError(f"redirect refused for {url}")
        return response.read()


def write_atomic(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".vendor-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="download and verify every file without writing",
    )
    args = parser.parse_args()

    for entry in load_manifest(args.manifest):
        url, relative_target = validate_entry(entry)
        data = fetch(url)
        actual = hashlib.sha256(data).hexdigest()
        if actual != entry["sha256"]:
            raise ValueError(
                f"checksum mismatch for {entry['name']}: "
                f"expected {entry['sha256']}, got {actual}"
            )
        destination = args.destination / relative_target
        action = "verified" if args.dry_run else "wrote"
        if not args.dry_run:
            write_atomic(destination, data)
        print(f"{action} {entry['name']} ({len(data)} bytes) -> {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
