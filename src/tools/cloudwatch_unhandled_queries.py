"""
CloudWatch Tool para guardar queries con intención no identificada.

Reemplaza el módulo S3 anterior. Escribe cada query no identificada
como un evento JSON en CloudWatch Logs, en el log group configurado
por CW_LOG_GROUP_UNHANDLED (default: /comafi/unhandled-queries).

Ventajas sobre S3:
- Sin bucket que gestionar ni políticas de ciclo de vida
- CloudWatch Logs Insights para análisis inmediato
- Integrado en el mismo stack de observabilidad que el sistema de audit
- Retención configurable (default: 90 días)
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from functools import partial
from typing import Any, Optional

import boto3
from pydantic import BaseModel

try:
    from ..config import AWS_REGION, CW_LOG_GROUP_UNHANDLED, CW_RETENTION_DAYS
except ImportError:
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from src.config import (
        AWS_REGION,
        CW_LOG_GROUP_UNHANDLED,
        CW_RETENTION_DAYS,
    )


class UnhandledQuery(BaseModel):
    """Modelo para queries no identificadas."""
    id: str
    timestamp: str
    query: str
    detected_intent: str
    entities: dict
    reason: str
    session_id: Optional[str] = None
    metadata: Optional[dict] = None


class CloudWatchUnhandledService:
    """Servicio async para guardar queries no identificadas en CloudWatch Logs."""

    def __init__(self) -> None:
        self._client: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client("logs", region_name=AWS_REGION)
        return self._client

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    @staticmethod
    def _daily_stream() -> str:
        return datetime.now(timezone.utc).strftime("%Y/%m/%d")

    def _ensure_log_group(self) -> None:
        client = self._get_client()
        try:
            client.create_log_group(logGroupName=CW_LOG_GROUP_UNHANDLED)
            client.put_retention_policy(
                logGroupName=CW_LOG_GROUP_UNHANDLED,
                retentionInDays=CW_RETENTION_DAYS,
            )
        except client.exceptions.ResourceAlreadyExistsException:
            pass

    def _ensure_log_stream(self, stream: str) -> None:
        client = self._get_client()
        try:
            client.create_log_stream(
                logGroupName=CW_LOG_GROUP_UNHANDLED,
                logStreamName=stream,
            )
        except client.exceptions.ResourceAlreadyExistsException:
            pass

    def _put_event(self, message: str) -> None:
        stream = self._daily_stream()
        self._ensure_log_group()
        self._ensure_log_stream(stream)
        ts_ms = int(time.time() * 1000)
        self._get_client().put_log_events(
            logGroupName=CW_LOG_GROUP_UNHANDLED,
            logStreamName=stream,
            logEvents=[{"timestamp": ts_ms, "message": message}],
        )

    async def save_unhandled_query(
        self,
        query: str,
        detected_intent: str,
        entities: dict,
        reason: str = "unknown_intent",
        session_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """
        Guarda una query no identificada en CloudWatch Logs.

        Returns:
            dict con id, log_group y success status
        """
        query_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        record = UnhandledQuery(
            id=query_id,
            timestamp=timestamp,
            query=query,
            detected_intent=detected_intent,
            entities=entities,
            reason=reason,
            session_id=session_id,
            metadata=metadata or {},
        )

        message = record.model_dump_json()

        try:
            await self._run(self._put_event, message)
            print(
                f"[CW] Unhandled query guardada: "
                f"{CW_LOG_GROUP_UNHANDLED}/{self._daily_stream()}"
            )
            return {
                "success": True,
                "id": query_id,
                "log_group": CW_LOG_GROUP_UNHANDLED,
            }

        except Exception as e:
            print(f"[CW] Error guardando unhandled query: {e}")
            return {
                "success": False,
                "id": query_id,
                "error": str(e),
            }


# Singleton del servicio
_cw_service: Optional[CloudWatchUnhandledService] = None


async def get_cw_service() -> CloudWatchUnhandledService:
    """Obtiene la instancia singleton del servicio CloudWatch."""
    global _cw_service
    if _cw_service is None:
        _cw_service = CloudWatchUnhandledService()
    return _cw_service


# =========================
# Tool function para usar en agentes
# =========================


async def save_unhandled_query_tool(
    query: str,
    intent: str,
    entities: dict,
    reason: str = "unknown_intent",
) -> str:
    """
    Tool para guardar queries no identificadas en CloudWatch Logs.

    Args:
        query: Consulta original del usuario
        intent: Intención detectada
        entities: Entidades extraídas
        reason: Razón del guardado

    Returns:
        JSON string con el resultado
    """
    service = await get_cw_service()
    result = await service.save_unhandled_query(
        query=query,
        detected_intent=intent,
        entities=entities,
        reason=reason,
    )
    return json.dumps(result)
