"""
Audit Dashboard — Interfaz Gradio para inspección y replay de sesiones.

Características:
  - Listado de sesiones con métricas (tokens, latencia, errores)
  - Replay textual paso a paso de cualquier sesión por ID
  - Filtro para ver solo sesiones con errores
  - Vista de versiones de prompts registrados

Lanzar en modo standalone:
    python -m src.audit_app
"""

from __future__ import annotations

from typing import Optional

import gradio as gr

from ..audit.audit_service import get_audit_service
from ..audit.models import SessionSummary
from ..audit.prompt_registry import get_prompt_registry
from ..audit.replay import SessionReplayer


# --------------------------------------------------------------------------
# Helpers async — Gradio natively awaits async handlers in its own event loop,
# so the asyncpg pool (also created in that loop) is always reachable.
# --------------------------------------------------------------------------

async def _load_sessions(only_errors: bool, limit: int) -> list[list]:
    """Carga sesiones y las formatea como filas de tabla."""
    svc = await get_audit_service()
    has_error: Optional[bool] = True if only_errors else None
    sessions: list[SessionSummary] = await svc.list_sessions(
        limit=limit, has_error=has_error
    )

    rows = []
    for s in sessions:
        prompt_v = ", ".join(f"{k}=v{v}" for k, v in s.prompt_versions.items())
        rows.append([
            s.session_id[:8] + "...",
            s.session_id,
            _fmt_ts_short(s.created_at),
            s.model_id.split(".")[-1].split("-v")[0],
            s.user_query or "-",
            s.total_tokens,
            f"{s.total_latency_ms}ms",
            prompt_v or "-",
            "[ERROR]" if s.has_error else "OK",
        ])
    return rows


async def _replay_session(session_id_input: str) -> str:
    """Genera el reporte de replay para un session_id."""
    sid = session_id_input.strip()
    if not sid:
        return "[!] Ingresa un Session ID valido."
    svc = await get_audit_service()
    replayer = SessionReplayer(svc)
    return await replayer.build_report(sid)


def _load_prompt_registry() -> str:
    """Carga y formatea la info del registro de prompts."""
    try:
        registry = get_prompt_registry()
        prompts = registry.list_prompts()
        lines = ["# Registro de Prompts\n"]
        for p in prompts:
            lines.append(f"## {p['name']}")
            lines.append(f"- **Descripcion**: {p['description']}")
            lines.append(f"- **Version actual**: v{p['current_version']}")
            lines.append(f"- **Hash actual**: `{p['current_hash']}`")
            lines.append(
                f"- **Versiones disponibles**: {', '.join(p['available_versions'])}"
            )
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"[ERROR] cargando registry: {e}"


# --------------------------------------------------------------------------
# Componentes Gradio
# --------------------------------------------------------------------------

_HEADERS = [
    "ID (corto)", "Session ID completo", "Fecha", "Modelo",
    "Query", "Tokens", "Latencia", "Prompts", "Estado"
]


def create_audit_interface() -> gr.Blocks:
    """Crea y retorna el Gradio Blocks del dashboard de auditoría."""

    with gr.Blocks(title="Audit Dashboard — TeVaBien AI", theme=gr.themes.Soft()) as demo:

        gr.Markdown("# Audit Dashboard — TeVaBien AI")
        gr.Markdown(
            "Inspecciona sesiones, reproduce conversaciones y audita versiones de prompts."
        )

        with gr.Tabs():

            # ──────────────────────────────────────────────
            # Tab 1: Listado de sesiones
            # ──────────────────────────────────────────────
            with gr.Tab("Sesiones"):
                with gr.Row():
                    only_errors_cb = gr.Checkbox(
                        label="Mostrar solo sesiones con errores",
                        value=False,
                    )
                    limit_slider = gr.Slider(
                        minimum=10, maximum=200, value=50, step=10,
                        label="Maximo de sesiones a mostrar",
                    )
                    refresh_btn = gr.Button("Cargar sesiones", variant="primary")

                sessions_table = gr.Dataframe(
                    headers=_HEADERS,
                    datatype=["str"] * len(_HEADERS),
                    interactive=False,
                    label="Sesiones registradas",
                    wrap=True,
                )

                session_id_hint = gr.Textbox(
                    label="Session ID seleccionado (copialo para usar en Replay)",
                    interactive=False,
                    placeholder="Haz clic en una fila de la tabla para ver el ID completo",
                )

                async def refresh(only_errors, limit):
                    return await _load_sessions(only_errors, int(limit))

                refresh_btn.click(
                    fn=refresh,
                    inputs=[only_errors_cb, limit_slider],
                    outputs=sessions_table,
                    api_name=False,
                )

                def on_select(evt: gr.SelectData, data):
                    """Extrae el session_id completo de la fila seleccionada."""
                    try:
                        row_idx = evt.index[0]
                        return data.iloc[row_idx, 1]
                    except Exception:
                        return ""

                sessions_table.select(
                    fn=on_select,
                    inputs=sessions_table,
                    outputs=session_id_hint,
                    api_name=False,
                )

            # ──────────────────────────────────────────────
            # Tab 2: Replay de sesión
            # ──────────────────────────────────────────────
            with gr.Tab("Replay de Sesion"):
                gr.Markdown(
                    "Pega un **Session ID** completo para ver el replay paso a paso.\n"
                    "Puedes copiarlo desde la tabla de Sesiones."
                )
                with gr.Row():
                    session_id_input = gr.Textbox(
                        label="Session ID",
                        placeholder="ej: 3f4a8b2c-1234-5678-abcd-ef0123456789",
                        scale=4,
                    )
                    replay_btn = gr.Button("Reproducir", variant="primary", scale=1)

                replay_output = gr.Textbox(
                    label="Replay de la sesion",
                    lines=40,
                    max_lines=80,
                    interactive=False,
                    show_copy_button=True,
                )

                async def do_replay(sid):
                    return await _replay_session(sid)

                replay_btn.click(
                    fn=do_replay,
                    inputs=session_id_input,
                    outputs=replay_output,
                    api_name=False,
                )

            # ──────────────────────────────────────────────
            # Tab 3: Prompt Registry
            # ──────────────────────────────────────────────
            with gr.Tab("Prompt Registry"):
                gr.Markdown(
                    "Estado actual del registro de prompts. "
                    "El hash cambia si alguien edita el texto sin bumping de version."
                )
                load_registry_btn = gr.Button("Cargar registry", variant="secondary")
                registry_output = gr.Markdown(
                    "Presiona 'Cargar registry' para ver el estado."
                )

                load_registry_btn.click(
                    fn=_load_prompt_registry,
                    outputs=registry_output,
                    api_name=False,
                )

    return demo


# --------------------------------------------------------------------------
# Helpers de formato
# --------------------------------------------------------------------------

def _fmt_ts_short(iso: str) -> str:
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso
