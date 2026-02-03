"""
Query Types - Mapeos de entidades a IDs de la API.

Contiene los diccionarios de mapeo para convertir nombres legibles
a IDs que acepta la API de TeVaBien.
"""

# Mapeo de categorías/comercios a IDs (case-insensitive)
TRADE = {
    "belleza": 1238,
    "vehiculos": 1245,
    "vehículos": 1245,
    "supermercados": 1236,
    "supermercado": 1236,
    "librerias": 1242,
    "combustible": 1235,
    "moda": 1232,
    "turismo": 1240,
    "vinotecas": 1244,
    "hogar/deco": 1241,
    "promos del mes": 1334,
    "e-commerce": 1243,
    "gastronomía": 1233,
    "gastronomia": 1233,
    "salud": 1237,
    "transporte": 1357,
    "jugueterias": 1246,
    "entretenimiento": 1234,
    "entretenimientos": 1234,
}

# Mapeo de localidades/estados a IDs
STATE = {
    "corrientes": 1,
    "buenos aires": 2,
    "9 de julio": 3,
}

# Mapeo de ciudades a IDs
CITY = {
    "corrientes": 1,
    "buenos aires": 2,
    "9 de julio": 3,
}

# Mapeo de días de la semana a IDs
DAYS_OF_THE_WEEK = {
    "lunes": 1,
    "martes": 2,
    "miercoles": 3,
    "miércoles": 3,
    "jueves": 4,
    "viernes": 5,
    "sabado": 6,
    "sabados": 6,
    "sábado": 6,
    "sábados": 6,
    "domingo": 7,
    "domingos": 7,
}

# Mapeo de tarjetas a IDs
CARDS = {
    "visa": 1,
    "mastercard": 2,
    "amex": 3,
    "modo": 4,
}
