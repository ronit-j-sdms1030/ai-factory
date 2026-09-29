"""Deterministic and OpenAI-compatible model gateways."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from phase1.llm import DeterministicIntakeLLM
from phase1.adapters.protocols import ModelCompletion
from phase1 import usage_ledger


class DeterministicModelGateway:
    def __init__(self, root=None):
        self._model = DeterministicIntakeLLM()
        self.root = root

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        skill: str,
        model: str | None = None,
        agent: str = "intake",
        requirement_id: str = "",
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        execute_tool: Any = None,
    ) -> ModelCompletion:
        del model, max_tokens, tools, execute_tool
        result = ModelCompletion(
            text=self._model.complete(messages, skill=skill),
            model="deterministic/intake",
            model_version="phase1-1",
            prompt_version="intake.skill.md",
            metadata={"provider": "local", "deterministic": True},
        )
        if self.root is not None:
            usage_ledger.record(
                self.root, result, messages, skill, agent=agent, requirement_id=requirement_id
            )
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
        messages: list[dict[str, Any]],
        *,
        skill: str,
        model: str | None = None,
        agent: str = "intake",
        requirement_id: str = "",
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        execute_tool: Any = None,
    ) -> ModelCompletion:
        chosen = model or self.model
        pending: list[dict[str, Any]] = (
            [{"role": "system", "content": skill}] if skill.strip() else []
        ) + list(messages)
        last = self._chat(
            chosen,
            pending,
            max_tokens=max_tokens,
            tools=tools,
        )
        hops = 0
        while tools and execute_tool and hops < 3:
            message = ((last.get("choices") or [{}])[0].get("message") or {})
            calls = message.get("tool_calls") or []
            if not calls:
                break
            pending.append(message)
            for call in calls:
                fn = (call.get("function") or {})
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else dict(raw)
                except json.JSONDecodeError:
                    args = {}
                pending.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or f"call-{hops}"),
                        "content": str(execute_tool(str(fn.get("name") or ""), args) or ""),
                    }
                )
            hops += 1
            last = self._chat(
                chosen,
                pending,
                max_tokens=max_tokens,
                tools=tools if hops < 3 else None,
            )
        choice = (last.get("choices") or [{}])[0]
        text = str((choice.get("message") or {}).get("content") or "")
        usage = last.get("usage") or {}
        metadata = {
            "provider": "litellm",
            "request_id": last.get("id") or last.get("_response_id"),
            "usage": {
                key: usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if key in usage
            },
            "finish_reason": choice.get("finish_reason"),
            "tool_hops": hops,
        }
        served = last.get("_routed_via") or (
            str(last.get("model") or "") if str(last.get("model") or "") not in {"", chosen} else ""
        )
        if served and served != chosen:
            metadata["served_by"] = str(served)
        result = ModelCompletion(
            text=text,
            model=str(last.get("model") or chosen),
            model_version=str(last.get("model") or chosen),
            prompt_version=self.prompt_version,
            metadata=metadata,
        )
        if self.root is not None:
            usage_ledger.record(
                self.root, result, messages, skill, agent=agent, requirement_id=requirement_id
            )
        return result

    def _chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "temperature": 0,
            "max_tokens": int(max_tokens or 1024),
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
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
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                data: dict[str, Any] = json.load(response)
                data["_response_id"] = response.headers.get("x-request-id")
                data["_routed_via"] = response.headers.get(
                    "llm_provider-x-routed-via"
                ) or response.headers.get("x-routed-via")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:800]
            raise RuntimeError(f"model {model} rejected ({exc.code}): {detail}") from exc
        return data
