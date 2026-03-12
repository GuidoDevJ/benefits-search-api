"""
Multi-agent graph — Grafo LangGraph con supervisor + benefits.

El grafo se compila una sola vez al inicio del proceso (singleton).
session_id y audit_service viajan en el estado del grafo por request,
eliminando la necesidad de recompilar en cada consulta.
"""

from langchain_aws import ChatBedrock
from langgraph.graph import END, StateGraph

from .agents.base_agent import AgentState
from .agents.benefits_agent import create_benefits_agent
from .agents.supervisor_agent import create_supervisor_agent
from .config import AWS_REGION, BEDROCK_MODEL_ID

# ── Singleton: LLM y grafo compilado una sola vez ──────────────────────────
_llm = ChatBedrock(model_id=BEDROCK_MODEL_ID, region_name=AWS_REGION)
_graph = None


def get_graph():
    """
    Retorna el grafo compilado (singleton).
    Se construye la primera vez que se llama y se reutiliza en adelante.
    """
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


def _build_graph():
    agents = ["benefits"]

    benefits = create_benefits_agent(_llm)
    supervisor = create_supervisor_agent(_llm, agents)

    workflow = StateGraph(AgentState)
    workflow.add_node("supervisor", supervisor)
    workflow.add_node("benefits", benefits)

    for agent in agents:
        workflow.add_edge(agent, "supervisor")

    def should_continue(state):
        next_agent = state.get("next", "finish")
        return "finish" if next_agent == "finish" else next_agent

    conditional_map = {agent: agent for agent in agents}
    conditional_map["finish"] = END

    workflow.add_conditional_edges("supervisor", should_continue, conditional_map)
    workflow.set_entry_point("supervisor")

    return workflow.compile()
