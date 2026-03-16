"""
Main - Demo CLI del sistema multiagente de búsqueda de beneficios.
"""

import asyncio

from langchain_core.messages import HumanMessage

from .graph import get_graph


async def run_benefits_query(query: str) -> None:
    result = await get_graph().ainvoke(
        {"messages": [HumanMessage(content=query)], "next": "", "context": {}}
    )
    final = result["messages"][-1]
    content = getattr(final, "content", str(final))
    if isinstance(content, dict):
        content = content.get("message", str(content))
    print(content)


if __name__ == "__main__":
    asyncio.run(run_benefits_query("promociones en supermercados"))
