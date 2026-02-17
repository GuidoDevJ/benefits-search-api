"""Tests for audit exporters + async pipeline (Phase 3 / PR 2)."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.audit.exporters.async_pipeline import AsyncPipeline
from src.audit.exporters.base import AuditExporter
from src.audit.exporters.jsonfile import JsonFileExporter
from src.audit.exporters.stdout import StdoutExporter
from src.audit.logger import AuditLogger
from src.audit.models import AuditEvent, AuditEventType
from src.audit.sampler import SamplingPolicy


def _make_event(
    trace_id="t1", span_id="s1", status="ok", **kwargs
) -> AuditEvent:
    return AuditEvent(
        trace_id=trace_id,
        span_id=span_id,
        event_type=AuditEventType.TOOL_CALL,
        agent="test",
        action="test",
        status=status,
        **kwargs,
    )


# ──────────────── Protocol conformance ────────────────


class TestExporterProtocol:
    def test_stdout_implements_protocol(self):
        assert isinstance(StdoutExporter(), AuditExporter)

    def test_jsonfile_implements_protocol(self):
        assert isinstance(JsonFileExporter(), AuditExporter)


# ──────────────── JsonFileExporter ────────────────


class TestJsonFileExporter:
    @pytest.mark.asyncio
    async def test_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = JsonFileExporter(log_dir=tmpdir)
            event = _make_event()
            await exporter.export(event)

            files = list(Path(tmpdir).glob("audit-*.jsonl"))
            assert len(files) == 1
            content = files[0].read_text(encoding="utf-8")
            parsed = json.loads(content.strip())
            assert parsed["trace_id"] == "t1"

    @pytest.mark.asyncio
    async def test_appends_multiple_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = JsonFileExporter(log_dir=tmpdir)
            for i in range(5):
                await exporter.export(_make_event(trace_id=f"t{i}"))

            files = list(Path(tmpdir).glob("audit-*.jsonl"))
            lines = files[0].read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 5


# ──────────────── StdoutExporter ────────────────


class TestStdoutExporter:
    @pytest.mark.asyncio
    async def test_export_does_not_raise(self, capsys):
        exporter = StdoutExporter()
        event = _make_event(latency_ms=100, tokens_input=50, tokens_output=30)
        await exporter.export(event)
        captured = capsys.readouterr()
        assert "[AUDIT]" in captured.err
        assert "tool.call" in captured.err


# ──────────────── AsyncPipeline ────────────────


class TestAsyncPipeline:
    @pytest.mark.asyncio
    async def test_enqueue_and_export(self):
        mock_exporter = AsyncMock()
        pipeline = AsyncPipeline(exporters=[mock_exporter])
        await pipeline.start()

        event = _make_event()
        pipeline.enqueue(event)
        await pipeline.flush()
        await pipeline.shutdown()

        mock_exporter.export.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_multiple_exporters(self):
        mock1 = AsyncMock()
        mock2 = AsyncMock()
        pipeline = AsyncPipeline(exporters=[mock1, mock2])
        await pipeline.start()

        event = _make_event()
        pipeline.enqueue(event)
        await pipeline.flush()
        await pipeline.shutdown()

        mock1.export.assert_called_once_with(event)
        mock2.export.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_exporter_failure_does_not_block(self):
        failing = AsyncMock()
        failing.export.side_effect = RuntimeError("boom")
        working = AsyncMock()
        pipeline = AsyncPipeline(exporters=[failing, working])
        await pipeline.start()

        event = _make_event()
        pipeline.enqueue(event)
        await pipeline.flush()
        await pipeline.shutdown()

        working.export.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_queue_full_drops_oldest(self):
        mock_exporter = AsyncMock()
        pipeline = AsyncPipeline(
            exporters=[mock_exporter], max_queue=3
        )

        for i in range(5):
            pipeline.enqueue(_make_event(trace_id=f"t{i}"))

        assert pipeline._queue.qsize() == 3

    @pytest.mark.asyncio
    async def test_many_events(self):
        mock_exporter = AsyncMock()
        pipeline = AsyncPipeline(exporters=[mock_exporter])
        await pipeline.start()

        for i in range(100):
            pipeline.enqueue(_make_event(trace_id=f"t{i}"))

        await pipeline.flush()
        await pipeline.shutdown()
        assert mock_exporter.export.call_count == 100

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self):
        mock_exporter = AsyncMock()
        pipeline = AsyncPipeline(exporters=[mock_exporter])
        await pipeline.start()
        await pipeline.shutdown()
        await pipeline.shutdown()


# ──────────────── Refactored AuditLogger ────────────────


class TestAuditLoggerWithPipeline:
    def setup_method(self):
        AuditLogger.reset()

    @pytest.mark.asyncio
    async def test_emit_enqueues_to_pipeline(self):
        mock_exporter = AsyncMock()
        pipeline = AsyncPipeline(exporters=[mock_exporter])
        logger = AuditLogger(
            sampler=SamplingPolicy(success_rate=1.0),
            pipeline=pipeline,
        )
        logger._enabled = True
        await pipeline.start()

        event = _make_event()
        logger.emit(event)

        await pipeline.flush()
        await pipeline.shutdown()
        mock_exporter.export.assert_called_once()

    @pytest.mark.asyncio
    async def test_emit_sanitizes_before_enqueue(self):
        mock_exporter = AsyncMock()
        pipeline = AsyncPipeline(exporters=[mock_exporter])
        logger = AuditLogger(
            sampler=SamplingPolicy(success_rate=1.0),
            pipeline=pipeline,
        )
        logger._enabled = True
        await pipeline.start()

        event = _make_event(data={"password": "secret123"})
        logger.emit(event)

        await pipeline.flush()
        await pipeline.shutdown()

        exported_event = mock_exporter.export.call_args[0][0]
        assert exported_event.data["password"] == "[REDACTED]"

    @pytest.mark.asyncio
    async def test_emit_respects_sampling(self):
        mock_exporter = AsyncMock()
        pipeline = AsyncPipeline(exporters=[mock_exporter])
        logger = AuditLogger(
            sampler=SamplingPolicy(success_rate=0.0),
            pipeline=pipeline,
        )
        logger._enabled = True
        await pipeline.start()

        logger.emit(_make_event())

        await pipeline.flush()
        await pipeline.shutdown()
        mock_exporter.export.assert_not_called()

    @pytest.mark.asyncio
    async def test_jsonfile_integration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = JsonFileExporter(log_dir=tmpdir)
            pipeline = AsyncPipeline(exporters=[exporter])
            logger = AuditLogger(
                sampler=SamplingPolicy(success_rate=1.0),
                pipeline=pipeline,
            )
            logger._enabled = True
            await pipeline.start()

            for i in range(10):
                logger.emit(_make_event(trace_id=f"t{i}"))

            await pipeline.flush()
            await pipeline.shutdown()

            files = list(Path(tmpdir).glob("audit-*.jsonl"))
            assert len(files) == 1
            lines = (
                files[0].read_text(encoding="utf-8").strip().split("\n")
            )
            assert len(lines) == 10
