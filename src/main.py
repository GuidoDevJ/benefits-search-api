"""
Main - Punto de entrada async para el sistema multiagente de búsqueda de beneficios.

Este script demuestra cómo usar el grafo multiagente async para buscar beneficios
de TeVaBien usando lenguaje natural.
"""

import asyncio
from langchain_core.messages import HumanMessage

from .graph import create_multiagent_graph


async def run_benefits_query(query: str, verbose: bool = True):
    """
    Ejecuta una consulta async de beneficios usando el sistema multiagente.

    Args:
        query: Consulta en lenguaje natural
        verbose: Si True, imprime información en consola

    Returns:
        El resultado del agente
    """
    if verbose:
        print(f"\n{'='*80}")
        print(f"Query: {query}")
        print(f"{'='*80}\n")

    # Crear el grafo
    graph = create_multiagent_graph()

    # Ejecutar la consulta de forma async
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=query)], "next": "", "context": {}}
    )

    # Extraer respuesta final
    final_message = result["messages"][-1]

    if verbose:
        print("\n=== Respuesta del Sistema ===")

        if hasattr(final_message, "content"):
            if isinstance(final_message.content, str):
                print(final_message.content)
            elif isinstance(final_message.content, dict):
                message = final_message.content.get(
                    "message", str(final_message.content)
                )
                print(message)
            else:
                print(final_message.content)
        else:
            print(final_message)

        print(f"\n{'='*80}\n")

    return result


async def main():
    """Función principal async con ejemplos de uso."""

    print("\n" + "=" * 80)
    title = " Sistema Multiagente de Búsqueda de Beneficios TeVaBien "
    print(title.center(80, "="))
    print("=" * 80)

    # Queries de ejemplo
    queries = ["promociones en supermercados"]

    # Ejecutar query
    await run_benefits_query(queries[0])
    print("\n[OK] Demo completada exitosamente")


if __name__ == "__main__":
    asyncio.run(main())
