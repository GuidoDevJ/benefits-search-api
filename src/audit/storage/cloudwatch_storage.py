"""
CloudWatchAuditStorage — Backend de persistencia basado en AWS CloudWatch Logs + Metrics.

Características:
- Serverless: sin base de datos que gestionar
- Integrado en el ecosistema AWS (misma cuenta que Bedrock)
- CloudWatch Logs Insights para consultas (eventual consistency ~segundos)
- CloudWatch Metrics para KPIs: latencia, tokens, error rate
- Log Streams diarios (YYYY/MM/DD) para distribución de carga
- Retention policy configurable (default: 90 días)

Log Groups:
  /comafi/audit/records  → un evento JSON por AuditRecord
  /comafi/audit/sessions → snapshot de SessionSummary (último valor por session_id)

Read path via CloudWatch Logs Insights:
  - get_session_records → filtra por session_id en records log group
  - get_session_summary → sort desc + limit 1 en sessions log group
  - list_sessions       → dedup por session_id en sessions log group

CloudWatch Metrics (namespace: comafi/audit):
  - LatencyMs   (dims: event_type, agent_name)
  - InputTokens / OutputTokens
  - ErrorCount
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from functools import partial
from typing import Any, Optional

import boto3

from .base import BaseAuditStorage
from ..models import AuditRecord, EventType, SessionSummary, TokenUsage

_LOG_GROUP_RECORDS  = "/comafi/audit/records"
_LOG_GROUP_SESSIONS = "/comafi/audit/sessions"
_METRICS_NAMESPACE  = "comafi/audit"
_RETENTION_DAYS     = 90
_QUERY_TIMEOUT_SEC  = 30


class CloudWatchAuditStorage(BaseAuditStorage):
    """
    Backend CloudWatch Logs + Metrics para el sistema de auditoría.

    La escritura usa boto3 (síncrono) envuelto en asyncio.run_in_executor.
    La lectura usa CloudWatch Logs Insights (start_query → polling → resultados).
    """

    def __init__(
        self,
        region: str = "us-east-1",
        log_group_records: str = _LOG_GROUP_RECORDS,
        log_group_sessions: str = _LOG_GROUP_SESSIONS,
        retention_days: int = _RETENTION_DAYS,
    ) -> None:
        self._region = region
        self._log_group_records = log_group_records
        self._log_group_sessions = log_group_sessions
        self._retention_days = retention_days
        self._logs: Any = None     # boto3 CloudWatch Logs client
        self._metrics: Any = None  # boto3 CloudWatch client
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def initialize(self) -> None:
        self._loop = asyncio.get_event_loop()
        self._logs = boto3.client("logs", region_name=self._region)
        self._metrics = boto3.client("cloudwatch", region_name=self._region)

        for group in (self._log_group_records, self._log_group_sessions):
            await self._run(self._ensure_log_group, group)

    # ------------------------------------------------------------------
    # Helpers de ejecución en executor
    # ------------------------------------------------------------------

    async def _run(self, fn, *args, **kwargs):
        """Ejecuta una función boto3 (síncrona) en el thread pool del loop."""
        return await self._loop.run_in_executor(None, partial(fn, *args, **kwargs))

    def _ensure_log_group(self, group: str) -> None:
        try:
            self._logs.create_log_group(logGroupName=group)
            self._logs.put_retention_policy(
                logGroupName=group,
                retentionInDays=self._retention_days,
            )
        except self._logs.exceptions.ResourceAlreadyExistsException:
            pass

    def _ensure_log_stream(self, group: str, stream: str) -> None:
        try:
            self._logs.create_log_stream(logGroupName=group, logStreamName=stream)
        except self._logs.exceptions.ResourceAlreadyExistsException:
            pass

    @staticmethod
    def _daily_stream() -> str:
        """Log Stream diario: YYYY/MM/DD"""
        return datetime.now(timezone.utc).strftime("%Y/%m/%d")

    def _put_event(self, group: str, message: str) -> None:
        """Escribe un único evento JSON en el log stream diario."""
        stream = self._daily_stream()
        self._ensure_log_stream(group, stream)
        ts_ms = int(time.time() * 1000)
        self._logs.put_log_events(
            logGroupName=group,
            logStreamName=stream,
            logEvents=[{"timestamp": ts_ms, "message": message}],
        )

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    async def save_record(self, record: AuditRecord) -> None:
        payload = record.model_dump(mode="json")
        payload["event_type"] = record.event_type.value
        if record.token_usage:
            payload["token_usage"] = record.token_usage.model_dump()
        message = json.dumps(payload, default=str)

        await self._run(self._put_event, self._log_group_records, message)
        await self._run(self._publish_metrics, record)

    def _publish_metrics(self, record: AuditRecord) -> None:
        """Publica métricas numéricas en CloudWatch Metrics."""
        metric_data = []
        dims = [{"Name": "event_type", "Value": record.event_type.value}]
        if record.agent_name:
            dims.append({"Name": "agent_name", "Value": record.agent_name})

        if record.latency_ms is not None:
            metric_data.append({
                "MetricName": "LatencyMs",
                "Dimensions": dims,
                "Value": float(record.latency_ms),
                "Unit": "Milliseconds",
            })

        if record.token_usage:
            metric_data.append({
                "MetricName": "InputTokens",
                "Dimensions": dims,
                "Value": float(record.token_usage.input_tokens),
                "Unit": "Count",
            })
            metric_data.append({
                "MetricName": "OutputTokens",
                "Dimensions": dims,
                "Value": float(record.token_usage.output_tokens),
                "Unit": "Count",
            })

        if record.is_error:
            metric_data.append({
                "MetricName": "ErrorCount",
                "Dimensions": dims,
                "Value": 1.0,
                "Unit": "Count",
            })

        if metric_data:
            self._metrics.put_metric_data(
                Namespace=_METRICS_NAMESPACE,
                MetricData=metric_data,
            )

    async def upsert_session(self, summary: SessionSummary) -> None:
        """Escribe el estado actual de la sesión en el log group de sessions."""
        payload = summary.model_dump(mode="json")
        message = json.dumps(payload, default=str)
        await self._run(self._put_event, self._log_group_sessions, message)

    # ------------------------------------------------------------------
    # Read path — CloudWatch Logs Insights
    # ------------------------------------------------------------------

    async def _run_insights_query(
        self,
        log_group: str,
        query_string: str,
        hours_back: int = 24 * 7,
    ) -> list[dict]:
        """
        Ejecuta una query de CloudWatch Logs Insights y espera el resultado.
        Timeout: _QUERY_TIMEOUT_SEC segundos.

        start_time siempre se acota al retention configurado - 1 día para
        evitar MalformedQueryException cuando el rango excede la retención
        o cae antes de la creación del log group.
        """
        end_time = int(time.time())

        # Nunca superar la retention configurada (margen de 1 día)
        max_lookback_secs = (self._retention_days - 1) * 86400
        lookback_secs = min(hours_back * 3600, max_lookback_secs)
        start_time = end_time - lookback_secs

        try:
            resp = await self._run(
                self._logs.start_query,
                logGroupName=log_group,
                startTime=start_time,
                endTime=end_time,
                queryString=query_string,
            )
        except Exception as exc:
            print(f"[CW] StartQuery error on {log_group}: {exc}")
            return []

        query_id = resp["queryId"]

        deadline = time.time() + _QUERY_TIMEOUT_SEC
        while time.time() < deadline:
            try:
                result = await self._run(
                    self._logs.get_query_results, queryId=query_id
                )
            except Exception as exc:
                print(f"[CW] GetQueryResults error: {exc}")
                return []

            status = result["status"]
            if status in ("Complete", "Failed", "Cancelled", "Timeout"):
                if status != "Complete":
                    return []
                return [
                    {item["field"]: item["value"] for item in row}
                    for row in result.get("results", [])
                ]
            await asyncio.sleep(1)

        return []

    async def get_session_records(self, session_id: str) -> list[AuditRecord]:
        # like hace substring search sobre el JSON crudo — nunca descarta filas
        query = (
            "fields @message"
            f' | filter @message like \'"session_id": "{session_id}"\''
            " | sort @timestamp asc"
            " | limit 1000"
        )
        rows = await self._run_insights_query(self._log_group_records, query)
        records = []
        for row in rows:
            try:
                data = json.loads(row.get("@message", "{}"))
                # Verificar que el session_id corresponde exactamente
                if data.get("session_id") == session_id:
                    records.append(_dict_to_record(data))
            except Exception:
                pass
        return records

    async def get_session_summary(self, session_id: str) -> Optional[SessionSummary]:
        query = (
            "fields @message"
            f' | filter @message like \'"session_id": "{session_id}"\''
            " | sort @timestamp desc"
            " | limit 1"
        )
        rows = await self._run_insights_query(self._log_group_sessions, query)
        if not rows:
            return None
        try:
            data = json.loads(rows[0].get("@message", "{}"))
            return _dict_to_summary(data)
        except Exception:
            return None

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        has_error: Optional[bool] = None,
    ) -> list[SessionSummary]:
        # Traer más eventos de los necesarios y deduplicar en Python
        # (más confiable que depender de parse+dedup en CWL Insights)
        error_filter = ""
        if has_error is not None:
            val = "true" if has_error else "false"
            error_filter = f' | filter @message like \'"has_error": {val}\''

        fetch_limit = (limit + offset) * 5  # margen para dedup
        query = (
            "fields @message"
            f"{error_filter}"
            " | sort @timestamp desc"
            f" | limit {fetch_limit}"
        )
        rows = await self._run_insights_query(self._log_group_sessions, query)

        # Dedup en Python: quedarse con el snapshot más reciente por session_id
        seen: set = set()
        summaries: list[SessionSummary] = []
        for row in rows:
            try:
                data = json.loads(row.get("@message", "{}"))
                sid = data.get("session_id")
                if not sid or sid in seen:
                    continue
                seen.add(sid)
                summaries.append(_dict_to_summary(data))
            except Exception:
                pass

        return summaries[offset: offset + limit]

    async def close(self) -> None:
        # boto3 clients no requieren cierre explícito
        self._logs = None
        self._metrics = None


# --------------------------------------------------------------------------
# Helpers de deserialización
# --------------------------------------------------------------------------

def _dict_to_record(d: dict) -> AuditRecord:
    token_usage = None
    tu = d.get("token_usage")
    if tu and isinstance(tu, dict):
        token_usage = TokenUsage(
            input_tokens=tu.get("input_tokens", 0),
            output_tokens=tu.get("output_tokens", 0),
            total_tokens=tu.get("total_tokens", 0),
        )

    return AuditRecord(
        audit_id=d["audit_id"],
        session_id=d["session_id"],
        sequence_num=d.get("sequence_num", 0),
        timestamp=d["timestamp"],
        event_type=EventType(d["event_type"]),
        agent_name=d.get("agent_name"),
        model_id=d["model_id"],
        prompt_name=d.get("prompt_name"),
        prompt_version=d.get("prompt_version"),
        prompt_hash=d.get("prompt_hash"),
        input_payload=d.get("input_payload"),
        output_payload=d.get("output_payload"),
        tool_name=d.get("tool_name"),
        latency_ms=d.get("latency_ms"),
        token_usage=token_usage,
        cache_hit=d.get("cache_hit"),
        is_error=bool(d.get("is_error", False)),
        error_payload=d.get("error_payload"),
        content_hash=d.get("content_hash"),
    )


def _dict_to_summary(d: dict) -> SessionSummary:
    return SessionSummary(
        session_id=d["session_id"],
        created_at=d["created_at"],
        model_id=d["model_id"],
        prompt_versions=d.get("prompt_versions") or {},
        total_records=d.get("total_records", 0),
        total_latency_ms=d.get("total_latency_ms", 0),
        total_input_tokens=d.get("total_input_tokens", 0),
        total_output_tokens=d.get("total_output_tokens", 0),
        has_error=bool(d.get("has_error", False)),
        user_query=d.get("user_query"),
        final_response=d.get("final_response"),
    )
