"""
Multi-agent graph — Grafo LangGraph con supervisor + benefits.

Acepta `session_id` y `audit_service` opcionales para que cada
invocación quede completamente auditada sin cambiar la interfaz
pública cuando el audit está deshabilitado.
"""

from typing import TYPE_CHECKING, Optional

from langchain_aws import ChatBedrock
from langgraph.graph import END, StateGraph

from .agents.base_agent import AgentState
from .agents.benefits_agent import create_benefits_agent
from .agents.supervisor_agent import create_supervisor_agent
from .config import AWS_REGION, BEDROCK_MODEL_ID

if TYPE_CHECKING:
    from .audit.audit_service import AuditService


def create_multiagent_graph(
    session_id: Optional[str] = None,
    audit_service: Optional["AuditService"] = None,
):
    """
    Construye y compila el grafo multiagente.

    Args:
        session_id    : ID de sesión para auditoría. Si None, no audita.
        audit_service : Instancia de AuditService. Si None, no audita.

    Returns:
        Grafo compilado listo para ainvoke().
    """
    llm = ChatBedrock(
        model_id=BEDROCK_MODEL_ID,
        region_name=AWS_REGION,
    )

    agents = ["benefits"]

    benefits = create_benefits_agent(
        llm,
        session_id=session_id,
        audit_service=audit_service,
    )
    supervisor = create_supervisor_agent(
        llm,
        agents,
        session_id=session_id,
        audit_service=audit_service,
    )

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

    workflow.add_conditional_edges(
        "supervisor", should_continue, conditional_map
    )
    workflow.set_entry_point("supervisor")

    return workflow.compile()
