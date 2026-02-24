"""
SQLiteAuditStorage — Backend de persistencia basado en SQLite + aiosqlite.

Características:
- Async nativo via aiosqlite
- WAL mode para writes concurrentes sin bloqueo de reads
- Índices optimizados para las queries más comunes (session, fecha, event_type)
- Esquema append-only: nunca se actualizan registros, solo se insertan
- La tabla `sessions` es un resumen mutable que se upserta con cada nuevo record

Schema:
  sessions      → resumen por sesión (1 fila por sesión)
  audit_records → eventos individuales (N filas por sesión, append-only)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import aiosqlite

from .base import BaseAuditStorage
from ..models import AuditRecord, EventType, SessionSummary, TokenUsage

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT    PRIMARY KEY,
    created_at          TEXT    NOT NULL,
    model_id            TEXT    NOT NULL,
    prompt_versions     TEXT    NOT NULL DEFAULT '{}',
    total_records       INTEGER NOT NULL DEFAULT 0,
    total_latency_ms    INTEGER NOT NULL DEFAULT 0,
    total_input_tokens  INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    has_error           INTEGER NOT NULL DEFAULT 0,
    user_query          TEXT,
    final_response      TEXT
);

CREATE TABLE IF NOT EXISTS audit_records (
    audit_id        TEXT    PRIMARY KEY,
    session_id      TEXT    NOT NULL REFERENCES sessions(session_id),
    sequence_num    INTEGER NOT NULL DEFAULT 0,
    timestamp       TEXT    NOT NULL,
    event_type      TEXT    NOT NULL,
    agent_name      TEXT,
    model_id        TEXT    NOT NULL,
    prompt_name     TEXT,
    prompt_version  TEXT,
    prompt_hash     TEXT,
    input_payload   TEXT,
    output_payload  TEXT,
    tool_name       TEXT,
    latency_ms      INTEGER,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cache_hit       INTEGER,
    is_error        INTEGER NOT NULL DEFAULT 0,
    error_payload   TEXT,
    content_hash    TEXT
);

CREATE INDEX IF NOT EXISTS idx_records_session
    ON audit_records (session_id, sequence_num ASC);

CREATE INDEX IF NOT EXISTS idx_records_event_type
    ON audit_records (event_type);

CREATE INDEX IF NOT EXISTS idx_sessions_created
    ON sessions (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sessions_has_error
    ON sessions (has_error);
"""

_INSERT_RECORD = """
INSERT INTO audit_records (
    audit_id, session_id, sequence_num, timestamp, event_type,
    agent_name, model_id, prompt_name, prompt_version, prompt_hash,
    input_payload, output_payload, tool_name, latency_ms,
    input_tokens, output_tokens, cache_hit, is_error, error_payload, content_hash
) VALUES (
    ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?
)
"""

_UPSERT_SESSION = """
INSERT INTO sessions (
    session_id, created_at, model_id, prompt_versions,
    total_records, total_latency_ms, total_input_tokens, total_output_tokens,
    has_error, user_query, final_response
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    total_records       = excluded.total_records,
    total_latency_ms    = excluded.total_latency_ms,
    total_input_tokens  = excluded.total_input_tokens,
    total_output_tokens = excluded.total_output_tokens,
    has_error           = excluded.has_error,
    user_query          = COALESCE(excluded.user_query,     sessions.user_query),
    final_response      = COALESCE(excluded.final_response, sessions.final_response),
    prompt_versions     = excluded.prompt_versions
"""


class SQLiteAuditStorage(BaseAuditStorage):

    def __init__(self, db_path: str = "data/audit.db") -> None:
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_DDL)
        await self._db.commit()

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    async def save_record(self, record: AuditRecord) -> None:
        inp = record.token_usage.input_tokens if record.token_usage else None
        out = record.token_usage.output_tokens if record.token_usage else None

        await self._db.execute(_INSERT_RECORD, (
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
            _bool_to_int(record.cache_hit),
            int(record.is_error),
            _dump(record.error_payload),
            record.content_hash,
        ))
        await self._db.commit()

    async def upsert_session(self, summary: SessionSummary) -> None:
        await self._db.execute(_UPSERT_SESSION, (
            summary.session_id,
            summary.created_at,
            summary.model_id,
            json.dumps(summary.prompt_versions),
            summary.total_records,
            summary.total_latency_ms,
            summary.total_input_tokens,
            summary.total_output_tokens,
            int(summary.has_error),
            summary.user_query,
            summary.final_response,
        ))
        await self._db.commit()

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    async def get_session_records(self, session_id: str) -> list[AuditRecord]:
        cur = await self._db.execute(
            "SELECT * FROM audit_records WHERE session_id = ? ORDER BY sequence_num ASC",
            (session_id,),
        )
        rows = await cur.fetchall()
        return [_row_to_record(dict(row)) for row in rows]

    async def get_session_summary(self, session_id: str) -> Optional[SessionSummary]:
        cur = await self._db.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = await cur.fetchone()
        return _row_to_summary(dict(row)) if row else None

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        has_error: Optional[bool] = None,
    ) -> list[SessionSummary]:
        where, params = "", []
        if has_error is not None:
            where = "WHERE has_error = ?"
            params.append(int(has_error))

        params.extend([limit, offset])
        cur = await self._db.execute(
            f"SELECT * FROM sessions {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        )
        rows = await cur.fetchall()
        return [_row_to_summary(dict(row)) for row in rows]

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None


# ------------------------------------------------------------------
# Helpers de serialización / deserialización
# ------------------------------------------------------------------

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


def _bool_to_int(v: Optional[bool]) -> Optional[int]:
    return None if v is None else int(v)


def _row_to_record(d: dict) -> AuditRecord:
    token_usage = None
    inp = d.get("input_tokens")
    out = d.get("output_tokens")
    if inp is not None or out is not None:
        token_usage = TokenUsage(
            input_tokens=inp or 0,
            output_tokens=out or 0,
            total_tokens=(inp or 0) + (out or 0),
        )

    cache_raw = d.get("cache_hit")
    cache_hit = None if cache_raw is None else bool(cache_raw)

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
        is_error=bool(d.get("is_error", 0)),
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
        has_error=bool(d.get("has_error", 0)),
        user_query=d.get("user_query"),
        final_response=d.get("final_response"),
    )
