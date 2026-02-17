from __future__ import annotations

import sys

from src.audit.models import AuditEvent

_COLORS = {
    "ok": "\033[32m",
    "error": "\033[31m",
    "timeout": "\033[33m",
    "retry": "\033[36m",
}
_RESET = "\033[0m"


class StdoutExporter:
    """Exporter para desarrollo: print coloreado al stderr."""

    async def export(self, event: AuditEvent) -> None:
        color = _COLORS.get(event.status, "")
        parts = [
            f"{color}[AUDIT]{_RESET}",
            event.event_type.value,
            f"trace={event.trace_id}",
            f"span={event.span_id}",
            f"agent={event.agent}",
            f"status={event.status}",
        ]
        if event.latency_ms is not None:
            parts.append(f"latency={event.latency_ms:.0f}ms")
        if event.tokens_input is not None:
            parts.append(f"tokens={event.tokens_input}→{event.tokens_output}")
        if event.cost_usd is not None:
            parts.append(f"cost=${event.cost_usd:.5f}")
        print(" ".join(parts), file=sys.stderr)

    async def flush(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass
