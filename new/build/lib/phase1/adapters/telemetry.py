"""No-op and optional OpenTelemetry adapters with PII-safe attributes."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from phase1.rails import mask_personal_data

SENSITIVE_PARTS = {
    "content", "email", "message", "name", "phone", "prompt", "request",
    "secret", "text", "token",
}


def safe_attributes(attributes: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        lowered = key.lower().replace("-", "_").split("_")
        if SENSITIVE_PARTS.intersection(lowered):
            safe[key] = "[REDACTED]"
        elif isinstance(value, str):
            safe[key] = mask_personal_data(value)
        elif isinstance(value, (bool, int, float)):
            safe[key] = value
        else:
            safe[key] = str(value)
    return safe


class NoOpTelemetry:
    @contextmanager
    def span(self, name: str, attributes: dict[str, Any] | None = None):
        del name
        safe_attributes(attributes)
        yield None

    def event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        del name
        safe_attributes(attributes)


class OpenTelemetryAdapter:
    def __init__(self, service_name: str = "governed-ai-factory"):
        try:
            from opentelemetry import trace
        except ImportError as exc:
            raise RuntimeError("telemetry adapter requires the 'telemetry' extra") from exc
        self.tracer = trace.get_tracer(service_name)

    def span(self, name: str, attributes: dict[str, Any] | None = None):
        return self.tracer.start_as_current_span(
            name, attributes=safe_attributes(attributes)
        )

    def event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span.is_recording():
            span.add_event(name, safe_attributes(attributes))
