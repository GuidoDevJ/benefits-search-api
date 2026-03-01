"""
FastAPI — API REST + Gradio UI para el sistema de búsqueda de beneficios.

Rutas:
  GET  /           → health check
  POST /benefits   → API REST (JSON)
  GET  /audit/session/{id} → detalle de sesión auditada
  GET  /chat       → interfaz Gradio (montada como sub-app)
"""

import time
from contextlib import asynccontextmanager
from uuid import uuid4

import gradio as gr
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from ..config import AUDIT_ENABLED, BEDROCK_MODEL_ID
from ..graph import create_multiagent_graph
from ..ui.audit_interface import create_audit_interface
from ..ui.chat_interface import create_chat_interface


class QueryRequest(BaseModel):
    query: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("App iniciando")
    if AUDIT_ENABLED:
        from ..audit.audit_service import get_audit_service
        await get_audit_service()
        print("[AUDIT] CloudWatch inicializado.")
    yield
    print("App cerrando")


app = FastAPI(lifespan=lifespan)

# ── Gradio montado en /chat y /audit-ui ─────────────────────────────────────
# Ambas interfaces corren en el mismo proceso uvicorn en el puerto 8000.
# WebSockets de Gradio requieren WORKERS=1 o sticky sessions en el ALB.
app = gr.mount_gradio_app(app, create_chat_interface(), path="/chat")
app = gr.mount_gradio_app(app, create_audit_interface(), path="/audit-ui")

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


def _extract_response(result: dict) -> str:
    try:
        final_message = result["messages"][-1]
        if hasattr(final_message, "content"):
            if isinstance(final_message.content, str):
                return final_message.content
            elif isinstance(final_message.content, dict):
                return final_message.content.get(
                    "message", str(final_message.content)
                )
            return str(final_message.content)
        return str(final_message)
    except Exception as exc:
        return f"Error al procesar la respuesta: {exc}"


@app.get("/audit/session/{session_id}")
async def get_audit_session(session_id: str):
    """
    Devuelve el resumen y todos los registros de auditoría de una sesión.

    Response:
        {
            "session": { ...SessionSummary... },
            "records": [ ...AuditRecord... ]
        }
    """
    if not AUDIT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Audit deshabilitado (AUDIT_ENABLED=false)",
        )

    from ..audit.audit_service import get_audit_service
    svc = await get_audit_service()

    summary = await svc.get_session(session_id)
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sesión '{session_id}' no encontrada",
        )

    records = await svc.get_session_records(session_id)

    def _record_to_dict(r):
        d = r.model_dump()
        d["event_type"] = r.event_type.value
        if r.token_usage:
            d["token_usage"] = r.token_usage.model_dump()
        return d

    return JSONResponse(content={
        "session": {
            **summary.model_dump(),
            "total_tokens": summary.total_tokens,
        },
        "records": [_record_to_dict(r) for r in records],
    })


@app.post("/benefits")
async def get_benefits(query: QueryRequest):
    session_id = str(uuid4())
    t_start = time.monotonic()
    audit_service = None

    if AUDIT_ENABLED:
        from ..audit.audit_service import get_audit_service
        audit_service = await get_audit_service()
        await audit_service.record_user_input(
            session_id=session_id,
            model_id=BEDROCK_MODEL_ID,
            query=query.query,
        )

    print(f"[API] session={session_id[:8]} query={query.query!r}")
    try:
        graph = create_multiagent_graph(
            session_id=session_id,
            audit_service=audit_service,
        )
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=query.query)],
                "next": "",
                "context": {},
            }
        )
        response_content = _extract_response(result)

        if audit_service:
            total_ms = int((time.monotonic() - t_start) * 1000)
            await audit_service.record_final_response(
                session_id=session_id,
                model_id=BEDROCK_MODEL_ID,
                response=response_content,
                total_latency_ms=total_ms,
            )

        return JSONResponse(content={
            "response": response_content,
            "session_id": session_id,
        })

    except Exception as exc:
        error_msg = f"Ocurrió un error al procesar tu consulta: {exc}"
        print(f"[API][ERROR] session={session_id[:8]}: {exc}")

        if audit_service:
            await audit_service.record_error(
                session_id=session_id,
                model_id=BEDROCK_MODEL_ID,
                agent_name=None,
                error=exc,
            )

        return JSONResponse(
            content={"error": error_msg, "session_id": session_id},
            status_code=500,
        )
