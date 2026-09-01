"""Test-suite defaults.

Tracing is switched off for the whole suite. ``LANGSMITH_TRACING`` lives in
``.env``, which the tests load for Mongo and model credentials, so enabling it
for real runs silently enrolled the tests too — a pytest run posted dozens of
fabricated jobs into the project, complete with deliberately-failing fixtures
named "model refused" and "github down" sitting beside genuine pipeline runs.

Traces are for finding out what the system did. Filling them with what the
tests pretended it did makes them worthless.

Done in ``pytest_configure`` rather than a fixture because it has to happen
before collection: importing a test module is enough to construct a traced
client, and by the time a fixture runs that decision is already made.
"""

from __future__ import annotations

import os


def pytest_configure(config):  # noqa: ARG001 — pytest hook signature
    for key in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"):
        os.environ.pop(key, None)
    os.environ["LANGSMITH_TRACING"] = "false"
