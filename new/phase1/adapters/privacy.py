"""Personal-data masking adapters."""

from __future__ import annotations

import json
import re
from urllib import request

from phase1 import rails

# "today" is a product word, not a personal date. Presidio otherwise writes <DATE_TIME>.
_RELATIVE_WHEN = re.compile(
    r"^(today|tomorrow|yesterday|tonight|now|daily|weekly|weekday|weekend)$",
    re.I,
)


def _drop_relative_dates(text: str, findings: object) -> list:
    if not isinstance(findings, list):
        return []
    kept = []
    for item in findings:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        kind = str(item.get("entity_type") or "").upper()
        if kind in {"DATE_TIME", "DATE"}:
            start = int(item.get("start") or 0)
            end = int(item.get("end") or 0)
            span = (text or "")[start:end].strip()
            if _RELATIVE_WHEN.fullmatch(span):
                continue
        kept.append(item)
    return kept


class LocalPrivacy:
    def screen(self, text: str) -> str:
        return rails.screen_input(text)


class PresidioPrivacy:
    def __init__(
        self,
        analyzer_url: str,
        anonymizer_url: str,
        *,
        language: str = "en",
        timeout: float = 5,
    ):
        self.analyzer_url = analyzer_url.rstrip("/")
        self.anonymizer_url = anonymizer_url.rstrip("/")
        self.language = language
        self.timeout = timeout

    def _post(self, url: str, payload: dict) -> object:
        req = request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout) as response:
            return json.load(response)

    def screen(self, text: str) -> str:
        # Local rails reject empty and adversarial turns before any network egress.
        clean = rails.screen_input(text)
        findings = self._post(
            f"{self.analyzer_url}/analyze",
            {"text": clean, "language": self.language},
        )
        findings = _drop_relative_dates(clean, findings)
        if not findings:
            return clean
        result = self._post(
            f"{self.anonymizer_url}/anonymize",
            {"text": clean, "analyzer_results": findings},
        )
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise rails.InputRefused("Presidio returned no anonymized text")
        return result["text"]
