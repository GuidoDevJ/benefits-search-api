from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class AuditEventType(str, Enum):
    REQUEST_START = "request.start"
    REQUEST_END = "request.end"
    AGENT_ROUTE = "agent.route"
    AGENT_DECISION = "agent.decision"
    AGENT_RETRY = "agent.retry"
    AGENT_FALLBACK = "agent.fallback"
    LLM_INVOKE = "llm.invoke"
    LLM_ERROR = "llm.error"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"
    NLP_EXTRACT = "nlp.extract"
    CACHE_HIT = "cache.hit"
    CACHE_MISS = "cache.miss"
    API_CALL = "api.call"
    S3_WRITE = "s3.write"
    PUSH_SEND = "push.send"
    SERIALIZE = "data.serialize"
    PROMPT_EFFICIENCY = "prompt.efficiency"


class ErrorDetail(BaseModel):
    type: str
    message: str
    traceback: str | None = None
    recoverable: bool = True
    input_snapshot: dict | None = None
    environment: dict | None = None


class AuditEvent(BaseModel):
    event_version: str = "3.0"
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    event_type: AuditEventType
    agent: str
    action: str
    status: Literal["ok", "error", "timeout", "retry"] = "ok"
    latency_ms: float | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None
    data: dict = Field(default_factory=dict)
    error: ErrorDetail | None = None
