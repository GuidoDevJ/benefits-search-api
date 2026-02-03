from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from ..graph import create_multiagent_graph


class QueryRequest(BaseModel):
    query: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("App iniciando")

    yield  # 👈 La app corre acá

    # Shutdown
    print("App cerrando")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_cors_headers(request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.get("/")
async def read_root():
    return JSONResponse(
        content={"message": "Bienvenido al sistema de beneficios TeVaBien"}
    )


def extract_response_content(result: dict) -> str:
    """
    Extrae el contenido de respuesta del resultado del grafo.

    Args:
        result: Resultado del grafo multiagente

    Returns:
        Texto de la respuesta extraído
    """
    try:
        final_message = result["messages"][-1]

        if hasattr(final_message, "content"):
            if isinstance(final_message.content, str):
                return final_message.content
            elif isinstance(final_message.content, dict):
                return final_message.content.get("message", str(final_message.content))
            else:
                return str(final_message.content)
        return str(final_message)
    except Exception as e:
        return f"Error al procesar la respuesta: {str(e)}"


@app.post("/benefits")
async def get_benefits(query: QueryRequest):
    print(f"Consulta recibida: {query}")
    try:
        # Crear el grafo
        graph = create_multiagent_graph()

        # Ejecutar la consulta de forma async
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=query.query)], "next": "", "context": {}}
        )

        response_content = extract_response_content(result)
        return JSONResponse(content={"response": response_content})

    except Exception as e:
        error_msg = f"Ocurrió un error al procesar tu consulta: {str(e)}"
        print(f"Error en get_benefits: {e}")
        return JSONResponse(content={"error": error_msg}, status_code=500)
