"""Does this screen actually compile?

The preview compiles each screen in the browser and strikes through the ones
that fail, but that happens *after* publication — by then the screen is
committed, the pull request is open, and the pipeline has reported success.
One of fourteen screens on a real requirement (``AuditTrail``) reached GATE 2
in exactly that state: generated, committed, reviewed as if it worked, and
unrenderable.

This runs the same parse server-side while the agent can still do something
about it. Same Babel, same ``classic`` runtime as ``ui_preview`` — checking
against a different runtime would pass screens the preview cannot run.

**A missing checker must never block generation.** Node or ``@babel/core``
being unavailable is an environment problem; screens generated without the
check are exactly what the pipeline produced yesterday, which is a worse
outcome than checked screens and a far better one than no screens. Every
failure path here returns "nothing is known to be broken".
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_jsx.js"

# Babel lives in the Express backend's dependencies rather than being vendored
# again here. Overridable because that assumes both services are checked out
# together, which a container build may not do.
_DEFAULT_NODE_PATH = Path(__file__).resolve().parents[2] / "backend" / "node_modules"

# Generous: this is one process for a whole design, and it competes with model
# calls that take minutes. Long enough never to fail a healthy run.
_TIMEOUT_SECONDS = 60


def available() -> bool:
    """Whether a compile check can run at all."""
    return bool(shutil.which("node")) and _SCRIPT.exists() and _node_path().exists()


def _node_path() -> Path:
    override = os.environ.get("JSX_CHECK_NODE_PATH")
    return Path(override) if override else _DEFAULT_NODE_PATH


def compile_errors(sources: dict[str, str]) -> dict[str, str]:
    """Screen name to parser error, for the screens that do not compile.

    Returns ``{}`` when everything compiles *and* when the check cannot run —
    the caller cannot distinguish, deliberately. "Nothing is known to be
    broken" is the only honest answer in both cases, and treating an absent
    checker as "everything is broken" would fail every screen on a machine
    without node.
    """
    if not sources:
        return {}
    if not available():
        log.warning(
            "skipping the JSX compile check — node or @babel/core not found at %s", _node_path()
        )
        return {}

    env = {**os.environ, "NODE_PATH": str(_node_path())}
    try:
        result = subprocess.run(
            ["node", str(_SCRIPT)],
            input=json.dumps(sources),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("JSX compile check did not run: %s", exc)
        return {}

    if result.returncode != 0:
        log.warning("JSX compile check exited %s: %s", result.returncode, result.stderr[:300])
        return {}

    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        log.warning("JSX compile check returned unparseable output")
        return {}

    if "__error" in parsed:
        log.warning("JSX compile check rejected its input: %s", parsed["__error"])
        return {}

    return {name: error for name, error in parsed.items() if error}
