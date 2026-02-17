"""Tests for audit system - Phase 1 + 2 (models, context, logger, sanitizer, sampler)."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.audit.context import TraceContext
from src.audit.models import AuditEvent, AuditEventType, ErrorDetail
from src.audit.sampler import SamplingPolicy
from src.audit.sanitizer import sanitize
from src.audit.logger import AuditLogger


# ──────────────────────────── Models ────────────────────────────


class TestAuditEvent:
    def test_create_minimal_event(self):
        event = AuditEvent(
            trace_id="abc123",
            span_id="def456",
            event_type=AuditEventType.TOOL_CALL,
            agent="benefits",
            action="search",
        )
        assert event.event_version == "3.0"
        assert event.status == "ok"
        assert event.data == {}
        assert event.error is None

    def test_create_full_event(self):
        event = AuditEvent(
            trace_id="abc123",
            span_id="def456",
            parent_span_id="parent01",
            event_type=AuditEventType.LLM_INVOKE,
            agent="benefits",
            action="invoke",
            status="ok",
            latency_ms=245.3,
            tokens_input=150,
            tokens_output=80,
            cost_usd=0.00035,
            data={"model": "claude-3-haiku"},
        )
        assert event.tokens_input == 150
        assert event.cost_usd == 0.00035

    def test_event_serializes_to_json(self):
        event = AuditEvent(
            trace_id="abc123",
            span_id="def456",
            event_type=AuditEventType.TOOL_CALL,
            agent="benefits",
            action="search",
        )
        json_str = event.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["event_version"] == "3.0"
        assert parsed["event_type"] == "tool.call"

    def test_all_event_types_have_dot_notation(self):
        for t in AuditEventType:
            assert "." in t.value, f"{t.name} should use dot notation"


class TestErrorDetail:
    def test_error_with_snapshot(self):
        error = ErrorDetail(
            type="ValueError",
            message="invalid input",
            traceback="Traceback ...",
            recoverable=False,
            input_snapshot={"query": "test"},
            environment={"model": "claude-3-haiku", "python": "3.11"},
        )
        assert error.recoverable is False
        assert error.input_snapshot["query"] == "test"


# ──────────────────────────── Context ────────────────────────────


class TestTraceContext:
    def test_new_trace_creates_ids(self):
        trace_id = TraceContext.new_trace()
        assert len(trace_id) == 12
        assert TraceContext.get_trace_id() == trace_id
        assert len(TraceContext.get_span_id()) == 8
        assert TraceContext.get_parent_span_id() is None

    def test_new_span_sets_parent(self):
        TraceContext.new_trace()
        first_span = TraceContext.get_span_id()
        second_span = TraceContext.new_span()
        assert TraceContext.get_parent_span_id() == first_span
        assert TraceContext.get_span_id() == second_span

    def test_traceparent_w3c_format(self):
        TraceContext.new_trace()
        tp = TraceContext.to_traceparent()
        parts = tp.split("-")
        assert len(parts) == 4
        assert parts[0] == "00"
        assert parts[3] == "01"

    def test_from_traceparent(self):
        TraceContext.from_traceparent("00-aabbccddee11-ff223344-01")
        assert TraceContext.get_trace_id() == "aabbccddee11"
        assert TraceContext.get_span_id() == "ff223344"


# ──────────────────────────── Sanitizer ────────────────────────────


class TestSanitizer:
    def test_redacts_email(self):
        data = {"user": "john@example.com"}
        result = sanitize(data)
        assert "john@example.com" not in str(result)
        assert "[REDACTED_EMAIL]" in result["user"]

    def test_redacts_credit_card(self):
        data = {"card": "4111-1111-1111-1111"}
        result = sanitize(data)
        assert "[REDACTED_CARD]" in result["card"]

    def test_redacts_jwt(self):
        fake_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiam9obiJ9.abc123def456"
        data = {"token_val": fake_jwt}
        result = sanitize(data)
        assert "[REDACTED_JWT]" in result["token_val"]

    def test_redacts_aws_key(self):
        data = {"key": "AKIAIOSFODNN7EXAMPLE"}
        result = sanitize(data)
        assert "[REDACTED_AWS_KEY]" in result["key"]

    def test_redacts_sensitive_keys(self):
        data = {"password": "super_secret", "api_key": "sk-123", "name": "John"}
        result = sanitize(data)
        assert result["password"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"
        assert result["name"] == "John"

    def test_deep_nested_sanitization(self):
        data = {
            "user": {
                "email": "john@example.com",
                "prefs": {"secret": "hidden"},
            },
            "items": [{"password": "abc"}, "normal"],
        }
        result = sanitize(data)
        assert "[REDACTED_EMAIL]" in result["user"]["email"]
        assert result["user"]["prefs"]["secret"] == "[REDACTED]"
        assert result["items"][0]["password"] == "[REDACTED]"
        assert result["items"][1] == "normal"

    def test_does_not_mutate_input(self):
        data = {"password": "original"}
        sanitize(data)
        assert data["password"] == "original"

    def test_handles_none(self):
        assert sanitize(None) is None

    def test_handles_plain_string(self):
        result = sanitize("contact john@example.com for info")
        assert "[REDACTED_EMAIL]" in result


# ──────────────────────────── Sampler ────────────────────────────


class TestSampler:
    def _make_event(self, status="ok", latency_ms=None):
        return AuditEvent(
            trace_id="t1",
            span_id="s1",
            event_type=AuditEventType.TOOL_CALL,
            agent="test",
            action="test",
            status=status,
            latency_ms=latency_ms,
        )

    def test_errors_always_recorded(self):
        sampler = SamplingPolicy(success_rate=0.0)
        event = self._make_event(status="error")
        assert sampler.should_record(event) is True

    def test_timeouts_always_recorded(self):
        sampler = SamplingPolicy(success_rate=0.0)
        event = self._make_event(status="timeout")
        assert sampler.should_record(event) is True

    def test_slow_requests_always_recorded(self):
        sampler = SamplingPolicy(success_rate=0.0, slow_threshold_ms=1000)
        event = self._make_event(latency_ms=1500)
        assert sampler.should_record(event) is True

    def test_success_sampled(self):
        sampler = SamplingPolicy(success_rate=0.0)
        event = self._make_event()
        assert sampler.should_record(event) is False

    def test_success_rate_100_always_records(self):
        sampler = SamplingPolicy(success_rate=1.0)
        event = self._make_event()
        assert sampler.should_record(event) is True

    @patch.dict(os.environ, {"AUDIT_DEBUG": "true"})
    def test_debug_mode_always_records(self):
        sampler = SamplingPolicy(success_rate=0.0)
        event = self._make_event()
        assert sampler.should_record(event) is True


# ──────────────────────────── Logger ────────────────────────────


class TestAuditLogger:
    def setup_method(self):
        AuditLogger.reset()

    def test_singleton(self):
        a = AuditLogger.get()
        b = AuditLogger.get()
        assert a is b

    def test_reset_clears_singleton(self):
        a = AuditLogger.get()
        AuditLogger.reset()
        b = AuditLogger.get()
        assert a is not b

    @pytest.mark.asyncio
    async def test_emit_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.audit.exporters.jsonfile import JsonFileExporter
            from src.audit.exporters.async_pipeline import AsyncPipeline

            exporter = JsonFileExporter(log_dir=tmpdir)
            pipeline = AsyncPipeline(exporters=[exporter])
            logger = AuditLogger(
                sampler=SamplingPolicy(success_rate=1.0),
                pipeline=pipeline,
            )
            logger._enabled = True
            await pipeline.start()

            event = AuditEvent(
                trace_id="t123",
                span_id="s456",
                event_type=AuditEventType.TOOL_CALL,
                agent="benefits",
                action="search",
            )
            logger.emit(event)
            await pipeline.flush()
            await pipeline.shutdown()

            files = list(Path(tmpdir).glob("audit-*.jsonl"))
            assert len(files) == 1
            content = files[0].read_text(encoding="utf-8")
            parsed = json.loads(content.strip())
            assert parsed["trace_id"] == "t123"
            assert parsed["event_type"] == "tool.call"

    @pytest.mark.asyncio
    async def test_emit_sanitizes_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.audit.exporters.jsonfile import JsonFileExporter
            from src.audit.exporters.async_pipeline import AsyncPipeline

            exporter = JsonFileExporter(log_dir=tmpdir)
            pipeline = AsyncPipeline(exporters=[exporter])
            logger = AuditLogger(
                sampler=SamplingPolicy(success_rate=1.0),
                pipeline=pipeline,
            )
            logger._enabled = True
            await pipeline.start()

            event = AuditEvent(
                trace_id="t1",
                span_id="s1",
                event_type=AuditEventType.TOOL_CALL,
                agent="test",
                action="test",
                data={"password": "secret123", "query": "supermercados"},
            )
            logger.emit(event)
            await pipeline.flush()
            await pipeline.shutdown()

            files = list(Path(tmpdir).glob("audit-*.jsonl"))
            content = files[0].read_text(encoding="utf-8")
            parsed = json.loads(content.strip())
            assert parsed["data"]["password"] == "[REDACTED]"
            assert parsed["data"]["query"] == "supermercados"

    @patch.dict(os.environ, {"AUDIT_ENABLED": "false"})
    def test_disabled_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(log_dir=tmpdir)
            event = AuditEvent(
                trace_id="t1",
                span_id="s1",
                event_type=AuditEventType.TOOL_CALL,
                agent="test",
                action="test",
            )
            logger.emit(event)
            files = list(Path(tmpdir).glob("audit-*.jsonl"))
            assert len(files) == 0

    def test_trace_propagation_in_events(self):
        """Verifica que trace_id se propaga entre spans."""
        TraceContext.new_trace()
        trace_id = TraceContext.get_trace_id()
        first_span = TraceContext.get_span_id()

        TraceContext.new_span()
        second_span = TraceContext.get_span_id()
        parent = TraceContext.get_parent_span_id()

        event1 = AuditEvent(
            trace_id=trace_id,
            span_id=first_span,
            event_type=AuditEventType.REQUEST_START,
            agent="api",
            action="start",
        )
        event2 = AuditEvent(
            trace_id=trace_id,
            span_id=second_span,
            parent_span_id=parent,
            event_type=AuditEventType.TOOL_CALL,
            agent="benefits",
            action="search",
        )

        assert event1.trace_id == event2.trace_id
        assert event2.parent_span_id == event1.span_id
