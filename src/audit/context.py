from __future__ import annotations

import uuid
from contextvars import ContextVar

_trace_id: ContextVar[str] = ContextVar("audit_trace_id", default="")
_span_id: ContextVar[str] = ContextVar("audit_span_id", default="")
_parent_span_id: ContextVar[str | None] = ContextVar("audit_parent_span_id", default=None)


class TraceContext:
    """Propagación async-safe de trace/span IDs con soporte W3C traceparent."""

    @staticmethod
    def new_trace() -> str:
        trace_id = uuid.uuid4().hex[:12]
        _trace_id.set(trace_id)
        _span_id.set(uuid.uuid4().hex[:8])
        _parent_span_id.set(None)
        return trace_id

    @staticmethod
    def new_span() -> str:
        current_span = _span_id.get("")
        if current_span:
            _parent_span_id.set(current_span)
        span_id = uuid.uuid4().hex[:8]
        _span_id.set(span_id)
        return span_id

    @staticmethod
    def get_trace_id() -> str:
        return _trace_id.get("")

    @staticmethod
    def get_span_id() -> str:
        return _span_id.get("")

    @staticmethod
    def get_parent_span_id() -> str | None:
        return _parent_span_id.get(None)

    @staticmethod
    def to_traceparent() -> str:
        trace = _trace_id.get("")
        span = _span_id.get("")
        return f"00-{trace}-{span}-01"

    @staticmethod
    def from_traceparent(header: str) -> None:
        parts = header.split("-")
        if len(parts) >= 3:
            _trace_id.set(parts[1])
            _span_id.set(parts[2])
            _parent_span_id.set(None)
