"""Local substitutes find real defects and never claim a vendor pass."""

from __future__ import annotations

from phase1 import tooling
from phase4 import scanners


def test_secret_is_blocking():
    report = scanners.scan_files({"src/api/x.ts": "const password = 'hunter2'\nexport const ticket = 't'\n"})
    assert report["ok"] is False
    assert any(row["tool"] == "gitleaks" for row in report["blocking"])


def test_inventory_is_never_silent_skip():
    statuses = {row["status"] for row in scanners.inventory()}
    assert "skipped" not in statuses
    assert statuses <= {"available", "substitute"}


def test_tooling_lists_tenant_keys():
    report = tooling.report()
    names = [row["tool"] for row in report["tenant_keys"]]
    assert "Microsoft Entra ID" in names
    assert "E2B cloud" in names
    assert report["sandbox"]["status"] in {"substitute", "keyed"}
