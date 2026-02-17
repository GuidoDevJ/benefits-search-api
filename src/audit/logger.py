from __future__ import annotations

import logging
import os
from typing import ClassVar

from src.audit.exporters.async_pipeline import AsyncPipeline
from src.audit.exporters.jsonfile import JsonFileExporter
from src.audit.exporters.stdout import StdoutExporter
from src.audit.models import AuditEvent, ErrorDetail
from src.audit.sampler import SamplingPolicy
from src.audit.sanitizer import sanitize

_logger = logging.getLogger("audit")

_EXPORTER_MAP = {
    "stdout": StdoutExporter,
    "jsonfile": JsonFileExporter,
}


class AuditLogger:
    """Singleton que recibe eventos, aplica sampling + sanitización,
    y los despacha via async pipeline a exporters pluggables.
    """

    _instance: ClassVar[AuditLogger | None] = None

    def __init__(
        self,
        sampler: SamplingPolicy | None = None,
        pipeline: AsyncPipeline | None = None,
        log_dir: str | None = None,
    ):
        self._sampler = sampler or SamplingPolicy(
            success_rate=float(
                os.getenv("SUCCESS_SAMPLE_RATE", "0.10")
            ),
            error_rate=float(
                os.getenv("ERROR_SAMPLE_RATE", "1.00")
            ),
            slow_threshold_ms=float(
                os.getenv("SLOW_REQUEST_THRESHOLD_MS", "1500")
            ),
        )
        self._enabled = (
            os.getenv("AUDIT_ENABLED", "true").lower() == "true"
        )

        if pipeline is not None:
            self._pipeline = pipeline
        else:
            exporter_name = os.getenv("AUDIT_EXPORTER", "jsonfile")
            log_dir = log_dir or os.getenv("AUDIT_LOG_DIR", "logs")
            exporter_cls = _EXPORTER_MAP.get(
                exporter_name, JsonFileExporter
            )
            if exporter_cls is JsonFileExporter:
                exporter = exporter_cls(log_dir=log_dir)
            else:
                exporter = exporter_cls()
            self._pipeline = AsyncPipeline(exporters=[exporter])

    @classmethod
    def get(cls) -> AuditLogger:
        if cls._instance is None:
            cls._instance = AuditLogger()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (para tests)."""
        cls._instance = None

    async def start(self) -> None:
        """Iniciar el worker de la pipeline async."""
        await self._pipeline.start()

    def emit(self, event: AuditEvent) -> None:
        """Non-blocking. Aplica sampling + sanitización y encola."""
        if not self._enabled:
            return
        if not self._sampler.should_record(event):
            return
        safe_event = self._sanitize_event(event)
        self._pipeline.enqueue(safe_event)

    async def flush(self) -> None:
        """Drain completo de la cola."""
        await self._pipeline.flush()

    async def shutdown(self) -> None:
        """Flush + cerrar exporters."""
        await self._pipeline.shutdown()

    @staticmethod
    def _sanitize_event(event: AuditEvent) -> AuditEvent:
        sanitized_data = sanitize(event.data) if event.data else {}
        sanitized_error = None
        if event.error:
            error_dict = event.error.model_dump()
            if error_dict.get("input_snapshot"):
                error_dict["input_snapshot"] = sanitize(
                    error_dict["input_snapshot"]
                )
            sanitized_error = ErrorDetail(**error_dict)
        return event.model_copy(
            update={"data": sanitized_data, "error": sanitized_error}
        )
