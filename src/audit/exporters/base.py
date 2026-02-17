from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.audit.models import AuditEvent


@runtime_checkable
class AuditExporter(Protocol):
    """Interfaz para exportar eventos de audit."""

    async def export(self, event: AuditEvent) -> None: ...

    async def flush(self) -> None: ...

    async def shutdown(self) -> None: ...
