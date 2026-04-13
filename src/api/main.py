"""
FastAPI — API REST + Gradio UI para el sistema de búsqueda de beneficios.

Rutas:
  GET  /                       → health check
  POST /benefits               → API REST (JSON)
  DELETE /benefits/memory      → limpia historial de un usuario
  GET  /audit/session/{id}     → detalle de sesión auditada
  GET  /chat                   → interfaz Gradio (montada como sub-app)
"""

from contextlib import asynccontextmanager
from typing import Optional
from uuid import uuid4

import gradio as gr
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import (
    AUDIT_ENABLED,
    BEDROCK_MODEL_ID,
    MEMORY_ENABLED,
)
from ..ui.audit_interface import create_audit_interface
from ..ui.chat_interface import create_chat_interface
from ..services.query_orchestrator import get_orchestrator


class QueryRequest(BaseModel):
    query: str
    phone_number: Optional[str] = None   # Número WhatsApp del usuario


class ClearMemoryRequest(BaseModel):
    phone_number: str


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

# ── Gradio montado en /chat y /audit-ui ──────────────────────────────────────
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


@app.get("/audit/session/{session_id}")
async def get_audit_session(session_id: str):
    """Devuelve el resumen y registros de auditoría de una sesión."""
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


@app.delete("/benefits/memory")
async def clear_user_memory(req: ClearMemoryRequest):
    """Limpia el historial de conversación de un usuario."""
    if not MEMORY_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Memoria deshabilitada (MEMORY_ENABLED=false)",
        )
    from ..memory import get_memory_service
    memory = await get_memory_service()
    await memory.clear(req.phone_number)
    return JSONResponse(content={
        "ok": True,
        "message": f"Historial limpiado para {req.phone_number[-4:]}",
    })


@app.post("/benefits")
async def get_benefits(query: QueryRequest):
    """Endpoint principal de búsqueda de beneficios."""
    session_id = str(uuid4())
    phone = (query.phone_number or "").strip() or None
    audit_service = None

    if AUDIT_ENABLED:
        from ..audit.audit_service import get_audit_service
        audit_service = await get_audit_service()

    try:
        result = await get_orchestrator().handle(
            query=query.query,
            phone=phone,
            session_id=session_id,
            audit_service=audit_service,
            log_prefix="[API]",
        )
        return JSONResponse(content={
            "response": result.response,
            "session_id": result.session_id,
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
