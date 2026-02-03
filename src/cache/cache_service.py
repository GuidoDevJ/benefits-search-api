"""
Cache Service - Servicio genérico de caché async con Redis.

Proporciona una API simple para cachear datos con TTL,
con fallback graceful si Redis no está disponible.
"""

import hashlib
import json
import os
from functools import wraps
from typing import Any, Callable, Optional

from .redis_client import get_redis_client


class CacheService:
    """
    Servicio de caché async genérico usando Redis.

    Proporciona métodos para get/set/delete con TTL
    y manejo de fallback si Redis no está disponible.
    """

    # TTL por defecto: 24 horas
    DEFAULT_TTL = 86400

    # Prefijo para todas las keys
    KEY_PREFIX = "comafi:"

    def __init__(self):
        """Inicializa el servicio de caché."""
        self._redis = None
        self._default_ttl = int(
            os.getenv("CACHE_TTL_DEFAULT", str(self.DEFAULT_TTL))
        )
        self._initialized = False

    async def initialize(self) -> None:
        """Inicializa la conexión async a Redis."""
        if not self._initialized:
            self._redis = await get_redis_client()
            self._initialized = True

    async def is_available(self) -> bool:
        """Verifica si el caché está disponible."""
        if not self._initialized:
            await self.initialize()
        return await self._redis.is_connected()

    def _make_key(self, key: str) -> str:
        """
        Genera una key con prefijo.

        Args:
            key: Key original

        Returns:
            Key con prefijo
        """
        return f"{self.KEY_PREFIX}{key}"

    async def get(self, key: str) -> Optional[Any]:
        """
        Obtiene un valor del caché.

        Args:
            key: Clave a buscar

        Returns:
            Valor deserializado o None si no existe
        """
        if not await self.is_available():
            return None

        try:
            full_key = self._make_key(key)
            value = await self._redis.client.get(full_key)

            if value is None:
                return None

            # Deserializar JSON
            return json.loads(value)
        except Exception as e:
            print(f"[Cache] Error en get({key}): {e}")
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None
    ) -> bool:
        """
        Guarda un valor en el caché.

        Args:
            key: Clave para almacenar
            value: Valor a almacenar (será serializado a JSON)
            ttl: Tiempo de vida en segundos (default: 24h)

        Returns:
            True si se guardó exitosamente
        """
        if not await self.is_available():
            return False

        try:
            full_key = self._make_key(key)
            ttl = ttl or self._default_ttl

            # Serializar a JSON
            serialized = json.dumps(value, ensure_ascii=False)

            await self._redis.client.setex(full_key, ttl, serialized)
            return True
        except Exception as e:
            print(f"[Cache] Error en set({key}): {e}")
            return False

    async def delete(self, key: str) -> bool:
        """
        Elimina un valor del caché.

        Args:
            key: Clave a eliminar

        Returns:
            True si se eliminó exitosamente
        """
        if not await self.is_available():
            return False

        try:
            full_key = self._make_key(key)
            await self._redis.client.delete(full_key)
            return True
        except Exception as e:
            print(f"[Cache] Error en delete({key}): {e}")
            return False

    async def exists(self, key: str) -> bool:
        """
        Verifica si una clave existe en el caché.

        Args:
            key: Clave a verificar

        Returns:
            True si existe
        """
        if not await self.is_available():
            return False

        try:
            full_key = self._make_key(key)
            return bool(await self._redis.client.exists(full_key))
        except Exception:
            return False

    async def get_ttl(self, key: str) -> int:
        """
        Obtiene el TTL restante de una clave.

        Args:
            key: Clave a verificar

        Returns:
            TTL en segundos, -1 si no tiene, -2 si no existe
        """
        if not await self.is_available():
            return -2

        try:
            full_key = self._make_key(key)
            return await self._redis.client.ttl(full_key)
        except Exception:
            return -2

    async def clear_pattern(self, pattern: str) -> int:
        """
        Elimina todas las claves que coincidan con un patrón.

        Args:
            pattern: Patrón de búsqueda (ej: "benefits:*")

        Returns:
            Número de claves eliminadas
        """
        if not await self.is_available():
            return 0

        try:
            full_pattern = self._make_key(pattern)
            keys = await self._redis.client.keys(full_pattern)
            if keys:
                return await self._redis.client.delete(*keys)
            return 0
        except Exception as e:
            print(f"[Cache] Error en clear_pattern({pattern}): {e}")
            return 0

    @staticmethod
    def generate_key(*args, **kwargs) -> str:
        """
        Genera una clave de caché basada en argumentos.

        Útil para generar claves únicas basadas en parámetros de función.

        Args:
            *args: Argumentos posicionales
            **kwargs: Argumentos nombrados

        Returns:
            Hash MD5 de los argumentos
        """
        # Crear representación string de los argumentos
        key_parts = [str(arg) for arg in args]
        key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
        key_string = ":".join(key_parts)

        # Generar hash
        return hashlib.md5(key_string.encode()).hexdigest()


# Instancia global singleton
_cache_service: Optional[CacheService] = None


async def get_cache_service() -> CacheService:
    """
    Obtiene la instancia singleton del servicio de caché async.

    Returns:
        Instancia de CacheService inicializada
    """
    global _cache_service
    if _cache_service is None:
        _cache_service = CacheService()
    if not _cache_service._initialized:
        await _cache_service.initialize()
    return _cache_service


def cached(
    ttl: Optional[int] = None,
    key_prefix: str = ""
) -> Callable:
    """
    Decorador para cachear resultados de funciones async.

    Args:
        ttl: Tiempo de vida en segundos
        key_prefix: Prefijo adicional para la clave

    Returns:
        Función decorada con caché

    Example:
        @cached(ttl=3600, key_prefix="user")
        async def get_user(user_id: int):
            return await fetch_user_from_db(user_id)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            cache = await get_cache_service()

            # Generar clave única
            func_name = func.__name__
            args_hash = CacheService.generate_key(*args, **kwargs)
            cache_key = f"{key_prefix}:{func_name}:{args_hash}"

            # Intentar obtener del caché
            cached_value = await cache.get(cache_key)
            if cached_value is not None:
                print(f"[Cache] HIT: {cache_key}")
                return cached_value

            # Ejecutar función y cachear resultado
            print(f"[Cache] MISS: {cache_key}")
            result = await func(*args, **kwargs)

            if result is not None:
                await cache.set(cache_key, result, ttl)

            return result

        return wrapper
    return decorator
