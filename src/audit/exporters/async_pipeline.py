from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.audit.exporters.base import AuditExporter

from src.audit.models import AuditEvent

_logger = logging.getLogger("audit.pipeline")


class AsyncPipeline:
    """Cola async con worker de fondo. Nunca bloquea el request."""

    def __init__(
        self,
        exporters: list[AuditExporter],
        max_queue: int = 10_000,
    ):
        self._exporters = exporters
        self._max_queue = max_queue
        self._queue: asyncio.Queue[AuditEvent | None] = asyncio.Queue(
            maxsize=max_queue
        )
        self._worker_task: asyncio.Task | None = None
        self._started = False

    def enqueue(self, event: AuditEvent) -> None:
        """Non-blocking. Si la cola está llena, drop oldest."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                _logger.warning("Audit queue full, dropping event")

    async def _worker(self) -> None:
        """Loop que consume la cola y exporta a todos los exporters."""
        while True:
            event = await self._queue.get()
            if event is None:
                self._queue.task_done()
                break
            for exporter in self._exporters:
                try:
                    await exporter.export(event)
                except Exception as e:
                    _logger.warning(
                        "Exporter %s failed: %s",
                        type(exporter).__name__,
                        e,
                    )
            self._queue.task_done()

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._worker_task = asyncio.create_task(self._worker())

    async def flush(self) -> None:
        """Espera a que la cola se vacíe."""
        if not self._started:
            return
        await self._queue.join()

    async def shutdown(self) -> None:
        """Flush + detener worker + shutdown exporters."""
        if not self._started:
            return
        await self._queue.put(None)
        if self._worker_task:
            await self._worker_task
        for exporter in self._exporters:
            try:
                await exporter.shutdown()
            except Exception as e:
                _logger.warning(
                    "Exporter %s shutdown failed: %s",
                    type(exporter).__name__,
                    e,
                )
        self._started = False
