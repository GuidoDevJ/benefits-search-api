"""
Interfaz de chat async con Gradio para el sistema de búsqueda de beneficios.

- Genera un session_id (UUID) por consulta para trazabilidad completa.
- Registra input, NLP result, respuesta y errores en AuditService.
- El session_id se muestra en el acordeón de trazabilidad para que
  el usuario lo copie y lo use en el Audit Dashboard (replay).
- Clasificación determinística (fast_classify) con fallback a LLM.
- Si AUDIT_ENABLED=false, funciona idéntico a la versión original.
"""

import time
from typing import List, Tuple
from uuid import uuid4

import gradio as gr
from langchain_core.messages import HumanMessage

from ..config import AUDIT_ENABLED, BEDROCK_MODEL_ID
from ..graph import get_graph
from ..tools.query_pipeline import classify_and_validate


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
) -> Tuple[str, str]:
    """
    Procesa un mensaje del usuario.

    Returns:
        Tuple (respuesta: str, session_id: str)
    """
    if not message or not message.strip():
        return "Por favor, escribe una consulta válida.", ""

    session_id = str(uuid4())
    t_start = time.monotonic()
    audit_service = None

    if AUDIT_ENABLED:
        from ..audit.audit_service import get_audit_service
        audit_service = await get_audit_service()

    try:
        # ── 1. Validación y clasificación (pipeline compartido) ─────────
        classification_dict, rejection = await classify_and_validate(message)

        if audit_service:
            await audit_service.record_user_input(
                session_id=session_id,
                model_id=BEDROCK_MODEL_ID,
                query=message,
                nlp_result=classification_dict or {"intent": "unknown"},
            )

        if rejection:
            if audit_service:
                total_ms = int((time.monotonic() - t_start) * 1000)
                await audit_service.record_final_response(
                    session_id=session_id,
                    model_id=BEDROCK_MODEL_ID,
                    response=rejection,
                    total_latency_ms=total_ms,
                )
            return rejection, session_id

        # ── 2. Invocar grafo con entidades pre-clasificadas ────────────
        result = await get_graph().ainvoke(
            {
                "messages": [HumanMessage(content=message)],
                "next": "",
                "context": {"classification": classification_dict},
                "session_id": session_id,
                "audit_service": audit_service,
            }
        )
        response_content = _extract_response(result)

        if audit_service:
            total_ms = int((time.monotonic() - t_start) * 1000)
            await audit_service.record_final_response(
                session_id=session_id,
                model_id=BEDROCK_MODEL_ID,
                response=response_content,
                total_latency_ms=total_ms,
            )

        return response_content, session_id

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

        return error_msg, session_id


def create_chat_interface() -> gr.Blocks:
    """
    Crea la interfaz de chat con Gradio Blocks.
    Incluye panel de trazabilidad con el session_id de cada consulta.
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
            "¡Bienvenido! Pregúntame sobre promociones, descuentos y beneficios. "
            "Puedo ayudarte a encontrar ofertas en gastronomía, entretenimiento, "
            "viajes y más."
        )

        chatbot = gr.Chatbot(height=450)
        msg_input = gr.Textbox(
            placeholder="Ej: descuentos en restaurantes",
            label="Tu consulta",
            show_label=False,
        )

        with gr.Row():
            submit_btn = gr.Button("Enviar", variant="primary")
            clear_btn = gr.Button("Limpiar")

        gr.Examples(examples=examples, inputs=msg_input)

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

        async def respond(user_msg, history):
            if not user_msg or not user_msg.strip():
                return history, history, "", ""
            response, sid = await chat_function(user_msg, history)
            updated = history + [(user_msg, response)]
            return updated, updated, "", sid

        submit_btn.click(
            fn=respond,
            inputs=[msg_input, history_state],
            outputs=[chatbot, history_state, msg_input, session_id_box],
            api_name=False,
        )
        msg_input.submit(
            fn=respond,
            inputs=[msg_input, history_state],
            outputs=[chatbot, history_state, msg_input, session_id_box],
            api_name=False,
        )
        clear_btn.click(
            fn=lambda: ([], [], "", ""),
            outputs=[chatbot, history_state, msg_input, session_id_box],
            api_name=False,
        )

    return demo
