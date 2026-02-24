"""
src.audit — Sistema de auditoría modular para el agente de beneficios.

Exports principales:
    AuditService            → fachada principal, usar via get_audit_service()
    get_audit_service()     → singleton inicializado (backend según AUDIT_BACKEND)
    SessionReplayer         → motor de replay para debugging
    PromptRegistry          → versionado de prompts, usar via get_prompt_registry()
    get_prompt_registry()
    AuditCallbackHandler    → LangChain callback (uso opcional/complementario)
    SQLiteAuditStorage      → backend SQLite (default, AUDIT_BACKEND=sqlite)
    PostgresAuditStorage    → backend PostgreSQL (AUDIT_BACKEND=postgres)
    AuditRecord, SessionSummary, EventType, TokenUsage → modelos de datos
"""

from .audit_service import AuditService, get_audit_service
from .interceptor import AuditCallbackHandler
from .models import AuditRecord, EventType, SessionSummary, TokenUsage
from .prompt_registry import PromptRegistry, get_prompt_registry
from .replay import SessionReplayer
from .storage.postgres_storage import PostgresAuditStorage
from .storage.sqlite_storage import SQLiteAuditStorage

__all__ = [
    "AuditService",
    "get_audit_service",
    "AuditCallbackHandler",
    "AuditRecord",
    "EventType",
    "SessionSummary",
    "TokenUsage",
    "PromptRegistry",
    "get_prompt_registry",
    "SessionReplayer",
    "SQLiteAuditStorage",
    "PostgresAuditStorage",
]
