"""
UserContext — Representación unificada del contexto del usuario para un turno.

Campos estables (cacheables 12h en Redis):
  identificado, nombre, segmento_key/display, channel_ids, product_ids, productos

Campos dinámicos (calculados siempre en runtime):
  provincia_key/state_ids (prefs pueden cambiar si el usuario da su ubicación)
  top_categoria/dias, last_searched_ago (contadores de prefs, alta volatilidad)
  is_new_session, greeting_name (estado de sesión)
  segment_conflict (depende de la clasificación actual)
  context_block (texto armado para el system prompt)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

USER_CONTEXT_CACHE_KEY_PREFIX = "comafi:user_context:"
USER_CONTEXT_CACHE_TTL = 12 * 3600  # 12 h — sólo campos de identidad


@dataclass
class SegmentConflict:
    """Conflicto entre el segmento pedido y el segmento real del usuario."""
    requested_key: str   # "black"
    actual_key: str      # "premium"
    note: str            # instrucción lista para inyectar en INSTRUCCIÓN


@dataclass
class UserContext:
    """Contexto completo del usuario para un turno de conversación."""

    # ── Identidad (desde user_profile — cacheables 12h) ───────────────
    identificado: bool = False
    nombre: Optional[str] = None           # primer nombre, capitalizado
    segmento_display: Optional[str] = None  # "Comafi Unico Black"
    segmento_key: str = "standard"          # clave normalizada interna
    channel_ids: list[int] = field(default_factory=list)   # p.ej. [1352]
    product_ids: list[int] = field(default_factory=list)   # IDs o[] tarjetas
    productos: list[str] = field(default_factory=list)     # nombres tarjetas

    # ── Geografía (query > prefs > perfil bancario) ───────────────────
    provincia_key: Optional[str] = None     # "corrientes"
    provincia_source: Optional[str] = None  # "query" | "prefs" | "profile"
    state_ids: Optional[list[int]] = None   # [70] para TeVaBien API

    # ── Preferencias históricas (prefs Redis) ─────────────────────────
    top_categoria: Optional[str] = None
    top_dias: Optional[list[str]] = None
    last_categoria: Optional[str] = None
    last_searched_ago: Optional[str] = None  # "hace 3 min"
    location_asked: bool = False

    # ── Sesión (dinámico, no cacheable) ───────────────────────────────
    is_new_session: bool = False
    greeting_name: Optional[str] = None     # None = no saludar

    # ── Conflicto de segmento (dinámico, depende de clasificación) ────
    segment_conflict: Optional[SegmentConflict] = None

    # ── Bloque de texto para system prompt (dinámico) ─────────────────
    context_block: str = ""

    # ── Serialización para Redis ──────────────────────────────────────

    def to_stable_dict(self) -> dict:
        """Sólo los campos de identidad — los únicos que vale la pena cachear."""
        return {
            "identificado":     self.identificado,
            "nombre":           self.nombre,
            "segmento_display": self.segmento_display,
            "segmento_key":     self.segmento_key,
            "channel_ids":      self.channel_ids,
            "product_ids":      self.product_ids,
            "productos":        self.productos,
        }

    @classmethod
    def from_stable_dict(cls, d: dict) -> "UserContext":
        return cls(
            identificado=d.get("identificado", False),
            nombre=d.get("nombre"),
            segmento_display=d.get("segmento_display"),
            segmento_key=d.get("segmento_key", "standard"),
            channel_ids=d.get("channel_ids") or [],
            product_ids=d.get("product_ids") or [],
            productos=d.get("productos") or [],
        )
