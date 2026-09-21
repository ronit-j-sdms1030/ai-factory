"""Deterministic and OpenAI-compatible model gateways."""

from __future__ import annotations

import json
from typing import Any
from urllib import request

from phase1.llm import DeterministicIntakeLLM
from phase1.adapters.protocols import ModelCompletion
from phase1 import usage_ledger


class DeterministicModelGateway:
    def __init__(self, root=None):
        self._model = DeterministicIntakeLLM()
        self.root = root

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        skill: str,
        model: str | None = None,
    ) -> ModelCompletion:
        del model
        result = ModelCompletion(
            text=self._model.complete(messages, skill=skill),
            model="deterministic/intake",
            model_version="phase1-1",
            prompt_version="intake.skill.md",
            metadata={"provider": "local", "deterministic": True},
        )
        if self.root is not None:
            usage_ledger.record(self.root, result, messages, skill, agent="intake")
        return result


class LiteLLMModelGateway:
    """Calls LiteLLM's OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 30,
        prompt_version: str = "intake.skill.md",
        root=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.prompt_version = prompt_version
        self.root = root

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        skill: str,
        model: str | None = None,
    ) -> ModelCompletion:
        chosen = model or self.model
        payload = {
            "model": chosen,
            "temperature": 0,
            "max_tokens": 1024,
            "messages": (
                [{"role": "system", "content": skill}] if skill.strip() else []
            )
            + list(messages),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if "openrouter.ai" in self.base_url:
            headers["HTTP-Referer"] = "http://127.0.0.1:8787"
            headers["X-Title"] = "Governed SDLC demo"
        req = request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout) as response:
            data: dict[str, Any] = json.load(response)
            headers = response.headers
        choice = (data.get("choices") or [{}])[0]
        text = str((choice.get("message") or {}).get("content") or "")
        usage = data.get("usage") or {}
        metadata = {
            "provider": "litellm",
            "request_id": data.get("id") or headers.get("x-request-id"),
            "usage": {
                key: usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if key in usage
            },
            "finish_reason": choice.get("finish_reason"),
        }
        result = ModelCompletion(
            text=text,
            model=str(data.get("model") or chosen),
            model_version=str(
                headers.get("x-litellm-model-id")
                or data.get("model")
                or chosen
            ),
            prompt_version=self.prompt_version,
            metadata=metadata,
        )
        if self.root is not None:
            usage_ledger.record(self.root, result, messages, skill, agent="intake")
        return result
