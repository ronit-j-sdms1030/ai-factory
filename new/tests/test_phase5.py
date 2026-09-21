"""Phase 5 seams — skip-if-missing tools, recorded cluster artefacts."""

from __future__ import annotations

from phase5 import dast, deploy, flags, kyverno, load, rollback


def test_eight_scanners_named():
    from phase4 import scanners

    assert len(scanners.TOOLS) == 8


def test_uat_url_is_recorded_without_cluster():
    uat = deploy.uat("REQ-9")
    assert uat["status"] == "substitute"
    assert "preview" in uat["url"]


def test_zap_and_locust_use_substitutes_when_missing():
    zap = dast.scan("/preview/REQ-9", html="<html><body>ok</body></html>")
    assert zap["tool"] == "owasp-zap"
    assert zap["status"] in {"substitute", "available"}
    assert load.inventory()["status"] in {"substitute", "available"}


def test_rollback_has_four_mechanisms():
    plan = rollback.plan("REQ-9")
    assert set(plan["mechanisms"]) == {
        "image_tag_revert",
        "feature_flag_off",
        "migration_down",
        "pitr",
    }


def test_flagd_and_kyverno_are_artefacts():
    assert "release-req-9" in flags.flagd("REQ-9")
    assert "verifyImages" in kyverno.cluster_policy("REQ-9")
