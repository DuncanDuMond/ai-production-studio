"""Content-free manual OpenTelemetry spans; exporting is explicitly opt-in.

Never install a global provider: upstream auto-instrumentation can attach raw
prompts and documents. Studio owns this isolated provider and its allowlist.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from threading import Lock

_LOCK = Lock()
_PROVIDER = None
_SAFE_ATTRIBUTES = {
    "studio.mode", "studio.engine", "studio.status", "studio.framework",
    "studio.document_count", "studio.retrieved_count", "studio.prompt_chars",
    "studio.output_chars", "studio.reference_chars",
}
_DISABLED_VALUES = {"1", "true", "yes", "on"}


def export_enabled() -> bool:
    """Report configured export only when the SDK is not explicitly disabled."""
    endpoint = os.getenv("STUDIO_OTLP_ENDPOINT", "").strip()
    disabled = os.getenv("OTEL_SDK_DISABLED", "false").strip().lower()
    return bool(endpoint) and disabled not in _DISABLED_VALUES


def create_provider(*, exporter=None):
    """Create a private provider (optional injected in-memory exporter in tests)."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider(resource=Resource({
        "service.name": "ai-production-studio", "service.version": "0.1.0",
    }))
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        endpoint = os.getenv("STUDIO_OTLP_ENDPOINT", "").strip()
        if export_enabled():
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
                endpoint=endpoint, timeout=3,
            )))
    return provider


def get_provider():
    global _PROVIDER
    with _LOCK:
        if _PROVIDER is None:
            _PROVIDER = create_provider()
        return _PROVIDER


@contextmanager
def span(name: str, attributes: dict | None = None):
    """Only allow counts/enums; do not record exception messages or stack traces."""
    from opentelemetry.trace import StatusCode

    safe = {key: value for key, value in (attributes or {}).items() if key in _SAFE_ATTRIBUTES}
    tracer = get_provider().get_tracer("studio.engine", "0.1.0")
    with tracer.start_as_current_span(
        name, attributes=safe, record_exception=False, set_status_on_exception=False,
    ) as active:
        try:
            yield active
        except Exception:
            active.set_status(StatusCode.ERROR)
            raise


def trace_id(active) -> str | None:
    context = active.get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None
