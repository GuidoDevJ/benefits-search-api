"""
Benefits Agent — Busca beneficios y formatea la respuesta.

Flujo:
  1. Lee UserContext (producido por user_profile_agent_node)
  2. Construye Entities desde la clasificación + UserContext
  3. Llama search_benefits_with_profile (filtrado determinístico Python)
  4. Construye el system prompt con orden y validación fijos
  5. LLM solo formatea — no decide qué mostrar

El LLM recibe ≤ 10 beneficios ya filtrados y priorizados por segmento.
No usa tool calling (bind_tools); evita errores de Bedrock con bloques
tool_use/tool_result cuando no hay tools definidas en el request.

Orden fijo del system prompt:
  1. base_system  — instrucciones del agente + format_hint
  2. user_ctx     — context_block de UserContext
  3. results      — datos de búsqueda + instrucción de formateo
"""

import re
import time
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
    from ..models.user_context import UserContext
    from ..cache import get_cache_service
    from ..config import CACHE_ENABLED
except ImportError:
    from src.agents.base_agent import AgentState, messages_to_dict
    from src.tools.benefits_api import search_benefits_with_profile
    from src.models.typed_entities import Entities
    from src.models.user_context import UserContext
    from src.cache import get_cache_service
    from src.config import CACHE_ENABLED

_LAST_RESULTS_KEY_PREFIX = "comafi:last_results:"
_LAST_RESULTS_TTL = 3600  # 1h


def _extract_token_usage(response) -> Optional[TokenUsage]:
    try:
        return TokenUsage.from_response(response)
    except Exception:
        return None


# Frases de acuse de recibo que el LLM tiende a generar antes de los resultados.
# Se eliminan determinísticamente para que la respuesta empiece directo al grano.
_ACK_RE = re.compile(
    r"^(?:"
    r"entendido[,.]?\s*"
    r"|correcto[,.]?\s*"
    r"|claro[,.]?\s*"
    r"|por supuesto[,.]?\s*"
    r"|perfecto[,.]?\s*"
    r"|de acuerdo[,.]?\s*"
    r"|voy a buscar[^.!?]*[.!?]\s*"
    r"|vamos a buscar[^.!?]*[.!?]\s*"
    r"|te voy a mostrar[^.!?]*[.!?]\s*"
    r"|procedo a buscar[^.!?]*[.!?]\s*"
    r")(?:[,.]?\s*)?",
    re.IGNORECASE,
)


def _strip_ack(text: str) -> str:
    """Elimina frases de confirmación al inicio de la respuesta del LLM."""
    return _ACK_RE.sub("", text).lstrip()


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

    discarded = len(data) - len(valid_items)
    if discarded > 0:
        print(f"[Benefits] {discarded} items descartados por validación")
        n = len(valid_items)
        result["data"] = valid_items
        result["mostrando"] = n
        result["total"] = n
        result["restantes"] = 0
        result["hay_mas"] = False

    return result


def _build_search_context_note(search_context: Optional[dict]) -> str:
    """
    Genera la nota de contexto de búsqueda para inyectar en la INSTRUCCIÓN.

    Le indica al LLM exactamente qué buscó el usuario para que construya
    el encabezado correcto sin inferir ni inventar categorías.
    """
    if not search_context:
        return ""

    negocio = (search_context.get("negocio") or "").strip()
    categoria = (search_context.get("categoria_benefits") or "").strip()

    if negocio and not categoria:
        return (
            f"\nCONTEXTO DE BÚSQUEDA: el usuario buscó por negocio "
            f"'{negocio}'. "
            f"Encabezado OBLIGATORIO: 'Encontré X beneficios en "
            f"{negocio.title()}:'. "
            "NO menciones ni inferras ninguna categoría que no "
            "fue buscada."
        )
    if categoria and not negocio:
        return (
            f"\nCONTEXTO DE BÚSQUEDA: el usuario buscó por categoría "
            f"'{categoria}'. "
            f"Encabezado: 'Encontré X beneficios en {categoria}:'"
        )
    if negocio and categoria:
        return (
            f"\nCONTEXTO DE BÚSQUEDA: negocio='{negocio}', "
            f"categoría='{categoria}'. "
            f"Encabezado: 'Encontré X beneficios de "
            f"{negocio.title()}:'. "
            "NO agregues ni inferras otra categoría."
        )
    return (
        "\nCONTEXTO DE BÚSQUEDA: búsqueda genérica sin negocio ni "
        "categoría. Encabezado: 'Encontré X beneficios:'"
    )


def _extract_segment_hint(user_context: Optional[UserContext]) -> str:
    """
    Devuelve el hint de tono para la instrucción de resultados vacíos.

    Lee directamente desde user_context.segmento_key — sin parsear strings.
    """
    seg = (user_context.segmento_key if user_context else None) or "standard"
    if seg == "black":
        return (
            "con tono sofisticado, sin perder la exclusividad. "
            "Si no hay beneficios del segmento, ofrecé los generales "
            "de mayor valor "
        )
    if seg in ("premium", "premium_platinum"):
        return (
            "con tono premium. "
            "Si no hay beneficios exclusivos, ofrecé los generales "
            "de mayor descuento "
        )
    if seg in ("plan_sueldo", "pyme"):
        return "de forma directa, destacando alternativas con mayor ahorro "
    return "con tono amable "


def _build_system_prompt(
    base_system: str,
    user_ctx: str,
    tool_result: dict,
    has_benefits: bool,
    serializer,
    search_context: Optional[dict] = None,
    segment_conflict_note: Optional[str] = None,
    user_context: Optional[UserContext] = None,
) -> str:
    """
    Ensambla el system prompt como string para compatibilidad con ChatBedrock.

    Maneja los 3 casos posibles de tool_result:
      A. Error sin datos → mensaje de error explícito y amable
      B. Sin resultados (data=[]) → instrucción contextual explícita
      C. Con resultados → datos validados + instrucción de formateo
    """
    dynamic_sections: list[str] = []

    if user_ctx:
        dynamic_sections.append(f"---\nCONTEXTO DEL CLIENTE:\n{user_ctx}")

    error = tool_result.get("error")
    data = tool_result.get("data")
    has_data = bool(isinstance(data, list) and data)

    if error and not has_data:
        result_block = (
            "RESULTADOS DE BÚSQUEDA:\n"
            '{"status": "error"}\n\n'
            "INSTRUCCIÓN: Informale al usuario con tono amable que no pudiste "
            "obtener los beneficios en este momento y que intente de nuevo "
            "en unos minutos. Usá el tono indicado en el contexto del cliente."
        )
    elif not has_data:
        segment_hint = _extract_segment_hint(user_context)
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
        validated = _validate_tool_result(tool_result)
        n_shown = len(validated.get("data", []))

        global_fallback_note = ""
        if tool_result.get("is_global_fallback"):
            global_fallback_note = (
                "\nIMPORTANTE: Estos resultados son a nivel NACIONAL. "
                "La zona del cliente no tiene este comercio. "
                "Aclaráselo: 'No encontré [negocio] en tu zona, "
                "pero a nivel nacional están disponibles:'. "
                "Usá el tono del segmento indicado."
            )

        search_ctx_note = _build_search_context_note(search_context)

        hay_mas = tool_result.get("hay_mas", False)
        restantes = tool_result.get("restantes", 0)
        if hay_mas:
            pagination_note = (
                f" Hay {restantes} beneficios adicionales sin mostrar. "
                f"Al final de tu respuesta DEBÉS preguntar: "
                f"'¿Querés que te liste los siguientes {restantes}?'"
            )
        else:
            pagination_note = (
                " Estos son TODOS los resultados disponibles — no hay más. "
                "NO preguntes si quieren ver más resultados. "
                "Si es pertinente, podés sugerir una categoría relacionada."
            )

        conflict_note = segment_conflict_note or ""

        result_block = (
            f"RESULTADOS DE BÚSQUEDA:\n{serializer.serialize(validated)}\n\n"
            f"INSTRUCCIÓN: NO confirmes la búsqueda ni digas 'Entendido', "
            f"'Correcto', 'Claro', 'Por supuesto' ni ninguna frase de "
            f"acuse de recibo. Comenzá DIRECTAMENTE con el encabezado. "
            f"Hay exactamente {n_shown} beneficio(s) en el "
            f"resultado. Usá ese número exacto al mencionarlos — nunca "
            f"inventes ni redondees."
            f"{conflict_note}"
            f"{search_ctx_note}"
            f"{pagination_note}"
            f"{global_fallback_note} "
            "Formateá estos resultados para el usuario según las reglas "
            "del prompt."
        )

    dynamic_sections.append(result_block)

    dynamic_text = "\n\n".join(dynamic_sections)
    return f"{base_system}\n\n{dynamic_text}" if dynamic_text else base_system


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
        user_context: Optional[UserContext] = state.get("user_context")

        # ── Clasificación del contexto ────────────────────────────────
        classification = context.get("classification", {})
        categoria = classification.get("categoria_benefits")
        negocio = classification.get("negocio")
        dias_raw = classification.get("dias")
        dia_raw = classification.get("dia")
        dias: Optional[list[str]] = (
            dias_raw if dias_raw else ([dia_raw] if dia_raw else None)
        )
        tipo_beneficio_raw = classification.get("tipo_beneficio")
        offset = context.get("offset", 0)

        # ── Segmento: downgrade si hay conflicto de segmento ──────────
        if user_context and user_context.segment_conflict:
            segmento = user_context.segment_conflict.actual_key
            segment_conflict_note: Optional[str] = (
                user_context.segment_conflict.note
            )
        else:
            segmento = classification.get("segmento")
            segment_conflict_note = None

        # ── Provincia: ya resuelta por user_profile_agent ─────────────
        provincia_query: Optional[str] = (
            user_context.provincia_key if user_context else (
                classification.get("provincia")
            )
        )

        # ── Entities ──────────────────────────────────────────────────
        entities = Entities(
            categoria=categoria,
            dias=dias,
            negocio=negocio,
            segmento=segmento,
            tipo_beneficio=tipo_beneficio_raw,
            provincia=provincia_query,
        )

        # ── Context block y saludo (desde UserContext) ────────────────
        greeting_name: Optional[str] = (
            user_context.greeting_name if user_context else None
        )
        user_ctx: str = (
            user_context.context_block if user_context else ""
        )

        # ── Limpiar trailing AIMessages (Bedrock los rechaza) ─────────
        filtered_messages = list(messages)
        while filtered_messages and isinstance(
            filtered_messages[-1], AIMessage
        ):
            filtered_messages.pop()

        _last_content = (
            filtered_messages[-1].content if filtered_messages else ""
        )
        user_query: str = (
            _last_content
            if isinstance(_last_content, str)
            else str(_last_content)
        )

        # ── Buscar beneficios (filtrado determinístico) ───────────────
        tool_error: Optional[Exception] = None
        tool_result = None
        _served_from_cache = False
        phone = state.get("phone_number")

        # Cache hit: si hay negocio y tenemos caché de resultados previos
        if (
            entities.negocio
            and phone
            and CACHE_ENABLED
            and offset == 0
        ):
            try:
                cache = await get_cache_service()
                _cache_key = f"{_LAST_RESULTS_KEY_PREFIX}{phone}"
                cached_data = await cache.get(_cache_key)
                if cached_data and isinstance(cached_data, list):
                    neg_lower = entities.negocio.lower()
                    filtered = [
                        item for item in cached_data
                        if isinstance(item, dict)
                        and neg_lower in (item.get("nom") or "").lower()
                    ]
                    if filtered:
                        tool_result = {
                            "data": filtered,
                            "total": len(filtered),
                            "mostrando": len(filtered),
                            "restantes": 0,
                            "hay_mas": False,
                            "_from_last_results_cache": True,
                        }
                        _served_from_cache = True
                        print(
                            f"[Benefits] Cache hit last_results: "
                            f"{len(filtered)} items para '{entities.negocio}'"
                        )
            except Exception as ce:
                print(f"[Benefits] Error leyendo last_results cache: {ce}")

        t_tool = time.monotonic()
        if not _served_from_cache:
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
        tool_latency_ms = int((time.monotonic() - t_tool) * 1000)

        has_benefits = bool(
            isinstance((tool_result or {}).get("data"), list)
            and tool_result["data"]
        )

        # ── Persistir resultados frescos en caché (sin error, con datos) ─
        if (
            not _served_from_cache
            and not tool_error
            and has_benefits
            and phone
            and CACHE_ENABLED
        ):
            try:
                cache = await get_cache_service()
                _cache_key = f"{_LAST_RESULTS_KEY_PREFIX}{phone}"
                await cache.set(
                    _cache_key,
                    tool_result["data"],
                    ttl=_LAST_RESULTS_TTL,
                )
            except Exception as ce:
                print(f"[Benefits] Error guardando last_results cache: {ce}")

        # ── Fallback: sin resultados + hay filtro de días → relajar ──
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
                    print("[Benefits] Fallback activado: sin filtro de días")
            except Exception as fb_exc:
                print(f"[Benefits] Fallback search failed: {fb_exc}")

        tool_api_error = bool(
            tool_result
            and tool_result.get("error")
            and not (tool_result.get("data") or [])
        )

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
                is_error=tool_error is not None or tool_api_error,
                error=tool_error,
            )

        # ── System prompt ─────────────────────────────────────────────
        system_content = _build_system_prompt(
            base_system=_base_system,
            user_ctx=user_ctx,
            tool_result=tool_result or {
                "data": [], "total": 0, "mostrando": 0,
                "restantes": 0, "hay_mas": False,
            },
            has_benefits=has_benefits,
            serializer=serializer,
            search_context={
                "negocio": negocio,
                "categoria_benefits": categoria,
            },
            segment_conflict_note=segment_conflict_note,
            user_context=user_context,
        )

        format_messages: list[BaseMessage] = [
            SystemMessage(content=system_content),
        ] + filtered_messages

        t0 = time.monotonic()
        response = await llm.ainvoke(format_messages)
        latency_ms = int((time.monotonic() - t0) * 1000)
        token_usage = _extract_token_usage(response)

        # ── Strip frases de acuse de recibo (determinístico, pre-saludo) ─
        _clean_content = _strip_ack(response.content or "")
        if _clean_content != response.content:
            response = AIMessage(content=_clean_content)

        # ── Prepend saludo determinístico (no delegado al LLM) ────────
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
