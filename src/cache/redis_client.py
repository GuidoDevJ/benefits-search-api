"""
Redis Client - Conexión singleton async a Redis.

Este módulo proporciona una conexión singleton asíncrona a Redis con manejo
de errores y reconexión automática.
"""

import os
from typing import Optional

import redis.asyncio as redis
from redis.exceptions import ConnectionError, TimeoutError


class RedisClient:
    """
    Cliente singleton async para conexión a Redis.

    Attributes:
        _instance: Instancia singleton del cliente
        _client: Cliente Redis asíncrono subyacente
        _initialized: Flag para controlar inicialización async
    """

    _instance: Optional["RedisClient"] = None
    _client: Optional[redis.Redis] = None
    _initialized: bool = False

    def __new__(cls) -> "RedisClient":
        """Implementa el patrón singleton."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def initialize(self) -> None:
        """Inicializa la conexión async a Redis si no existe."""
        if not self._initialized:
            await self._connect()
            self._initialized = True

    async def _connect(self) -> None:
        """Establece la conexión async a Redis usando variables de entorno."""
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD", None)
        db = int(os.getenv("REDIS_DB", "0"))

        try:
            self._client = redis.Redis(
                host=host,
                port=port,
                password=password if password else None,
                db=db,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            # Verificar conexión
            await self._client.ping()
            print(f"[Redis] Conectado a {host}:{port} DB:{db}")
        except (ConnectionError, TimeoutError) as e:
            print(f"[Redis] Error de conexión: {e}")
            self._client = None

    @property
    def client(self) -> Optional[redis.Redis]:
        """Retorna el cliente Redis async."""
        return self._client

    async def is_connected(self) -> bool:
        """Verifica si hay conexión activa a Redis."""
        if self._client is None:
            return False
        try:
            await self._client.ping()
            return True
        except (ConnectionError, TimeoutError):
            return False

    async def reconnect(self) -> bool:
        """Intenta reconectar a Redis."""
        if self._client:
            await self._client.close()
        self._client = None
        self._initialized = False
        await self._connect()
        self._initialized = True
        return await self.is_connected()

    async def health_check(self) -> dict:
        """
        Realiza un health check de la conexión.

        Returns:
            dict con estado de conexión e información
        """
        if self._client is None:
            return {"status": "disconnected", "error": "No client"}

        try:
            info = await self._client.info("server")
            return {
                "status": "connected",
                "redis_version": info.get("redis_version", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def close(self) -> None:
        """Cierra la conexión a Redis."""
        if self._client:
            await self._client.close()
            self._client = None
            self._initialized = False


# Instancia global singleton
_redis_client: Optional[RedisClient] = None


async def get_redis_client() -> RedisClient:
    """
    Obtiene la instancia singleton del cliente Redis async.

    Returns:
        Instancia de RedisClient inicializada
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
    if not _redis_client._initialized:
        await _redis_client.initialize()
    return _redis_client
