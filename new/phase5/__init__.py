"""Phase 5 artefacts — QA, DAST, Locust, Argo UAT, flags, Kyverno, rollback, monitor."""

from __future__ import annotations

from typing import Any

from phase5 import dast, deploy, flags, kyverno, load, monitor, qa, rollback


def pack_uat(
    requirement_id: str,
    tests: list[dict[str, Any]],
    *,
    preview_url: str = "",
    cluster: str | None = None,
    html: str = "",
) -> dict[str, Any]:
    uat = deploy.uat(requirement_id, cluster)
    url = preview_url or uat["url"]
    qa_report = qa.execute(url, tests, html=html)
    zap = dast.scan(url, html=html)
    load_report = load.run(url)
    files = {
        f"requirements/{requirement_id}/uat_deploy/STATUS.md": _uat_status(
            requirement_id, uat, qa_report, zap, load_report
        ),
        f"requirements/{requirement_id}/uat_deploy/locustfile.py": load.locustfile(
            url, requirement_id
        ),
        f"requirements/{requirement_id}/uat_deploy/preview.html": html
        or "<!-- no screen snapshot -->\n",
        f"requirements/{requirement_id}/uat_deploy/argo-application.yaml": (
            f"# recorded Application {uat['application']}\n# status: {uat['status']}\n"
        ),
    }
    return {
        "files": files,
        "uat": {
            **uat,
            "preview": url,
            "qa": qa_report,
            "dast": zap,
            "load": load_report,
        },
    }


def pack_release(
    requirement_id: str,
    *,
    image_tag: str = "previous",
    attested: bool = True,
) -> dict[str, Any]:
    canary = flags.canary(requirement_id)
    rb = rollback.plan(requirement_id, image_tag)
    watch = monitor.snapshot(requirement_id)
    admission = kyverno.admit(attested=attested, cluster=False)
    files = {
        f"requirements/{requirement_id}/release/STATUS.md": _release_status(
            requirement_id, canary, rb, watch, admission
        ),
        f"requirements/{requirement_id}/release/flagd.json": flags.flagd(requirement_id),
        f"requirements/{requirement_id}/release/kyverno.yaml": kyverno.cluster_policy(
            requirement_id
        ),
        f"requirements/{requirement_id}/release/rollback.json": _json(rb),
        f"requirements/{requirement_id}/release/admission.json": _json(admission),
    }
    return {
        "files": files,
        "release": {
            "canary": canary,
            "rollback": rb,
            "monitor": watch,
            "admission": admission,
        },
    }


def _json(data: Any) -> str:
    import json

    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _uat_status(
    rid: str,
    uat: dict[str, Any],
    qa_report: dict[str, Any],
    zap: dict[str, Any],
    load_report: dict[str, Any],
) -> str:
    return (
        f"# UAT {rid}\n\n"
        f"- url: {uat['url']}\n"
        f"- argo: {uat['application']} ({uat['status']})\n"
        f"- qa reachable: {qa_report['reachable']}\n"
        f"- zap: {zap['status']}\n"
        f"- locust: {load_report['status']}\n"
        f"- note: {uat['note']}\n"
    )


def _release_status(
    rid: str,
    canary: dict[str, Any],
    rb: dict[str, Any],
    watch: dict[str, Any],
    admission: dict[str, Any],
) -> str:
    return (
        f"# Release {rid}\n\n"
        "- Cosign: local key, Rekor tlog not used\n"
        f"- canary flag: {canary['flag']} default {canary['default']} ({canary['provider']})\n"
        f"- kyverno: {admission['status']} allowed={admission['allowed']}\n"
        f"- rollback: {', '.join(rb['mechanisms'])}\n"
        f"- monitor errors: {watch['errors']}\n"
    )
