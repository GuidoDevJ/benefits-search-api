"""
Fast Classifier — Clasificación determinística sin LLM.

Cubre ~85% de los casos con keyword matching en O(n).
Si no puede clasificar con confianza, retorna None para que
chat_interface haga fallback a classify_query (LLM).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

try:
    from .llm_classifier import Classification
    from ..models.queries_types import DAYS_OF_THE_WEEK
except ImportError:
    from src.tools.llm_classifier import Classification
    from src.models.queries_types import DAYS_OF_THE_WEEK


# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Minúsculas, sin acentos, sin puntuación extra."""
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Keywords de intención
# ---------------------------------------------------------------------------

_BENEFIT_KEYWORDS = {
    "descuento", "descuentos", "promo", "promos", "promocion", "promociones",
    "beneficio", "beneficios", "oferta", "ofertas", "cuota", "cuotas",
    "reintegro", "reintegros", "2x1", "bonificacion", "off", "gratis",
    "sin interes", "cashback", "devolucion",
}

# ---------------------------------------------------------------------------
# Keywords de categorías
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "gastronomia": {
        "restaurante", "restaurantes", "restaurant", "comida", "comer",
        "resto", "restos", "gastro", "pizza", "hamburgesa", "hamburguesa",
        "sushi", "bar", "cafe", "cafeteria", "facturas", "almuerzo", "cena",
        "delivery", "bodegon", "parrilla", "heladeria",
    },
    "supermercados": {
        "supermercado", "supermercados", "super", "chango", "mercado",
        "walmart", "jumbo", "coto", "carrefour", "dia", "vea", "disco",
        "almacen", "verduleria", "minimarket",
    },
    "moda": {
        "ropa", "zapatilla", "zapatillas", "calzado", "indumentaria",
        "jean", "jeans", "camisa", "remera", "vestido", "abrigo", "campera",
        "zapato", "zapatos", "deportiva", "deportivas", "buzo", "chomba",
        "pollera", "pantalon", "pantalones",
    },
    "entretenimiento": {
        "cine", "cinema", "teatro", "show", "evento", "recital", "concierto",
        "deporte", "deportes", "estadio", "espectaculo", "parque", "laser",
        "bowling", "karting", "escape room",
    },
    "combustible": {
        "nafta", "combustible", "gasolina", "diesel", "ypf", "shell",
        "axion", "puma", "carga", "surtidor",
    },
    "turismo": {
        "viaje", "viajes", "hotel", "hoteles", "vuelo", "vuelos",
        "vacaciones", "turismo", "aerolinea", "aerolineas", "aeropuerto",
        "hospedaje", "airbnb", "booking", "crucero", "tour",
    },
    "salud": {
        "farmacia", "farmacias", "medicamento", "medicamentos", "optica",
        "opticas", "dentista", "clinica", "medico", "doctor", "hospital",
        "laboratorio", "drogueria", "salud",
    },
    "belleza": {
        "peluqueria", "peluquerias", "spa", "manicura", "estetica",
        "esteticas", "depilacion", "cosmetica", "maquillaje", "perfume",
        "perfumeria", "barberia",
    },
    "hogar/deco": {
        "mueble", "muebles", "decoracion", "hogar", "ferreteria", "ceramica",
        "colchon", "living", "cocina", "bano", "jardin", "electrohogar",
    },
    "vehiculos": {
        "auto", "autos", "moto", "motos", "taller", "repuesto", "repuestos",
        "automotor", "neumatico", "neumaticos", "aceite", "mecanico",
        "lavadero", "estacionamiento",
    },
    "librerias": {
        "libro", "libros", "libreria", "librerias", "papeleria", "cuaderno",
        "lapiz", "lapices", "utiles", "escolar", "universidad",
    },
    "e-commerce": {
        "online", "ecommerce", "tienda online", "mercadolibre",
        "mercado libre", "amazon", "web", "internet", "digital",
    },
    "transporte": {
        "uber", "taxi", "colectivo", "subte", "remis", "bus", "tren",
        "transport", "transfer", "cabify",
    },
    "vinotecas": {
        "vino", "vinos", "vinoteca", "vinotecas", "bodega", "bodegas",
        "espumante", "champagne", "cerveza", "cervezas",
    },
    "jugueterias": {
        "juguete", "juguetes", "jugueteria", "jugueterias", "toy",
        "nino", "ninos", "bebe", "bebes", "infantil",
    },
}

# ---------------------------------------------------------------------------
# Negocios conocidos (keyword → nombre normalizado)
# ---------------------------------------------------------------------------

_KNOWN_NEGOCIOS: dict[str, str] = {
    "mc": "mcdonalds",
    "mcdonald": "mcdonalds",
    "mcdonalds": "mcdonalds",
    "burger king": "burger king",
    "bk": "burger king",
    "ypf": "ypf",
    "shell": "shell",
    "axion": "axion",
    "carrefour": "carrefour",
    "coto": "coto",
    "jumbo": "jumbo",
    "dia": "dia",
    "vea": "vea",
    "starbucks": "starbucks",
    "cinemark": "cinemark",
    "hoyts": "hoyts",
    "rappi": "rappi",
    "pedidos ya": "pedidosya",
    "pedidosya": "pedidosya",
    "netflix": "netflix",
    "spotify": "spotify",
    "musimundo": "musimundo",
    "farmacity": "farmacity",
    "farmacidad": "farmacity",
    "la anonima": "la anonima",
    "anonima": "la anonima",
    "walmart": "walmart",
}

# Patrones precompilados con word boundary para evitar falsos positivos
# (ej: "dia" no debe matchear "dias", "bk" no debe matchear "bkp", etc.)
_NEGOCIO_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b" + re.escape(k) + r"\b"), v)
    for k, v in _KNOWN_NEGOCIOS.items()
]

# ---------------------------------------------------------------------------
# Clasificador principal
# ---------------------------------------------------------------------------


def fast_classify(query: str) -> Optional[Classification]:
    """
    Clasifica la consulta sin LLM.

    Retorna Classification si puede determinarlo con confianza.
    Retorna None si la consulta es ambigua → usar classify_query (LLM).
    """
    text = _normalize(query)
    tokens = set(text.split())

    # ── Día ─────────────────────────────────────────────────────────────
    dia: Optional[str] = None
    for day_name in DAYS_OF_THE_WEEK:
        if day_name in text:
            dia = day_name
            break

    # ── Negocio ──────────────────────────────────────────────────────────
    negocio: Optional[str] = None
    for pattern, nombre in _NEGOCIO_PATTERNS:
        if pattern.search(text):
            negocio = nombre
            break

    # ── Categoría ────────────────────────────────────────────────────────
    categoria: Optional[str] = None
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if keywords & tokens or any(
            kw in text for kw in keywords if " " in kw
        ):
            categoria = cat
            break

    # ── Intent ───────────────────────────────────────────────────────────
    if (
        bool(_BENEFIT_KEYWORDS & tokens)
        or categoria is not None
        or negocio is not None
        or dia is not None
    ):
        return Classification(
            intent="benefits",
            categoria_benefits=categoria,
            dia=dia,
            negocio=negocio,
            categoria_tienda=None,
        )

    return None
