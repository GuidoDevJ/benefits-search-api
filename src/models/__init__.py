"""
Models package — Modelos de datos del proyecto.

Contiene:
- queries_types: Mapeos de entidades a IDs de la API de TeVaBien
- typed_entities: Modelos Pydantic para entidades NLP
"""

from .queries_types import (
    TRADES,
    CHANNELS,
    PRODUCTS,
    SEGMENT_TO_PRODUCTS,
    DAYS_OF_THE_WEEK,
    TRADE_ALIASES,
    SEGMENT_ALIASES,
    resolve_trade_ids,
    resolve_days,
    normalize_segment,
    normalize_product_name,
)
from .typed_entities import Entities

__all__ = [
    "TRADES",
    "CHANNELS",
    "PRODUCTS",
    "SEGMENT_TO_PRODUCTS",
    "DAYS_OF_THE_WEEK",
    "TRADE_ALIASES",
    "SEGMENT_ALIASES",
    "resolve_trade_ids",
    "resolve_days",
    "normalize_segment",
    "normalize_product_name",
    "Entities",
]
