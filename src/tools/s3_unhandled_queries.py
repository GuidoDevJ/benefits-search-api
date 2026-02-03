"""
S3 Tool para guardar queries con intención no identificada.

Este módulo proporciona funcionalidad async para almacenar en S3
las consultas que el sistema no pudo clasificar, permitiendo
análisis posterior y mejora continua del NLP.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import aioboto3
from pydantic import BaseModel

# Importar config
try:
    from ..config import (
        AWS_ACCESS_KEY_ID,
        AWS_SECRET_ACCESS_KEY,
        AWS_REGION,
        S3_BUCKET_UNHANDLED,
    )
except ImportError:
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from src.config import (
        AWS_ACCESS_KEY_ID,
        AWS_SECRET_ACCESS_KEY,
        AWS_REGION,
        S3_BUCKET_UNHANDLED,
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


class S3UnhandledService:
    """Servicio async para guardar queries no identificadas en S3."""

    def __init__(self):
        self._session = None

    def _get_session(self):
        """Obtiene o crea la sesión de aioboto3."""
        if self._session is None:
            self._session = aioboto3.Session(
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_REGION,
            )
        return self._session

    def _generate_s3_key(self, query_id: str) -> str:
        """
        Genera la key de S3 con estructura de fecha.
        Formato: unhandled-queries/YYYY/MM/DD/{uuid}.json
        """
        now = datetime.now(timezone.utc)
        return f"unhandled-queries/{now.year}/{now.month:02d}/{now.day:02d}/{query_id}.json"

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
        Guarda una query no identificada en S3.

        Args:
            query: La consulta original del usuario
            detected_intent: La intención detectada (ej: "unknown")
            entities: Entidades extraídas del NLP
            reason: Razón del guardado (unknown_intent, no_results, etc.)
            session_id: ID de sesión opcional
            metadata: Metadata adicional opcional

        Returns:
            dict con id, s3_key y success status
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

        s3_key = self._generate_s3_key(query_id)

        try:
            session = self._get_session()
            async with session.client("s3") as s3:
                await s3.put_object(
                    Bucket=S3_BUCKET_UNHANDLED,
                    Key=s3_key,
                    Body=record.model_dump_json(indent=2),
                    ContentType="application/json",
                )

            print(f"[S3] Query guardada: {s3_key}")
            return {
                "success": True,
                "id": query_id,
                "s3_key": s3_key,
            }

        except Exception as e:
            print(f"[S3] Error guardando query: {e}")
            return {
                "success": False,
                "id": query_id,
                "error": str(e),
            }


# Singleton del servicio
_s3_service: Optional[S3UnhandledService] = None


async def get_s3_service() -> S3UnhandledService:
    """Obtiene la instancia singleton del servicio S3."""
    global _s3_service
    if _s3_service is None:
        _s3_service = S3UnhandledService()
    return _s3_service


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
    Tool para guardar queries no identificadas.

    Esta función puede ser usada como tool de LangChain.

    Args:
        query: Consulta original del usuario
        intent: Intención detectada
        entities: Entidades extraídas
        reason: Razón del guardado

    Returns:
        JSON string con el resultado
    """
    service = await get_s3_service()
    result = await service.save_unhandled_query(
        query=query,
        detected_intent=intent,
        entities=entities,
        reason=reason,
    )
    return json.dumps(result)


# =========================
# Demo / Test
# =========================

if __name__ == "__main__":
    import asyncio

    async def test():
        service = await get_s3_service()
        result = await service.save_unhandled_query(
            query="quiero comprar un auto usado",
            detected_intent="unknown",
            entities={"categoria": None, "negocio": None},
            reason="unknown_intent",
        )
        print(f"Resultado: {result}")

    asyncio.run(test())
