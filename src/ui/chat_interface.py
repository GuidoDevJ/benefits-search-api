"""
Interfaz de chat async con Gradio para el sistema de búsqueda de beneficios.

Este módulo proporciona una interfaz web interactiva usando Gradio para
consultar beneficios de TeVaBien a través del sistema multiagente async.
"""

import gradio as gr
from typing import List, Tuple
from langchain_core.messages import HumanMessage

from ..graph import create_multiagent_graph
from ..tools.nlp_processor import nlp_pipeline
from ..tools.s3_unhandled_queries import get_s3_service
from ..tools.push_notifications import send_push_notification


def extract_response_content(result: dict) -> str:
    """
    Extrae el contenido de respuesta del resultado del grafo.

    Args:
        result: Resultado del grafo multiagente

    Returns:
        Texto de la respuesta extraído
    """
    try:
        final_message = result["messages"][-1]

        if hasattr(final_message, "content"):
            if isinstance(final_message.content, str):
                return final_message.content
            elif isinstance(final_message.content, dict):
                return final_message.content.get(
                    "message", str(final_message.content)
                )
            else:
                return str(final_message.content)
        return str(final_message)
    except Exception as e:
        return f"Error al procesar la respuesta: {str(e)}"


async def chat_function(message: str, history: List[Tuple[str, str]]) -> str:
    """
    Función async principal del chat que procesa mensajes del usuario.

    Args:
        message: Mensaje del usuario
        history: Historial de conversación

    Returns:
        Respuesta del sistema
    """
    if not message or not message.strip():
        return "Por favor, escribe una consulta válida."

    try:
        # Procesar NLP para detectar intención
        nlp_result = nlp_pipeline(message)

        # Si la intención es desconocida, guardar en S3 y enviar push notification
        if nlp_result.intent == "unknown":
            # Guardar en S3
            try:
                s3_service = await get_s3_service()
                await s3_service.save_unhandled_query(
                    query=message,
                    detected_intent=nlp_result.intent,
                    entities=nlp_result.entities.model_dump(),
                    reason="unknown_intent",
                )
            except Exception as s3_error:
                print(f"[S3] Error guardando query no identificada: {s3_error}")

            # Enviar push notification
            try:
                await send_push_notification(
                    f"Query no identificada: {message}"
                )
            except Exception as push_error:
                print(f"[Push] Error enviando notificación: {push_error}")

            return (
                "No pude identificar tu consulta. "
                "Intenta preguntarme sobre promociones, descuentos o beneficios "
                "en categorías como gastronomía, supermercados, entretenimiento, etc."
            )

        # Crear el grafo
        graph = create_multiagent_graph()

        # Ejecutar la consulta de forma async
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=message)],
                "next": "",
                "context": {"nlp_result": nlp_result.model_dump()}
            }
        )

        return extract_response_content(result)

    except Exception as e:
        error_msg = f"Ocurrió un error al procesar tu consulta: {str(e)}"
        print(f"Error en chat_function: {e}")
        return error_msg


def create_chat_interface():
    """
    Crea y configura la interfaz de chat con Gradio.

    Returns:
        Interfaz de Gradio configurada
    """
    examples = [
        "promociones en supermercados",
        "descuentos en restaurantes",
        "beneficios en gastronomía",
        "ofertas en entretenimiento",
        "promociones para viajar"
    ]

    # Gradio detecta automáticamente funciones async
    chat = gr.ChatInterface(
        fn=chat_function,
        title="🎁 Asistente de Beneficios TeVaBien",
        description=(
            "¡Bienvenido! Pregúntame sobre promociones, descuentos y "
            "beneficios disponibles. Puedo ayudarte a encontrar ofertas en "
            "diferentes categorías como gastronomía, entretenimiento, "
            "viajes y más."
        ),
        examples=examples
    )

    return chat
