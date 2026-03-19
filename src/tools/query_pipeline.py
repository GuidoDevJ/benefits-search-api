"""
Query Pipeline — Pipeline compartido de validación y clasificación.

Centraliza la lógica usada por la API REST y la interfaz Gradio para
garantizar comportamiento idéntico en ambos canales.
"""

from __future__ import annotations

from typing import Optional, Tuple

from .nlp_processor import is_valid_query
from .llm_classifier import classify_query
from .fast_classifier import fast_classify

_INVALID_RESPONSE = (
    "No pude entender tu consulta. "
    "Podés preguntarme sobre:\n"
    "• Descuentos y beneficios: gastronomía, supermercados, "
    "entretenimiento, etc."
)

_UNKNOWN_RESPONSE = (
    "No puedo ayudarte con eso. "
    "Podés preguntarme sobre:\n"
    "• Descuentos y beneficios: gastronomía, supermercados, "
    "entretenimiento, etc."
)


async def classify_and_validate(
    query: str,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Valida y clasifica una consulta de usuario.

    Returns:
        (classification_dict, None) si la consulta es válida y clasificada.
        (None, rejection_message) si debe rechazarse (NLP inválido o intent desconocido).
    """
    if not query or not query.strip():
        return None, _INVALID_RESPONSE

    if not is_valid_query(query):
        return None, _INVALID_RESPONSE

    classification = fast_classify(query)
    if classification is None:
        classification = await classify_query(query)

    if classification.intent == "unknown":
        try:
            from .cloudwatch_unhandled_queries import get_cw_service
            cw = await get_cw_service()
            await cw.save_unhandled_query(
                query=query,
                detected_intent="unknown",
                entities={},
                reason="unknown_intent",
            )
        except Exception as err:
            print(f"[CW] Error guardando query: {err}")

        try:
            from .push_notifications import send_push_notification
            await send_push_notification(f"Query no identificada: {query}")
        except Exception as err:
            print(f"[Push] Error: {err}")

        return None, _UNKNOWN_RESPONSE

    return classification.model_dump(), None
