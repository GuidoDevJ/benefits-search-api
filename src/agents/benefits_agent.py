"""
Benefits Agent — Busca beneficios y formatea la respuesta.

Flujo:
  1. Construye Entities desde la clasificación del contexto
  2. Llama search_benefits_with_profile (filtrado determinístico Python)
  3. Inyecta resultados ya filtrados en el system prompt
  4. LLM solo formatea — no decide qué mostrar

El LLM recibe ≤ 10 beneficios ya filtrados y priorizados por segmento.
No usa tool calling (bind_tools); evita errores de Bedrock con bloques
tool_use/tool_result cuando no hay tools definidas en el request.
"""

import time
from datetime import datetime
from typing import Optional

from langchain_aws import ChatBedrock
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from src.audit.models import TokenUsage
from src.audit.prompt_registry import get_prompt_registry
from src.serialization import get_serializer

try:
    from .base_agent import AgentState, messages_to_dict
    from ..tools.benefits_api import search_benefits_with_profile
    from ..models.typed_entities import Entities
except ImportError:
    from src.agents.base_agent import AgentState, messages_to_dict
    from src.tools.benefits_api import search_benefits_with_profile
    from src.models.typed_entities import Entities


def _extract_token_usage(response) -> Optional[TokenUsage]:
    try:
        return TokenUsage.from_response(response)
    except Exception:
        return None


def _build_user_context_block(
    user_profile: dict,
    user_prefs: dict,
    is_new_session: bool,
    has_phone: bool = False,
) -> str:
    """
    Genera el bloque de contexto del cliente para el system prompt.

    - Saludo por nombre: SOLO en la primera consulta (is_new_session=True).
    - Ciudad/provincia: siempre si está guardada en prefs.
    - Solicitar ubicación: al final de la respuesta, si no hay ciudad guardada
      y todavía no se preguntó en esta sesión.
    """
    _DIAS_ES = [
        "lunes", "martes", "miércoles", "jueves",
        "viernes", "sábado", "domingo",
    ]
    hoy = datetime.now()
    lines: list[str] = [
        f"- Fecha actual: {_DIAS_ES[hoy.weekday()]} "
        f"{hoy.strftime('%d/%m/%Y')}",
    ]

    # Datos del perfil (solo si identificado)
    if user_profile.get("identificado"):
        nombre = (
            user_profile.get("nombre_completo")
            or user_profile.get("nombre")
        )
        if nombre and is_new_session:
            lines.append(f"- Nombre del cliente: {nombre}")
            lines.append(
                "  (IMPORTANTE: saludá al cliente por su nombre SOLO en "
                "este primer mensaje. En respuestas siguientes NO lo saludes "
                "ni repitas su nombre.)"
            )
        if user_profile.get("segmento"):
            seg = user_profile["segmento"]
            lines.append(f"- Segmento: {seg}")
            seg_low = seg.lower()
            if "black" in seg_low:
                lines.append(
                    "- Tono: sofisticado y exclusivo. "
                    "Destacá beneficios Black primero."
                )
            elif "premium" in seg_low or "platinum" in seg_low:
                lines.append(
                    "- Tono: premium. "
                    "Priorizá beneficios exclusivos del segmento."
                )
            elif "sueldo" in seg_low or "pyme" in seg_low:
                lines.append(
                    "- Tono: amigable y directo. "
                    "Destacá el ahorro concreto en pesos y porcentaje."
                )
        if user_profile.get("productos"):
            prods = ", ".join(user_profile["productos"])
            lines.append(f"- Productos: {prods}")

    # Ciudad/provincia guardada en preferencias
    ciudad_display = user_prefs.get("ciudad_display")
    if ciudad_display:
        lines.append(f"- Zona/ciudad del cliente: {ciudad_display}")
        lines.append(
            "  Mencioná la zona cuando sea relevante para contextualizar "
            "los beneficios (ej: 'beneficios disponibles en tu zona')."
        )
    elif has_phone:
        # Solo preguntar por zona si hay teléfono (para poder persistirlo)
        location_asked = user_prefs.get("location_asked", False)
        if not location_asked:
            lines.append(
                "- Zona del cliente: no registrada. "
                "Al FINAL de tu respuesta añadí esta pregunta: "
                "'¿De qué zona o ciudad sos? "
                "Así puedo mostrarte beneficios más cercanos.' "
                "Preguntalo una única vez."
            )

    header = "Contexto del cliente:\n" + "\n".join(lines)
    if user_profile.get("identificado") and is_new_session:
        header += "\n\nDestacá los beneficios exclusivos de su segmento."
    return header


def create_benefits_agent(llm: ChatBedrock):
    registry = get_prompt_registry()
    prompt_version = registry.get("benefits")
    model_id: str = getattr(llm, "model_id", "unknown")
    serializer = get_serializer()

    _base_system = prompt_version.content
    _format_hint = serializer.get_format_instruction()
    if _format_hint:
        _base_system = f"{_base_system}\n\n{_format_hint}"

    async def benefits_agent_node(state: AgentState):
        session_id = state.get("session_id")
        audit_service = state.get("audit_service")
        messages: list[BaseMessage] = state["messages"]
        context = state.get("context", {})
        user_profile: dict = state.get("user_profile") or {}
        user_prefs: dict = state.get("user_prefs") or {}
        is_new_session: bool = state.get("is_new_session", False)

        # ── Extraer clasificación del contexto ────────────────────────
        classification = context.get("classification", {})
        categoria = classification.get("categoria_benefits")
        negocio = classification.get("negocio")
        # Soporte multi-día: usa "dias" si existe, sino "dia" como lista
        dias_raw = classification.get("dias")
        dia_raw = classification.get("dia")
        dias: Optional[list[str]] = (
            dias_raw
            if dias_raw
            else ([dia_raw] if dia_raw else None)
        )
        segmento = classification.get("segmento")
        tipo_beneficio_raw = classification.get("tipo_beneficio")
        offset = context.get("offset", 0)

        # ── Construir Entities (toda la lógica, sin LLM) ──────────────
        entities = Entities(
            categoria=categoria,
            dias=dias,
            negocio=negocio,
            segmento=segmento,
            tipo_beneficio=tipo_beneficio_raw,
        )

        # ── System prompt con contexto del usuario ────────────────────
        system_content = _base_system
        phone_number = state.get("phone_number")
        user_ctx = _build_user_context_block(
            user_profile,
            user_prefs,
            is_new_session,
            has_phone=bool(phone_number),
        )
        if user_ctx:
            system_content = f"{user_ctx}\n\n{system_content}"

        # ── Limpiar trailing AIMessages (Bedrock los rechaza) ─────────
        filtered_messages = list(messages)
        while filtered_messages and isinstance(
            filtered_messages[-1], AIMessage
        ):
            filtered_messages.pop()

        user_query = (
            filtered_messages[-1].content if filtered_messages else ""
        )

        # ── Buscar beneficios (filtrado determinístico) ───────────────
        tool_error: Optional[Exception] = None
        tool_result = None
        t_tool = time.monotonic()
        try:
            tool_result = await search_benefits_with_profile(
                query=user_query,
                entities=entities,
                user_profile=user_profile if user_profile else None,
                offset=offset,
            )
        except Exception as exc:
            tool_error = exc
            tool_result = {"error": str(exc)}
        finally:
            tool_latency_ms = int(
                (time.monotonic() - t_tool) * 1000
            )

        has_benefits = bool(
            isinstance((tool_result or {}).get("data"), list)
            and tool_result["data"]
        )

        # ── Fallback: sin resultados + hay filtro de días → relajar días ─
        if not has_benefits and not tool_error and entities.dias:
            try:
                entities_relaxed = Entities(
                    categoria=categoria,
                    negocio=negocio,
                    segmento=segmento,
                    tipo_beneficio=tipo_beneficio_raw,
                )
                tool_fallback = await search_benefits_with_profile(
                    query=user_query,
                    entities=entities_relaxed,
                    user_profile=user_profile if user_profile else None,
                )
                if tool_fallback.get("data"):
                    tool_result = tool_fallback
                    tool_result["fallback"] = "sin_dias"
                    has_benefits = True
                    print(
                        "[Benefits] Fallback activado: sin filtro de días"
                    )
            except Exception as fb_exc:
                print(f"[Benefits] Fallback search failed: {fb_exc}")

        if audit_service and session_id:
            await audit_service.record_tool_execution(
                session_id=session_id,
                model_id=model_id,
                agent_name="benefits",
                tool_name="search_benefits_with_profile",
                tool_args={
                    "query": user_query,
                    "categoria": categoria,
                    "dias": dias,
                    "negocio": negocio,
                    "segmento": segmento,
                    "user_identified": user_profile.get("identificado"),
                },
                tool_result=tool_result,
                latency_ms=tool_latency_ms,
                is_error=tool_error is not None,
                error=tool_error,
            )

        # ── Inyectar resultados en system prompt → LLM formatea ───────
        tool_content = serializer.serialize(tool_result)
        format_messages: list[BaseMessage] = [
            SystemMessage(
                content=(
                    f"{system_content}\n\n"
                    f"RESULTADOS DE BÚSQUEDA:\n{tool_content}\n\n"
                    "Formateá estos resultados para el usuario."
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
