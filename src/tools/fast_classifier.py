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

# Respuestas afirmativas cortas → se interpretan como "ver más"
# Solo aplican cuando el mensaje tiene ≤ 3 tokens (evitar falsos positivos).
_VER_MAS_AFFIRMATIVES = {
    "dale", "si", "sí", "ok", "claro", "bueno", "va",
    "genial", "perfecto", "listo", "vamos", "anda", "sí!",
    "dale!", "ok!", "claro!", "bueno!", "si!", "va!",
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

_NEGOCIO_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b" + re.escape(key) + r"\b"), name)
    for key, name in _KNOWN_NEGOCIOS.items()
]

# ── Extracción heurística de candidatos de negocio ────────────────────────
# Tokens que se descartan al evaluar si un fragmento es un candidato negocio.
# Refleja stop-tokens + días + artículos típicos del español rioplatense.
_NEGOCIO_STOP_TOKENS: frozenset[str] = frozenset({
    "el", "la", "los", "las", "un", "una", "de", "del", "en",
    "y", "e", "o", "a", "al", "con", "por", "para", "mi",
    "este", "esta", "hay", "hay", "tiene", "tienen",
    "hoy", "manana", "ayer",
    "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
})

# Conjunto de todos los tokens de una sola palabra de categorías conocidas.
# Se usa para distinguir "en carrefour" (negocio) de "en gastronomia" (cat).
_ALL_CATEGORY_TOKENS: frozenset[str] = frozenset(
    token
    for keywords in _CATEGORY_KEYWORDS.values()
    for token in keywords
    if " " not in token
)

# ── Patrones de follow-up ("dame mas info de X") ─────────────────────────
# Detectan cuando el usuario pide más información sobre un comercio visto
# en la respuesta anterior, sin repetir la categoría explícitamente.

_FOLLOW_UP_RE = re.compile(
    r"(?:dame|da|dime|mostrame|quiero|necesito)\s+"
    r"(?:mas\s+)?"
    r"(?:info|informacion|detalle|detalles?|datos?)\s+"
    r"(?:de|sobre|acerca\s+de)\s+"
    r"(.{2,40}?)$"
)
_INFO_ABOUT_RE = re.compile(
    r"^(?:informacion|info)\s+(?:de|sobre)\s+(.{2,40}?)$"
)
_TELL_ME_RE = re.compile(
    r"^(?:hablame|contame|que\s+(?:es|onda)\s+(?:con\s+)?)\s+(.{2,40}?)$"
)

_FOLLOW_UP_STRIP_PREFIX = re.compile(
    r"^(?:la|el|los|las|una?|del?)\s+"
)
_FOLLOW_UP_STRIP_ROLE = re.compile(
    r"^(?:tienda|local|negocio|comercio|shop|store)\s+"
)


def _detect_follow_up_negocio(query: str) -> Optional[str]:
    """
    Detecta patrones de follow-up y extrae el nombre del negocio.

    Cubre: "dame mas info de Ver", "info de ShopGallery",
           "informacion sobre Freddo", "hablame de Kansas", etc.

    Retorna el candidato normalizado o None si no hay match o es ambiguo.
    """
    norm = _normalize(query)

    entity: Optional[str] = None
    for pattern in (_FOLLOW_UP_RE, _INFO_ABOUT_RE, _TELL_ME_RE):
        m = pattern.search(norm)
        if m:
            entity = m.group(1).strip()
            break

    if not entity:
        return None

    # Limpiar artículos y roles ("la tienda Ver" → "ver")
    entity = _FOLLOW_UP_STRIP_PREFIX.sub("", entity).strip()
    entity = _FOLLOW_UP_STRIP_ROLE.sub("", entity).strip()

    if len(entity) < 2:
        return None

    # Descartar si todos los tokens son keywords genéricos (beneficios/categorías)
    known = _ALL_CATEGORY_TOKENS | _BENEFIT_KEYWORDS | _NEGOCIO_STOP_TOKENS
    useful = set(entity.split()) - known
    if not useful:
        return None

    return entity


# Patrón: texto después de "en"/"para"/"sobre" que podría ser un negocio.
# "sobre" cubre "quiero saber mas sobre Kansas", "info sobre Freddo", etc.
# Captura hasta 4 palabras (máx. nombre comercial razonable).
# Termina en fin de texto, coma, punto, o antes de palabras de corte.
_NEGOCIO_CANDIDATE_RE = re.compile(
    r"(?:^|\s)(?:en|para|sobre)\s+"
    r"([a-zà-ɏ][a-zà-ɏ0-9]{1,25}"
    r"(?:\s+[a-zà-ɏ0-9]{2,20}){0,3}?)"
    r"(?=\s+(?:hoy|el|la|los|las|un|una|con|por|\d)|[,.]|\s*$)"
)


def _extract_negocio_candidate(text: str, tokens: set[str]) -> Optional[str]:
    """
    Extrae un candidato de negocio cuando no hay match en _KNOWN_NEGOCIOS.

    Busca texto después de 'en' / 'para' que:
      1. No sea un keyword de beneficio (_BENEFIT_KEYWORDS).
      2. No sea una categoría conocida (_ALL_CATEGORY_TOKENS).
      3. Tenga al menos 1 token no-stopword con longitud >= 3.

    El candidato NO está validado contra datos reales. El orquestador
    lo pasa a resolve_negocio() (Business Index) para confirmarlo.
    Si el índice no lo reconoce, el candidato multi-word se descarta
    para evitar 0-resultados con un substring incorrecto.

    Args:
        text:   Texto normalizado de la query.
        tokens: Set de tokens del texto normalizado.

    Returns:
        String candidato (lowercase, sin acentos) o None.
    """
    match = _NEGOCIO_CANDIDATE_RE.search(text)
    if not match:
        return None

    candidate = match.group(1).strip()
    candidate_tokens = set(candidate.split())

    # Rechazar si todos los tokens son keywords conocidos (categoría o beneficio)
    known_tokens = _ALL_CATEGORY_TOKENS | _BENEFIT_KEYWORDS | _NEGOCIO_STOP_TOKENS
    useful_tokens = candidate_tokens - known_tokens
    if not useful_tokens:
        return None

    # Al menos un token útil debe tener >= 3 caracteres (evita falsos como "es")
    if not any(len(t) >= 3 for t in useful_tokens):
        return None

    return candidate

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

# Tokens de día que terminan en 's': excluidos de la singularización
# para evitar que "lunes" → "lune" dé falsos positivos en keywords.
_DAYS_WITH_S_SUFFIX = frozenset({"lunes", "martes", "viernes"})


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


# ── Matching de categoría ────────────────────────────────────────────────

def _match_category(text: str, tokens: set[str]) -> Optional[str]:
    """
    Busca la categoría de comercio en el texto normalizado.

    Estrategia (en orden de prioridad):
      1. Nombre canónico de la categoría  → "entretenimiento" matchea
         la clave "entretenimiento" aunque no esté en el keyword set.
      2. Keywords exactas del set (token match directo).
      3. Forma singular del token plural  → "combustibles" → "combustible",
         cubre el 90 % de los plurales españoles en este dominio.
      4. Keywords multi-palabra (substring sobre el texto normalizado).

    La singularización excluye días de semana que terminan en 's'
    (lunes, martes, viernes) para evitar "lune", "marte", "vierne".
    """
    singular_tokens: set[str] = {
        t[:-1]
        if (t.endswith("s") and len(t) > 3 and t not in _DAYS_WITH_S_SUFFIX)
        else t
        for t in tokens
    }

    for cat, keywords in _CATEGORY_KEYWORDS.items():
        # 1. Nombre canónico (singular o plural simple: cat + 's')
        if cat in tokens or cat in singular_tokens:
            return cat

        multi = {kw for kw in keywords if " " in kw}
        single = keywords - multi

        # 2 + 3. Keyword exacta o singularizada
        if single & tokens or single & singular_tokens:
            return cat

        # 4. Frases multi-palabra (substring)
        if any(kw in text for kw in multi):
            return cat

    return None


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

    # Afirmativos puros (todos los tokens son afirmativos) → "ver más"
    # Ej: "dale", "ok dale", "si claro", "sii", "daleee" → ver_mas
    # "dale vuelta", "bueno pero quiero sushi" → LLM
    # Se colapsan caracteres repetidos: "sii" → "si", "daleee" → "dale"
    collapsed_tokens = {re.sub(r"(.)\1+", r"\1", t) for t in tokens}
    if collapsed_tokens and collapsed_tokens.issubset(_VER_MAS_AFFIRMATIVES):
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

    # ── Follow-up ("dame mas info de X") — tiene prioridad sobre el resto ──
    # Si el usuario pide info de un negocio específico que vio en la respuesta
    # anterior, extraerlo directo sin necesidad de la categoría explícita.
    follow_up_negocio = _detect_follow_up_negocio(query)
    if follow_up_negocio:
        # Verificar primero si el nombre coincide con un negocio conocido
        _fu_norm = _normalize(follow_up_negocio)
        for pattern, nombre in _NEGOCIO_PATTERNS:
            if pattern.search(_fu_norm):
                follow_up_negocio = nombre
                break
        return Classification(intent="benefits", negocio=follow_up_negocio)

    # ── Negocio ───────────────────────────────────────────────────────
    negocio: Optional[str] = None
    for pattern, nombre in _NEGOCIO_PATTERNS:
        if pattern.search(text):
            negocio = nombre
            break

    # Si no hay negocio conocido, intentar extracción heurística.
    # El candidato se valida en el orquestador contra el Business Index.
    # Si el índice no lo confirma, se descarta para evitar 0-resultados.
    if negocio is None:
        negocio = _extract_negocio_candidate(text, tokens)

    # ── Categoría ─────────────────────────────────────────────────────
    categoria = _match_category(text, tokens)

    # ── Intent ────────────────────────────────────────────────────────
    # Segmento solo ("soy black", "soy premium") no cuenta como señal de
    # búsqueda — requiere al menos otra entidad para disparar benefits.
    # El orquestador intercepta ese caso y pide clarificación.
    has_signal = (
        bool(_BENEFIT_KEYWORDS & tokens)
        or categoria is not None
        or negocio is not None
        or dias is not None
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
