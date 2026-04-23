"""
Query Types — Mapeos de entidades a IDs de la API de TeVaBien.

Fuente de verdad: trades.md, categories.md, products.md, channels.md
Los IDs vienen directamente de los archivos .md del proyecto.

Estructura del beneficio en la API:
  r[]   → rubros/trades (IDs de categoría solamente)
  c[]   → IDs de canal/segmento exclusivo (1352=Black, 1479=PremiumPlatinum)
  o[]   → productos requeridos (Mastercard Black=191, Visa Platinum=194, etc.;
           vacío = válido para todos)
  pr[]  → marca de tarjeta (151=Visa, 152=MC; siempre presente, no filtrar)
  cti[] → canal de pago (MODO, débito, +5% Mastercard)
  a     → string de días válidos ("1234567" = todos, "56" = sáb+dom)
  b     → nombre del comercio
"""

import re
import unicodedata

# ── TRADES ───────────────────────────────────────────────────────────────────
# Clave normalizada (sin acentos, min.) -> lista de IDs en campo r[]
# Muchas categorias tienen multiples IDs en la API (duplicados historicos).
# El filtro busca: ANY(id in item["r"]) para el set de IDs de la categoría.
TRADES: dict[str, list[int]] = {
    "gastronomia":     [1212, 1233, 1301],
    "bares":           [1476],
    "moda":            [1232, 1254, 1307],
    "supermercados":   [1236, 1257, 1314],
    "belleza":         [1238, 1259, 1298, 1313],  # incluye Spa, Beauty
    "salud":           [1237, 1326, 1309],         # incluye Ópticas
    "turismo":         [1213, 1240, 1261, 1315, 1304],  # incluye Hotelería
    "vehiculos":       [1245, 1266],
    "combustible":     [1235, 1360],
    "librerias":       [1242, 1263, 1305],
    "entretenimiento": [1234, 1351, 1214],
    "hogar_deco":      [1241, 1262, 1303],
    "ecommerce":       [1243, 1264, 1300],
    "transporte":      [1357],
    "jugueterias":     [1246, 1267, 1308],         # incluye Toys & Play, Niños
    "promos_del_mes":  [1333, 1334],
    "vinotecas":       [1265, 1316],               # incluye Vinos
    "mascotas":        [1275, 1306],               # incluye Pet Shop
    "cercanos":        [1478],
    "modo":            [1472],
    "imperdibles":     [1335],
    "perfumeria":      [1311],
    "deportes":        [1276],
    "cine":            [1234, 1356],
}

# ── CHANNELS ─────────────────────────────────────────────────────────────────
# Segmento del cliente -> IDs en campo c[] que marcan beneficios exclusivos.
# Fuente: channels.md
CHANNELS: dict[str, list[int]] = {
    "black":            [1352],        # Unico Black
    "premium_platinum": [1479],        # Premium Platinum (subset de premium)
    "premium":          [1350, 1479],  # Premium + Premium Platinum
    "plan_sueldo":      [1346],
    "pyme":             [1347],
}

# ── PRODUCTS ─────────────────────────────────────────────────────────────────
# Nombre de tarjeta -> IDs en campo o[]
# Fuente: products.md
# o[] vacio en un beneficio = valido para TODAS las tarjetas.
PRODUCTS: dict[str, list[int]] = {
    "visa":                 [161],
    "visa_debito":          [164],
    "visa_gold":            [210],
    "visa_platinum":        [194],
    "visa_signature":       [195],
    "mastercard":           [162],
    "mastercard_gold":      [209],
    "mastercard_platinum":  [190],
    "mastercard_black":     [191],
}

# ── SEGMENT_TO_PRODUCTS ──────────────────────────────────────────────────────
# Product IDs por segmento (para filtrar o[]).
# Black tiene acceso a su tarjeta + todas las inferiores.
SEGMENT_TO_PRODUCTS: dict[str, list[int]] = {
    "black":         [191, 190, 195, 194, 209, 210, 161, 162, 164],
    "premium":       [190, 195, 194, 209, 210, 161, 162, 164],
    "plan_sueldo":   [161, 162, 164],
    "pyme":          [161, 162],
    "standard":      [161, 162, 164],
}

# ── CARD_CATEGORIES ──────────────────────────────────────────────────────────
# Grupos de tarjetas (pr[] brand IDs). Fuente: categories.md
CARD_CATEGORIES: dict[str, list[int]] = {
    "visa":       [151, 153, 159],  # Visa, Visa Proven, Visa Comafichicas
    "mastercard": [152],
    "varios":     [117],
}

# ── DAYS_OF_THE_WEEK ─────────────────────────────────────────────────────────
# Dia(s) -> int o list[int] (1=lunes ... 7=domingo).
# Campo "a" del beneficio: "1234567"=todos, "56"=sab+dom.
# Multi-dia: "fin de semana" -> [6, 7]
DAYS_OF_THE_WEEK: dict[str, int | list[int]] = {
    "lunes":            1,
    "martes":           2,
    "miercoles":        3,
    "miércoles":        3,
    "jueves":           4,
    "viernes":          5,
    "sabado":           6,
    "sábado":           6,
    "domingo":          7,
    # Plurales
    "lunes a viernes":    [1, 2, 3, 4, 5],
    "entre semana":       [1, 2, 3, 4, 5],
    "fin de semana":      [6, 7],
    "finde":              [6, 7],
    "fin de semanas":     [6, 7],
    "todos los dias":     [1, 2, 3, 4, 5, 6, 7],
    "todos los días":     [1, 2, 3, 4, 5, 6, 7],
}

# ── TRADE_ALIASES ────────────────────────────────────────────────────────────
# Texto libre (normalizado) -> clave en TRADES.
# Usado por fast_classify y build_filter_params.
TRADE_ALIASES: dict[str, str] = {
    # Gastronomía
    "restaurante":   "gastronomia",
    "restaurantes":  "gastronomia",
    "resto":         "gastronomia",
    "restos":        "gastronomia",
    "comida":        "gastronomia",
    "comer":         "gastronomia",
    "pizza":         "gastronomia",
    "sushi":         "gastronomia",
    "cafe":          "gastronomia",
    "cafeteria":     "gastronomia",
    "delivery":      "gastronomia",
    "parrilla":      "gastronomia",
    "heladeria":     "gastronomia",
    # Bares
    "bar":           "bares",
    "pub":           "bares",
    "cerveceria":    "bares",
    "tragos":        "bares",
    "copas":         "bares",
    "after":         "bares",
    # Supermercados
    "super":         "supermercados",
    "supermercado":  "supermercados",
    "mercado":       "supermercados",
    "chango":        "supermercados",
    # Moda
    "ropa":          "moda",
    "calzado":       "moda",
    "zapatillas":    "moda",
    "indumentaria":  "moda",
    # Belleza
    "peluqueria":    "belleza",
    "estetica":      "belleza",
    "spa":           "belleza",
    "manicura":      "belleza",
    "depilacion":    "belleza",
    "cosmetica":     "belleza",
    "maquillaje":    "belleza",
    "barberia":      "belleza",
    # Perfumería (subcat de belleza)
    "perfume":       "perfumeria",
    "perfumeria":    "perfumeria",
    # Salud
    "farmacia":      "salud",
    "farmacias":     "salud",
    "medicamento":   "salud",
    "optica":        "salud",
    "opticas":       "salud",
    "dentista":      "salud",
    "clinica":       "salud",
    "medico":        "salud",
    # Turismo
    "hotel":         "turismo",
    "vuelo":         "turismo",
    "viaje":         "turismo",
    "vacaciones":    "turismo",
    "hospedaje":     "turismo",
    "aerolinea":     "turismo",
    "crucero":       "turismo",
    # Entretenimiento
    "cine":          "cine",
    "cinema":        "cine",
    "teatro":        "entretenimiento",
    "recital":       "entretenimiento",
    "concierto":     "entretenimiento",
    "show":          "entretenimiento",
    "estadio":       "entretenimiento",
    "bowling":       "entretenimiento",
    "karting":       "entretenimiento",
    "escape room":   "entretenimiento",
    "deporte":       "deportes",
    "deportes":      "deportes",
    "gym":           "entretenimiento",
    "gimnasio":      "entretenimiento",
    # Vehículos
    "auto":          "vehiculos",
    "autos":         "vehiculos",
    "moto":          "vehiculos",
    "taller":        "vehiculos",
    "repuesto":      "vehiculos",
    "neumatico":     "vehiculos",
    "lavadero":      "vehiculos",
    # Combustible
    "nafta":         "combustible",
    "gasolina":      "combustible",
    "diesel":        "combustible",
    "surtidor":      "combustible",
    # Hogar
    "hogar":         "hogar_deco",
    "deco":          "hogar_deco",
    "decoracion":    "hogar_deco",
    "mueble":        "hogar_deco",
    "muebles":       "hogar_deco",
    "ferreteria":    "hogar_deco",
    "colchon":       "hogar_deco",
    # E-commerce
    "online":        "ecommerce",
    "tienda online": "ecommerce",
    "web":           "ecommerce",
    "internet":      "ecommerce",
    # Transporte
    "uber":          "transporte",
    "taxi":          "transporte",
    "remis":         "transporte",
    "cabify":        "transporte",
    "colectivo":     "transporte",
    "subte":         "transporte",
    # Librerías
    "libro":         "librerias",
    "libreria":      "librerias",
    "papeleria":     "librerias",
    "utiles":        "librerias",
    # Jugueterías
    "juguete":       "jugueterias",
    "juguetes":      "jugueterias",
    "toy":           "jugueterias",
    "nino":          "jugueterias",
    "bebe":          "jugueterias",
    # Mascotas
    "mascota":       "mascotas",
    "mascotas":      "mascotas",
    "perro":         "mascotas",
    "gato":          "mascotas",
    "veterinaria":   "mascotas",
    "pet":           "mascotas",
    # Vinotecas
    "vino":          "vinotecas",
    "vinos":         "vinotecas",
    "vinoteca":      "vinotecas",
    "bodega":        "vinotecas",
    "espumante":     "vinotecas",
    "champagne":     "vinotecas",
    # Promos del Mes
    "promo del mes":   "promos_del_mes",
    "promos del mes":  "promos_del_mes",
    "novedad":         "promos_del_mes",
    "imperdible":      "imperdibles",
    "imperdibles":     "imperdibles",
    # Cercanos
    "cerca":         "cercanos",
    "cercano":       "cercanos",
    "cercanos":      "cercanos",
    "zona":          "cercanos",
    "barrio":        "cercanos",
    "en mi zona":    "cercanos",
    "alrededor":     "cercanos",
}

# ── SEGMENT_ALIASES ──────────────────────────────────────────────────────────
# Normaliza "segmento" de sofia-api-users -> clave en CHANNELS.
SEGMENT_ALIASES: dict[str, str] = {
    "unico black":        "black",
    "comafi black":       "black",
    "black":              "black",
    "premium platinum":   "premium_platinum",
    "premium":            "premium",
    "plan sueldo":        "plan_sueldo",
    "sueldo":             "plan_sueldo",
    "pyme":               "pyme",
    "negocios":           "pyme",
    "negocios y pyme":    "pyme",
    "masivo":             "standard",
    "standard":           "standard",
    "classico":           "standard",
}

# ── PRODUCT_NAME_ALIASES ─────────────────────────────────────────────────────
# Normaliza nombres de producto de sofia-api-users -> clave en PRODUCTS.
PRODUCT_NAME_ALIASES: dict[str, str] = {
    "mastercard black":            "mastercard_black",
    "tarjeta mastercard black":    "mastercard_black",
    "unico black":                 "mastercard_black",
    "visa signature":              "visa_signature",
    "tarjeta visa signature":      "visa_signature",
    "visa platinum":               "visa_platinum",
    "tarjeta visa platinum":       "visa_platinum",
    "mastercard platinum":         "mastercard_platinum",
    "tarjeta mastercard platinum": "mastercard_platinum",
    "mastercard gold":             "mastercard_gold",
    "tarjeta mastercard gold":     "mastercard_gold",
    "visa gold":                   "visa_gold",
    "tarjeta visa gold":           "visa_gold",
    "visa":                        "visa",
    "tarjeta de credito visa":     "visa",
    "mastercard":                  "mastercard",
    "tarjeta de credito mastercard": "mastercard",
    "visa debito":                 "visa_debito",
    "tarjeta de debito visa":      "visa_debito",
    "debito":                      "visa_debito",
}


def resolve_trade_ids(categoria: str) -> list[int]:
    """
    Resuelve una categoría (texto libre o clave) a su lista de IDs de trade.

    Primero intenta alias, luego búsqueda directa en TRADES.
    Retorna lista vacía si no encuentra match.
    """
    key = TRADE_ALIASES.get(categoria.lower(), categoria.lower())
    return TRADES.get(key, [])


def resolve_days(dia: str) -> list[int]:
    """
    Resuelve un día o expresión multi-día a lista de números (1-7).

    Retorna lista vacía si no reconoce la expresión.
    """
    val = DAYS_OF_THE_WEEK.get(dia.lower())
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


def normalize_segment(raw: str) -> str:
    """
    Normaliza el segmento que llega de sofia-api-users a clave interna.

    Ej: "UNICO BLACK" → "black", "PREMIUM PLATINUM" → "premium_platinum"
    """
    return SEGMENT_ALIASES.get(raw.strip().lower(), "standard")


def normalize_product_name(raw: str) -> str | None:
    """
    Normaliza el nombre de un producto de sofia-api-users a clave interna.

    Retorna None si no reconoce el producto.
    """
    return PRODUCT_NAME_ALIASES.get(raw.strip().lower())


# ── PROVINCES ────────────────────────────────────────────────────────────────
# Clave normalizada (sin acentos, minúsculas) → nombre display oficial.
# 24 provincias argentinas + CABA.
PROVINCES: dict[str, str] = {
    "caba":               "Ciudad Autónoma de Buenos Aires",
    "capital federal":    "Ciudad Autónoma de Buenos Aires",
    "buenos aires":       "Buenos Aires",
    "catamarca":          "Catamarca",
    "chaco":              "Chaco",
    "chubut":             "Chubut",
    "cordoba":            "Córdoba",
    "corrientes":         "Corrientes",
    "entre rios":         "Entre Ríos",
    "formosa":            "Formosa",
    "jujuy":              "Jujuy",
    "la pampa":           "La Pampa",
    "la rioja":           "La Rioja",
    "mendoza":            "Mendoza",
    "misiones":           "Misiones",
    "neuquen":            "Neuquén",
    "rio negro":          "Río Negro",
    "salta":              "Salta",
    "san juan":           "San Juan",
    "san luis":           "San Luis",
    "santa cruz":         "Santa Cruz",
    "santa fe":           "Santa Fe",
    "santiago del estero": "Santiago del Estero",
    "tierra del fuego":   "Tierra del Fuego",
    "tucuman":            "Tucumán",
}

# Aliases: variantes, ciudades capitales y nombres coloquiales → clave.
PROVINCE_ALIASES: dict[str, str] = {
    # CABA
    "ciudad autonoma":    "caba",
    "capital":            "caba",
    "bsas capital":       "caba",
    # Buenos Aires
    "provincia de buenos aires": "buenos aires",
    "pba":                "buenos aires",
    "provincia":          "buenos aires",
    "conurbano":          "buenos aires",
    "gba":                "buenos aires",
    "la plata":           "buenos aires",
    # Córdoba
    "city of cordoba":    "cordoba",
    "cba":                "cordoba",
    # Corrientes
    "corrientes capital": "corrientes",
    # Entre Ríos
    "entre rios":         "entre rios",
    "parana":             "entre rios",
    # Jujuy
    "jujuy":              "jujuy",
    "san salvador":       "jujuy",
    # Mendoza
    "mendoza capital":    "mendoza",
    # Misiones
    "posadas":            "misiones",
    # Neuquén
    "neuquen capital":    "neuquen",
    # Salta
    "salta capital":      "salta",
    # San Juan
    "san juan capital":   "san juan",
    # Santa Fe
    "rosario":            "santa fe",
    "sfr":                "santa fe",
    # Tucumán
    "san miguel de tucuman": "tucuman",
    "tucuman capital":    "tucuman",
    # Chaco
    "resistencia":        "chaco",
    # Santiago del Estero
    "santiago":           "santiago del estero",
    # Tierra del Fuego
    "ushuaia":            "tierra del fuego",
    "rio grande":         "tierra del fuego",
    # Formosa
    "formosa capital":    "formosa",
}


_LOCATION_PREFIXES = (
    "soy de ", "vivo en ", "estoy en ", "desde ", "de ",
    "me encuentro en ", "mi ciudad es ", "mi zona es ",
    "mi provincia es ", "en ",
)


def _norm_province(s: str) -> str:
    s = s.lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def resolve_province(text: str) -> tuple[str, str] | None:
    """
    Intenta resolver un texto a una provincia argentina.

    Retorna (clave_normalizada, nombre_display) o None si no reconoce.

    Ej: "soy de córdoba" → ("cordoba", "Córdoba")
         "Corrientes"    → ("corrientes", "Corrientes")
    """
    normalized = _norm_province(text)

    clean = normalized
    for prefix in _LOCATION_PREFIXES:
        if clean.startswith(prefix):
            clean = clean[len(prefix):].strip()
            break

    key = (
        PROVINCE_ALIASES.get(clean)
        or (clean if clean in PROVINCES else None)
    )
    if key and key in PROVINCES:
        return (key, PROVINCES[key])

    for alias, k in PROVINCE_ALIASES.items():
        if alias in normalized and k in PROVINCES:
            return (k, PROVINCES[k])
    for pkey, display in PROVINCES.items():
        if pkey in normalized:
            return (pkey, display)

    return None
