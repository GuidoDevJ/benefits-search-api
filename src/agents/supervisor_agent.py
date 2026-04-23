"""
Supervisor Agent — Enruta consultas al agente apropiado.

El QueryOrchestrator siempre resuelve la intención (fast_classify → LLM)
ANTES de invocar el grafo y la almacena en context["classification"]["intent"].
Por esto, el supervisor es 100% determinístico: nunca necesita llamar al LLM.

El parámetro `llm` se mantiene por compatibilidad de interfaz con el grafo,
pero no se utiliza.
"""


def create_supervisor_agent(llm, agents: list[str]):
    """
    Crea el nodo supervisor del grafo LangGraph.

    Args:
        llm:    Instancia de ChatBedrock (no usada; mantenida por compatibilidad).
        agents: Lista de agentes disponibles (ej: ["benefits"]).

    Returns:
        Función async `supervisor_node` para registrar en el grafo.
    """
    agents_set = set(agents)

    async def supervisor_node(state):
        context = state.get("context", {})

        # ── Ciclo completo: benefits ya ejecutó → terminar ─────────────
        if context.get("has_benefits") is not None:
            return {"next": "finish"}

        # ── Routing determinístico ──────────────────────────────────────
        # El orchestrator garantiza que classification.intent llega resuelto.
        # Casos posibles:
        #   - intent="benefits" → agente benefits
        #   - intent ausente / no válido → finish (no hay ruta)
        intent = context.get("classification", {}).get("intent")
        if intent in agents_set:
            return {"next": intent}

        # Sin intent válido en contexto → no hay agente que manejar
        return {"next": "finish"}

    return supervisor_node
