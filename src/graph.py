from langchain_aws import ChatBedrock
from langgraph.graph import END, StateGraph

from .agents.base_agent import AgentState
from .agents.benefits_agent import create_benefits_agent
from .agents.supervisor_agent import create_supervisor_agent
from .config import AWS_REGION, BEDROCK_MODEL_ID


def create_multiagent_graph():
    """
    Crea el grafo multiagente con benefits como punto de entrada
    y supervisor como controlador de calidad al final.

    Flujo:
        benefits → (has_benefits?) → END (si positivo)
                                   → supervisor (si negativo)
                                       → benefits (retry) → END
                                       → END (finish)
    """

    llm = ChatBedrock(
        model_id=BEDROCK_MODEL_ID,
        region_name=AWS_REGION,
    )

    benefits = create_benefits_agent(llm)
    supervisor = create_supervisor_agent(llm)

    workflow = StateGraph(AgentState)

    # Agregar nodos
    workflow.add_node("benefits", benefits)
    workflow.add_node("supervisor", supervisor)

    # Después de benefits, decidir si ir al supervisor o terminar
    def after_benefits(state):
        context = state.get("context", {})
        has_benefits = context.get("has_benefits", False)

        if has_benefits:
            return "end"
        return "supervisor"

    workflow.add_conditional_edges(
        "benefits",
        after_benefits,
        {"end": END, "supervisor": "supervisor"},
    )

    # Después del supervisor, decidir si reintentar o terminar
    def after_supervisor(state):
        next_action = state.get("next", "finish")
        if next_action == "retry":
            return "retry"
        return "finish"

    workflow.add_conditional_edges(
        "supervisor",
        after_supervisor,
        {"retry": "benefits", "finish": END},
    )

    # Punto de entrada: benefits agent directamente
    workflow.set_entry_point("benefits")

    graph = workflow.compile()

    return graph
