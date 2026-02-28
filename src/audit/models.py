"""
Audit Models — Pydantic models para todos los eventos de auditoría.

Cada interacción con el sistema genera uno o más AuditRecord.
Una sesión agrupa todos los registros de una conversación.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Tipos de eventos que se registran en el sistema de auditoría."""
    USER_INPUT = "user_input"
    SUPERVISOR_DECISION = "supervisor_decision"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    AGENT_RESPONSE = "agent_response"
    ERROR = "error"


class TokenUsage(BaseModel):
    """Uso de tokens en una llamada al modelo."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_response_metadata(cls, metadata: dict) -> "TokenUsage":
        """
        Extrae token usage de los response_metadata de LangChain/Bedrock.

        Bedrock Converse API usa camelCase (inputTokens / outputTokens).
        Algunos wrappers usan snake_case (input_tokens / output_tokens).
        Se prueban ambos formatos.
        """
        usage = metadata.get("usage", {})
        inp = (
            usage.get("inputTokens")
            or usage.get("input_tokens")
            or 0
        )
        out = (
            usage.get("outputTokens")
            or usage.get("output_tokens")
            or 0
        )
        return cls(input_tokens=inp, output_tokens=out, total_tokens=inp + out)

    @classmethod
    def from_response(cls, response) -> "TokenUsage":
        """
        Extrae token usage de un AIMessage de LangChain.

        Orden de prioridad:
          1. response.usage_metadata      (LangChain ≥0.2, snake_case)
          2. response.response_metadata["usage"]  (Bedrock Converse, camelCase/snake)
          3. response.additional_kwargs["usage"]  (ChatBedrock legacy: prompt_tokens/
                                                   completion_tokens)

        Se imprimen los campos disponibles si los tres caminos devuelven 0,
        para facilitar el diagnóstico en producción.
        """
        def _pick(d: dict) -> tuple[int, int]:
            """Devuelve (input, output) probando todas las variantes de clave."""
            inp = (
                d.get("input_tokens")
                or d.get("inputTokens")
                or d.get("prompt_tokens")
                or 0
            )
            out = (
                d.get("output_tokens")
                or d.get("outputTokens")
                or d.get("completion_tokens")
                or 0
            )
            return int(inp), int(out)

        # 1. usage_metadata (TypedDict, ya en snake_case)
        um = getattr(response, "usage_metadata", None)
        if um and isinstance(um, dict):
            inp, out = _pick(um)
            if inp or out:
                return cls(input_tokens=inp, output_tokens=out, total_tokens=inp + out)

        # 2. response_metadata["usage"] (Bedrock Converse API)
        meta = getattr(response, "response_metadata", {}) or {}
        usage = meta.get("usage", {})
        if usage:
            inp, out = _pick(usage)
            if inp or out:
                return cls(input_tokens=inp, output_tokens=out, total_tokens=inp + out)

        # 3. additional_kwargs["usage"] (ChatBedrock legacy, prompt_tokens / completion_tokens)
        kwargs = getattr(response, "additional_kwargs", {}) or {}
        usage = kwargs.get("usage", {})
        if usage:
            inp, out = _pick(usage)
            if inp or out:
                return cls(input_tokens=inp, output_tokens=out, total_tokens=inp + out)

        # Nada encontrado — loguear para diagnóstico
        print(
            "[AUDIT][TOKEN] No se pudieron extraer tokens del response. "
            f"usage_metadata={um!r} | "
            f"response_metadata keys={list(meta.keys())} | "
            f"additional_kwargs keys={list(kwargs.keys())}"
        )
        return cls(input_tokens=0, output_tokens=0, total_tokens=0)


class AuditRecord(BaseModel):
    """
    Registro atómico de un evento de auditoría.

    Un 'audit_id' es único globalmente.
    Un 'session_id' agrupa todos los eventos de una conversación.
    El 'sequence_num' garantiza el orden de replay dentro de una sesión.
    """

    # Identidad
    audit_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    sequence_num: int = 0
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Clasificación
    event_type: EventType
    agent_name: Optional[str] = None

    # Modelo
    model_id: str

    # Prompt versionado
    prompt_name: Optional[str] = None
    prompt_version: Optional[str] = None
    prompt_hash: Optional[str] = None  # SHA256[:16] del contenido del prompt

    # Contenido de la interacción
    input_payload: Optional[dict[str, Any]] = None   # Lo que entró
    output_payload: Optional[dict[str, Any]] = None  # Lo que salió

    # Tool específico
    tool_name: Optional[str] = None

    # Métricas
    latency_ms: Optional[int] = None
    token_usage: Optional[TokenUsage] = None

    # Contexto adicional
    cache_hit: Optional[bool] = None

    # Estado de error
    is_error: bool = False
    error_payload: Optional[dict[str, Any]] = None

    # Integridad (SHA256 de input + output para detectar tampering)
    content_hash: Optional[str] = None

    def compute_content_hash(self) -> str:
        """Genera un hash SHA256 del par input/output para integridad."""
        payload = json.dumps(
            {"input": self.input_payload, "output": self.output_payload},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def seal(self) -> "AuditRecord":
        """Calcula y asigna el content_hash. Llamar antes de persistir."""
        self.content_hash = self.compute_content_hash()
        return self


class SessionSummary(BaseModel):
    """
    Resumen agregado de una sesión completa.
    Se actualiza incrementalmente con cada nuevo AuditRecord.
    """

    session_id: str
    created_at: str
    model_id: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)

    # Acumuladores
    total_records: int = 0
    total_latency_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    # Flags
    has_error: bool = False

    # Snapshots del primer y último mensaje
    user_query: Optional[str] = None
    final_response: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens
