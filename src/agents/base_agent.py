from typing import TypedDict, Annotated
from langchain_aws import ChatBedrock
from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    ToolMessage,
    AIMessage
)
import operator


class AgentState(TypedDict):
    """Estado compartido entre todos los agentes"""
    messages: Annotated[list[BaseMessage], operator.add]
    next: str
    context: dict


def create_agent(llm: ChatBedrock, tools: list, system_prompt: str):
    """
    Factory para crear agentes con herramientas.

    Solución óptima: Solo guardar en el estado el resultado final,
    no los mensajes intermedios de tool execution.
    """

    # Crear un mapeo de herramientas por nombre
    tool_map = {tool.name: tool for tool in tools} if tools else {}

    def agent_node(state: AgentState):
        messages = state["messages"]

        # Crear lista temporal con system prompt
        temp_messages = []
        if system_prompt:
            temp_messages.append(SystemMessage(content=system_prompt))
        temp_messages.extend(messages)

        # Invocar el modelo
        if tools:
            llm_with_tools = llm.bind_tools(tools)
            response = llm_with_tools.invoke(temp_messages)
        else:
            response = llm.invoke(temp_messages)

        # Si hay tool calls, ejecutarlos en un loop
        # hasta que el modelo no genere más tool calls
        while hasattr(response, 'tool_calls') and response.tool_calls:
            tool_messages = []

            for tool_call in response.tool_calls:
                tool_name = tool_call['name']
                tool_args = tool_call['args']

                # Ejecutar la herramienta
                if tool_name in tool_map:
                    tool_result = tool_map[tool_name].invoke(tool_args)
                    tool_messages.append(
                        ToolMessage(
                            content=str(tool_result),
                            tool_call_id=tool_call['id']
                        )
                    )

            # Agregar el response y tool messages al contexto temporal
            temp_messages = temp_messages + [response] + tool_messages

            # Invocar nuevamente para obtener la siguiente respuesta
            response = llm_with_tools.invoke(temp_messages)

        # Retornar solo el mensaje final (sin tool_calls)
        # Esto mantiene el estado limpio
        return {"messages": [response]}

    return agent_node
