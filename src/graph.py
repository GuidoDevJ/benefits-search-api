"""
Multi-agent graph — Grafo LangGraph con supervisor + benefits.

Singleton: el grafo se compila una sola vez al inicio del proceso.
session_id y audit_service viajan en el estado por request.
"""

from langchain_aws import ChatBedrock
from langgraph.graph import END, StateGraph

from .agents.base_agent import AgentState
from .agents.benefits_agent import create_benefits_agent
from .agents.supervisor_agent import create_supervisor_agent
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
_llm = ChatBedrock(
    model_id=BEDROCK_MODEL_ID,
    region_name=AWS_REGION,
    **({"guardrails": _guardrails} if _guardrails else {}),
)
_graph = None


def get_graph():
    """Retorna el grafo compilado (singleton)."""
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


def _build_graph():
    benefits = create_benefits_agent(_llm)
    supervisor = create_supervisor_agent(_llm, ["benefits"])

    workflow = StateGraph(AgentState)
    workflow.add_node("supervisor", supervisor)
    workflow.add_node("benefits", benefits)
    workflow.add_edge("benefits", "supervisor")

    def should_continue(state):
        return (
            "finish"
            if state.get("next", "finish") == "finish"
            else state["next"]
        )

    workflow.add_conditional_edges(
        "supervisor",
        should_continue,
        {"benefits": "benefits", "finish": END},
    )
    workflow.set_entry_point("supervisor")
    return workflow.compile()
