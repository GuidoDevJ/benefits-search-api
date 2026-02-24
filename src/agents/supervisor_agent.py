"""
Supervisor Agent — Agente async que coordina los demás agentes.

Cambios respecto a la versión original:
  - Carga el system prompt desde PromptRegistry (versionado + hash).
  - Acepta `session_id` y `audit_service` opcionales para auditoría.
  - Registra la decisión de routing con latencia y token usage.
  - Si audit_service es None, funciona exactamente igual que antes.
"""

import time
from typing import TYPE_CHECKING, Optional

from langchain_aws import ChatBedrock
from langchain_core.messages import BaseMessage, SystemMessage

from src.audit.models import TokenUsage
from src.audit.prompt_registry import get_prompt_registry

if TYPE_CHECKING:
    from src.audit.audit_service import AuditService


def _messages_to_dict(messages: list[BaseMessage]) -> list[dict]:
    """Convierte mensajes LangChain a dicts serializables para auditoría."""
    result = []
    for msg in messages:
        role = msg.__class__.__name__.replace("Message", "").lower()
        content = (
            msg.content if isinstance(msg.content, str) else str(msg.content)
        )
        result.append({"role": role, "content": content[:2000]})
    return result


def _extract_token_usage(response) -> Optional[TokenUsage]:
    """Extrae token usage de un AIMessage (usage_metadata o response_metadata)."""
    try:
        return TokenUsage.from_response(response)
    except Exception:
        return None


def create_supervisor_agent(
    llm: ChatBedrock,
    agents: list[str],
    session_id: Optional[str] = None,
    audit_service: Optional["AuditService"] = None,
):
    """
    Crea el nodo async del supervisor.

    Args:
        llm           : Modelo de lenguaje.
        agents        : Lista de nombres de agentes disponibles.
        session_id    : ID de sesión para auditoría (opcional).
        audit_service : Servicio de auditoría (opcional).
    """
    registry = get_prompt_registry()
    prompt_version = registry.get("supervisor")
    agents_str = ", ".join(agents)
    system_prompt = prompt_version.render(agents_str=agents_str)

    model_id: str = getattr(llm, "model_id", "unknown")

    async def supervisor_node(state):
        context = state.get("context", {})
        has_benefits = context.get("has_benefits", None)

        # Si benefits ya ejecutó (cualquier resultado), finalizar
        if has_benefits is not None:
            return {"next": "finish"}

        messages: list[BaseMessage] = state["messages"]
        full_messages = [SystemMessage(content=system_prompt)] + messages

        t0 = time.monotonic()
        response = await llm.ainvoke(full_messages)
        latency_ms = int((time.monotonic() - t0) * 1000)

        next_agent = response.content.strip().lower()
        if next_agent not in agents and next_agent != "finish":
            next_agent = "finish"

        # ── Auditoría ──────────────────────────────────────────
        if audit_service and session_id:
            await audit_service.record_supervisor_decision(
                session_id=session_id,
                model_id=model_id,
                decision=next_agent,
                input_messages=_messages_to_dict(full_messages),
                latency_ms=latency_ms,
                token_usage=_extract_token_usage(response),
            )
        # ───────────────────────────────────────────────────────

        return {"next": next_agent}

    return supervisor_node
