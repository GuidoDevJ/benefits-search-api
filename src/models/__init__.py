"""
Models package - Modelos de datos del proyecto.

Contiene:
- queries_types: Mapeos de entidades a IDs de la API
- typed_entities: Modelos Pydantic para entidades
"""

from .queries_types import TRADE, STATE, CITY, DAYS_OF_THE_WEEK, CARDS
from .typed_entities import Entities

__all__ = [
    "TRADE",
    "STATE",
    "CITY",
    "DAYS_OF_THE_WEEK",
    "CARDS",
    "Entities",
]
