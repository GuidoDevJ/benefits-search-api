"""
Interfaz de chat async con Gradio para el sistema de búsqueda de beneficios.

- Genera un session_id (UUID) por consulta para trazabilidad completa.
- Registra input, NLP result, respuesta y errores en AuditService.
- El session_id se muestra en el acordeón de trazabilidad para que
  el usuario lo copie y lo use en el Audit Dashboard (replay).
- Clasificación determinística (fast_classify) con fallback a LLM.
- Si AUDIT_ENABLED=false, funciona idéntico a la versión original.
- Soporte de número de WhatsApp para identificación personalizada.
"""

import time
from typing import List, Optional, Tuple
from uuid import uuid4

import gradio as gr
from langchain_core.messages import HumanMessage

from ..config import (
    AUDIT_ENABLED,
    BEDROCK_MODEL_ID,
    MEMORY_ENABLED,
    MOCK_USER_PROFILE,
    USER_IDENTIFICATION_ENABLED,
)
from ..graph import get_graph
from ..tools.nlp_processor import is_valid_query
from ..tools.llm_classifier import classify_query
from ..tools.fast_classifier import fast_classify
from ..tools.push_notifications import send_push_notification
from ..tools.cloudwatch_unhandled_queries import get_cw_service


_DIAS_DISPLAY = {
    "lunes": "los lunes", "martes": "los martes",
    "miercoles": "los miércoles", "jueves": "los jueves",
    "viernes": "los viernes", "sabado": "los sábados",
    "domingo": "los domingos",
}


def _format_dias(dias: list[str]) -> str:
    """Convierte lista de días a texto legible en español."""
    if set(dias) >= {"sabado", "domingo"}:
        return "el fin de semana"
    parts = [_DIAS_DISPLAY.get(d, d) for d in dias]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " y " + parts[-1]


def _get_top_from_prefs(user_prefs: dict) -> tuple:
    """
    Lee contadores de uso y retorna (top_categoria, top_dias).

    Umbral: ≥ 2 usos para considerar preferencia estable.
    """
    cat_counts = user_prefs.get("cat_counts", {})
    top_cat = None
    if cat_counts:
        best = max(cat_counts, key=cat_counts.get)
        if cat_counts[best] >= 2:
            top_cat = best

    day_counts = user_prefs.get("day_counts", {})
    top_dias = [d for d, c in day_counts.items() if c >= 2] or None
    return top_cat, top_dias


def _needs_clarification(
    clf: dict,
    gathering: dict,
    user_prefs: Optional[dict] = None,
) -> tuple[bool, str]:
    """
    Determina si falta información para hacer una búsqueda útil.

    Considera las preferencias guardadas del usuario como contexto implícito.
    Cuando falta info, pregunta categoría + días en un solo turno.

    Retorna (True, pregunta) si falta contexto.
    Retorna (False, "") si se puede buscar.
    """
    merged = {**gathering, **{k: v for k, v in clf.items() if v is not None}}
    up = user_prefs or {}
    top_cat, _ = _get_top_from_prefs(up)

    has_categoria = (
        merged.get("categoria_benefits")
        or merged.get("negocio")
        or merged.get("segmento")
        or top_cat  # preferencia guardada cuenta como contexto
    )
    has_tipo = merged.get("tipo_beneficio")

    if has_categoria or has_tipo:
        return False, ""

    # Falta categoría → armar pregunta con contexto disponible
    provincia = merged.get("provincia")
    prefix = (
        "Los beneficios Comafi aplican en todo el país. "
        if provincia else ""
    )
    known_dias = merged.get("dias") or (
        [merged["dia"]] if merged.get("dia") else None
    )

    if known_dias:
        dias_str = _format_dias(known_dias)
        return True, (
            f"{prefix}¿Qué tipo de beneficio buscás para {dias_str}?\n\n"
            "Por ejemplo: gastronomía, supermercados, moda, "
            "entretenimiento, combustible, turismo, cine, salud, belleza..."
        )

    return True, (
        f"{prefix}¿Qué tipo de comercio te interesa y para cuándo?\n\n"
        "Por ejemplo: _gastronomía los sábados_, "
        "_supermercados los lunes_, _cine este fin de semana_..."
    )


def _merge_context(gathering: dict, clf: dict) -> dict:
    """
    Combina el contexto acumulado con la nueva clasificación.

    Los valores nuevos (no-None) sobreescriben los anteriores.
    """
    merged = dict(gathering)
    for key, val in clf.items():
        if val is not None and key != "gathering":
            merged[key] = val
    # Asegurar intent
    if "intent" not in merged:
        merged["intent"] = "benefits"
    return merged


def _extract_response(result: dict) -> str:
    try:
        final_message = result["messages"][-1]
        if hasattr(final_message, "content"):
            if isinstance(final_message.content, str):
                return final_message.content
            elif isinstance(final_message.content, dict):
                return final_message.content.get(
                    "message", str(final_message.content)
                )
            return str(final_message.content)
        return str(final_message)
    except Exception as exc:
        return f"Error al procesar la respuesta: {exc}"


async def chat_function(
    message: str,
    history: List[Tuple[str, str]],
    phone_number: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    Procesa un mensaje del usuario.

    Returns:
        Tuple (respuesta: str, session_id: str, user_info: str)
    """
    if not message or not message.strip():
        return "Por favor, escribe una consulta válida.", "", ""

    session_id = str(uuid4())
    t_start = time.monotonic()
    audit_service = None
    phone = (phone_number or "").strip() or None

    if AUDIT_ENABLED:
        from ..audit.audit_service import get_audit_service
        audit_service = await get_audit_service()

    try:
        # ── 1. Validación rápida con spaCy ─────────────────────────────
        if not is_valid_query(message):
            resp = (
                "No pude entender tu consulta. "
                "Podés preguntarme sobre:\n"
                "• Descuentos y beneficios: gastronomía, supermercados, "
                "entretenimiento, etc."
            )
            if audit_service:
                await audit_service.record_user_input(
                    session_id=session_id,
                    model_id=BEDROCK_MODEL_ID,
                    query=message,
                    nlp_result={"intent": "invalid", "entities": {}},
                )
                total_ms = int((time.monotonic() - t_start) * 1000)
                await audit_service.record_final_response(
                    session_id=session_id,
                    model_id=BEDROCK_MODEL_ID,
                    response=resp,
                    total_latency_ms=total_ms,
                )
            return resp, session_id, ""

        # ── 2. Clasificación ───────────────────────────────────────────
        classification = fast_classify(message)
        if classification is None:
            classification = await classify_query(message)
        classification_dict = classification.model_dump()

        if audit_service:
            await audit_service.record_user_input(
                session_id=session_id,
                model_id=BEDROCK_MODEL_ID,
                query=message,
                nlp_result=classification_dict,
            )

        # ── 3. Cargar prefs del usuario (ciudad persistida) ────────────
        user_prefs: dict = {}
        if phone:
            try:
                from ..memory import get_prefs_service
                prefs_svc = await get_prefs_service()
                user_prefs = await prefs_svc.load(phone)
            except Exception as e:
                print(f"[Chat] Error cargando prefs: {e}")

        # ── 4. intent="location" → guardar ciudad y confirmar ──────────
        if classification.intent == "location" and classification.provincia:
            from ..models.queries_types import PROVINCES
            pkey = classification.provincia
            display = PROVINCES.get(pkey, pkey.title())
            if phone:
                try:
                    from ..memory import get_prefs_service
                    prefs_svc = await get_prefs_service()
                    await prefs_svc.set_location(phone, pkey, display)
                    user_prefs["ciudad"] = pkey
                    user_prefs["ciudad_display"] = display
                except Exception as e:
                    print(f"[Chat] Error guardando ubicación: {e}")
            resp = (
                f"Perfecto, registre tu zona: **{display}**. "
                "Ahora tus consultas incluiran beneficios disponibles "
                "en tu zona. "
                "Que tipo de beneficios estas buscando?"
            )
            if audit_service:
                total_ms = int((time.monotonic() - t_start) * 1000)
                await audit_service.record_final_response(
                    session_id=session_id,
                    model_id=BEDROCK_MODEL_ID,
                    response=resp,
                    total_latency_ms=total_ms,
                )
            user_info = _build_user_info_text(None, user_prefs)
            return resp, session_id, user_info

        # ── 5. Rechazar intents desconocidos ───────────────────────────
        if classification.intent == "unknown":
            try:
                s3_service = await get_cw_service()
                await s3_service.save_unhandled_query(
                    query=message,
                    detected_intent="unknown",
                    entities={},
                    reason="unknown_intent",
                )
            except Exception as s3_err:
                print(f"[CW] Error guardando query: {s3_err}")
            try:
                await send_push_notification(
                    f"Query no identificada: {message}"
                )
            except Exception as push_err:
                print(f"[Push] Error: {push_err}")

            resp = (
                "No puedo ayudarte con eso. "
                "Podés preguntarme sobre:\n"
                "• Descuentos y beneficios: gastronomía, supermercados, "
                "entretenimiento, etc."
            )
            if audit_service:
                total_ms = int((time.monotonic() - t_start) * 1000)
                await audit_service.record_final_response(
                    session_id=session_id,
                    model_id=BEDROCK_MODEL_ID,
                    response=resp,
                    total_latency_ms=total_ms,
                )
            return resp, session_id, ""

        # ── 6. Si la query incluye provincia, persistirla ──────────────────
        if (
            phone
            and classification.provincia
            and not user_prefs.get("ciudad")
        ):
            from ..models.queries_types import PROVINCES
            pkey = classification.provincia
            display = PROVINCES.get(pkey, pkey.title())
            try:
                from ..memory import get_prefs_service
                prefs_svc = await get_prefs_service()
                await prefs_svc.set_location(phone, pkey, display)
                user_prefs["ciudad"] = pkey
                user_prefs["ciudad_display"] = display
            except Exception as e:
                print(f"[Chat] Error guardando ubicación inline: {e}")

        # ── 7. Cargar historial de conversación ────────────────────────────
        mem_history = []
        if phone and MEMORY_ENABLED:
            try:
                from ..memory import get_memory_service
                memory_svc = await get_memory_service()
                mem_history = await memory_svc.load_history(phone)
            except Exception as e:
                print(f"[Chat] Error cargando memoria: {e}")

        is_new_session = len(history) == 0

        # ── 8. Marcar "ya se preguntó la ubicación" si aplica ──────────────
        if phone and not user_prefs.get("ciudad"):
            try:
                from ..memory import get_prefs_service
                prefs_svc = await get_prefs_service()
                await prefs_svc.update(phone, location_asked=True)
                user_prefs["location_asked"] = True
            except Exception:
                pass

        # ── 9. Identificar usuario ─────────────────────────────────────────
        user_profile_dict: Optional[dict] = None
        if phone and USER_IDENTIFICATION_ENABLED:
            try:
                from ..tools.user_profile import fetch_user_profile
                profile = await fetch_user_profile(phone)
                user_profile_dict = profile.model_dump()
                if profile.identificado:
                    nombre = (
                        profile.nombre_completo or profile.nombre or ""
                    )
                    print(
                        f"[Chat] session={session_id[:8]} "
                        f"usuario={nombre} ({profile.segmento})"
                    )
            except Exception as e:
                print(f"[Chat] Error identificando usuario: {e}")

        # ── 10. Gathering / ver_mas / búsqueda ────────────────────────────
        search_context: dict = {}
        prefs_svc_ref = None
        if phone:
            try:
                from ..memory import get_prefs_service
                prefs_svc_ref = await get_prefs_service()
                search_context = await prefs_svc_ref.load_search_context(
                    phone
                )
            except Exception:
                pass

        # ── 10b. intent="ver_mas" → página siguiente de la última búsqueda
        if classification.intent == "ver_mas":
            if search_context and not search_context.get("gathering"):
                page = search_context.get("page", 1) + 1
                merged_clf = {
                    k: v for k, v in search_context.items()
                    if k not in ("gathering", "page")
                }
                merged_clf["intent"] = "benefits"
                merged_clf["page"] = page
                if prefs_svc_ref and phone:
                    try:
                        await prefs_svc_ref.save_search_context(
                            phone, merged_clf, gathering=False
                        )
                    except Exception:
                        pass
                graph_context = {
                    "classification": merged_clf,
                    "offset": (page - 1) * 5,
                }
            else:
                resp = (
                    "No tengo una búsqueda anterior para continuar. "
                    "¿Qué tipo de beneficio buscás?"
                )
                if audit_service:
                    total_ms = int((time.monotonic() - t_start) * 1000)
                    await audit_service.record_final_response(
                        session_id=session_id,
                        model_id=BEDROCK_MODEL_ID,
                        response=resp,
                        total_latency_ms=total_ms,
                    )
                return resp, session_id, _build_user_info_text(
                    user_profile_dict, user_prefs
                )
        else:
            # ── Flujo normal: gathering + clarification ────────────────────
            gathering = (
                search_context if search_context.get("gathering") else {}
            )
            merged_clf = _merge_context(gathering, classification_dict)

            needs_more, clarification_q = _needs_clarification(
                classification_dict, gathering, user_prefs
            )
            if needs_more:
                if prefs_svc_ref and phone:
                    try:
                        await prefs_svc_ref.save_search_context(
                            phone, merged_clf, gathering=True
                        )
                    except Exception:
                        pass
                if audit_service:
                    total_ms = int((time.monotonic() - t_start) * 1000)
                    await audit_service.record_final_response(
                        session_id=session_id,
                        model_id=BEDROCK_MODEL_ID,
                        response=clarification_q,
                        total_latency_ms=total_ms,
                    )
                user_info = _build_user_info_text(None, user_prefs)
                return clarification_q, session_id, user_info

            # Inyectar preferencias guardadas si faltan en merged_clf
            top_cat, top_dias = _get_top_from_prefs(user_prefs)
            if top_cat and not merged_clf.get("categoria_benefits"):
                merged_clf["categoria_benefits"] = top_cat
            if top_dias and not merged_clf.get("dias"):
                merged_clf["dias"] = top_dias
                if len(top_dias) == 1:
                    merged_clf["dia"] = top_dias[0]

            # Guardar como last_search (gathering=False, page=1)
            merged_clf["page"] = 1
            if prefs_svc_ref and phone:
                try:
                    await prefs_svc_ref.save_search_context(
                        phone, merged_clf, gathering=False
                    )
                except Exception:
                    pass

            graph_context = {"classification": merged_clf}

        # ── 11. Invocar grafo con contexto completo ────────────────────────
        messages = mem_history + [HumanMessage(content=message)]
        result = await get_graph().ainvoke({
            "messages": messages,
            "next": "",
            "context": graph_context,
            "session_id": session_id,
            "audit_service": audit_service,
            "phone_number": phone,
            "user_profile": user_profile_dict,
            "user_prefs": user_prefs,
            "is_new_session": is_new_session,
        })
        response_content = _extract_response(result)

        # ── 12. Actualizar contadores de preferencias ─────────────────
        if phone and prefs_svc_ref and classification.intent != "ver_mas":
            try:
                cat = merged_clf.get("categoria_benefits")
                dias = merged_clf.get("dias")
                await prefs_svc_ref.update_search_prefs(phone, cat, dias)
            except Exception as e:
                print(f"[Chat] Error actualizando prefs: {e}")

        # ── 13. Guardar nueva interacción en memoria ───────────────────
        if phone and MEMORY_ENABLED:
            try:
                from ..memory import get_memory_service
                memory_svc = await get_memory_service()
                new_ai_msg = result["messages"][-1]
                await memory_svc.save_messages(
                    phone,
                    [HumanMessage(content=message), new_ai_msg],
                )
            except Exception as e:
                print(f"[Chat] Error guardando memoria: {e}")

        if audit_service:
            total_ms = int((time.monotonic() - t_start) * 1000)
            await audit_service.record_final_response(
                session_id=session_id,
                model_id=BEDROCK_MODEL_ID,
                response=response_content,
                total_latency_ms=total_ms,
            )

        user_info = _build_user_info_text(user_profile_dict, user_prefs)
        return response_content, session_id, user_info

    except Exception as exc:
        error_msg = f"Ocurrió un error al procesar tu consulta: {exc}"
        print(f"[Chat][ERROR] session={session_id[:8]}: {exc}")
        if audit_service:
            await audit_service.record_error(
                session_id=session_id,
                model_id=BEDROCK_MODEL_ID,
                agent_name=None,
                error=exc,
            )
        return error_msg, session_id, ""


def _build_user_info_text(
    user_profile: Optional[dict],
    user_prefs: dict,
) -> str:
    """Genera el texto corto para el panel 'Usuario identificado'."""
    parts = []
    if user_profile and user_profile.get("identificado"):
        nombre = (
            user_profile.get("nombre_completo")
            or user_profile.get("nombre")
            or ""
        )
        seg = user_profile.get("segmento") or ""
        if nombre:
            parts.append(nombre)
        if seg:
            parts.append(seg)
    elif user_profile is not None:
        parts.append("No identificado")
    ciudad = user_prefs.get("ciudad_display")
    if ciudad:
        parts.append(ciudad)
    return " | ".join(parts) if parts else ""


def _mock_numbers_markdown() -> str:
    """Genera el bloque de ayuda con los números de prueba disponibles."""
    from ..tools.user_profile_mocks import list_mock_phones
    lines = ["**Números de prueba disponibles:**\n"]
    for p in list_mock_phones():
        seg = p["segmento"] or "No identificado"
        prods = ", ".join(p["productos"]) if p["productos"] else "—"
        nombre = p["nombre"] or "—"
        lines.append(f"- `{p['phone']}` - **{seg}** - {nombre} ({prods})")
    return "\n".join(lines)


def create_chat_interface() -> gr.Blocks:
    """
    Crea la interfaz de chat con Gradio Blocks.
    Incluye:
    - Input de número WhatsApp para identificación personalizada
    - Panel de usuario identificado
    - Panel de trazabilidad con el session_id
    """
    examples = [
        "promociones en supermercados",
        "descuentos en restaurantes",
        "ofertas en entretenimiento",
        "beneficios los lunes",
        "descuentos en YPF",
    ]

    with gr.Blocks(title="Asistente de Beneficios TeVaBien") as demo:
        gr.Markdown("# Asistente de Beneficios TeVaBien")
        gr.Markdown(
            "Preguntame sobre promociones, descuentos y beneficios. "
            "Puedo ayudarte a encontrar ofertas en gastronomía, "
            "entretenimiento, viajes y más."
        )

        # ── Panel de identificación ──────────────────────────────────
        with gr.Row():
            phone_input = gr.Textbox(
                label="Numero de WhatsApp (opcional)",
                placeholder="+5491100000001",
                scale=3,
                info=(
                    "Ingresa tu número para recibir beneficios "
                    "personalizados según tu segmento."
                ),
            )
            user_info_box = gr.Textbox(
                label="Usuario identificado",
                interactive=False,
                scale=2,
                show_label=True,
            )

        # Mostrar números de prueba solo cuando MOCK está activo
        if MOCK_USER_PROFILE:
            with gr.Accordion("Numeros de prueba (MOCK activo)", open=False):
                gr.Markdown(_mock_numbers_markdown())

        # ── Chat principal ───────────────────────────────────────────
        chatbot = gr.Chatbot(height=430)
        msg_input = gr.Textbox(
            placeholder="Ej: descuentos en restaurantes",
            label="Tu consulta",
            show_label=False,
        )

        with gr.Row():
            submit_btn = gr.Button("Enviar", variant="primary")
            clear_btn = gr.Button("Limpiar chat")

        gr.Examples(examples=examples, inputs=msg_input)

        # ── Trazabilidad ─────────────────────────────────────────────
        with gr.Accordion("Trazabilidad de sesión", open=False):
            gr.Markdown(
                "Copia el **Session ID** para hacer replay en el "
                "Audit Dashboard (`python -m src.audit_app`)."
            )
            session_id_box = gr.Textbox(
                label="Session ID (última consulta)",
                interactive=False,
                show_copy_button=True,
            )

        history_state = gr.State([])

        async def respond(user_msg, history, phone):
            if not user_msg or not user_msg.strip():
                return history, history, "", "", ""
            response, sid, user_info = await chat_function(
                user_msg, history, phone_number=phone
            )
            updated = history + [(user_msg, response)]
            return updated, updated, "", sid, user_info

        submit_btn.click(
            fn=respond,
            inputs=[msg_input, history_state, phone_input],
            outputs=[
                chatbot, history_state, msg_input,
                session_id_box, user_info_box,
            ],
            api_name=False,
        )
        msg_input.submit(
            fn=respond,
            inputs=[msg_input, history_state, phone_input],
            outputs=[
                chatbot, history_state, msg_input,
                session_id_box, user_info_box,
            ],
            api_name=False,
        )
        clear_btn.click(
            fn=lambda: ([], [], "", "", ""),
            outputs=[
                chatbot, history_state, msg_input,
                session_id_box, user_info_box,
            ],
            api_name=False,
        )

    return demo
