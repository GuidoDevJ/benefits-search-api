from langchain_aws import ChatBedrock
from langgraph.graph import END, StateGraph

from .agents.base_agent import AgentState
from .agents.benefits_agent import create_benefits_agent
from .agents.supervisor_agent import create_supervisor_agent
from .config import AWS_REGION, BEDROCK_MODEL_ID


def create_multiagent_graph():
    """Crea el grafo multiagente con supervisor para búsqueda de beneficios"""

    llm = ChatBedrock(
        model_id=BEDROCK_MODEL_ID,
        region_name=AWS_REGION,
    )

    # Crear agente de beneficios
    benefits = create_benefits_agent(llm)

    # Lista de agentes para el supervisor
    agents = ["benefits"]
    supervisor = create_supervisor_agent(llm, agents)

    # Crear el grafo
    workflow = StateGraph(AgentState)

    # Agregar nodos
    workflow.add_node("supervisor", supervisor)
    workflow.add_node("benefits", benefits)

    # Agregar edges desde cada agente de vuelta al supervisor
    for agent in agents:
        workflow.add_edge(agent, "supervisor")

    # Agregar edges condicionales desde el supervisor
    def should_continue(state):
        next_agent = state.get("next", "finish")
        if next_agent == "finish":
            return "finish"
        return next_agent

    # Crear el mapeo de edges
    conditional_map = {agent: agent for agent in agents}
    conditional_map["finish"] = END

    workflow.add_conditional_edges("supervisor", should_continue, conditional_map)

    # Punto de entrada
    workflow.set_entry_point("supervisor")

    # Compilar el grafo
    graph = workflow.compile()

    return graph
