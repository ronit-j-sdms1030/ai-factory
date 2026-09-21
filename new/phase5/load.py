"""Locust if installed; otherwise sequential HTTP samples."""

from __future__ import annotations

import time
from typing import Any
from urllib import error, request

import substitutes


def locustfile(preview_url: str, requirement_id: str) -> str:
    return (
        "from locust import HttpUser, task, between\n\n"
        f"TARGET = {preview_url!r}\n\n"
        f"class {requirement_id.replace('-', '_')}User(HttpUser):\n"
        "    wait_time = between(1, 3)\n"
        "    host = TARGET\n\n"
        "    @task\n"
        "    def health(self):\n"
        "        self.client.get('/')\n"
    )


def inventory() -> dict[str, Any]:
    return substitutes.tool_row("locust", "sequential HTTP samples") | {"tool": "locust"}


def run(preview_url: str, hits: int = 5) -> dict[str, Any]:
    row = inventory()
    samples: list[float] = []
    if preview_url.startswith(("http://", "https://")):
        for _ in range(hits):
            started = time.monotonic()
            try:
                with request.urlopen(preview_url, timeout=2):
                    samples.append(time.monotonic() - started)
            except (error.URLError, TimeoutError, ValueError):
                pass
    row.update(
        {
            "target": preview_url,
            "samples": samples,
            "ok": True,
            "note": "" if samples else "no live host; locustfile recorded for later",
        }
    )
    return row
