"""
SessionReplayer — Motor de replay para reconstruir conversaciones auditadas.

Permite:
  1. Cargar una sesión completa y verla paso a paso
  2. Generar un reporte de texto estructurado (ideal para debugging)
  3. Extraer el historial de mensajes para re-ejecutar el agente
     con distintos prompts (A/B testing manual)

Uso:
    replayer = SessionReplayer(audit_service)
    report   = await replayer.build_report("session-uuid-aqui")
    print(report)

    # Para re-ejecutar con prompt distinto:
    history  = await replayer.extract_message_history("session-uuid")
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .audit_service import AuditService
from .models import AuditRecord, EventType, SessionSummary


class SessionReplayer:

    def __init__(self, audit_service: AuditService) -> None:
        self._svc = audit_service

    async def build_report(self, session_id: str) -> str:
        """
        Genera un reporte textual completo de la sesión.
        Ideal para pegar en un issue de debugging o para revisión manual.
        """
        summary = await self._svc.get_session(session_id)
        if summary is None:
            return f"[ERROR] Session '{session_id}' no encontrada en la DB."

        records = await self._svc.get_session_records(session_id)

        lines: list[str] = []
        lines.append("=" * 70)
        lines.append(f"  REPLAY -- Session {session_id}")
        lines.append("=" * 70)
        lines.append(_fmt_summary(summary))
        lines.append("")
        lines.append("-" * 70)
        lines.append("  EVENTOS")
        lines.append("-" * 70)

        for rec in records:
            lines.append(_fmt_record(rec))

        lines.append("-" * 70)
        lines.append("  TOTALES")
        lines.append("-" * 70)
        lines.append(_fmt_totals(summary))
        lines.append("=" * 70)

        return "\n".join(lines)

    async def get_summary(self, session_id: str) -> Optional[SessionSummary]:
        return await self._svc.get_session(session_id)

    async def get_records(self, session_id: str) -> list[AuditRecord]:
        return await self._svc.get_session_records(session_id)

    async def extract_message_history(self, session_id: str) -> list[dict]:
        """
        Extrae el historial de mensajes usuario/asistente de una sesión.
        Útil para re-ejecutar el agente con el mismo input.

        Retorna: [{"role": "human", "content": "..."}, {"role": "assistant", ...}]
        """
        records = await self._svc.get_session_records(session_id)
        history: list[dict] = []

        for rec in records:
            if rec.event_type == EventType.USER_INPUT:
                q = (rec.input_payload or {}).get("query", "")
                if q:
                    history.append({"role": "human", "content": q})

            elif rec.event_type == EventType.AGENT_RESPONSE:
                r = (rec.output_payload or {}).get("response", "")
                if r:
                    history.append({"role": "assistant", "content": r})

        return history

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        has_error: Optional[bool] = None,
    ) -> list[SessionSummary]:
        return await self._svc.list_sessions(
            limit=limit, offset=offset, has_error=has_error
        )


# --------------------------------------------------------------------------
# Formatters
# --------------------------------------------------------------------------

def _fmt_summary(s: SessionSummary) -> str:
    created = _fmt_ts(s.created_at)
    versions = ", ".join(f"{k}=v{v}" for k, v in s.prompt_versions.items())
    error_flag = "[ERROR]" if s.has_error else "OK"
    lines = [
        f"  Fecha        : {created}",
        f"  Modelo       : {s.model_id}",
        f"  Prompt vers. : {versions or '-'}",
        f"  Error        : {error_flag}",
    ]
    if s.user_query:
        lines.append(f"  Query usuario: {s.user_query}")
    return "\n".join(lines)


def _fmt_record(rec: AuditRecord) -> str:
    ts = _fmt_ts(rec.timestamp)
    latency = f"{rec.latency_ms}ms" if rec.latency_ms is not None else "-"

    if rec.event_type == EventType.USER_INPUT:
        q = (rec.input_payload or {}).get("query", "")
        nlp = (rec.input_payload or {}).get("nlp_result") or {}
        intent = nlp.get("intent", "-")
        return (
            f"\n[#{rec.sequence_num:02d}] USER INPUT @ {ts}\n"
            f"     Query  : {q}\n"
            f"     Intent : {intent}"
        )

    elif rec.event_type == EventType.SUPERVISOR_DECISION:
        decision = (rec.output_payload or {}).get("decision", "-")
        tokens = _fmt_tokens(rec)
        pv = rec.prompt_version or "-"
        ph = rec.prompt_hash or "-"
        return (
            f"\n[#{rec.sequence_num:02d}] SUPERVISOR DECISION @ {ts} (+{latency})\n"
            f"     Decision     : {decision}\n"
            f"     Prompt       : v{pv} [hash:{ph}]\n"
            f"     Tokens       : {tokens}"
        )

    elif rec.event_type == EventType.LLM_CALL:
        agent = rec.agent_name or "-"
        content_preview = _preview((rec.output_payload or {}).get("content", ""))
        tool_calls = (rec.output_payload or {}).get("tool_calls")
        pv = rec.prompt_version or "-"
        ph = rec.prompt_hash or "-"
        tokens = _fmt_tokens(rec)

        lines = [
            f"\n[#{rec.sequence_num:02d}] LLM CALL [{agent}] @ {ts} (+{latency})",
            f"     Prompt       : v{pv} [hash:{ph}]",
            f"     Tokens       : {tokens}",
        ]
        if tool_calls:
            for tc in tool_calls:
                lines.append(
                    f"     Tool call    : {tc.get('name')}({tc.get('args', {})})"
                )
        else:
            lines.append(f"     Output       : {content_preview}")
        return "\n".join(lines)

    elif rec.event_type == EventType.TOOL_CALL:
        agent = rec.agent_name or "-"
        tool = rec.tool_name or "-"
        args = (rec.input_payload or {}).get("tool_args", {})
        result_preview = _preview((rec.output_payload or {}).get("result", ""))
        cache = "HIT" if rec.cache_hit else ("MISS" if rec.cache_hit is False else "-")
        return (
            f"\n[#{rec.sequence_num:02d}] TOOL CALL [{agent}] @ {ts} (+{latency})\n"
            f"     Tool   : {tool}\n"
            f"     Args   : {args}\n"
            f"     Cache  : {cache}\n"
            f"     Result : {result_preview}"
        )

    elif rec.event_type == EventType.AGENT_RESPONSE:
        response_preview = _preview((rec.output_payload or {}).get("response", ""), n=300)
        return (
            f"\n[#{rec.sequence_num:02d}] AGENT RESPONSE @ {ts} (+{latency})\n"
            f"     {response_preview}"
        )

    elif rec.event_type == EventType.ERROR:
        agent = rec.agent_name or "-"
        err = rec.error_payload or {}
        return (
            f"\n[#{rec.sequence_num:02d}] [ERROR] [{agent}] @ {ts}\n"
            f"     Tipo   : {err.get('type', '-')}\n"
            f"     Mensaje: {err.get('message', '-')}"
        )

    return f"\n[#{rec.sequence_num:02d}] {rec.event_type.value} @ {ts}"


def _fmt_totals(s: SessionSummary) -> str:
    total_sec = s.total_latency_ms / 1000
    llm_cost_estimate = (
        (s.total_input_tokens * 0.00025 + s.total_output_tokens * 0.00125) / 1000
    )
    return (
        f"  Latencia total : {s.total_latency_ms}ms ({total_sec:.2f}s)\n"
        f"  Tokens (in)    : {s.total_input_tokens:,}\n"
        f"  Tokens (out)   : {s.total_output_tokens:,}\n"
        f"  Tokens (total) : {s.total_tokens:,}\n"
        f"  Costo estimado : ~${llm_cost_estimate:.6f} USD (Haiku pricing)\n"
        f"  Total records  : {s.total_records}"
    )


def _fmt_tokens(rec: AuditRecord) -> str:
    if rec.token_usage is None:
        return "-"
    u = rec.token_usage
    return f"{u.input_tokens} in / {u.output_tokens} out"


def _preview(text: str, n: int = 120) -> str:
    """Trunca texto largo para display en el reporte."""
    text = (text or "").strip().replace("\n", " ")
    if len(text) > n:
        return text[:n] + "..."
    return text or "-"


def _fmt_ts(iso: str) -> str:
    """Formatea un timestamp ISO 8601 a formato legible."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return iso
