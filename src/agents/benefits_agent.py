"""
Benefits Agent — Busca beneficios y formatea la respuesta.

Flujo:
  1. Construye Entities desde la clasificación del contexto
  2. Llama search_benefits_with_profile (filtrado determinístico Python)
  3. Construye el system prompt con orden y validación fijos
  4. LLM solo formatea — no decide qué mostrar

El LLM recibe ≤ 10 beneficios ya filtrados y priorizados por segmento.
No usa tool calling (bind_tools); evita errores de Bedrock con bloques
tool_use/tool_result cuando no hay tools definidas en el request.

Orden fijo del system prompt:
  1. base_system  — instrucciones del agente + format_hint
  2. user_ctx     — contexto del cliente (segmento, prefs, recencia, zona)
  3. results      — datos de búsqueda + instrucción de formateo
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
    - Preferencias históricas: categoria y días favoritos (>= 2 usos).
    - Recencia: si vuelve después de una búsqueda previa, mencionar última cat.
    - Ciudad/provincia: siempre si está guardada en prefs.
    - Solicitar ubicación: al final, si no hay ciudad y no se preguntó aún.
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

    # ── Perfil del cliente ────────────────────────────────────────────
    if user_profile.get("identificado"):
        nombre = (
            user_profile.get("nombre_completo")
            or user_profile.get("nombre")
        )
        if nombre and is_new_session:
            lines.append(f"- Nombre del cliente: {nombre}")
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

    # ── Preferencias históricas (contadores >= 2) ─────────────────────
    cat_counts = user_prefs.get("cat_counts", {})
    day_counts = user_prefs.get("day_counts", {})
    top_cat = None
    if cat_counts:
        best = max(cat_counts, key=cat_counts.get)
        if cat_counts[best] >= 2:
            top_cat = best
    top_dias = [d for d, c in day_counts.items() if c >= 2] or None

    if top_cat:
        lines.append(
            f"- Categoría favorita del cliente: {top_cat} "
            f"(buscada {cat_counts[top_cat]} veces)."
        )
    if top_dias:
        dias_str = ", ".join(top_dias)
        lines.append(f"- Días habituales de búsqueda: {dias_str}.")

    # ── Recencia: última búsqueda (usuario recurrente) ────────────────
    last_cat = user_prefs.get("last_categoria")
    last_at = user_prefs.get("last_searched_at")
    if last_cat and last_at and not is_new_session:
        try:
            from datetime import timezone
            from datetime import datetime as dt
            last_dt = dt.fromisoformat(last_at)
            mins_ago = int(
                (dt.now(timezone.utc) - last_dt).total_seconds() / 60
            )
            if mins_ago < 120:
                lines.append(
                    f"- Hace {mins_ago} min buscó: {last_cat}. "
                    "Si la consulta es amplia, podés sugerirle continuar "
                    "con esa categoría."
                )
        except Exception:
            pass

    # ── Zona/ciudad ───────────────────────────────────────────────────
    ciudad_display = user_prefs.get("ciudad_display")
    if ciudad_display:
        lines.append(f"- Zona/ciudad del cliente: {ciudad_display}")
        lines.append(
            "  Mencioná la zona cuando sea relevante para contextualizar "
            "los beneficios (ej: 'beneficios disponibles en tu zona')."
        )
    elif has_phone:
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


def _validate_tool_result(tool_result: dict) -> dict:
    """
    Valida la estructura de tool_result antes de inyectarlo en el prompt.

    Garantiza que `data` sea una lista de dicts con las claves esperadas
    (nom, ben, pago, dias). Los items con estructura inválida se descartan
    para no confundir al LLM.

    Returns:
        Copia del tool_result con `data` validada (nunca lanza excepción).
    """
    _REQUIRED_KEYS = {"nom", "ben", "pago", "dias"}
    result = dict(tool_result)
    data = result.get("data")

    if not isinstance(data, list):
        result["data"] = []
        return result

    valid_items = []
    for item in data:
        if isinstance(item, dict) and _REQUIRED_KEYS.issubset(item.keys()):
            valid_items.append(item)
        else:
            keys = (
                list(item.keys()) if isinstance(item, dict) else type(item)
            )
            print(
                f"[Benefits] Item descartado por estructura inválida: {keys}"
            )

    # Actualizar contadores si hubo descartes
    discarded = len(data) - len(valid_items)
    if discarded > 0:
        print(f"[Benefits] {discarded} items descartados por validación")
        result["data"] = valid_items
        result["mostrando"] = len(valid_items)

    return result


def _build_system_prompt(
    base_system: str,
    user_ctx: str,
    tool_result: dict,
    has_benefits: bool,
    serializer,
) -> str:
    """
    Ensambla el system prompt con orden fijo y validación de datos.

    Orden fijo (nunca altera):
      1. base_system  — instrucciones del agente
      2. user_ctx     — contexto del cliente
      3. results      — datos de búsqueda + instrucción de formateo

    Maneja los 3 casos posibles de tool_result:
      A. Error sin datos → mensaje de error explícito y amable
      B. Sin resultados (data=[]) → instrucción contextual explícita
         con tono coherente al segmento del cliente
      C. Con resultados → datos validados + instrucción de formateo
    """
    sections: list[str] = [base_system]

    if user_ctx:
        sections.append(f"---\nCONTEXTO DEL CLIENTE:\n{user_ctx}")

    error = tool_result.get("error")
    data = tool_result.get("data")
    has_data = bool(isinstance(data, list) and data)

    if error and not has_data:
        # Caso A: error en la obtención de beneficios
        result_block = (
            "RESULTADOS DE BÚSQUEDA:\n"
            '{"status": "error"}\n\n'
            "INSTRUCCIÓN: Informale al usuario con tono amable que no pudiste "
            "obtener los beneficios en este momento y que intente de nuevo "
            "en unos minutos. Usá el tono indicado en el contexto del cliente."
        )
    elif not has_data:
        # Caso B: sin resultados — generar instrucción explícita con contexto
        # de segmento para evitar incoherencia entre tono y contenido vacío
        segment_hint = _extract_segment_hint(user_ctx)
        result_block = (
            f"RESULTADOS DE BÚSQUEDA:\n{serializer.serialize(tool_result)}\n\n"
            f"INSTRUCCIÓN: No hay beneficios para los filtros aplicados. "
            f"Informale al usuario {segment_hint}"
            "y sugerí alternativas concretas (otra categoría, otro día, "
            "o quitar el filtro de día si aplica). "
            "NO menciones que 'no hay beneficios exclusivos' si el usuario "
            "no pidió explícitamente beneficios exclusivos."
        )
    else:
        # Caso C: resultados válidos → validar estructura y formatear
        validated = _validate_tool_result(tool_result)
        result_block = (
            f"RESULTADOS DE BÚSQUEDA:\n{serializer.serialize(validated)}\n\n"
            "INSTRUCCIÓN: Formateá estos resultados para el usuario "
            "según las reglas del prompt."
        )

    sections.append(result_block)
    return "\n\n".join(sections)


def _extract_segment_hint(user_ctx: str) -> str:
    """
    Extrae el hint de tono del bloque de contexto del cliente
    para usarlo en la instrucción de resultados vacíos.

    Returns:
        String corto para completar la instrucción de no-resultados.
        Siempre termina sin punto (se concatena con más texto).
    """
    ctx_lower = user_ctx.lower()
    if "sofisticado" in ctx_lower or "black" in ctx_lower:
        return (
            "con tono sofisticado, sin perder la exclusividad. "
            "Si no hay beneficios del segmento, ofrecé los generales "
            "de mayor valor "
        )
    if "premium" in ctx_lower or "platinum" in ctx_lower:
        return (
            "con tono premium. "
            "Si no hay beneficios exclusivos, ofrecé los generales "
            "de mayor descuento "
        )
    if "directo" in ctx_lower or "sueldo" in ctx_lower or "pyme" in ctx_lower:
        return (
            "de forma directa, destacando alternativas con mayor ahorro "
        )
    return "con tono amable "


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

        # ── Saludo (primera sesión + usuario identificado) ────────────
        # El saludo se prepend directamente en Python sobre la respuesta
        # del LLM — nunca se delega al modelo para evitar que lo repita
        # en mensajes siguientes o lo ignore por reglas del prompt base.
        greeting_name: Optional[str] = None
        if is_new_session and user_profile.get("identificado"):
            _nombre = (
                user_profile.get("nombre_completo")
                or user_profile.get("nombre")
            )
            if _nombre:
                greeting_name = _nombre.split()[0].title()

        # ── Contexto del usuario (se inyecta DESPUÉS del base_system) ─
        phone_number = state.get("phone_number")
        user_ctx = _build_user_context_block(
            user_profile,
            user_prefs,
            is_new_session,
            has_phone=bool(phone_number),
        )

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
            tool_result = {"error": str(exc), "data": []}
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

        # ── Construir system prompt con orden fijo y validación ───────
        system_content = _build_system_prompt(
            base_system=_base_system,
            user_ctx=user_ctx,
            tool_result=tool_result or {
                "data": [], "total": 0, "mostrando": 0,
                "restantes": 0, "hay_mas": False,
            },
            has_benefits=has_benefits,
            serializer=serializer,
        )

        format_messages: list[BaseMessage] = [
            SystemMessage(content=system_content),
        ] + filtered_messages

        t0 = time.monotonic()
        response = await llm.ainvoke(format_messages)
        latency_ms = int((time.monotonic() - t0) * 1000)
        token_usage = _extract_token_usage(response)

        # ── Prepend saludo determinístico (no delegado al LLM) ───────
        if greeting_name:
            response = AIMessage(
                content=f"¡Hola, {greeting_name}! {response.content or ''}"
            )

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
