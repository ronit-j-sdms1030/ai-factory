#!/usr/bin/env python3
"""Seed one full-governance run through Gate 7 for a live demo."""

from __future__ import annotations

import os
from pathlib import Path

from phase1 import directory
from phase1.platform import Phase1
from tests.test_phase1 import finish_intake


def main() -> None:
    root = Path(os.getenv("RUNTIME_ROOT") or Path(__file__).resolve().parents[1] / ".runtime")
    root.mkdir(parents=True, exist_ok=True)
    p1 = Phase1(root)
    run = p1.submit(directory.actor("u-requester"), "full_governance", "Book meeting rooms.")
    rid = run["requirement"]["id"]
    finish_intake(p1, rid)
    for actor, outcome in (
        ("u-po", "approve"),
        ("u-bo", "approve"),
        ("u-ctl", "approve"),
        ("u-arch", "approve"),
        ("u-ux", "approve"),
        ("u-ba", "approve"),
        ("u-tl", "approve"),
        ("u-sl-qa", "approve"),
        ("u-se", "approve"),
        ("u-requester", "approve"),
        ("u-rm", "approve"),
    ):
        p1.decide(rid, directory.actor(actor), outcome)
    done = p1.get(rid)
    print(f"{rid} {done['phase']} runtime={root}")


if __name__ == "__main__":
    main()
