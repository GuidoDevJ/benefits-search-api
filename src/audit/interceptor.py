"""
AuditCallbackHandler — LangChain AsyncCallbackHandler opcional.

Sirve como capa de captura automática cuando se quiere integrar
el audit directamente al runtime de LangChain (p.ej. para capturar
llamadas LLM sin modificar el código de los agentes).

En este proyecto, los agentes ya tienen instrumentación manual explícita,
por lo que este handler es complementario y útil principalmente para:
  - Capturar llamadas LLM de nuevos agentes sin tener que instrumentarlos
  - Auditar chains o runnables de LangChain que no pasen por nuestros agentes

Uso:
    from src.audit.interceptor import AuditCallbackHandler

    handler = AuditCallbackHandler(session_id="...", audit_service=service)
    result = await llm.ainvoke(messages, config={"callbacks": [handler]})
"""

from __future__ import annotations

import time
from typing import Any, Optional
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from .audit_service import AuditService
from .models import TokenUsage


class AuditCallbackHandler(AsyncCallbackHandler):
    """
    Callback handler que captura eventos LLM y los registra en AuditService.

    Correlaciona on_chat_model_start / on_llm_end usando run_id de LangChain.
    El agent_name se infiere de los tags pasados al invocar el LLM:
        llm.ainvoke(messages, config={"tags": ["supervisor"]})
    """

    def __init__(
        self,
        session_id: str,
        model_id: str,
        audit_service: AuditService,
    ) -> None:
        super().__init__()
        self.session_id = session_id
        self.model_id = model_id
        self.audit_service = audit_service
        # run_id → {"start": float, "messages": list, "agent": str}
        self._pending: dict[str, dict] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list],
        *,
        run_id: UUID,
        tags: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> None:
        agent_name = _infer_agent(tags)
        flat_messages = _flatten_messages(messages)
        self._pending[str(run_id)] = {
            "start": time.monotonic(),
            "messages": flat_messages,
            "agent": agent_name,
        }

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        ctx = self._pending.pop(str(run_id), None)
        if ctx is None:
            return

        latency_ms = int((time.monotonic() - ctx["start"]) * 1000)

        # Extraer contenido de la respuesta
        output_content = ""
        tool_calls = None
        if response.generations:
            gen = response.generations[0][0]
            if hasattr(gen, "message"):
                msg = gen.message
                output_content = msg.content if isinstance(msg.content, str) else ""
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    tool_calls = [
                        {"name": tc["name"], "args": tc["args"]}
                        for tc in msg.tool_calls
                    ]

        # Extraer token usage de llm_output
        token_usage: Optional[TokenUsage] = None
        llm_output = response.llm_output or {}
        usage = llm_output.get("usage", {})
        if usage:
            inp = usage.get("input_tokens", 0) or 0
            out = usage.get("output_tokens", 0) or 0
            token_usage = TokenUsage(
                input_tokens=inp, output_tokens=out, total_tokens=inp + out
            )

        await self.audit_service.record_llm_call(
            session_id=self.session_id,
            model_id=self.model_id,
            agent_name=ctx["agent"],
            input_messages=ctx["messages"],
            output_content=output_content,
            latency_ms=latency_ms,
            token_usage=token_usage,
            tool_calls_requested=tool_calls,
        )

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._pending.pop(str(run_id), None)
        await self.audit_service.record_error(
            session_id=self.session_id,
            model_id=self.model_id,
            agent_name="unknown",
            error=error if isinstance(error, Exception) else Exception(str(error)),
        )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _infer_agent(tags: Optional[list[str]]) -> str:
    """Infiere el nombre del agente a partir de los tags de LangChain."""
    known = {"supervisor", "benefits"}
    if tags:
        for tag in tags:
            if tag in known:
                return tag
    return "unknown"


def _flatten_messages(messages: list[list]) -> list[dict]:
    """Aplana la estructura anidada de mensajes que LangChain pasa al callback."""
    result = []
    for batch in messages:
        for msg in batch:
            role = msg.__class__.__name__.replace("Message", "").lower()
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            result.append({"role": role, "content": content[:2000]})
    return result
