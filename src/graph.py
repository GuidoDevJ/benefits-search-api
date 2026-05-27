"""
Multi-agent graph — Grafo LangGraph con supervisor + user_profile + benefits.

Flujo:
  supervisor → user_profile_agent → benefits → supervisor (loop)

- supervisor:           routing determinístico por intent
- user_profile_agent:   construye UserContext (sin LLM, caché Redis 12h)
- benefits:             busca + formatea con LLM

Singleton: el grafo se compila una sola vez al inicio del proceso.
session_id y audit_service viajan en el estado por request.
"""

from langchain_aws import ChatBedrock
from langgraph.graph import END, StateGraph

from .agents.base_agent import AgentState
from .agents.benefits_agent import create_benefits_agent
from .agents.supervisor_agent import create_supervisor_agent
from .agents.user_profile_agent import create_user_profile_agent
from .config import (
    AWS_REGION,
    BEDROCK_GUARDRAIL_ID,
    BEDROCK_GUARDRAIL_VERSION,
    BEDROCK_MODEL_ID,
)

_guardrails = (
    {
        "guardrailIdentifier": BEDROCK_GUARDRAIL_ID,
        "guardrailVersion": BEDROCK_GUARDRAIL_VERSION,
    }
    if BEDROCK_GUARDRAIL_ID
    else None
)

# LLM con guardrail: supervisor maneja input crudo del usuario.
_llm_guarded = ChatBedrock(
    model_id=BEDROCK_MODEL_ID,
    region_name=AWS_REGION,
    **({"guardrails": _guardrails} if _guardrails else {}),
)

# LLM sin guardrail: benefits solo formatea datos curados de la API interna.
# El guardrail de prompt-injection bloquea el sistema de inyección de datos
# estructurados (RESULTADOS DE BÚSQUEDA) que es parte del diseño del agente.
_llm_benefits = ChatBedrock(
    model_id=BEDROCK_MODEL_ID,
    region_name=AWS_REGION,
)

_graph = None


def get_graph():
    """Retorna el grafo compilado (singleton)."""
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


def _build_graph():
    user_profile = create_user_profile_agent()
    benefits = create_benefits_agent(_llm_benefits)
    supervisor = create_supervisor_agent(_llm_guarded, ["benefits"])

    workflow = StateGraph(AgentState)
    workflow.add_node("supervisor", supervisor)
    workflow.add_node("user_profile_agent", user_profile)
    workflow.add_node("benefits", benefits)

    # user_profile_agent siempre precede a benefits
    workflow.add_edge("user_profile_agent", "benefits")
    workflow.add_edge("benefits", "supervisor")

    def should_continue(state):
        return (
            "finish"
            if state.get("next", "finish") == "finish"
            else state["next"]
        )

    # El supervisor sigue enrutando a "benefits"; el mapa lo redirige
    # a user_profile_agent para que construya el contexto primero.
    workflow.add_conditional_edges(
        "supervisor",
        should_continue,
        {"benefits": "user_profile_agent", "finish": END},
    )
    workflow.set_entry_point("supervisor")
    return workflow.compile()
