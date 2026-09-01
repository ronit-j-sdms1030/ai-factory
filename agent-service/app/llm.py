"""Structured LLM calls against OpenRouter.

Written fresh rather than transliterated from ``llm.service.js``. The JS
implementation forces a tool call, JSON-parses the arguments, then hand-rolls
validation (``findMissingFields``) and a repair pass for double-encoded
fields (``normalizeDoubleEncodedFields``). Pydantic subsumes all three: the
schema is generated from the model, validation is the parse, and coercion is
declarative.

Two behaviours from the JS are deliberately preserved because each exists in
response to an observed failure:

* **Retry on malformed output.** A large schema intermittently drops a
  required field or returns truncated JSON. Retrying with a fresh sample
  usually succeeds where re-parsing the same bad payload never will.
* **The ``content`` exemption.** Some providers double-encode nested
  structures, so a string that looks like JSON gets parsed into an object.
  Applied blindly this corrupts fields that are meant to stay raw text — a
  generated ``package.json`` file body is itself valid JSON, and parsing it
  turned a string into a dict and crashed the pipeline runner downstream.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from langsmith.wrappers import wrap_openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from . import config

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Field names whose value must never be re-parsed even if it looks like JSON.
_RAW_TEXT_FIELDS = {"content"}


def _client() -> OpenAI:
    """The OpenRouter client, wrapped so LangSmith can see the call.

    LangSmith instruments LangChain and LangGraph automatically, and these
    agents use neither — they call the OpenAI SDK directly. Without this
    wrapper, setting LANGSMITH_API_KEY produces a working Studio login and not
    a single trace from the pipeline, which is a confusing way to discover
    that tracing was never wired up.

    The wrapper is inert unless LANGSMITH_TRACING is enabled, so it costs
    nothing when nobody is watching.
    """
    return wrap_openai(
        OpenAI(
            api_key=config.openrouter_api_key(),
            base_url=config.OPENROUTER_BASE_URL,
        )
    )


def _unwrap_double_encoded(value: Any, key: str | None = None) -> Any:
    """Re-parse values a provider returned as JSON-encoded strings.

    ``key`` is threaded through so raw-text fields can be exempted; see the
    module docstring for why that exemption is load-bearing.
    """
    if isinstance(value, list):
        return [_unwrap_double_encoded(v) for v in value]
    if isinstance(value, dict):
        return {k: _unwrap_double_encoded(v, k) for k, v in value.items()}
    if isinstance(value, str) and key not in _RAW_TEXT_FIELDS:
        stripped = value.strip()
        if stripped[:1] in ("{", "[") and stripped[-1:] in ("}", "]"):
            try:
                return _unwrap_double_encoded(json.loads(stripped))
            except json.JSONDecodeError:
                return value
    return value


def call_structured(
    *,
    model: str,
    messages: list[dict[str, str]],
    schema: type[T],
    max_tokens: int = 4000,
    retries: int = 1,
    timeout: float = 90.0,
) -> T:
    """Force the model to answer as ``schema`` and return a validated instance.

    Raises ``RuntimeError`` if every attempt produces output that fails
    validation.
    """
    tool = {
        "type": "function",
        "function": {
            "name": schema.__name__,
            "description": (schema.__doc__ or schema.__name__).strip(),
            "parameters": schema.model_json_schema(),
        },
    }

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = _client().chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                tools=[tool],  # type: ignore[list-item]
                tool_choice={"type": "function", "function": {"name": schema.__name__}},
                max_tokens=max_tokens,
                timeout=timeout,
            )
            choice = response.choices[0] if response.choices else None
            if choice is None:
                raise RuntimeError("the model returned no response")

            calls = choice.message.tool_calls or []
            if not calls:
                raise RuntimeError("the model did not return a structured response")

            raw = json.loads(calls[0].function.arguments)
            return schema.model_validate(_unwrap_double_encoded(raw))

        except (ValidationError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            log.warning(
                "structured call to %s failed (attempt %d/%d): %s",
                model, attempt + 1, retries + 1, exc,
            )

    raise RuntimeError(f"{model} returned malformed output after retrying: {last_error}")


def call_text(
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 700,
    timeout: float = 60.0,
) -> str:
    """Plain completion, used where the reply is conversational rather than structured."""
    response = _client().chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=max_tokens,
        timeout=timeout,
    )
    choice = response.choices[0] if response.choices else None
    if choice is None:
        raise RuntimeError("the model returned no response")
    return (choice.message.content or "").strip()
