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

import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

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


# ── Detección de consultas de ayuda/capacidades ───────────────────────────

_HELP_PHRASES = frozenset(
    {
        "con que me podes ayudar",
        "con que me podras ayudar",
        "con que me puedes ayudar",
        "en que me ayudas",
        "en que me podes ayudar",
        "en que me puedes ayudar",
        "que podes hacer",
        "que podras hacer",
        "que puedes hacer",
        "que haces",
        "que hacen",
        "para que sirves",
        "para que sirve",
        "ayuda",
        "help",
        "menu",
        "opciones",
        "que ofrecen",
        "que ofreces",
        "que me podes ofrecer",
        "que me puedes ofrecer",
        "como funciona",
        "que es esto",
        "que podes mostrarme",
        "que podes mostrar",
        "que tipos de beneficios hay",
        "que beneficios hay",
        "que opciones hay",
        "que consultas puedo hacer",
        "sobre que me podes ayudar",
        "sobre que me puedes ayudar",
    }
)

_CAPABILITIES_RESPONSE = (
    "Puedo ayudarte a encontrar descuentos y beneficios. "
    "Algunas cosas que podés consultarme:\n"
    "• Descuentos por categoría: gastronomía, supermercados, combustible, "
    "moda, cine, salud y más\n"
    "• Beneficios para días específicos (ej: 'descuentos los jueves')\n"
    "• Beneficios en comercios concretos (ej: 'promos en Carrefour')\n"
    "• Cuotas sin interés disponibles\n"
    "• Beneficios por zona o ciudad\n\n"
    "¿Sobre qué querés consultar?"
)

_UNKNOWN_RESPONSE = (
    "Solo puedo ayudarte con descuentos y beneficios de tu tarjeta Comafi. "
    "Podés preguntarme sobre:\n"
    "• Gastronomía, supermercados, combustible, moda, cine y más\n"
    "• Beneficios para días específicos o comercios concretos"
)

# ── Confirmación de exit intent ───────────────────────────────────────────

_PENDING_EXIT_KEY = "pending_exit_query"

_EXIT_CONFIRM_QUESTION = (
    "Antes de continuar, ¿lo que consultaste tiene alguna relación con "
    "descuentos, beneficios o comercios de tu tarjeta Comafi? "
    "Respondé Sí o No."
)

_EXIT_CONFIRMED_BACK = (
    "Perfecto, seguí consultando sobre descuentos y beneficios."
)

_EXIT_CONFIRMED_OUT = (
    "Entendido. Eso está fuera de lo que puedo ayudarte acá. "
    "Cuando lo necesites, volvé a escribir exactamente:\n\n"
    '"{query}"'
)

_EXIT_REASK = (
    "No entendí tu respuesta. "
    "¿Lo que preguntaste antes tiene que ver con beneficios o descuentos "
    "de tu tarjeta? Respondé Sí o No."
)

async def _classify_confirmation(query: str) -> str:
    """
    Usa el mismo LLM del pipeline para determinar si la respuesta del
    usuario a la pregunta de confirmación es AFIRMATIVO, NEGATIVO o DUDOSO.

    Retorna siempre una de esas tres strings. En caso de error retorna DUDOSO.
    """
    try:
        from ..tools.llm_classifier import _llm
    except ImportError:
        from src.tools.llm_classifier import _llm

    from langchain_core.messages import HumanMessage

    prompt = (
        "El usuario respondió a la pregunta: "
        "'¿Lo que consultaste tiene alguna relación con descuentos, "
        "beneficios o comercios de tu tarjeta Comafi?'\n\n"
        f'Su respuesta fue: "{query}"\n\n'
        "Analizá si es una respuesta afirmativa o negativa a esa pregunta.\n"
        "Respondé ÚNICAMENTE con una de estas tres palabras: "
        "AFIRMATIVO, NEGATIVO o DUDOSO"
    )

    try:
        response = await _llm.ainvoke([HumanMessage(content=prompt)])
        raw = response.content
        result = (raw if isinstance(raw, str) else str(raw)).strip().upper()
        for keyword in ("AFIRMATIVO", "NEGATIVO", "DUDOSO"):
            if keyword in result:
                return keyword
        return "DUDOSO"
    except Exception as exc:
        print(f"[ExitConfirm] Error clasificando confirmación: {exc}")
        return "DUDOSO"


def _is_help_query(query: str) -> bool:
    """Detecta si la consulta es una pregunta sobre capacidades del agente."""
    normalized = _normalize_text(query)
    return normalized in _HELP_PHRASES or any(
        phrase in normalized for phrase in _HELP_PHRASES if len(phrase) > 8
    )


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
            fast_classify,
            _VER_MAS_AFFIRMATIVES,
        )
    except ImportError:
        from src.tools.fast_classifier import (
            fast_classify,
            _VER_MAS_AFFIRMATIVES,
        )

    for msg in reversed(history):
        if not isinstance(msg, HumanMessage):
            continue
        normalized = _normalize_text(msg.content)
        tokens = set(normalized.split())
        if not tokens or tokens.issubset(_VER_MAS_AFFIRMATIVES) or len(normalized) <= 2:
            continue
        clf = fast_classify(msg.content)
        if clf and clf.intent == "benefits":
            return clf.model_dump()

    return None


def _count_benefits_pages_in_history(history: list) -> int:
    """
    Cuenta cuántas páginas de beneficios ya se mostraron en la conversación
    escaneando los AIMessages del historial.

    Se usa para estimar el offset correcto cuando search_context expiró
    y el bot necesita continuar la paginación sin perder el lugar.
    """
    _MARKERS = ("🎁", "beneficio", "descuento", "% off", "cuotas")
    count = 0
    for msg in history:
        if not isinstance(msg, AIMessage):
            continue
        content = (
            msg.content if isinstance(msg.content, str) else str(msg.content)
        ).lower()
        if any(m in content for m in _MARKERS):
            count += 1
    return count


# ── Rescate de gathering activo ───────────────────────────────────────────


def _rescue_gathering_response(
    query: str,
    gathering_ctx: dict,
) -> Optional[Any]:
    """
    Interpreta una respuesta corta del usuario cuando hay un gathering activo.

    El LLM sin historial clasifica "entretenimiento" o "combustible" como
    intent="unknown" porque son palabras sueltas sin señal de beneficio.
    Este helper agrega un hint ("beneficios <query>") para activar
    fast_classify y hereda dias/provincia del gathering anterior.

    Se invoca SOLO cuando intent="unknown" + search_context.gathering=True,
    es decir cuando el bot hizo una pregunta de clarificación y el usuario
    está respondiendo con la categoría o entidad que faltaba.

    Returns:
        Classification con intent="benefits" si pudo inferir, None si no.
    """
    try:
        from ..tools.fast_classifier import fast_classify
        from ..tools.llm_classifier import Classification
    except ImportError:
        from src.tools.fast_classifier import fast_classify
        from src.tools.llm_classifier import Classification

    # Agregar señal explícita de beneficio para activar fast_classify.
    # Ej: "entretenimiento" → fast_classify("beneficios entretenimiento")
    # devuelve Classification(intent="benefits", categoria="entretenimiento")
    clf = fast_classify(f"beneficios {query.strip()}")
    if clf is None or clf.intent != "benefits":
        return None

    # Heredar días y provincia del contexto de gathering previo si la
    # respuesta corta del usuario no los incluye.
    inherited_dias: Optional[list] = gathering_ctx.get("dias") or (
        [gathering_ctx["dia"]] if gathering_ctx.get("dia") else None
    )
    return Classification(
        intent="benefits",
        categoria_benefits=clf.categoria_benefits,
        negocio=clf.negocio,
        segmento=clf.segmento or gathering_ctx.get("segmento"),
        tipo_beneficio=clf.tipo_beneficio,
        provincia=clf.provincia or gathering_ctx.get("provincia"),
        dias=clf.dias or inherited_dias,
        dia=(
            clf.dia
            or (
                inherited_dias[0]
                if inherited_dias and len(inherited_dias) == 1
                else None
            )
        ),
    )


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
    exit_intent: bool = False
    detected_intent: Optional[str] = None
    trigger_text: Optional[str] = None
    flow_id: Optional[str] = None


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

        # ── 0. Consultas de ayuda/capacidades (salida sin LLM ni Redis) ────
        if _is_help_query(query):
            total_ms = int((time.monotonic() - t_start) * 1000)
            if audit_service:
                await audit_service.record_user_input(
                    session_id=session_id,
                    model_id=BEDROCK_MODEL_ID,
                    query=query,
                    nlp_result={"intent": "help"},
                )
                await audit_service.record_final_response(
                    session_id=session_id,
                    model_id=BEDROCK_MODEL_ID,
                    response=_CAPABILITIES_RESPONSE,
                    total_latency_ms=total_ms,
                )
            return OrchestratorResult(
                response=_CAPABILITIES_RESPONSE,
                session_id=session_id,
                is_early_exit=True,
                total_ms=total_ms,
            )

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
                # Excepción: si el usuario tiene una confirmación de exit
                # pendiente (ej: respondió "no"/"si" a la pregunta de salida),
                # la query corta es válida — no la descartar.
                _has_pending = False
                if phone:
                    try:
                        from ..memory import get_prefs_service as _gps
                        _ps = await _gps()
                        _sc = await _ps.load_search_context(phone)
                        _has_pending = bool(_sc.get(_PENDING_EXIT_KEY))
                    except Exception:
                        pass

                if not _has_pending:
                    resp = _UNKNOWN_RESPONSE
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
                            total_latency_ms=int((time.monotonic() - t_start) * 1000),
                        )
                    return OrchestratorResult(
                        response=resp,
                        session_id=session_id,
                        is_early_exit=True,
                        total_ms=int((time.monotonic() - t_start) * 1000),
                        exit_intent=True,
                    )

            if classification is None:
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

        # ── 3b. Cargar search_context (temprano para rescue de gathering) ──
        # Debe cargarse ANTES del check de intent="unknown" para que el
        # rescue pueda usarlo cuando el usuario responde a una clarificación.
        search_context: dict = {}
        if phone and prefs_svc:
            try:
                search_context = await prefs_svc.load_search_context(phone)
            except Exception:
                pass

        # ── 3c. Segmento sin señal de beneficio → clarificación ─────────────
        # Caso: "soy black", "cliente premium" — solo se identifica el segmento
        # sin pedir beneficios concretos. Se pide qué categoría busca.
        #
        # NO aplica si la query tiene keywords explícitas de beneficio:
        # "beneficios de comafi black" → tiene "beneficios" → flujo normal.
        # Solo aplica para identificaciones puras: "soy black", "cliente premium".
        from ..tools.fast_classifier import _BENEFIT_KEYWORDS
        _query_tokens = set(_normalize_text(query).split())
        _query_has_benefit_kw = bool(_BENEFIT_KEYWORDS & _query_tokens)

        if (
            classification.intent == "benefits"
            and classification_dict.get("segmento")
            and not classification_dict.get("categoria_benefits")
            and not classification_dict.get("negocio")
            and not classification_dict.get("dias")
            and not classification_dict.get("tipo_beneficio")
            and not _query_has_benefit_kw
        ):
            seg = classification_dict["segmento"]
            seg_display = seg.replace("_", " ").title()
            if "sueldo" in seg:
                seg_display = "Plan Sueldo"

            if phone and prefs_svc:
                try:
                    await prefs_svc.save_search_context(
                        phone,
                        {"segmento": seg, "intent": "benefits"},
                        gathering=True,
                    )
                except Exception:
                    pass

            # No afirmar el segmento del usuario — el perfil no está cargado aún.
            # Solo preguntar qué busca, mencionando el segmento como contexto.
            if "black" in seg:
                resp = (
                    f"¿Qué tipo de beneficios {seg_display} querés explorar? "
                    "Podés pedirme gastronomía, turismo, moda, combustible y más."
                )
            elif "premium" in seg or "platinum" in seg:
                resp = (
                    f"¿Qué beneficios {seg_display} querés ver? "
                    "Tenés descuentos en gastronomía, moda, salud, viajes y más."
                )
            else:
                resp = (
                    f"¿Qué tipo de beneficios {seg_display} estás buscando? "
                    "Podés pedirme gastronomía, supermercados, combustible, moda y más."
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

        # ── 3.5 Resolver negocio vía Business Index ──────────────────────────
        # fast_classify puede haber extraído un candidato heurístico en
        # classification.negocio (texto después de "en"/"para" no reconocido
        # como negocio conocido). Lo validamos contra el índice invertido.
        #
        # Lógica:
        #   - Si el índice confirma el token → se usa como substring filter.
        #   - Si el índice no lo encuentra Y el candidato es multi-word o largo
        #     (> 15 chars) → se descarta. Un negocio conocido como "ypf" o
        #     "carrefour" pasa siempre (vienen de _KNOWN_NEGOCIOS, no de aquí).
        #   - El check "classification.negocio not in _KNOWN_NEGOCIOS" permite
        #     identificar si el valor fue puesto por la heurística.
        if classification.intent == "benefits" and classification.negocio:
            try:
                from ..tools.fast_classifier import _KNOWN_NEGOCIOS
                from ..tools.business_index import resolve_negocio
                from ..tools.llm_classifier import Classification as _Clf

                _is_heuristic_candidate = (
                    classification.negocio not in _KNOWN_NEGOCIOS.values()
                )
                if _is_heuristic_candidate:
                    resolved = await resolve_negocio(classification.negocio)
                    if resolved:
                        print(
                            f"{log_prefix} [BizIndex] "
                            f"'{classification.negocio}' → '{resolved}'"
                        )
                        classification = _Clf(
                            **{**classification_dict, "negocio": resolved}
                        )
                        classification_dict = classification.model_dump()
                    else:
                        # Candidato heurístico no confirmado en el índice.
                        # Descartamos multi-word o strings largos; los cortos
                        # (p.ej. nombre de 1 sola palabra corta) se dejan pasar
                        # porque el substring match puede funcionar igual.
                        _candidate = classification.negocio
                        if " " in _candidate or len(_candidate) > 15:
                            print(
                                f"{log_prefix} [BizIndex] candidato "
                                f"'{_candidate}' no confirmado → descartado"
                            )
                            classification = _Clf(
                                **{**classification_dict, "negocio": None}
                            )
                            classification_dict = classification.model_dump()
            except Exception as _biz_exc:
                print(f"{log_prefix} [BizIndex] error en resolución: {_biz_exc}")

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

        # ── 5. intent="unknown" → rescue gathering o salida temprana ────────
        if classification.intent == "unknown":
            # Si hay un gathering activo el usuario está respondiendo a una
            # pregunta de clarificación. El LLM sin historial clasifica
            # "entretenimiento" como unknown; intentamos interpretarlo
            # como categoría/entidad antes de renderizar el error.
            if search_context.get("gathering"):
                rescued = _rescue_gathering_response(query, search_context)
                if rescued is not None:
                    print(
                        f"{log_prefix} Gathering rescue: "
                        f"'{query}' → cat={rescued.categoria_benefits} "
                        f"dias={rescued.dias}"
                    )
                    classification = rescued
                    classification_dict = classification.model_dump()

        if classification.intent == "unknown":
            if on_unknown_query:
                try:
                    await on_unknown_query(query)
                except Exception as exc:
                    print(f"{log_prefix} Error en on_unknown_query: {exc}")

            total_ms = int((time.monotonic() - t_start) * 1000)
            pending_query = search_context.get(_PENDING_EXIT_KEY)

            # ── Turno N+1: el usuario responde a la confirmación ──────────
            if pending_query and phone and prefs_svc:
                result = await _classify_confirmation(query)
                print(
                    f"{log_prefix} ExitConfirm: "
                    f"'{query}' → {result} (pending='{pending_query[:40]}')"
                )

                # Limpiar el estado pendiente del search_context
                try:
                    clean_ctx = dict(search_context)
                    clean_ctx.pop(_PENDING_EXIT_KEY, None)
                    await prefs_svc.update(
                        phone, search_context=clean_ctx
                    )
                except Exception as exc:
                    print(f"{log_prefix} Error limpiando pending: {exc}")

                if result == "AFIRMATIVO":
                    resp = _EXIT_CONFIRMED_BACK
                    exit_flag = False
                elif result == "NEGATIVO":
                    resp = _EXIT_CONFIRMED_OUT.format(query=pending_query)
                    exit_flag = True
                else:
                    # DUDOSO — re-preguntar una vez, manteniendo el pending
                    try:
                        reask_ctx = dict(search_context)
                        await prefs_svc.update(
                            phone, search_context=reask_ctx
                        )
                    except Exception:
                        pass
                    resp = _EXIT_REASK
                    exit_flag = False

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
                    exit_intent=exit_flag,
                    trigger_text=pending_query if exit_flag else None,
                )

            # ── Turno N: nueva query desconocida → guardar y preguntar ────
            if phone and prefs_svc:
                try:
                    new_ctx = dict(search_context)
                    new_ctx[_PENDING_EXIT_KEY] = query
                    await prefs_svc.update(phone, search_context=new_ctx)
                    resp = _EXIT_CONFIRM_QUESTION
                except Exception as exc:
                    print(f"{log_prefix} Error guardando pending: {exc}")
                    resp = _UNKNOWN_RESPONSE
            else:
                # Sin sesión persistida → respuesta directa
                resp = _UNKNOWN_RESPONSE

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
                exit_intent=False,
            )

        # ── 6. Persistir provincia inline (query mixta beneficio+zona) ───
        if (
            phone
            and prefs_svc
            and classification.provincia
            and not user_prefs.get("ciudad")
        ):
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
                status = "identificado" if profile.identificado else "no identificado"
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

        # ── 10. Resolver graph_context ────────────────────────────────────
        graph_context: dict = {}
        merged_clf: dict = {}

        if classification.intent == "ver_mas":
            if search_context and not search_context.get("gathering"):
                # ── Caso normal: search_context fresco en Redis ───────────
                page = search_context.get("page", 1) + 1
                merged_clf = {
                    k: v
                    for k, v in search_context.items()
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
                # ── Cadena de fallback cuando search_context está ausente ──
                # Nivel 1: re-clasificar mensajes anteriores del historial
                recovered_clf = _recover_classification_from_history(history)

                # Nivel 2: last_full_search guardado en Redis/prefs.
                # Cubre el caso más común: query abreviada o LLM-clasificada
                # donde fast_classify no puede re-clasificar el mensaje.
                if not recovered_clf and prefs_svc and phone:
                    try:
                        recovered_clf = await prefs_svc.load_last_full_search(phone)
                        if recovered_clf:
                            print(
                                f"{log_prefix} ver_mas: recuperado de "
                                f"last_full_search — "
                                f"cat={recovered_clf.get('categoria_benefits')} "
                                f"dias={recovered_clf.get('dias')}"
                            )
                    except Exception:
                        pass

                if recovered_clf:
                    # Estimar la página correcta contando respuestas de
                    # beneficios ya mostradas, para no repetir desde el inicio.
                    pages_shown = max(_count_benefits_pages_in_history(history), 1)
                    recovered_clf["intent"] = "benefits"
                    recovered_clf["page"] = pages_shown + 1
                    merged_clf = recovered_clf
                    graph_context = {
                        "classification": merged_clf,
                        "offset": pages_shown * 5,
                    }
                else:
                    # Nivel 3: last_categoria de user_prefs (ya cargado,
                    # sin call extra a Redis). Garantiza que al menos se
                    # repite la categoría correcta en lugar de pedir aclaración.
                    last_cat = user_prefs.get("last_categoria") if user_prefs else None
                    if last_cat:
                        print(
                            f"{log_prefix} ver_mas: usando last_categoria "
                            f"de prefs: {last_cat}"
                        )
                        merged_clf = {
                            "intent": "benefits",
                            "categoria_benefits": last_cat,
                            "page": 2,
                        }
                        graph_context = {"classification": merged_clf, "offset": 5}
                    else:
                        # Sin ningún contexto recuperable: respuesta genérica
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
            gathering = search_context if search_context.get("gathering") else {}

            # Soft-hint: si la búsqueda anterior tenía categoría o negocio
            # y la nueva clasificación no detectó ninguno (ej: "ofertas en
            # combustibles" donde "combustibles" plural no matcheó), heredar
            # el contexto previo para evitar una clarificación innecesaria.
            # Solo aplica cuando no hay gathering activo.
            _prev_cat = search_context.get("categoria_benefits")
            _prev_neg = search_context.get("negocio")
            if (
                not search_context.get("gathering")
                and (_prev_cat or _prev_neg)
                and not classification_dict.get("categoria_benefits")
                and not classification_dict.get("negocio")
                and classification.intent == "benefits"
            ):
                _inherit_keys = [k for k in (
                    "categoria_benefits", "negocio", "provincia"
                ) if k in search_context]
                gathering = {k: search_context[k] for k in _inherit_keys}
                print(
                    f"{log_prefix} Soft-hint: heredando "
                    f"cat={gathering.get('categoria_benefits')} "
                    f"neg={gathering.get('negocio')} "
                    "del search_context anterior"
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

            if phone and prefs_svc:
                try:
                    await prefs_svc.save_last_full_search(phone, merged_clf)
                except Exception:
                    pass

            graph_context = {"classification": merged_clf}

        # ── 12. Invocar grafo ─────────────────────────────────────────────
        _max_hist = int(os.getenv("MAX_HISTORY_MSGS", "6"))
        _trimmed = history[-_max_hist:] if len(history) > _max_hist else history
        messages = _trimmed + [HumanMessage(content=query)]
        result = await get_graph().ainvoke(
            {
                "messages": messages,
                "next": "",
                "context": graph_context,
                "session_id": session_id,
                "audit_service": audit_service,
                "phone_number": phone,
                "user_profile": user_profile_dict,
                "user_prefs": user_prefs,
                "is_new_session": is_new_session,
            }
        )

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
                return final_message.content.get("message", str(final_message.content))
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
