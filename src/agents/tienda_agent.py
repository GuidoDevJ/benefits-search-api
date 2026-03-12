"""
Tienda Agent — Agente async especializado en búsqueda de productos en Tienda Comafi.

Sigue el mismo patrón que benefits_agent:
  - Carga el system prompt desde PromptRegistry (versionado + hash).
  - Acepta `session_id` y `audit_service` opcionales para auditoría.
  - Registra cada llamada LLM y ejecución de tool con latencia.
  - Si audit_service es None, funciona igual que antes.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Optional

from langchain_aws import ChatBedrock
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from src.audit.models import TokenUsage
from src.audit.prompt_registry import get_prompt_registry
from src.serialization import get_serializer

if TYPE_CHECKING:
    from src.audit.audit_service import AuditService

try:
    from ..tools.tienda_api import search_tienda, search_tienda_async
except ImportError:
    from src.tools.tienda_api import search_tienda, search_tienda_async

try:
    from .base_agent import AgentState
except ImportError:
    from src.agents.base_agent import AgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _messages_to_dict(messages: list[BaseMessage]) -> list[dict]:
    result = []
    for msg in messages:
        role = msg.__class__.__name__.replace("Message", "").lower()
        content = (
            msg.content if isinstance(msg.content, str)
            else str(msg.content)
        )
        entry: dict = {"role": role, "content": content[:2000]}
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            entry["tool_calls"] = [
                {"name": tc["name"], "args": tc["args"]}
                for tc in msg.tool_calls
            ]
        if hasattr(msg, "tool_call_id"):
            entry["tool_call_id"] = msg.tool_call_id
        result.append(entry)
    return result


def _extract_token_usage(response) -> Optional[TokenUsage]:
    try:
        return TokenUsage.from_response(response)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_tienda_agent(
    llm: ChatBedrock,
    session_id: Optional[str] = None,
    audit_service: Optional["AuditService"] = None,
):
    """
    Crea el nodo async del agente de Tienda Comafi.

    Args:
        llm           : Modelo de lenguaje.
        session_id    : ID de sesión para auditoría (opcional).
        audit_service : Servicio de auditoría (opcional).
    """
    registry = get_prompt_registry()
    prompt_version = registry.get("tienda")

    tools = [search_tienda]
    tool_map = {tool.name: tool for tool in tools}
    model_id: str = getattr(llm, "model_id", "unknown")

    async def tienda_agent_node(state: AgentState):
        messages: list[BaseMessage] = state["messages"]
        context = state.get("context", {})
        serializer = get_serializer()

        system_content = prompt_version.content
        format_hint = serializer.get_format_instruction()
        if format_hint:
            system_content = f"{system_content}\n\n{format_hint}"

        # Solo pasar HumanMessages: evita que el AIMessage de un agente
        # anterior quede en la última posición (Bedrock rechaza tool calls
        # cuando el último mensaje es de rol "assistant").
        human_messages = [m for m in messages if isinstance(m, HumanMessage)]
        temp_messages: list[BaseMessage] = [
            SystemMessage(content=system_content)
        ] + human_messages

        llm_with_tools = llm.bind_tools(tools)
        has_tienda = False

        # ── Primera llamada LLM ────────────────────────────────────────
        t0 = time.monotonic()
        response = await llm_with_tools.ainvoke(temp_messages)
        latency_ms = int((time.monotonic() - t0) * 1000)
        token_usage = _extract_token_usage(response)

        if audit_service and session_id:
            tool_calls_req = [
                {"name": tc["name"], "args": tc["args"]}
                for tc in (response.tool_calls or [])
            ]
            await audit_service.record_llm_call(
                session_id=session_id,
                model_id=model_id,
                agent_name="tienda",
                input_messages=_messages_to_dict(temp_messages),
                output_content=response.content or "",
                latency_ms=latency_ms,
                token_usage=token_usage,
                prompt_name="tienda",
                tool_calls_requested=tool_calls_req or None,
            )
        # ──────────────────────────────────────────────────────────────

        # ── Loop de tool execution ─────────────────────────────────────
        while hasattr(response, "tool_calls") and response.tool_calls:
            tool_messages: list[ToolMessage] = []

            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                if tool_name not in tool_map:
                    continue

                tool_error: Optional[Exception] = None
                tool_result = None
                t_tool = time.monotonic()

                try:
                    if tool_name == "search_tienda":
                        tool_result = await search_tienda_async(
                            query=tool_args.get("query", ""),
                            precio_max=tool_args.get("precio_max"),
                            categoria=tool_args.get("categoria"),
                        )
                    else:
                        tool_result = await tool_map[tool_name].ainvoke(
                            tool_args
                        )
                except Exception as exc:
                    tool_error = exc
                    tool_result = {"error": str(exc)}
                finally:
                    tool_latency_ms = int((time.monotonic() - t_tool) * 1000)

                # Detectar si hay productos en el resultado
                if tool_name == "search_tienda" and tool_result:
                    try:
                        rd = (
                            tool_result if isinstance(tool_result, dict)
                            else json.loads(tool_result)
                        )
                        has_tienda = bool(rd.get("data"))
                    except Exception:
                        has_tienda = False

                # ── Auditar ejecución del tool ─────────────────────────
                if audit_service and session_id:
                    await audit_service.record_tool_execution(
                        session_id=session_id,
                        model_id=model_id,
                        agent_name="tienda",
                        tool_name=tool_name,
                        tool_args=tool_args,
                        tool_result=tool_result,
                        latency_ms=tool_latency_ms,
                        is_error=tool_error is not None,
                        error=tool_error,
                    )
                # ───────────────────────────────────────────────────────

                tool_content = serializer.serialize(tool_result)
                tool_messages.append(
                    ToolMessage(
                        content=tool_content,
                        tool_call_id=tool_call["id"],
                    )
                )

            temp_messages = temp_messages + [response] + tool_messages

            # ── Llamada LLM post-tool ──────────────────────────────────
            t0 = time.monotonic()
            response = await llm_with_tools.ainvoke(temp_messages)
            latency_ms = int((time.monotonic() - t0) * 1000)
            token_usage = _extract_token_usage(response)

            if audit_service and session_id:
                tool_calls_req = [
                    {"name": tc["name"], "args": tc["args"]}
                    for tc in (response.tool_calls or [])
                ]
                await audit_service.record_llm_call(
                    session_id=session_id,
                    model_id=model_id,
                    agent_name="tienda",
                    input_messages=_messages_to_dict(temp_messages),
                    output_content=response.content or "",
                    latency_ms=latency_ms,
                    token_usage=token_usage,
                    prompt_name="tienda",
                    tool_calls_requested=tool_calls_req or None,
                )
            # ───────────────────────────────────────────────────────────

        context["has_tienda"] = has_tienda
        return {"messages": [response], "context": context}

    return tienda_agent_node
