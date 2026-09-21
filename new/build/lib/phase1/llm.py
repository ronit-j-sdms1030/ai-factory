"""Model egress seam. LiteLLM in production; a deterministic stub for tests.

No agent calls a provider directly. The skill file is the stable prefix.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import intake_skill


class LLM(Protocol):
    def complete(self, messages: list[dict[str, str]], *, skill: str) -> str: ...


class DeterministicIntakeLLM:
    """Asks four questions then emits a scope report from the answers.

    Used when no LiteLLM endpoint is configured. The budget still applies:
    this stub never asks an eleventh question because the loop will not let it.
    """

    QUESTIONS = (
        "Who will use this, and in which department?",
        "What happens today when this work is done without the system?",
        "What should be true afterwards — in your own words, not a feature list?",
        "What is explicitly out of scope, including native apps or other runtimes?",
    )

    def complete(self, messages: list[dict[str, str]], *, skill: str) -> str:
        del skill  # injected by the caller as the prefix; stub does not send it
        asked = sum(1 for m in messages if m.get("role") == "assistant" and '"type": "question"' in m.get("content", ""))
        if asked < len(self.QUESTIONS):
            return json.dumps({"type": "question", "text": self.QUESTIONS[asked]})

        answers = [m.get("content", "") for m in messages if m.get("role") == "user"]
        request = answers[0] if answers else "the submitted request"
        who = answers[1] if len(answers) > 1 else "named users"
        today = answers[2] if len(answers) > 2 else "a manual process"
        success = answers[3] if len(answers) > 3 else "the manual process stops failing"
        out = answers[4] if len(answers) > 4 else "native mobile, desktop, and anything outside React/Node-or-Python/PostgreSQL"
        return json.dumps(
            {
                "type": "scope_report",
                "in_scope": [
                    f"A web application for {who}, replacing: {today}",
                    f"Built from the request: {request}",
                ],
                "out_of_scope": [
                    out,
                    "Native mobile applications, desktop installers, and ML training platforms",
                ],
                "success": success,
                "assumptions": [
                    "Access is through a browser. Entra identity is the client's.",
                ],
                "open_questions": [
                    "Which existing system holds the source records today?",
                ],
            }
        )


class DeterministicBRDLLM:
    def complete(self, messages: list[dict[str, str]], *, skill: str) -> str:
        del skill, messages
        return json.dumps(
            {
                "type": "critique",
                "findings": [
                    "Confirm the owning department for shared records before decomposition.",
                ],
            }
        )


def intake_llm() -> LLM:
    return DeterministicIntakeLLM()
