"""
Benefits Agent - Agente async especializado en búsqueda de beneficios.

Este agente:
1. Recibe consultas en lenguaje natural
2. Ejecuta search_benefits para obtener datos
3. Lee el JSON resultante
4. Presenta información clara al usuario
"""

# Standard library imports
import json

# Third-party imports
from langchain_openai import ChatOpenAI

try:
    from ..tools.benefits_api import search_benefits, search_benefits_async
except ImportError:
    from src.tools.benefits_api import search_benefits, search_benefits_async


SYSTEM_PROMPT = """Eres un asistente de beneficios TeVaBien. Usa search_benefits pasando la consulta EXACTA del usuario.

Formato respuesta:
🎁 Encontré X beneficios:
1. **Comercio** 🏷️ Beneficio | 💳 Medio | 📅 Días

Si no hay resultados: "No encontré beneficios. Intenta otra búsqueda."
Usa emojis: 🎁💳🏷️🍔🛒⛽👗📍📅✅. No uses: ❌😀🔥💰👍"""


def create_benefits_agent(llm: ChatOpenAI):
    """
    Crea el agente async de búsqueda de beneficios.

    Args:
        llm: Modelo de lenguaje a usar

    Returns:
        Función async del nodo del agente
    """
    from langchain_core.messages import SystemMessage, ToolMessage
    from .base_agent import AgentState

    tools = [search_benefits]
    tool_map = {tool.name: tool for tool in tools}

    async def benefits_agent_node(state: AgentState):
        """Nodo async del agente con tool execution."""
        messages = state["messages"]
        context = state.get("context", {})

        # Crear lista temporal con system prompt
        temp_messages = []
        if SYSTEM_PROMPT:
            temp_messages.append(SystemMessage(content=SYSTEM_PROMPT))
        temp_messages.extend(messages)

        # Invocación async con tools
        llm_with_tools = llm.bind_tools(tools)
        response = await llm_with_tools.ainvoke(temp_messages)

        # Variable para trackear si hay beneficios
        has_benefits = False

        # Loop de tool execution hasta que no haya más tool_calls
        while hasattr(response, "tool_calls") and response.tool_calls:
            tool_messages = []

            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                if tool_name in tool_map:
                    # Ejecutar tool async si es search_benefits
                    if tool_name == "search_benefits":
                        tool_result = await search_benefits_async(
                            tool_args.get("query", "")
                        )
                    else:
                        tool_result = await tool_map[tool_name].ainvoke(tool_args)

                    # Verificar si hay beneficios en el resultado
                    if tool_name == "search_benefits" and tool_result:
                        try:
                            if isinstance(tool_result, dict):
                                result_dict = tool_result
                            elif isinstance(tool_result, str):
                                result_dict = json.loads(tool_result)
                            else:
                                result_dict = {}

                            success_data = result_dict.get("success", [])
                            has_benefits = (
                                isinstance(success_data, list)
                                and len(success_data) > 0
                            )
                        except Exception:
                            has_benefits = False

                    # Convertir tool_result a JSON string si es dict
                    if isinstance(tool_result, dict):
                        tool_content = json.dumps(tool_result, ensure_ascii=False)
                    else:
                        tool_content = str(tool_result)

                    tool_messages.append(
                        ToolMessage(
                            content=tool_content,
                            tool_call_id=tool_call["id"]
                        )
                    )

            # Agregar al contexto temporal
            temp_messages = temp_messages + [response] + tool_messages

            # Invocar nuevamente async hasta obtener respuesta final
            response = await llm_with_tools.ainvoke(temp_messages)

        # Actualizar contexto con flag de beneficios
        context["has_benefits"] = has_benefits

        return {"messages": [response], "context": context}

    return benefits_agent_node
