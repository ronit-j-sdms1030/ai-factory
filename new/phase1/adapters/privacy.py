"""Personal-data masking adapters."""

from __future__ import annotations

import json
from urllib import request

from phase1 import rails


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
        result = self._post(
            f"{self.anonymizer_url}/anonymize",
            {"text": clean, "analyzer_results": findings},
        )
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise rails.InputRefused("Presidio returned no anonymized text")
        return result["text"]
