"""
Supervisor Agent - Agente async que controla respuestas negativas del benefits agent.

Se ejecuta solo cuando benefits no encontró resultados, para intentar
reformular la búsqueda o dar una respuesta útil al usuario.
"""

from langchain_aws import ChatBedrock
from langchain_core.messages import SystemMessage


def create_supervisor_agent(llm: ChatBedrock):
    """
    Crea un agente supervisor async que actúa como controlador de calidad.

    Se ejecuta únicamente cuando el benefits agent no encontró resultados,
    para decidir si reformular la búsqueda o finalizar.
    """

    system_prompt = (
        "Eres un supervisor que controla la calidad de las respuestas "
        "del agente de beneficios.\n\n"
        "El agente de beneficios no encontró resultados para la consulta "
        "del usuario. Tu tarea es analizar la consulta original y decidir "
        "si tiene sentido reintentar con una búsqueda reformulada.\n\n"
        "Reglas:\n"
        "1. Si la consulta del usuario es clara y sobre beneficios/"
        "promociones/descuentos pero no se encontraron resultados, "
        "responde 'RETRY' para reintentar\n"
        "2. Si la consulta ya fue reintentada (verás mensajes previos "
        "del agente de beneficios) → responde 'FINISH'\n"
        "3. Si la consulta no tiene sentido o no está relacionada "
        "con beneficios → responde 'FINISH'\n\n"
        "Responde ÚNICAMENTE con: 'RETRY' o 'FINISH'"
    )

    async def supervisor_node(state):
        """Nodo async del supervisor - controlador de calidad."""
        context = state.get("context", {})

        # Si ya se reintentó una vez, finalizar para evitar loops
        if context.get("retried", False):
            return {"next": "finish"}

        messages = (
            [SystemMessage(content=system_prompt)] + state["messages"]
        )

        response = await llm.ainvoke(messages)
        decision = response.content.strip().upper()

        if decision == "RETRY":
            context["retried"] = True
            return {"next": "retry", "context": context}

        return {"next": "finish"}

    return supervisor_node
