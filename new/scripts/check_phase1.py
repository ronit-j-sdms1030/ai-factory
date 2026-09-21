#!/usr/bin/env python3
"""One-command Phase 1 acceptance verifier."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str]) -> None:
    print(f"\n== {label} ==")
    subprocess.run(command, cwd=ROOT, check=True)


def static_acceptance() -> None:
    compose = (ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    required = {
        "deterministic compose default": "MODEL_ADAPTER: ${MODEL_ADAPTER:-deterministic}" in compose,
        "cosign compose signer": "SIGNER_ADAPTER: cosign" in compose,
        "local cosign key init": "cosign generate-key-pair" in compose,
        "cosign runtime binary": "/usr/local/bin/cosign" in dockerfile,
        "non-root runtime": "USER app" in dockerfile,
        "canonical frontend documented": "../frontend" in readme,
        "compose cookie login for frontend": "PHASE1_DEV_MODE:" in compose,
    }
    failures = [name for name, passed in required.items() if not passed]
    if failures:
        raise SystemExit("static acceptance failed: " + ", ".join(failures))
    print("static acceptance: 7 passed")


def main() -> int:
    project_python = ROOT / ".venv/bin/python"
    python = str(project_python) if project_python.exists() else sys.executable
    static_acceptance()
    run(
        "Python compile",
        [
            python,
            "-m",
            "compileall",
            "-q",
            "-x",
            r"/(\.venv|build|__pycache__)/",
            ".",
        ],
    )
    project_ruff = ROOT / ".venv/bin/ruff"
    ruff = str(project_ruff) if project_ruff.exists() else shutil.which("ruff")
    if ruff:
        run("Ruff", [ruff, "check", "."])
    else:
        print("\n== Ruff ==\nSKIP: ruff executable is not installed")
    run("Pytest", [python, "-m", "pytest", str(ROOT / "tests"), "-q"])

    docker = shutil.which("docker")
    if not docker:
        raise SystemExit("docker CLI is required for compose config validation")
    run(
        "Compose config",
        [docker, "compose", "-f", str(ROOT / "deploy/docker-compose.yml"), "config", "--quiet"],
    )

    opa = shutil.which("opa")
    if opa:
        run("OPA syntax", [opa, "check", str(ROOT / "deploy/opa/governance.rego")])
    else:
        print("\n== OPA syntax ==\nSKIP: opa executable is not installed; parity is covered statically")
    print("\nPhase 1 verifier passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
