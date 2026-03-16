"""
Supervisor Agent — Enruta consultas al agente apropiado.

Optimización de costo: si la clasificación ya viene resuelta en el contexto
(fast_classify lo determina ~85% de las veces), se omite la llamada al LLM.
"""

import time
from typing import TYPE_CHECKING, Optional

from langchain_aws import ChatBedrock
from langchain_core.messages import BaseMessage, SystemMessage

from src.audit.models import TokenUsage
from src.audit.prompt_registry import get_prompt_registry

try:
    from .base_agent import messages_to_dict
except ImportError:
    from src.agents.base_agent import messages_to_dict

if TYPE_CHECKING:
    from src.audit.audit_service import AuditService


def _extract_token_usage(response) -> Optional[TokenUsage]:
    try:
        return TokenUsage.from_response(response)
    except Exception:
        return None


def create_supervisor_agent(llm: ChatBedrock, agents: list[str]):
    registry = get_prompt_registry()
    prompt_version = registry.get("supervisor")
    agents_str = ", ".join(agents)
    model_id: str = getattr(llm, "model_id", "unknown")

    async def supervisor_node(state):
        session_id = state.get("session_id")
        audit_service: Optional["AuditService"] = state.get("audit_service")
        context = state.get("context", {})

        # Si benefits ya ejecutó, terminamos.
        if context.get("has_benefits") is not None:
            return {"next": "finish"}

        # ── Optimización: saltar LLM si clasificación ya está resuelta ──
        # fast_classify resuelve ~85% de las consultas. Si intent="benefits",
        # no hace falta gastar tokens en el supervisor.
        classification = context.get("classification", {})
        if classification.get("intent") in agents:
            return {"next": classification["intent"]}

        # ── Fallback: LLM decide el ruteo ──────────────────────────────
        messages: list[BaseMessage] = state["messages"]
        system_prompt = prompt_version.render(agents_str=agents_str)
        full_messages = [SystemMessage(content=system_prompt)] + messages

        t0 = time.monotonic()
        response = await llm.ainvoke(full_messages)
        latency_ms = int((time.monotonic() - t0) * 1000)

        next_agent = response.content.strip().lower()
        if next_agent not in agents and next_agent != "finish":
            next_agent = "finish"

        if audit_service and session_id:
            await audit_service.record_supervisor_decision(
                session_id=session_id,
                model_id=model_id,
                decision=next_agent,
                input_messages=messages_to_dict(full_messages),
                latency_ms=latency_ms,
                token_usage=_extract_token_usage(response),
            )

        return {"next": next_agent}

    return supervisor_node
