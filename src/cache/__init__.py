"""
Módulo de caché para el sistema de beneficios.

Proporciona una capa de caché usando Redis para optimizar
las consultas a la API y reducir latencia.
"""

from .redis_client import get_redis_client, RedisClient
from .cache_service import CacheService, get_cache_service

__all__ = [
    "get_redis_client",
    "RedisClient",
    "CacheService",
    "get_cache_service",
]
