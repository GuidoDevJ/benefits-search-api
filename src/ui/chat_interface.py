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

from typing import List, Optional, Tuple
from uuid import uuid4

import gradio as gr

from ..config import (
    AUDIT_ENABLED,
    BEDROCK_MODEL_ID,
    MOCK_USER_PROFILE,
)
from ..tools.push_notifications import send_push_notification
from ..tools.cloudwatch_unhandled_queries import get_cw_service


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
    phone = (phone_number or "").strip() or None
    audit_service = None

    if AUDIT_ENABLED:
        from ..audit.audit_service import get_audit_service
        audit_service = await get_audit_service()

    try:
        async def on_unknown(q: str) -> None:
            try:
                cw = await get_cw_service()
                await cw.save_unhandled_query(
                    query=q,
                    detected_intent="unknown",
                    entities={},
                    reason="unknown_intent",
                )
            except Exception as exc:
                print(f"[CW] Error guardando query: {exc}")
            try:
                await send_push_notification(f"Query no identificada: {q}")
            except Exception as exc:
                print(f"[Push] Error: {exc}")

        from ..services.query_orchestrator import get_orchestrator
        result = await get_orchestrator().handle(
            query=message,
            phone=phone,
            session_id=session_id,
            audit_service=audit_service,
            log_prefix="[Chat]",
            on_unknown_query=on_unknown,
        )
        user_info = _build_user_info_text(result.user_profile, result.user_prefs)
        return result.response, result.session_id, user_info

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
