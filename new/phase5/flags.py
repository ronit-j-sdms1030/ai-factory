"""OpenFeature + flagd — production canary sits behind a flag."""

from __future__ import annotations

from typing import Any


def flagd(requirement_id: str) -> str:
    key = f"release-{requirement_id.lower()}"
    return (
        "{\n"
        '  "flags": {\n'
        f'    "{key}": {{\n'
        '      "state": "ENABLED",\n'
        '      "variants": { "on": true, "off": false },\n'
        '      "defaultVariant": "off"\n'
        "    }\n"
        "  }\n"
        "}\n"
    )


def canary(requirement_id: str) -> dict[str, Any]:
    key = f"release-{requirement_id.lower()}"
    import json

    import substitutes

    document = json.loads(flagd(requirement_id))
    return {
        "flag": key,
        "provider": "openfeature-python-substitute",
        "default": substitutes.evaluate_flag(document, key),
        "weight": 5,
    }
