"""
PostgresAuditStorage — Backend de persistencia basado en PostgreSQL + asyncpg.

Características:
- Pool de conexiones async nativo via asyncpg (min=1, max=10 por defecto)
- Mismo esquema lógico que SQLiteAuditStorage → migración sin cambiar AuditService
- Parámetros posicionales ($1, $2, ...) según el protocolo PostgreSQL
- Auto-commit por statement; operaciones críticas envueltas en transacción explícita
- JSON almacenado como TEXT (paridad con SQLite, evita conversión implícita de JSONB)
- BOOLEAN nativo para is_error / has_error (más idiomático en PG que INTEGER 0/1)

Requisito:
    pip install asyncpg

DSN de conexión:
    postgresql://user:password@host:5432/dbname
    postgresql://user:password@host:5432/dbname?ssl=require  (para RDS, Supabase, etc.)
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .base import BaseAuditStorage
from ..models import AuditRecord, EventType, SessionSummary, TokenUsage

# --------------------------------------------------------------------------
# DDL — cada sentencia se ejecuta de forma independiente (asyncpg no tiene
# executescript). Se usa IF NOT EXISTS para ser idempotente en reinicios.
# --------------------------------------------------------------------------

_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id          TEXT        PRIMARY KEY,
        created_at          TEXT        NOT NULL,
        model_id            TEXT        NOT NULL,
        prompt_versions     TEXT        NOT NULL DEFAULT '{}',
        total_records       INTEGER     NOT NULL DEFAULT 0,
        total_latency_ms    INTEGER     NOT NULL DEFAULT 0,
        total_input_tokens  INTEGER     NOT NULL DEFAULT 0,
        total_output_tokens INTEGER     NOT NULL DEFAULT 0,
        has_error           BOOLEAN     NOT NULL DEFAULT FALSE,
        user_query          TEXT,
        final_response      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_records (
        audit_id        TEXT        PRIMARY KEY,
        session_id      TEXT        NOT NULL REFERENCES sessions(session_id),
        sequence_num    INTEGER     NOT NULL DEFAULT 0,
        timestamp       TEXT        NOT NULL,
        event_type      TEXT        NOT NULL,
        agent_name      TEXT,
        model_id        TEXT        NOT NULL,
        prompt_name     TEXT,
        prompt_version  TEXT,
        prompt_hash     TEXT,
        input_payload   TEXT,
        output_payload  TEXT,
        tool_name       TEXT,
        latency_ms      INTEGER,
        input_tokens    INTEGER,
        output_tokens   INTEGER,
        cache_hit       BOOLEAN,
        is_error        BOOLEAN     NOT NULL DEFAULT FALSE,
        error_payload   TEXT,
        content_hash    TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_records_session
        ON audit_records (session_id, sequence_num ASC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_records_event_type
        ON audit_records (event_type)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_created
        ON sessions (created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_has_error
        ON sessions (has_error)
    """,
]

_INSERT_RECORD = """
INSERT INTO audit_records (
    audit_id, session_id, sequence_num, timestamp, event_type,
    agent_name, model_id, prompt_name, prompt_version, prompt_hash,
    input_payload, output_payload, tool_name, latency_ms,
    input_tokens, output_tokens, cache_hit, is_error, error_payload, content_hash
) VALUES (
    $1, $2, $3, $4, $5,
    $6, $7, $8, $9, $10,
    $11, $12, $13, $14,
    $15, $16, $17, $18, $19, $20
)
"""

_UPSERT_SESSION = """
INSERT INTO sessions (
    session_id, created_at, model_id, prompt_versions,
    total_records, total_latency_ms, total_input_tokens, total_output_tokens,
    has_error, user_query, final_response
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (session_id) DO UPDATE SET
    total_records       = EXCLUDED.total_records,
    total_latency_ms    = EXCLUDED.total_latency_ms,
    total_input_tokens  = EXCLUDED.total_input_tokens,
    total_output_tokens = EXCLUDED.total_output_tokens,
    has_error           = EXCLUDED.has_error,
    user_query          = COALESCE(EXCLUDED.user_query,     sessions.user_query),
    final_response      = COALESCE(EXCLUDED.final_response, sessions.final_response),
    prompt_versions     = EXCLUDED.prompt_versions
"""


class PostgresAuditStorage(BaseAuditStorage):
    """
    Backend PostgreSQL para el sistema de auditoría.

    Usa asyncpg con pool de conexiones para máximo throughput async.
    La interfaz es idéntica a SQLiteAuditStorage — el AuditService
    no necesita saber con cuál backend está hablando.
    """

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 10) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Any = None  # asyncpg.Pool — importado en initialize()

    async def initialize(self) -> None:
        try:
            import asyncpg  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "asyncpg no está instalado. "
                "Ejecutá: pip install asyncpg"
            ) from exc

        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            command_timeout=30,
        )
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for stmt in _DDL_STATEMENTS:
                    await conn.execute(stmt)

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    async def save_record(self, record: AuditRecord) -> None:
        inp = record.token_usage.input_tokens if record.token_usage else None
        out = record.token_usage.output_tokens if record.token_usage else None

        async with self._pool.acquire() as conn:
            await conn.execute(_INSERT_RECORD,
                record.audit_id,
                record.session_id,
                record.sequence_num,
                record.timestamp,
                record.event_type.value,
                record.agent_name,
                record.model_id,
                record.prompt_name,
                record.prompt_version,
                record.prompt_hash,
                _dump(record.input_payload),
                _dump(record.output_payload),
                record.tool_name,
                record.latency_ms,
                inp,
                out,
                record.cache_hit,        # BOOLEAN → None | True | False
                record.is_error,
                _dump(record.error_payload),
                record.content_hash,
            )

    async def upsert_session(self, summary: SessionSummary) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_UPSERT_SESSION,
                summary.session_id,
                summary.created_at,
                summary.model_id,
                json.dumps(summary.prompt_versions),
                summary.total_records,
                summary.total_latency_ms,
                summary.total_input_tokens,
                summary.total_output_tokens,
                summary.has_error,       # BOOLEAN nativo
                summary.user_query,
                summary.final_response,
            )

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    async def get_session_records(self, session_id: str) -> list[AuditRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM audit_records "
                "WHERE session_id = $1 ORDER BY sequence_num ASC",
                session_id,
            )
        return [_row_to_record(dict(row)) for row in rows]

    async def get_session_summary(self, session_id: str) -> Optional[SessionSummary]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM sessions WHERE session_id = $1",
                session_id,
            )
        return _row_to_summary(dict(row)) if row else None

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        has_error: Optional[bool] = None,
    ) -> list[SessionSummary]:
        if has_error is not None:
            query = (
                "SELECT * FROM sessions WHERE has_error = $1 "
                "ORDER BY created_at DESC LIMIT $2 OFFSET $3"
            )
            params = (has_error, limit, offset)
        else:
            query = (
                "SELECT * FROM sessions "
                "ORDER BY created_at DESC LIMIT $1 OFFSET $2"
            )
            params = (limit, offset)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [_row_to_summary(dict(row)) for row in rows]

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None


# --------------------------------------------------------------------------
# Helpers compartidos (misma lógica que sqlite_storage para paridad)
# --------------------------------------------------------------------------

def _dump(obj) -> Optional[str]:
    if obj is None:
        return None
    return json.dumps(obj, default=str)


def _load(s: Optional[str]) -> Optional[dict]:
    if s is None:
        return None
    try:
        return json.loads(s)
    except Exception:
        return {"_raw": s}


def _row_to_record(d: dict) -> AuditRecord:
    inp = d.get("input_tokens")
    out = d.get("output_tokens")
    token_usage = None
    if inp is not None or out is not None:
        token_usage = TokenUsage(
            input_tokens=inp or 0,
            output_tokens=out or 0,
            total_tokens=(inp or 0) + (out or 0),
        )

    # PG devuelve BOOLEAN nativo; SQLite devuelve 0/1
    cache_raw = d.get("cache_hit")
    cache_hit: Optional[bool] = None if cache_raw is None else bool(cache_raw)

    return AuditRecord(
        audit_id=d["audit_id"],
        session_id=d["session_id"],
        sequence_num=d["sequence_num"],
        timestamp=d["timestamp"],
        event_type=EventType(d["event_type"]),
        agent_name=d.get("agent_name"),
        model_id=d["model_id"],
        prompt_name=d.get("prompt_name"),
        prompt_version=d.get("prompt_version"),
        prompt_hash=d.get("prompt_hash"),
        input_payload=_load(d.get("input_payload")),
        output_payload=_load(d.get("output_payload")),
        tool_name=d.get("tool_name"),
        latency_ms=d.get("latency_ms"),
        token_usage=token_usage,
        cache_hit=cache_hit,
        is_error=bool(d.get("is_error", False)),
        error_payload=_load(d.get("error_payload")),
        content_hash=d.get("content_hash"),
    )


def _row_to_summary(d: dict) -> SessionSummary:
    return SessionSummary(
        session_id=d["session_id"],
        created_at=d["created_at"],
        model_id=d["model_id"],
        prompt_versions=_load(d.get("prompt_versions")) or {},
        total_records=d.get("total_records", 0),
        total_latency_ms=d.get("total_latency_ms", 0),
        total_input_tokens=d.get("total_input_tokens", 0),
        total_output_tokens=d.get("total_output_tokens", 0),
        has_error=bool(d.get("has_error", False)),
        user_query=d.get("user_query"),
        final_response=d.get("final_response"),
    )
