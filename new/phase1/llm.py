"""Model egress seam. LiteLLM in production; a deterministic stub for tests.

No agent calls a provider directly. The skill file is the stable prefix.
"""

from __future__ import annotations

import json
from typing import Protocol


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
        from phase1.rails import scope_from_conversation

        del skill  # injected by the caller as the prefix; stub does not send it
        asked = sum(1 for m in messages if m.get("role") == "assistant" and '"type": "question"' in m.get("content", ""))
        if asked < len(self.QUESTIONS):
            return json.dumps({"type": "question", "text": self.QUESTIONS[asked]})
        return json.dumps(scope_from_conversation(messages))


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
