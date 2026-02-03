"""
Supervisor Agent - Agente async que coordina los demás agentes.
"""

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage


def create_supervisor_agent(llm: ChatOpenAI, agents: list[str]):
    """
    Crea un agente supervisor async que coordina la búsqueda de beneficios.

    El supervisor decide cuándo llamar al agente de beneficios y
    cuándo finalizar.
    """

    agents_str = ', '.join(agents)
    system_prompt = (
        f"Eres un supervisor que coordina la búsqueda de "
        f"beneficios.\n\n"
        f"Agentes disponibles: {agents_str}\n\n"
        "Capacidades de cada agente:\n"
        "- benefits: Busca y presenta beneficios de TeVaBien "
        "basándose en consultas del usuario\n\n"
        "Reglas:\n"
        "1. Si el usuario pregunta por promociones/beneficios/"
        "descuentos → llama a 'benefits'\n"
        "2. Si benefits ya respondió con información → "
        "responde 'FINISH'\n"
        "3. Si no hay consulta o la tarea está completa → "
        "responde 'FINISH'\n\n"
        "Responde ÚNICAMENTE con: 'benefits' o 'FINISH'"
    )

    async def supervisor_node(state):
        """Nodo async del supervisor."""
        # Verificar si el agente de beneficios ya fue ejecutado
        context = state.get("context", {})
        has_benefits = context.get("has_benefits", None)

        # Si benefits ya ejecutó (independiente del resultado), finalizar
        if has_benefits is not None:
            return {"next": "finish"}

        messages = (
            [SystemMessage(content=system_prompt)] + state["messages"]
        )

        # Invocación async
        response = await llm.ainvoke(messages)
        next_agent = response.content.strip().lower()

        if next_agent not in agents and next_agent != "finish":
            next_agent = "finish"

        return {"next": next_agent}

    return supervisor_node
