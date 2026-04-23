"""
QueryOrchestrator — Pipeline compartido de procesamiento de consultas.

Encapsula toda la lógica de negocio que antes estaba duplicada entre
main.py (API REST) y chat_interface.py (Gradio).

Ambos adaptadores llaman a QueryOrchestrator.handle() y solo se
ocupan de formatear el resultado para su propio contexto de salida.

Pipeline:
  1.  Validación de query (is_valid_query)
  2.  Clasificación (fast_classify → classify_query LLM)
  3.  Carga de preferencias del usuario
  4.  intent="location"  → persistir ciudad, salida temprana
  5.  intent="unknown"   → callback on_unknown + salida temprana
  6.  Persistir provincia inline si viene en la query
  7.  Cargar historial de conversación (Redis) → is_new_session
  8.  Marcar location_asked si corresponde
  9.  Identificar usuario (sofia-api-users)
  10. Cargar search_context
  11. intent="ver_mas"   → reconstruir contexto de paginación
  11b Flujo normal       → gather + clarification + inject prefs + autofill
  12. Invocar grafo LangGraph
  13. Actualizar contadores de preferencias
  14. Guardar nueva interacción en memoria
"""

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from langchain_core.messages import HumanMessage

try:
    from ..config import (
        BEDROCK_MODEL_ID,
        MEMORY_ENABLED,
        USER_IDENTIFICATION_ENABLED,
    )
    from ..graph import get_graph
    from .context_utils import (
        _autofill_today,
        _get_top_from_prefs,
        _merge_context,
        _needs_clarification,
    )
except ImportError:
    from src.config import (
        BEDROCK_MODEL_ID,
        MEMORY_ENABLED,
        USER_IDENTIFICATION_ENABLED,
    )
    from src.graph import get_graph
    from src.services.context_utils import (
        _autofill_today,
        _get_top_from_prefs,
        _merge_context,
        _needs_clarification,
    )


def _normalize_text(text: str) -> str:
    """Normaliza texto: minúsculas, sin acentos, sin puntuación extra."""
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ── Recuperación de contexto desde historial ─────────────────────────────

def _recover_classification_from_history(history: list) -> Optional[dict]:
    """
    Escanea el historial en orden inverso buscando el último HumanMessage
    que disparó una búsqueda de benefits.

    Se usa como fallback cuando search_context no está disponible:
    expiró (TTL), primer mensaje de la sesión en WhatsApp, o Redis falló.

    Returns:
        dict compatible con Classification.model_dump() (intent=benefits),
        o None si no hay nada recuperable.
    """
    try:
        from ..tools.fast_classifier import (
            fast_classify, _VER_MAS_AFFIRMATIVES,
        )
    except ImportError:
        from src.tools.fast_classifier import (
            fast_classify, _VER_MAS_AFFIRMATIVES,
        )

    for msg in reversed(history):
        if not isinstance(msg, HumanMessage):
            continue
        normalized = _normalize_text(msg.content)
        tokens = set(normalized.split())
        if (
            not tokens
            or tokens.issubset(_VER_MAS_AFFIRMATIVES)
            or len(normalized) <= 2
        ):
            continue
        clf = fast_classify(msg.content)
        if clf and clf.intent == "benefits":
            return clf.model_dump()

    return None


# ── Resultado del orquestador ─────────────────────────────────────────────

@dataclass
class OrchestratorResult:
    """
    Resultado del pipeline compartido.

    Attributes:
        response:       Texto de respuesta al usuario.
        session_id:     UUID de la sesión para trazabilidad.
        user_profile:   Perfil identificado o None.
        user_prefs:     Preferencias del usuario (ciudad, contadores, etc.).
        is_early_exit:  True si el pipeline salió antes de invocar el grafo
                        (location, unknown, ver_mas sin contexto).
        total_ms:       Latencia total en milisegundos.
    """
    response: str
    session_id: str
    user_profile: Optional[dict] = None
    user_prefs: dict = field(default_factory=dict)
    is_early_exit: bool = False
    total_ms: int = 0


# ── Orquestador ───────────────────────────────────────────────────────────

class QueryOrchestrator:
    """
    Ejecuta el pipeline completo de una consulta de beneficios.

    Uso:
        orchestrator = QueryOrchestrator()
        result = await orchestrator.handle(
            query="descuentos en gastronomia",
            phone="+5491100000003",
            session_id=str(uuid4()),
            audit_service=audit_service,
            log_prefix="[API]",
        )
    """

    async def handle(
        self,
        query: str,
        phone: Optional[str],
        session_id: Optional[str] = None,
        audit_service: Optional[Any] = None,
        log_prefix: str = "[Query]",
        on_unknown_query: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> OrchestratorResult:
        """
        Ejecuta el pipeline completo.

        Args:
            query:            Texto del usuario.
            phone:            Número de WhatsApp (puede ser None).
            session_id:       UUID de sesión (generado si no se provee).
            audit_service:    Servicio de auditoría (opcional).
            log_prefix:       Prefijo para logs ("[API]" o "[Chat]").
            on_unknown_query: Callback async para queries desconocidas
                              (ej: guardar en CloudWatch, push notification).

        Returns:
            OrchestratorResult con la respuesta y metadata.
        """
        if not session_id:
            session_id = str(uuid4())

        t_start = time.monotonic()

        # ── 1+2. Clasificación rápida + validación ────────────────────────
        # fast_classify corre PRIMERO: si reconoce la consulta (incluyendo
        # afirmativos cortos como "si", "dale", "ok" → ver_mas), es válida
        # por definición y se saltea is_valid_query.
        # Solo si fast_classify retorna None se valida con is_valid_query
        # y luego se cae al LLM classifier.
        from ..tools.fast_classifier import fast_classify
        from ..tools.llm_classifier import classify_query
        from ..tools.nlp_processor import is_valid_query

        classification = fast_classify(query)

        if classification is None:
            # fast_classify no lo reconoció — validar antes de gastar tokens
            if not query or not query.strip() or not is_valid_query(query):
                resp = (
                    "No pude entender tu consulta. "
                    "Podés preguntarme sobre:\n"
                    "• Descuentos y beneficios: gastronomía, supermercados, "
                    "entretenimiento, etc."
                )
                if audit_service:
                    await audit_service.record_user_input(
                        session_id=session_id,
                        model_id=BEDROCK_MODEL_ID,
                        query=query,
                        nlp_result={"intent": "invalid"},
                    )
                    await audit_service.record_final_response(
                        session_id=session_id,
                        model_id=BEDROCK_MODEL_ID,
                        response=resp,
                        total_latency_ms=int(
                            (time.monotonic() - t_start) * 1000
                        ),
                    )
                return OrchestratorResult(
                    response=resp,
                    session_id=session_id,
                    is_early_exit=True,
                    total_ms=int((time.monotonic() - t_start) * 1000),
                )
            classification = await classify_query(query)

        classification_dict = classification.model_dump()

        if audit_service:
            await audit_service.record_user_input(
                session_id=session_id,
                model_id=BEDROCK_MODEL_ID,
                query=query,
                nlp_result=classification_dict,
            )

        # ── 3. Cargar preferencias del usuario ────────────────────────────
        user_prefs: dict = {}
        prefs_svc = None
        if phone:
            try:
                from ..memory import get_prefs_service
                prefs_svc = await get_prefs_service()
                user_prefs = await prefs_svc.load(phone)
            except Exception as exc:
                print(f"{log_prefix} Error cargando prefs: {exc}")

        # ── 4. intent="location" → guardar ciudad, salida temprana ───────
        if classification.intent == "location" and classification.provincia:
            from ..models.queries_types import PROVINCES
            pkey = classification.provincia
            display = PROVINCES.get(pkey, pkey.title())
            if prefs_svc and phone:
                try:
                    await prefs_svc.set_location(phone, pkey, display)
                    user_prefs["ciudad"] = pkey
                    user_prefs["ciudad_display"] = display
                except Exception as exc:
                    print(f"{log_prefix} Error guardando ubicación: {exc}")
            resp = (
                f"Perfecto, registré tu zona: {display}. "
                "¿Qué tipo de beneficios estás buscando?"
            )
            total_ms = int((time.monotonic() - t_start) * 1000)
            if audit_service:
                await audit_service.record_final_response(
                    session_id=session_id,
                    model_id=BEDROCK_MODEL_ID,
                    response=resp,
                    total_latency_ms=total_ms,
                )
            return OrchestratorResult(
                response=resp,
                session_id=session_id,
                user_prefs=user_prefs,
                is_early_exit=True,
                total_ms=total_ms,
            )

        # ── 5. intent="unknown" → callback + salida temprana ─────────────
        if classification.intent == "unknown":
            if on_unknown_query:
                try:
                    await on_unknown_query(query)
                except Exception as exc:
                    print(f"{log_prefix} Error en on_unknown_query: {exc}")
            resp = (
                "No puedo ayudarte con eso. "
                "Podés preguntarme sobre:\n"
                "• Descuentos y beneficios: gastronomía, supermercados, "
                "entretenimiento, etc."
            )
            total_ms = int((time.monotonic() - t_start) * 1000)
            if audit_service:
                await audit_service.record_final_response(
                    session_id=session_id,
                    model_id=BEDROCK_MODEL_ID,
                    response=resp,
                    total_latency_ms=total_ms,
                )
            return OrchestratorResult(
                response=resp,
                session_id=session_id,
                is_early_exit=True,
                total_ms=total_ms,
            )

        # ── 6. Persistir provincia inline (query mixta beneficio+zona) ───
        if phone and prefs_svc and classification.provincia \
                and not user_prefs.get("ciudad"):
            from ..models.queries_types import PROVINCES
            pkey = classification.provincia
            display = PROVINCES.get(pkey, pkey.title())
            try:
                await prefs_svc.set_location(phone, pkey, display)
                user_prefs["ciudad"] = pkey
                user_prefs["ciudad_display"] = display
            except Exception as exc:
                print(f"{log_prefix} Error guardando provincia inline: {exc}")

        # ── 7. Historial de conversación → is_new_session ─────────────────
        history = []
        if phone and MEMORY_ENABLED:
            try:
                from ..memory import get_memory_service
                memory_svc = await get_memory_service()
                history = await memory_svc.load_history(phone)
            except Exception as exc:
                print(f"{log_prefix} Error cargando memoria: {exc}")
        is_new_session = len(history) == 0

        # ── 8. Marcar location_asked si aún no tiene ciudad ───────────────
        if phone and prefs_svc and not user_prefs.get("ciudad"):
            try:
                await prefs_svc.update(phone, location_asked=True)
                user_prefs["location_asked"] = True
            except Exception:
                pass

        # ── 9. Identificar usuario ────────────────────────────────────────
        user_profile_dict: Optional[dict] = None
        if phone and USER_IDENTIFICATION_ENABLED:
            try:
                from ..tools.user_profile import fetch_user_profile
                profile = await fetch_user_profile(phone)
                user_profile_dict = profile.model_dump()
                status = (
                    "identificado" if profile.identificado
                    else "no identificado"
                )
                print(
                    f"{log_prefix} session={session_id[:8]} "
                    f"usuario={status} ({phone[-4:]})"
                )
            except Exception as exc:
                print(f"{log_prefix} Error identificando usuario: {exc}")

        print(
            f"{log_prefix} session={session_id[:8]} "
            f"query={query!r} historial={len(history)} msgs"
        )

        # ── 10. Cargar search_context ─────────────────────────────────────
        search_context: dict = {}
        if phone and prefs_svc:
            try:
                search_context = await prefs_svc.load_search_context(phone)
            except Exception:
                pass

        # ── 11. Resolver graph_context ────────────────────────────────────
        graph_context: dict = {}
        merged_clf: dict = {}

        if classification.intent == "ver_mas":
            if search_context and not search_context.get("gathering"):
                # ── Caso normal: search_context fresco en Redis ───────────
                page = search_context.get("page", 1) + 1
                merged_clf = {
                    k: v for k, v in search_context.items()
                    if k not in ("gathering", "page")
                }
                merged_clf["intent"] = "benefits"
                merged_clf["page"] = page
                if phone and prefs_svc:
                    try:
                        await prefs_svc.save_search_context(
                            phone, merged_clf, gathering=False
                        )
                    except Exception:
                        pass
                graph_context = {
                    "classification": merged_clf,
                    "offset": (page - 1) * 5,
                }
            else:
                # ── Fallback: search_context ausente o expirado ───────────
                # Escanear el historial en orden inverso para recuperar la
                # última clasificación válida y continuar desde página 2.
                # Esto permite que "sí" / "dale" funcionen aunque Redis haya
                # expirado el search_context o sea una sesión nueva.
                recovered_clf = _recover_classification_from_history(history)
                if recovered_clf:
                    print(
                        f"{log_prefix} ver_mas sin search_context — "
                        f"recuperado del historial: "
                        f"cat={recovered_clf.get('categoria_benefits')} "
                        f"dias={recovered_clf.get('dias')}"
                    )
                    recovered_clf["intent"] = "benefits"
                    recovered_clf["page"] = 2
                    merged_clf = recovered_clf
                    graph_context = {
                        "classification": merged_clf,
                        "offset": 5,
                    }
                else:
                    # Sin historial recuperable: respuesta conversacional
                    # (primera interacción o historial solo afirmativos)
                    resp = (
                        "¿Qué tipo de beneficios querés ver? "
                        "Podés pedirme gastronomía, supermercados, "
                        "combustible, moda, cine, y muchas categorías más."
                    )
                    total_ms = int((time.monotonic() - t_start) * 1000)
                    if audit_service:
                        await audit_service.record_final_response(
                            session_id=session_id,
                            model_id=BEDROCK_MODEL_ID,
                            response=resp,
                            total_latency_ms=total_ms,
                        )
                    return OrchestratorResult(
                        response=resp,
                        session_id=session_id,
                        user_profile=user_profile_dict,
                        user_prefs=user_prefs,
                        is_early_exit=True,
                        total_ms=total_ms,
                    )
        else:
            gathering = (
                search_context if search_context.get("gathering") else {}
            )
            merged_clf = _merge_context(gathering, classification_dict)

            needs_more, clarification_q = _needs_clarification(
                classification_dict, gathering, user_prefs
            )
            if needs_more:
                if phone and prefs_svc:
                    try:
                        await prefs_svc.save_search_context(
                            phone, merged_clf, gathering=True
                        )
                    except Exception:
                        pass
                total_ms = int((time.monotonic() - t_start) * 1000)
                if audit_service:
                    await audit_service.record_final_response(
                        session_id=session_id,
                        model_id=BEDROCK_MODEL_ID,
                        response=clarification_q,
                        total_latency_ms=total_ms,
                    )
                return OrchestratorResult(
                    response=clarification_q,
                    session_id=session_id,
                    user_profile=user_profile_dict,
                    user_prefs=user_prefs,
                    is_early_exit=True,
                    total_ms=total_ms,
                )

            # Inyectar preferencias si faltan
            top_cat, top_dias = _get_top_from_prefs(user_prefs)
            if top_cat and not merged_clf.get("categoria_benefits"):
                merged_clf["categoria_benefits"] = top_cat
            if top_dias and not merged_clf.get("dias"):
                merged_clf["dias"] = top_dias
                if len(top_dias) == 1:
                    merged_clf["dia"] = top_dias[0]

            merged_clf = _autofill_today(merged_clf, user_prefs)
            merged_clf["page"] = 1

            if phone and prefs_svc:
                try:
                    await prefs_svc.save_search_context(
                        phone, merged_clf, gathering=False
                    )
                except Exception:
                    pass

            graph_context = {"classification": merged_clf}

        # ── 12. Invocar grafo ─────────────────────────────────────────────
        messages = history + [HumanMessage(content=query)]
        result = await get_graph().ainvoke({
            "messages":      messages,
            "next":          "",
            "context":       graph_context,
            "session_id":    session_id,
            "audit_service": audit_service,
            "phone_number":  phone,
            "user_profile":  user_profile_dict,
            "user_prefs":    user_prefs,
            "is_new_session": is_new_session,
        })

        # Extraer texto de la respuesta del grafo
        response_content = _extract_response(result)

        # ── 13. Actualizar contadores de preferencias ─────────────────────
        if phone and prefs_svc and classification.intent != "ver_mas":
            try:
                await prefs_svc.update_search_prefs(
                    phone,
                    merged_clf.get("categoria_benefits"),
                    merged_clf.get("dias"),
                )
            except Exception as exc:
                print(f"{log_prefix} Error actualizando prefs: {exc}")

        # ── 14. Guardar interacción en memoria ────────────────────────────
        if phone and MEMORY_ENABLED:
            try:
                from ..memory import get_memory_service
                memory_svc = await get_memory_service()
                await memory_svc.save_messages(
                    phone,
                    [HumanMessage(content=query), result["messages"][-1]],
                )
            except Exception as exc:
                print(f"{log_prefix} Error guardando memoria: {exc}")

        total_ms = int((time.monotonic() - t_start) * 1000)
        if audit_service:
            await audit_service.record_final_response(
                session_id=session_id,
                model_id=BEDROCK_MODEL_ID,
                response=response_content,
                total_latency_ms=total_ms,
            )

        return OrchestratorResult(
            response=response_content,
            session_id=session_id,
            user_profile=user_profile_dict,
            user_prefs=user_prefs,
            is_early_exit=False,
            total_ms=total_ms,
        )


def _extract_response(result: dict) -> str:
    """Extrae el texto de respuesta del resultado del grafo."""
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


# ── Singleton ─────────────────────────────────────────────────────────────

_orchestrator: Optional[QueryOrchestrator] = None


def get_orchestrator() -> QueryOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = QueryOrchestrator()
    return _orchestrator
