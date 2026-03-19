"""
Benefits Agent — Busca beneficios y formatea la respuesta.

Flujo: tool directa → resultados en system prompt → LLM formatea.
No usa tool calling (bind_tools); evita errores de Bedrock con bloques
tool_use/tool_result cuando no hay tools definidas en el request.
"""

import time
from typing import Optional

from langchain_aws import ChatBedrock
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from src.audit.models import TokenUsage
from src.audit.prompt_registry import get_prompt_registry
from src.serialization import get_serializer

try:
    from .base_agent import messages_to_dict
    from ..tools.benefits_api import search_benefits
except ImportError:
    from src.agents.base_agent import messages_to_dict
    from src.tools.benefits_api import search_benefits

try:
    from .base_agent import AgentState
except ImportError:
    from src.agents.base_agent import AgentState


def _extract_token_usage(response) -> Optional[TokenUsage]:
    try:
        return TokenUsage.from_response(response)
    except Exception:
        return None


def create_benefits_agent(llm: ChatBedrock):
    registry = get_prompt_registry()
    prompt_version = registry.get("benefits")
    model_id: str = getattr(llm, "model_id", "unknown")
    serializer = get_serializer()

    # System prompt base + format hint computados una sola vez
    _base_system = prompt_version.content
    _format_hint = serializer.get_format_instruction()
    if _format_hint:
        _base_system = f"{_base_system}\n\n{_format_hint}"

    async def benefits_agent_node(state: AgentState):
        session_id = state.get("session_id")
        audit_service = state.get("audit_service")
        messages: list[BaseMessage] = state["messages"]
        context = state.get("context", {})

        classification = context.get("classification", {})
        categoria = classification.get("categoria_benefits")
        dia = classification.get("dia")
        negocio = classification.get("negocio")

        system_content = _base_system
        if any([categoria, dia, negocio]):
            hints = ", ".join(
                f"{k}={v}"
                for k, v in [
                    ("categoría", categoria),
                    ("día", dia),
                    ("negocio", negocio),
                ]
                if v
            )
            system_content = (
                f"{system_content}\n\n"
                f"Entidades pre-clasificadas: {hints}."
            )

        # Eliminar trailing AIMessages (Bedrock los rechaza como último mensaje)
        filtered_messages = list(messages)
        while filtered_messages and isinstance(
            filtered_messages[-1], AIMessage
        ):
            filtered_messages.pop()

        user_query = (
            filtered_messages[-1].content if filtered_messages else ""
        )

        # ── Ejecución directa del tool ──────────────────────────────────
        tool_error: Optional[Exception] = None
        tool_result = None
        t_tool = time.monotonic()
        try:
            tool_result = await search_benefits.ainvoke({
                "query": user_query,
                "categoria": categoria,
                "dia": dia,
                "negocio": negocio,
            })
        except Exception as exc:
            tool_error = exc
            tool_result = {"error": str(exc)}
        finally:
            tool_latency_ms = int((time.monotonic() - t_tool) * 1000)

        has_benefits = bool(
            isinstance((tool_result or {}).get("data"), list)
            and tool_result["data"]
        )

        if audit_service and session_id:
            await audit_service.record_tool_execution(
                session_id=session_id,
                model_id=model_id,
                agent_name="benefits",
                tool_name="search_benefits",
                tool_args={
                    "query": user_query,
                    "categoria": categoria,
                    "dia": dia,
                    "negocio": negocio,
                },
                tool_result=tool_result,
                latency_ms=tool_latency_ms,
                is_error=tool_error is not None,
                error=tool_error,
            )

        # ── Formateo: resultados inyectados en system prompt ───────────
        tool_content = serializer.serialize(tool_result)
        format_messages: list[BaseMessage] = [
            SystemMessage(
                content=(
                    f"{system_content}\n\n"
                    f"RESULTADOS DE BÚSQUEDA:\n{tool_content}\n\n"
                    f"Formateá estos resultados para el usuario."
                )
            ),
        ] + filtered_messages

        t0 = time.monotonic()
        response = await llm.ainvoke(format_messages)
        latency_ms = int((time.monotonic() - t0) * 1000)
        token_usage = _extract_token_usage(response)

        if audit_service and session_id:
            await audit_service.record_llm_call(
                session_id=session_id,
                model_id=model_id,
                agent_name="benefits",
                input_messages=messages_to_dict(format_messages),
                output_content=response.content or "",
                latency_ms=latency_ms,
                token_usage=token_usage,
                prompt_name="benefits",
                tool_calls_requested=None,
            )

        context["has_benefits"] = has_benefits
        return {"messages": [response], "context": context}

    return benefits_agent_node
