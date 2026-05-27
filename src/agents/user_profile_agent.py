"""
User Profile Agent — Construye UserContext unificado (nodo determinístico, sin LLM).

Responsabilidades (centralizadas aquí, antes dispersas en 4 archivos):
  1. Resolución de identidad: segmento normalizado, channel_ids, product_ids
  2. Resolución de geografía: provincia (query > prefs > perfil bancario) → state_ids
  3. Análisis de preferencias: top_cat, top_dias, recencia
  4. Estado de sesión: is_new_session, greeting_name
  5. Detección de conflicto de segmento: downgrade + nota para el LLM

Caché Redis (TTL: 12h):
  - Clave: comafi:user_context:{phone_normalizado}
  - Almacena SÓLO los campos de identidad (estables entre sesiones).
  - Los campos dinámicos (prefs, sesión, conflicto, context_block)
    se computan siempre desde el estado actual del turno.
  - Beneficio: evita re-normalizar segmento y relookup de IDs en cada request.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

try:
    from .base_agent import AgentState
    from ..models.user_context import (
        SegmentConflict,
        UserContext,
        USER_CONTEXT_CACHE_KEY_PREFIX,
        USER_CONTEXT_CACHE_TTL,
    )
    from ..models.queries_types import (
        CHANNELS,
        SEGMENT_RANK,
        SEGMENT_TO_PRODUCTS,
        normalize_segment,
    )
    from ..tools.benefits_api import _resolve_state_ids
    from ..cache import get_cache_service
    from ..config import CACHE_ENABLED
except ImportError:
    from src.agents.base_agent import AgentState
    from src.models.user_context import (
        SegmentConflict,
        UserContext,
        USER_CONTEXT_CACHE_KEY_PREFIX,
        USER_CONTEXT_CACHE_TTL,
    )
    from src.models.queries_types import (
        CHANNELS,
        SEGMENT_RANK,
        SEGMENT_TO_PRODUCTS,
        normalize_segment,
    )
    from src.tools.benefits_api import _resolve_state_ids
    from src.cache import get_cache_service
    from src.config import CACHE_ENABLED


_DIAS_ES = [
    "lunes", "martes", "miércoles", "jueves",
    "viernes", "sábado", "domingo",
]


def _normalize_phone(phone: str) -> str:
    return "".join(c for c in phone if c.isdigit() or c == "+")


# ── Builder ───────────────────────────────────────────────────────────────────

class UserContextBuilder:
    """
    Construye un UserContext completo desde los datos ya cargados en el estado.

    Sin I/O — opera sobre dicts en memoria. Toda la lógica de perfil vive aquí.
    """

    def __init__(
        self,
        raw_profile: dict,
        raw_prefs: dict,
        classification: dict,
        is_new_session: bool = False,
        has_phone: bool = False,
    ) -> None:
        self._profile = raw_profile or {}
        self._prefs = raw_prefs or {}
        self._clf = classification or {}
        self._is_new_session = is_new_session
        self._has_phone = has_phone

    # ── API pública ───────────────────────────────────────────────────

    def build(self) -> UserContext:
        """Construye UserContext completo sin caché (cache MISS o sin teléfono)."""
        ctx = UserContext()
        self._resolve_identity(ctx)
        self._resolve_geography(ctx)
        self._resolve_preferences(ctx)
        self._resolve_session(ctx)
        self._resolve_segment_conflict(ctx)
        ctx.context_block = self._build_context_block(ctx)
        return ctx

    def build_stable_fields(self) -> dict:
        """Devuelve sólo los campos de identidad serializables para Redis."""
        ctx = UserContext()
        self._resolve_identity(ctx)
        return ctx.to_stable_dict()

    def build_from_stable(self, stable: dict) -> UserContext:
        """
        Construye UserContext completo desde campos de identidad cacheados.

        Los campos volátiles (prefs, sesión, conflicto, context_block)
        se calculan siempre frescos, independientemente del caché.
        """
        ctx = UserContext.from_stable_dict(stable)
        self._resolve_geography(ctx)
        self._resolve_preferences(ctx)
        self._resolve_session(ctx)
        self._resolve_segment_conflict(ctx)
        ctx.context_block = self._build_context_block(ctx)
        return ctx

    # ── Resolvers ─────────────────────────────────────────────────────

    def _resolve_identity(self, ctx: UserContext) -> None:
        p = self._profile
        if not p.get("identificado"):
            return

        ctx.identificado = True
        ctx.segmento_display = p.get("segmento")
        ctx.productos = list(p.get("productos") or [])

        raw_nombre = p.get("nombre_completo") or p.get("nombre")
        if raw_nombre:
            ctx.nombre = raw_nombre.split()[0].title()

        seg_raw = (p.get("segmento") or "").strip()
        ctx.segmento_key = normalize_segment(seg_raw) if seg_raw else "standard"
        ctx.channel_ids = list(CHANNELS.get(ctx.segmento_key) or [])
        ctx.product_ids = list(SEGMENT_TO_PRODUCTS.get(ctx.segmento_key) or [])

    def _resolve_geography(self, ctx: UserContext) -> None:
        # Prioridad: clasificación del turno > prefs guardadas > perfil bancario
        clf_prov = self._clf.get("provincia")
        prefs_prov = self._prefs.get("ciudad")
        profile_prov = self._profile.get("provincia")

        if clf_prov:
            ctx.provincia_key = clf_prov
            ctx.provincia_source = "query"
        elif prefs_prov:
            ctx.provincia_key = prefs_prov
            ctx.provincia_source = "prefs"
        elif profile_prov:
            ctx.provincia_key = profile_prov
            ctx.provincia_source = "profile"

        if ctx.provincia_key:
            ctx.state_ids = _resolve_state_ids(ctx.provincia_key)

    def _resolve_preferences(self, ctx: UserContext) -> None:
        cat_counts: dict = self._prefs.get("cat_counts") or {}
        if cat_counts:
            best = max(cat_counts, key=cat_counts.get)
            if cat_counts[best] >= 2:
                ctx.top_categoria = best

        day_counts: dict = self._prefs.get("day_counts") or {}
        top_dias = [d for d, c in day_counts.items() if c >= 2]
        ctx.top_dias = top_dias or None

        ctx.last_categoria = self._prefs.get("last_categoria")
        last_at: Optional[str] = self._prefs.get("last_searched_at")
        if ctx.last_categoria and last_at:
            try:
                last_dt = datetime.fromisoformat(last_at)
                mins_ago = int(
                    (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
                )
                if mins_ago < 30:
                    ctx.last_searched_ago = f"hace {mins_ago} min"
            except Exception:
                pass

        ctx.location_asked = bool(self._prefs.get("location_asked", False))

    def _resolve_session(self, ctx: UserContext) -> None:
        ctx.is_new_session = self._is_new_session
        if ctx.is_new_session and ctx.identificado and ctx.nombre:
            ctx.greeting_name = ctx.nombre

    def _resolve_segment_conflict(self, ctx: UserContext) -> None:
        req_seg: Optional[str] = self._clf.get("segmento")
        if not req_seg or not ctx.identificado:
            return
        try:
            req_key = normalize_segment(req_seg)
            usr_key = ctx.segmento_key
            if SEGMENT_RANK.get(req_key, 0) > SEGMENT_RANK.get(usr_key, 0):
                req_d = req_key.replace("_", " ").title()
                usr_d = usr_key.replace("_", " ").title()
                ctx.segment_conflict = SegmentConflict(
                    requested_key=req_key,
                    actual_key=usr_key,
                    note=(
                        f"\nIMPORTANTE: El usuario consultó beneficios "
                        f"{req_d} pero su segmento es {usr_d}. No tiene "
                        f"acceso a beneficios exclusivos {req_d}. "
                        f"Mostrá los beneficios disponibles para {usr_d} "
                        f"e informale con tono amable que no cuenta con "
                        f"el nivel {req_d}."
                    ),
                )
                print(
                    f"[UserProfile] Conflicto de segmento: "
                    f"pedido={req_key} > real={usr_key} → override"
                )
        except Exception as exc:
            print(f"[UserProfile] Error en conflicto de segmento: {exc}")

    def _build_context_block(self, ctx: UserContext) -> str:
        hoy = datetime.now()
        fecha = f"{_DIAS_ES[hoy.weekday()]} {hoy.strftime('%d/%m/%Y')}"
        lines: list[str] = [f"- Fecha actual: {fecha}"]

        # ── Identidad ─────────────────────────────────────────────────
        if ctx.identificado:
            if ctx.segmento_display:
                lines.append(f"- Segmento: {ctx.segmento_display}")
                seg = ctx.segmento_key
                if seg == "black":
                    lines.append(
                        "- Tono: sofisticado y exclusivo. "
                        "Destacá beneficios Black primero."
                    )
                elif seg in ("premium", "premium_platinum"):
                    lines.append(
                        "- Tono: premium. "
                        "Priorizá beneficios exclusivos del segmento."
                    )
                elif seg in ("plan_sueldo", "pyme"):
                    lines.append(
                        "- Tono: amigable y directo. "
                        "Destacá el ahorro concreto en pesos y porcentaje."
                    )
            if ctx.productos:
                lines.append(f"- Productos: {', '.join(ctx.productos)}")

        # ── Preferencias históricas ────────────────────────────────────
        if ctx.top_categoria:
            cat_count = (
                (self._prefs.get("cat_counts") or {}).get(ctx.top_categoria, 0)
            )
            lines.append(
                f"- Categoría favorita del cliente: {ctx.top_categoria} "
                f"(buscada {cat_count} veces)."
            )
        if ctx.top_dias:
            lines.append(
                f"- Días habituales de búsqueda: {', '.join(ctx.top_dias)}."
            )

        # ── Recencia ──────────────────────────────────────────────────
        if ctx.last_searched_ago and not ctx.is_new_session:
            lines.append(
                f"- {ctx.last_searched_ago} buscó: {ctx.last_categoria}. "
                "Si la consulta es amplia, podés sugerirle continuar "
                "con esa categoría."
            )

        # ── Zona/ciudad ───────────────────────────────────────────────
        ciudad_display: Optional[str] = self._prefs.get("ciudad_display")
        if ciudad_display:
            lines.append(f"- Zona/ciudad del cliente: {ciudad_display}")
            lines.append(
                "  Mencioná la zona cuando sea relevante para contextualizar "
                "los beneficios (ej: 'beneficios disponibles en tu zona')."
            )
        elif self._has_phone and not ctx.location_asked:
            lines.append(
                "- Zona del cliente: no registrada. "
                "Al FINAL de tu respuesta añadí esta pregunta: "
                "'¿De qué zona o ciudad sos? "
                "Así puedo mostrarte beneficios más cercanos.' "
                "Preguntalo una única vez."
            )

        return "Contexto del cliente:\n" + "\n".join(lines)


# ── Nodo LangGraph ────────────────────────────────────────────────────────────

def create_user_profile_agent():
    """
    Retorna el nodo user_profile_agent para registrar en el grafo.

    Estrategia de caché (Redis, TTL 12h):
      - HIT:  carga identidad cacheada → computa prefs/sesión/conflicto frescos
      - MISS: computa identidad → guarda en Redis → computa el resto
    """
    async def user_profile_agent_node(state: AgentState) -> dict:
        phone: Optional[str] = state.get("phone_number")
        raw_profile: dict = state.get("user_profile") or {}
        raw_prefs: dict = state.get("user_prefs") or {}
        context: dict = state.get("context") or {}
        classification: dict = context.get("classification") or {}
        is_new_session: bool = state.get("is_new_session", False)

        builder = UserContextBuilder(
            raw_profile=raw_profile,
            raw_prefs=raw_prefs,
            classification=classification,
            is_new_session=is_new_session,
            has_phone=bool(phone),
        )

        user_context: UserContext

        if phone and CACHE_ENABLED:
            cache_key = (
                f"{USER_CONTEXT_CACHE_KEY_PREFIX}{_normalize_phone(phone)}"
            )
            stable: Optional[dict] = None

            try:
                cache = await get_cache_service()
                stable = await cache.get(cache_key)
            except Exception as exc:
                print(f"[UserProfile] Error leyendo caché: {exc}")

            if stable:
                print(f"[UserProfile] Cache HIT identity: ...{phone[-4:]}")
                user_context = builder.build_from_stable(stable)
            else:
                stable = builder.build_stable_fields()
                user_context = builder.build_from_stable(stable)
                try:
                    cache = await get_cache_service()
                    await cache.set(
                        cache_key,
                        stable,
                        ttl=USER_CONTEXT_CACHE_TTL,
                    )
                    print(
                        f"[UserProfile] Cache SET identity: ...{phone[-4:]} "
                        f"(TTL=12h, seg={user_context.segmento_key})"
                    )
                except Exception as exc:
                    print(f"[UserProfile] Error guardando caché: {exc}")
        else:
            user_context = builder.build()

        return {"user_context": user_context}

    return user_profile_agent_node
