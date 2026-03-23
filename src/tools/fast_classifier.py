"""
Fast Classifier — Clasificación determinística sin LLM.

Cubre ~85% de los casos con keyword matching en O(n).
Si no puede clasificar con confianza, retorna None para que
el agente haga fallback a classify_query (LLM).

Cambios respecto a la versión anterior:
- Soporte multi-día: detecta "fin de semana", "lunes a viernes", etc.
  y retorna lista en lugar de string simple
- Keywords completas: agrega bares, promos_del_mes, cercanos,
  vinotecas, mascotas, perfumeria
- Usa TRADE_ALIASES de queries_types para normalización consistente
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

try:
    from .llm_classifier import Classification
    from ..models.queries_types import resolve_province
except ImportError:
    from src.tools.llm_classifier import Classification
    from src.models.queries_types import resolve_province


# ── Normalización ─────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Minúsculas, sin acentos, sin puntuación extra."""
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ── Keywords de intención ─────────────────────────────────────────────────

_BENEFIT_KEYWORDS = {
    "descuento", "descuentos", "promo", "promos", "promocion",
    "promociones", "beneficio", "beneficios", "oferta", "ofertas",
    "cuota", "cuotas", "reintegro", "reintegros", "2x1",
    "bonificacion", "off", "gratis", "sin interes", "cashback",
    "devolucion", "rebaja", "rebajas", "especial",
}

# Keywords de segmento (indican query sobre beneficios exclusivos)
_SEGMENT_KEYWORDS = {
    "black": "black",
    "comafi black": "black",
    "unico black": "black",
    "premium platinum": "premium_platinum",
    "premium": "premium",
    "plan sueldo": "plan_sueldo",
    "pyme": "pyme",
}

# Intención de ver más resultados (paginación conversacional)
_VER_MAS_PHRASES = {
    "ver mas", "mostrame mas", "hay mas", "mas opciones",
    "mas beneficios", "siguientes", "proximos", "otras opciones",
    "seguir viendo", "mas resultados", "siguiente pagina",
}

# Preferencia de tipo de beneficio
_BENEFIT_TYPE_PHRASES: list[tuple[str, str]] = [
    ("cuotas sin interes", "cuotas"),
    ("sin interes",        "cuotas"),
    ("cuotas",             "cuotas"),
    ("descuento",          "descuento"),
    ("descuentos",         "descuento"),
]

# ── Keywords de categorías ────────────────────────────────────────────────

_CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "gastronomia": {
        "gastronomia", "restaurante", "restaurantes", "restaurant",
        "comida", "comer", "resto", "restos", "gastro", "pizza",
        "hamburguesa", "sushi", "cafe", "cafeteria", "facturas",
        "almuerzo", "cena", "delivery", "bodegon", "parrilla",
        "heladeria", "empanada",
    },
    "bares": {
        "bar", "bares", "pub", "pubs", "cerveceria", "brewery",
        "tragos", "copas", "fernet", "after", "birra",
    },
    "supermercados": {
        "supermercado", "supermercados", "super", "chango", "mercado",
        "walmart", "jumbo", "coto", "carrefour", "dia", "vea",
        "disco", "almacen", "minimarket",
    },
    "moda": {
        "moda", "ropa", "zapatilla", "zapatillas", "calzado",
        "indumentaria", "jean", "jeans", "camisa", "remera",
        "vestido", "abrigo", "campera", "zapato", "zapatos",
        "deportiva", "deportivas", "buzo", "chomba", "pollera",
        "pantalon", "pantalones",
    },
    "entretenimiento": {
        "teatro", "show", "evento", "recital", "concierto", "estadio",
        "espectaculo", "parque", "laser", "bowling", "karting",
    },
    "cine": {
        "cine", "cinema", "pelicula", "peliculas", "cinemark", "hoyts",
    },
    "deportes": {
        "deporte", "deportes", "futbol", "tenis", "padel",
        "natacion", "crossfit",
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
        "farmacia", "farmacias", "medicamento", "medicamentos",
        "optica", "opticas", "dentista", "clinica", "medico",
        "doctor", "hospital", "laboratorio", "drogueria", "salud",
    },
    "belleza": {
        "peluqueria", "peluquerias", "spa", "manicura", "estetica",
        "esteticas", "depilacion", "cosmetica", "maquillaje",
        "barberia",
    },
    "perfumeria": {
        "perfume", "perfumes", "perfumeria", "perfumerias",
        "colonia", "fragancia",
    },
    "hogar_deco": {
        "mueble", "muebles", "decoracion", "hogar", "ferreteria",
        "ceramica", "colchon", "living", "cocina", "bano", "jardin",
        "electrohogar", "pintureria",
    },
    "vehiculos": {
        "auto", "autos", "moto", "motos", "taller", "repuesto",
        "repuestos", "automotor", "neumatico", "neumaticos", "aceite",
        "mecanico", "lavadero", "estacionamiento",
    },
    "librerias": {
        "libro", "libros", "libreria", "librerias", "papeleria",
        "cuaderno", "lapiz", "lapices", "utiles", "escolar",
    },
    "ecommerce": {
        "online", "ecommerce", "mercadolibre", "mercado libre",
        "amazon", "web", "internet", "digital", "tienda online",
    },
    "transporte": {
        "uber", "taxi", "colectivo", "subte", "remis", "bus", "tren",
        "transfer", "cabify",
    },
    "vinotecas": {
        "vino", "vinos", "vinoteca", "vinotecas", "bodega", "bodegas",
        "espumante", "champagne", "cerveza", "cervezas",
    },
    "jugueterias": {
        "juguete", "juguetes", "jugueteria", "jugueterias", "toy",
        "nino", "ninos", "bebe", "bebes", "infantil",
    },
    "mascotas": {
        "mascota", "mascotas", "perro", "perros", "gato", "gatos",
        "veterinaria", "pet", "petshop",
    },
    "promos_del_mes": {
        "promo del mes", "promos del mes", "promocion del mes",
        "promociones del mes", "oferta del mes", "novedad", "novedades",
    },
    "cercanos": {
        "cerca", "cercano", "cercanos", "zona", "barrio",
        "en mi zona", "alrededor", "cerca mio",
    },
    "imperdibles": {
        "imperdible", "imperdibles", "no te lo pierdas",
    },
}

# ── Negocios conocidos ────────────────────────────────────────────────────

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
    "farmacity": "farmacity",
    "la anonima": "la anonima",
    "anonima": "la anonima",
    "walmart": "walmart",
    "netflix": "netflix",
    "spotify": "spotify",
    "musimundo": "musimundo",
    "disco": "disco",
}

# ── Detección multi-día (frases completas, orden importa) ────────────────

# Lista ordenada: frases más específicas primero para evitar match parcial
_WEEKDAYS = ["lunes", "martes", "miercoles", "jueves", "viernes"]
_ALL_DAYS = _WEEKDAYS + ["sabado", "domingo"]

_MULTI_DAY_PHRASES: list[tuple[str, list[str]]] = [
    ("lunes a viernes", _WEEKDAYS),
    ("entre semana",    _WEEKDAYS),
    ("todos los dias",  _ALL_DAYS),
    ("fin de semana",      ["sabado", "domingo"]),
    ("finde",              ["sabado", "domingo"]),
    ("fin de semanas",     ["sabado", "domingo"]),
]

# Días simples (orden importa para evitar que "sabados" no matchee)
_SINGLE_DAYS: list[str] = [
    "lunes", "martes", "miercoles", "miercoles",
    "jueves", "viernes", "sabado", "domingo",
]


def _detect_days(text: str) -> Optional[list[str]]:
    """
    Detecta día(s) en el texto normalizado.

    Retorna lista de claves de día o None si no detecta.
    Soporta multi-día: "fin de semana" → ["sabado", "domingo"]
    """
    # Primero intentar frases multi-día (más específicas)
    for phrase, days in _MULTI_DAY_PHRASES:
        if phrase in text:
            return days

    # Luego días individuales
    for day in _SINGLE_DAYS:
        if day in text:
            return [day]

    return None


# ── Prefijos que indican respuesta de ubicación ───────────────────────────

_LOCATION_PREFIXES = (
    "soy de ", "vivo en ", "estoy en ", "desde ", "de ",
    "me encuentro en ", "mi ciudad es ", "mi zona es ",
    "mi provincia es ", "en ",
)


# ── Clasificador principal ────────────────────────────────────────────────

def fast_classify(query: str) -> Optional[Classification]:
    """
    Clasifica la consulta sin LLM.

    Retorna Classification si puede determinarlo con confianza.
    Retorna None si la consulta es ambigua → usar classify_query (LLM).
    """
    text = _normalize(query)
    tokens = set(text.split())

    # ── Ver más / paginación conversacional ───────────────────────────
    for phrase in _VER_MAS_PHRASES:
        if phrase in text:
            return Classification(intent="ver_mas")

    # ── Provincia (respuesta de ubicación pura) ───────────────────────
    # Si el mensaje es SOLO una provincia/ciudad (sin intención de beneficio),
    # retornar intent="location" directamente sin caer al LLM.
    province_result = resolve_province(query)
    if province_result:
        # Verificar que no haya señales de beneficio en el texto
        has_benefit_signal = bool(
            _BENEFIT_KEYWORDS & tokens
            or any(kw in text for kw in (
                "descuento", "promo", "beneficio", "oferta",
            ))
        )
        if not has_benefit_signal:
            pkey, _ = province_result
            return Classification(intent="location", provincia=pkey)

    # ── Segmento ──────────────────────────────────────────────────────
    segmento: Optional[str] = None
    for phrase, seg_key in _SEGMENT_KEYWORDS.items():
        if phrase in text:
            segmento = seg_key
            break

    # ── Tipo de beneficio (cuotas vs descuento) ───────────────────────
    tipo_beneficio: Optional[str] = None
    for phrase, tipo in _BENEFIT_TYPE_PHRASES:
        if phrase in text:
            tipo_beneficio = tipo
            break

    # ── Días (multi-día incluido) ─────────────────────────────────────
    dias = _detect_days(text)

    # ── Negocio ───────────────────────────────────────────────────────
    negocio: Optional[str] = None
    for keyword, nombre in _KNOWN_NEGOCIOS.items():
        if keyword in text:
            negocio = nombre
            break

    # ── Categoría ─────────────────────────────────────────────────────
    # Primero buscar frases multi-token, luego tokens individuales
    categoria: Optional[str] = None
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        multi = {kw for kw in keywords if " " in kw}
        single = keywords - multi
        if (single & tokens) or any(kw in text for kw in multi):
            categoria = cat
            break

    # ── Intent ────────────────────────────────────────────────────────
    has_signal = (
        bool(_BENEFIT_KEYWORDS & tokens)
        or categoria is not None
        or negocio is not None
        or dias is not None
        or segmento is not None
        or tipo_beneficio is not None
    )

    if not has_signal:
        return None

    # Convertir dias a string para el campo dia (compat) y lista para dias
    dia_str = dias[0] if dias and len(dias) == 1 else None

    # Provincia mencionada junto con la consulta de beneficios
    provincia_en_query: Optional[str] = None
    if province_result:
        provincia_en_query = province_result[0]

    return Classification(
        intent="benefits",
        categoria_benefits=categoria,
        dia=dia_str,
        dias=dias,
        negocio=negocio,
        segmento=segmento,
        categoria_tienda=None,
        provincia=provincia_en_query,
        tipo_beneficio=tipo_beneficio,
    )
