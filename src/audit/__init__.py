"""
src.audit — Sistema de auditoría modular para el agente de beneficios.

Exports principales:
    AuditService              → fachada principal, usar via get_audit_service()
    get_audit_service()       → singleton inicializado (backend CloudWatch)
    SessionReplayer           → motor de replay para debugging
    PromptRegistry            → versionado de prompts
    get_prompt_registry()
    AuditCallbackHandler      → LangChain callback (complementario)
    CloudWatchAuditStorage    → backend CloudWatch Logs + Metrics
    AuditRecord, SessionSummary, EventType, TokenUsage → modelos de datos
"""

from .audit_service import AuditService, get_audit_service
from .interceptor import AuditCallbackHandler
from .models import AuditRecord, EventType, SessionSummary, TokenUsage
from .prompt_registry import PromptRegistry, get_prompt_registry
from .replay import SessionReplayer
from .storage.cloudwatch_storage import CloudWatchAuditStorage

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
    "CloudWatchAuditStorage",
]
