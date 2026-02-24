"""
AuditService — Fachada principal del sistema de auditoría.

Principios de diseño:
  1. NUNCA propaga excepciones al flujo principal.
     Si el audit falla, el agente sigue funcionando.
  2. Mantiene un resumen de sesión en memoria y lo persiste
     de forma incremental con cada record guardado.
  3. Es un singleton a nivel de módulo (get_audit_service()).
  4. El caller solo necesita conocer esta clase; no interactúa
     directamente con el storage.
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from .models import AuditRecord, EventType, SessionSummary, TokenUsage
from .prompt_registry import get_prompt_registry
from .storage.base import BaseAuditStorage


class AuditService:

    def __init__(self, storage: BaseAuditStorage) -> None:
        """
        Args:
            storage: Backend de persistencia (SQLiteAuditStorage o
                     PostgresAuditStorage). Se inyecta desde el exterior
                     para que AuditService sea agnóstico al backend.
        """
        self._storage = storage
        # Cache en memoria para evitar un SELECT por cada record guardado
        self._sessions: dict[str, SessionSummary] = {}
        self._seq: dict[str, int] = {}  # contador de secuencia por sesión

    async def initialize(self) -> None:
        await self._storage.initialize()

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    def _next_seq(self, session_id: str) -> int:
        n = self._seq.get(session_id, 0) + 1
        self._seq[session_id] = n
        return n

    async def _ensure_session(self, session_id: str, model_id: str) -> None:
        """Crea el resumen de sesión en memoria y en DB si no existe."""
        if session_id not in self._sessions:
            registry = get_prompt_registry()
            self._sessions[session_id] = SessionSummary(
                session_id=session_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                model_id=model_id,
                prompt_versions=registry.get_all_current_versions(),
            )
            # Persistir la sesión vacía para que sea visible aunque la app cierre antes
            # de que se genere el primer record
            await self._storage.upsert_session(self._sessions[session_id])

    async def _persist(self, record: AuditRecord) -> None:
        """Guarda el record y actualiza el resumen de sesión. Silencia errores."""
        try:
            await self._storage.save_record(record)

            sess = self._sessions.get(record.session_id)
            if sess is None:
                return

            sess.total_records += 1
            if record.latency_ms:
                sess.total_latency_ms += record.latency_ms
            if record.token_usage:
                sess.total_input_tokens += record.token_usage.input_tokens
                sess.total_output_tokens += record.token_usage.output_tokens
            if record.is_error:
                sess.has_error = True

            await self._storage.upsert_session(sess)

        except Exception as exc:
            # Audit nunca rompe el flujo principal
            print(f"[AUDIT][ERROR] persist falló: {exc}")

    # ------------------------------------------------------------------
    # API pública de registro
    # ------------------------------------------------------------------

    async def record_user_input(
        self,
        session_id: str,
        model_id: str,
        query: str,
        nlp_result: Optional[dict] = None,
    ) -> None:
        try:
            await self._ensure_session(session_id, model_id)
            self._sessions[session_id].user_query = query

            record = AuditRecord(
                session_id=session_id,
                sequence_num=self._next_seq(session_id),
                event_type=EventType.USER_INPUT,
                model_id=model_id,
                input_payload={"query": query, "nlp_result": nlp_result},
            ).seal()

            await self._persist(record)

        except Exception as exc:
            print(f"[AUDIT][ERROR] record_user_input: {exc}")

    async def record_llm_call(
        self,
        session_id: str,
        model_id: str,
        agent_name: str,
        input_messages: list[dict],
        output_content: str,
        latency_ms: int,
        token_usage: Optional[TokenUsage] = None,
        prompt_name: Optional[str] = None,
        tool_calls_requested: Optional[list] = None,
    ) -> None:
        try:
            registry = get_prompt_registry()
            prompt_meta: Optional[dict] = None
            if prompt_name:
                try:
                    prompt_meta = registry.get_version_metadata(prompt_name)
                except KeyError:
                    pass

            record = AuditRecord(
                session_id=session_id,
                sequence_num=self._next_seq(session_id),
                event_type=EventType.LLM_CALL,
                agent_name=agent_name,
                model_id=model_id,
                prompt_name=prompt_name,
                prompt_version=prompt_meta["version"] if prompt_meta else None,
                prompt_hash=prompt_meta["hash"] if prompt_meta else None,
                input_payload={"messages": input_messages},
                output_payload={
                    "content": output_content,
                    "tool_calls": tool_calls_requested,
                },
                latency_ms=latency_ms,
                token_usage=token_usage,
            ).seal()

            await self._persist(record)

        except Exception as exc:
            print(f"[AUDIT][ERROR] record_llm_call: {exc}")

    async def record_tool_execution(
        self,
        session_id: str,
        model_id: str,
        agent_name: str,
        tool_name: str,
        tool_args: dict,
        tool_result: Any,
        latency_ms: int,
        cache_hit: Optional[bool] = None,
        is_error: bool = False,
        error: Optional[Exception] = None,
    ) -> None:
        try:
            error_payload: Optional[dict] = None
            if error:
                error_payload = {
                    "type": type(error).__name__,
                    "message": str(error),
                }

            # Truncar el resultado para no explotar la DB con payloads enormes
            result_str = str(tool_result)
            if len(result_str) > 4000:
                result_str = result_str[:4000] + "…[truncado]"

            record = AuditRecord(
                session_id=session_id,
                sequence_num=self._next_seq(session_id),
                event_type=EventType.TOOL_CALL,
                agent_name=agent_name,
                model_id=model_id,
                tool_name=tool_name,
                input_payload={"tool_args": tool_args},
                output_payload={"result": result_str},
                latency_ms=latency_ms,
                cache_hit=cache_hit,
                is_error=is_error,
                error_payload=error_payload,
            ).seal()

            await self._persist(record)

        except Exception as exc:
            print(f"[AUDIT][ERROR] record_tool_execution: {exc}")

    async def record_supervisor_decision(
        self,
        session_id: str,
        model_id: str,
        decision: str,
        input_messages: list[dict],
        latency_ms: int,
        token_usage: Optional[TokenUsage] = None,
    ) -> None:
        try:
            registry = get_prompt_registry()
            try:
                prompt_meta = registry.get_version_metadata("supervisor")
            except KeyError:
                prompt_meta = None

            record = AuditRecord(
                session_id=session_id,
                sequence_num=self._next_seq(session_id),
                event_type=EventType.SUPERVISOR_DECISION,
                agent_name="supervisor",
                model_id=model_id,
                prompt_name="supervisor",
                prompt_version=prompt_meta["version"] if prompt_meta else None,
                prompt_hash=prompt_meta["hash"] if prompt_meta else None,
                input_payload={"messages": input_messages},
                output_payload={"decision": decision},
                latency_ms=latency_ms,
                token_usage=token_usage,
            ).seal()

            await self._persist(record)

        except Exception as exc:
            print(f"[AUDIT][ERROR] record_supervisor_decision: {exc}")

    async def record_final_response(
        self,
        session_id: str,
        model_id: str,
        response: str,
        total_latency_ms: int,
    ) -> None:
        try:
            # Snapshot en el resumen de sesión
            sess = self._sessions.get(session_id)
            if sess:
                sess.final_response = response[:500]
                await self._storage.upsert_session(sess)

            record = AuditRecord(
                session_id=session_id,
                sequence_num=self._next_seq(session_id),
                event_type=EventType.AGENT_RESPONSE,
                model_id=model_id,
                output_payload={"response": response},
                latency_ms=total_latency_ms,
            ).seal()

            await self._persist(record)

        except Exception as exc:
            print(f"[AUDIT][ERROR] record_final_response: {exc}")

    async def record_error(
        self,
        session_id: str,
        model_id: str,
        agent_name: Optional[str],
        error: Exception,
    ) -> None:
        try:
            record = AuditRecord(
                session_id=session_id,
                sequence_num=self._next_seq(session_id),
                event_type=EventType.ERROR,
                agent_name=agent_name,
                model_id=model_id,
                is_error=True,
                error_payload={
                    "type": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(),
                },
            ).seal()

            await self._persist(record)

        except Exception as exc:
            print(f"[AUDIT][ERROR] record_error: {exc}")

    # ------------------------------------------------------------------
    # API pública de consulta
    # ------------------------------------------------------------------

    async def get_session(self, session_id: str) -> Optional[SessionSummary]:
        return await self._storage.get_session_summary(session_id)

    async def get_session_records(self, session_id: str) -> list[AuditRecord]:
        return await self._storage.get_session_records(session_id)

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        has_error: Optional[bool] = None,
    ) -> list[SessionSummary]:
        return await self._storage.list_sessions(
            limit=limit, offset=offset, has_error=has_error
        )

    async def close(self) -> None:
        await self._storage.close()


# --------------------------------------------------------------------------
# Singleton a nivel de módulo
# --------------------------------------------------------------------------

_service: Optional[AuditService] = None


def _build_storage() -> BaseAuditStorage:
    """
    Factory de storage que lee la configuración en runtime.
    Soporta AUDIT_BACKEND=sqlite (default) o AUDIT_BACKEND=postgres.
    """
    from src.config import AUDIT_BACKEND, AUDIT_DB_PATH, POSTGRES_DSN

    backend = (AUDIT_BACKEND or "sqlite").lower().strip()

    if backend == "postgres":
        if not POSTGRES_DSN:
            raise ValueError(
                "AUDIT_BACKEND=postgres requiere que POSTGRES_DSN "
                "esté definido en .env\n"
                "Ejemplo: POSTGRES_DSN=postgresql://user:pass@host:5432/dbname"
            )
        from .storage.postgres_storage import PostgresAuditStorage
        return PostgresAuditStorage(dsn=POSTGRES_DSN)

    # Default: SQLite
    from .storage.sqlite_storage import SQLiteAuditStorage
    return SQLiteAuditStorage(db_path=AUDIT_DB_PATH)


async def get_audit_service() -> AuditService:
    """
    Retorna el singleton de AuditService.
    Inicializa el backend la primera vez que se llama.
    Thread-safe en un único event loop (asyncio standard).

    El backend se selecciona via AUDIT_BACKEND en .env:
        AUDIT_BACKEND=sqlite   → SQLite local (default)
        AUDIT_BACKEND=postgres → PostgreSQL via asyncpg
    """
    global _service
    if _service is None:
        storage = _build_storage()
        _service = AuditService(storage=storage)
        await _service.initialize()
    return _service
