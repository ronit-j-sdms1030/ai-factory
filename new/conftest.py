"""Put the platform modules on the path for the test run.

`new/` is deliberately flat while it is small. A package layout can be imposed
when there is enough here to warrant one; imposing it now would be guessing at
a shape the architecture has not yet forced.
"""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))


@pytest.fixture(autouse=True)
def _offline_adapters(monkeypatch):
    """A developer shell exports real GitHub and model keys; tests must not use them."""
    if os.getenv("FACTORY_LIVE_TESTS") == "1":
        return
    monkeypatch.setenv("REPOSITORY_ADAPTER", "local")
    monkeypatch.setenv("MODEL_ADAPTER", "deterministic")
    for name in ("GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
