"""
Benefits Mocks — Datos de beneficios falsos para desarrollo local.

Activo cuando MOCK_BENEFITS=true en .env.
Cada state_id tiene un set de beneficios representativos de esa zona,
permitiendo probar el flujo completo:
  provincia → _resolve_state_ids → fetch paralelo → merge → filtrado → LLM

State IDs representados:
  Global (None)    → beneficios nacionales / ecommerce
  5   (BS AS GBA)  → Gran Buenos Aires
  310 (BS AS CABA) → Ciudad de Buenos Aires
  8   (Córdoba)    → Córdoba capital
  9   (Santa Fe)   → Santa Fe / Rosario
  7   (Mendoza)    → Mendoza
  10  (Tucumán)    → Tucumán
  Resto            → fallback al set global

Campos según BenefitItem:
  i=id, t=tipo(406=cuotas,407=descuento,409=ambos),
  c=canal/segmento, d=descuento%, q=cuotas, a=días("1234567"=todos),
  b=comercio, ct=medio pago, cti=ids canal pago, m=media,
  r=rubros, o=productos requeridos, f=fecha inicio, e=fecha fin,
  pr=marca tarjeta(151=Visa,152=MC)
"""

from typing import Optional

# ── Sets de beneficios por state_id ──────────────────────────────────────

# Rubros: 1=gastronomia, 2=bares, 3=supermercados, 4=moda, 5=salud,
#         6=turismo, 7=combustible, 8=entretenimiento, 9=ecommerce

_BASE_BENEFITS = [
    # Nacionales / sin zona específica
    {"i": 1001, "t": 407, "c": [],    "d": "25", "q": None, "a": "2",       "b": "Jumbo",                "ct": "MODO",       "cti": [], "m": None, "r": [3],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 1002, "t": 407, "c": [],    "d": "25", "q": None, "a": "2",       "b": "Disco",                "ct": "MODO",       "cti": [], "m": None, "r": [3],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 1003, "t": 407, "c": [],    "d": "25", "q": None, "a": "2",       "b": "Vea",                  "ct": "MODO",       "cti": [], "m": None, "r": [3],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 1004, "t": 409, "c": [],    "d": "20", "q": "3",  "a": "35",      "b": "Havanna",              "ct": "MODO",       "cti": [], "m": None, "r": [1],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 1005, "t": 407, "c": [],    "d": "25", "q": None, "a": "1234567", "b": "Freddo",               "ct": "MODO",       "cti": [], "m": None, "r": [1],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 1006, "t": 407, "c": [],    "d": "25", "q": None, "a": "1234567", "b": "CHUNGO",               "ct": "MODO",       "cti": [], "m": None, "r": [1],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 1007, "t": 409, "c": [12],  "d": "30", "q": "6",  "a": "6",       "b": "ShopGallery",          "ct": "Visa",       "cti": [], "m": None, "r": [4],    "o": [],        "f": 260101, "e": 261231, "pr": [151]},
    {"i": 1008, "t": 409, "c": [],    "d": "15", "q": "3",  "a": "6",       "b": "Macowens",             "ct": "MODO",       "cti": [], "m": None, "r": [4],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 1009, "t": 407, "c": [],    "d": "20", "q": None, "a": "1234567", "b": "YPF",                  "ct": "MODO",       "cti": [], "m": None, "r": [7],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 1010, "t": 406, "c": [],    "d": "0",  "q": "6",  "a": "1234567", "b": "Despegar",             "ct": "Mastercard", "cti": [], "m": None, "r": [6],    "o": [],        "f": 260101, "e": 261231, "pr": [152]},
]

_GBA_BENEFITS = [
    {"i": 2001, "t": 407, "c": [],    "d": "30", "q": None, "a": "1234567", "b": "Toledo Supermercados", "ct": "MODO",       "cti": [], "m": None, "r": [3],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 2002, "t": 409, "c": [14],  "d": "20", "q": "3",  "a": "34567",   "b": "Puppis",              "ct": "MODO",       "cti": [], "m": None, "r": [5],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 2003, "t": 407, "c": [],    "d": "15", "q": None, "a": "56",      "b": "Carrefour",            "ct": "MODO",       "cti": [], "m": None, "r": [3],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 2004, "t": 407, "c": [],    "d": "20", "q": None, "a": "1234567", "b": "Shell",                "ct": "MODO",       "cti": [], "m": None, "r": [7],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
]

_CABA_BENEFITS = [
    {"i": 3001, "t": 407, "c": [],    "d": "20", "q": None, "a": "1234567", "b": "La Parolaccia",        "ct": "MODO",       "cti": [], "m": None, "r": [1],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 3002, "t": 409, "c": [12],  "d": "25", "q": "3",  "a": "1234567", "b": "El Federal",           "ct": "Visa",       "cti": [], "m": None, "r": [1, 2], "o": [],        "f": 260101, "e": 261231, "pr": [151]},
    {"i": 3003, "t": 407, "c": [],    "d": "15", "q": None, "a": "56",      "b": "Coto",                 "ct": "MODO",       "cti": [], "m": None, "r": [3],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
]

_CORDOBA_BENEFITS = [
    {"i": 4001, "t": 407, "c": [],    "d": "20", "q": None, "a": "1234567", "b": "La Docta Parrilla",    "ct": "MODO",       "cti": [], "m": None, "r": [1],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 4002, "t": 409, "c": [],    "d": "15", "q": "3",  "a": "56",      "b": "Devré",                "ct": "MODO",       "cti": [], "m": None, "r": [4],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 4003, "t": 407, "c": [],    "d": "25", "q": None, "a": "1234567", "b": "Disco Córdoba",        "ct": "MODO",       "cti": [], "m": None, "r": [3],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
]

_SANTA_FE_BENEFITS = [
    {"i": 5001, "t": 407, "c": [],    "d": "20", "q": None, "a": "1234567", "b": "La Estancia Rosario",  "ct": "MODO",       "cti": [], "m": None, "r": [1],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 5002, "t": 407, "c": [],    "d": "15", "q": None, "a": "56",      "b": "Super Vea Rosario",    "ct": "MODO",       "cti": [], "m": None, "r": [3],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
]

_MENDOZA_BENEFITS = [
    {"i": 6001, "t": 407, "c": [],    "d": "20", "q": None, "a": "1234567", "b": "Vinoteca El Origen",   "ct": "MODO",       "cti": [], "m": None, "r": [1],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 6002, "t": 409, "c": [],    "d": "15", "q": "3",  "a": "1234567", "b": "Bodega Trapiche",      "ct": "Visa",       "cti": [], "m": None, "r": [6],    "o": [],        "f": 260101, "e": 261231, "pr": [151]},
]

_TUCUMAN_BENEFITS = [
    {"i": 7001, "t": 407, "c": [],    "d": "20", "q": None, "a": "1234567", "b": "El Portal del Norte",  "ct": "MODO",       "cti": [], "m": None, "r": [1],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
    {"i": 7002, "t": 407, "c": [],    "d": "15", "q": None, "a": "56",      "b": "Norte Supermercados",  "ct": "MODO",       "cti": [], "m": None, "r": [3],    "o": [],        "f": 260101, "e": 261231, "pr": [151, 152]},
]

# state_id → lista de beneficios mock de esa zona
_BENEFITS_BY_STATE: dict[Optional[int], list[dict]] = {
    None: _BASE_BENEFITS,           # global
    5:    _GBA_BENEFITS,            # BS AS - GBA principal
    310:  _CABA_BENEFITS,           # CABA
    338:  [],                       # BS AS zonas menores → vacío (se mergea con 5)
    344:  [], 346: [], 351: [], 345: [], 337: [], 352: [],
    355: [], 319: [], 353: [], 354: [],
    8:    _CORDOBA_BENEFITS,        # Córdoba capital
    342:  [], 343: [],              # Córdoba zonas menores
    9:    _SANTA_FE_BENEFITS,       # Santa Fe
    7:    _MENDOZA_BENEFITS,        # Mendoza
    10:   _TUCUMAN_BENEFITS,        # Tucumán
}


def get_mock_benefits(state_id: Optional[int]) -> list[dict]:
    """
    Retorna beneficios mock para el state_id dado.
    Si el state_id no está en el mapa, retorna el set global.
    """
    if state_id not in _BENEFITS_BY_STATE:
        return list(_BASE_BENEFITS)
    return list(_BENEFITS_BY_STATE[state_id])
