"""
NLP Processor para procesamiento de lenguaje natural en español.

Este módulo proporciona funcionalidades de clasificación de intenciones
y extracción de entidades usando spaCy.

IMPORTANTE: Este archivo se renombró de 'spacy.py' a 'nlp_processor.py'
para evitar conflictos de nombres con la librería spacy.
"""

# Standard library imports
from typing import Optional

# Third-party imports
import spacy
from pydantic import BaseModel
from spacy.matcher import PhraseMatcher

# Local imports
try:
    # Import relativo (cuando se usa como módulo)
    from .clasify_intent import get_filter
except ImportError:
    # Fallback para ejecución directa
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from src.tools.clasify_intent import get_filter

# =========================
# Load model
# =========================
nlp = spacy.load("es_core_news_sm")

# =========================
# Models
# =========================


class Entities(BaseModel):
    ciudad: Optional[str] = None
    tarjeta: Optional[str] = None
    dia: Optional[str] = None
    categoria: Optional[str] = None
    localidad: Optional[str] = None
    negocio: Optional[str] = None


class NLPResult(BaseModel):
    intent: str
    entities: Entities


# =========================
# Dictionaries
# =========================

ENTITIES = {
    "ciudad": ["9 de julio", "corrientes"],
    "categoria": [
        "belleza",
        "vehiculos",
        "vehículos",
        "supermercados",
        "supermercado",
        "librerias",
        "combustible",
        "moda",
        # "modo" removido - conflicto con tarjeta
        "turismo",
        "vinotecas",
        "hogar/deco",
        "promos del mes",
        "e-commerce",
        "gastronomía",
        "gastronomia",
        "salud",
        "transporte",
        "jugueterias",
        "entretenimiento",
        "entretenimientos",
    ],
    "tarjeta": ["visa", "mastercard", "amex", "modo"],
    "dia": [
        "lunes",
        "martes",
        "miercoles",
        "miércoles",
        "jueves",
        "viernes",
        "sabado",
        "sabados",
        "sábado",
        "sábados",
        "domingo",
        "domingos",
    ],
    "localidad": ["corrientes", "buenos aires"],
    "negocio": [
        # Gastronomía
        "freddo",
        "mcdonald's",
        "mcdonalds",
        "burger king",
        "starbucks",
        "mostaza",
        "kentucky",
        "kfc",
        "subway",
        "grido",
        # Supermercados
        "carrefour",
        "coto",
        "dia",
        "walmart",
        "jumbo",
        "disco",
        "vea",
        "changomas",
        # Combustible
        "ypf",
        "shell",
        "axion",
        "puma",
        # Farmacias
        "farmacity",
        "farmacias del dr. ahorro",
        # Cines
        "cinemark",
        "hoyts",
        "showcase",
        # Retail/Moda
        "falabella",
        "garbarino",
        "fravega",
        "musimundo",
    ],
}

INTENT_PATTERNS = {
    "consultar_beneficios": [
        "descuentos",
        "descuento",
        "beneficios",
        "beneficio",
        "promos",
        "promo",
        "promociones",
        "promoción",
        "ofertas",
        "oferta",
    ],
    "buscar_beneficios_tarjeta": ["con visa", "con mastercard"],
}


# =========================
# Matcher setup
# =========================

matcher = PhraseMatcher(nlp.vocab, attr="LOWER")

for intent, patterns in INTENT_PATTERNS.items():
    docs = [nlp(p) for p in patterns]
    matcher.add(intent, docs)


# =========================
# Entity extractor
# =========================


def extract_entities(text: str) -> Entities:
    doc = nlp(text)
    data = {}
    text_lower = text.lower()

    for ent_type, values in ENTITIES.items():
        # Normalizar valores a minúsculas
        values_lower = [v.lower() for v in values]

        # Primero intentar match exacto con tokens
        for token in doc:
            token_lower = token.text.lower()
            if token_lower in values_lower:
                # Guardar el valor original del diccionario
                idx = values_lower.index(token_lower)
                data[ent_type] = values[idx]
                break

        # Si no encontró match exacto, buscar coincidencias parciales
        # Ordenar por longitud (más largo primero) para priorizar matches específicos
        if ent_type not in data:
            # Crear lista de (índice, valor, longitud) y ordenar por longitud desc
            sorted_values = sorted(
                enumerate(values_lower), key=lambda x: len(x[1]), reverse=True
            )

            for i, value in sorted_values:
                if value in text_lower:
                    data[ent_type] = values[i]
                    break

    return Entities(**data)


# =========================
# Intent classifier
# =========================


def classify_intent(text: str) -> str:
    doc = nlp(text)
    matches = matcher(doc)

    if not matches:
        return "unknown"

    match_id, start, end = matches[0]
    return nlp.vocab.strings[match_id]


# =========================
# Pipeline
# =========================


def nlp_pipeline(text: str) -> NLPResult:
    entities = extract_entities(text)
    intent = classify_intent(text)

    return NLPResult(intent=intent, entities=entities)


# =========================
# Demo
# =========================

if __name__ == "__main__":

    q = "ofertas de belleza el viernes"

    result = nlp_pipeline(q)
    print(get_filter(result.entities))
