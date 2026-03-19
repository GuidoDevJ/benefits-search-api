"""
ConversationMemoryService — Memoria de conversación por usuario en Redis.

Estrategia:
- Clave: comafi:memory:{phone_number}
- Estructura: lista JSON de mensajes [{role, content}, ...]
- TTL: 24 horas por defecto (configurable con MEMORY_TTL_SECONDS)
- Ventana: últimos N mensajes (configurable con MEMORY_MAX_MESSAGES)

Uso:
    memory = await get_memory_service()
    history = await memory.load_history("+5491112345678")
    await memory.save_messages("+5491112345678", [HumanMessage(...), AIMessage(...)])
"""

import json
import os
from typing import Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

try:
    from ..cache.redis_client import get_redis_client
except ImportError:
    from src.cache.redis_client import get_redis_client


# Constantes de configuración
MEMORY_KEY_PREFIX = "comafi:memory:"
DEFAULT_TTL = int(os.getenv("MEMORY_TTL_SECONDS", str(24 * 3600)))   # 24h
DEFAULT_MAX_MESSAGES = int(os.getenv("MEMORY_MAX_MESSAGES", "20"))    # ventana de 20 mensajes


def _serialize_message(msg: BaseMessage) -> dict:
    """Serializa un mensaje LangChain a dict JSON-compatible."""
    role = "human" if isinstance(msg, HumanMessage) else "ai"
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    return {"role": role, "content": content}


def _deserialize_message(data: dict) -> BaseMessage:
    """Reconstruye un mensaje LangChain desde dict."""
    role = data.get("role", "human")
    content = data.get("content", "")
    return HumanMessage(content=content) if role == "human" else AIMessage(content=content)


class ConversationMemoryService:
    """
    Servicio de memoria conversacional persistente en Redis.

    Mantiene el historial de mensajes (humano + IA) por número de teléfono.
    Descarta mensajes del sistema (SystemMessage) para no sobrecargar el contexto.
    """

    def __init__(self, ttl: int = DEFAULT_TTL, max_messages: int = DEFAULT_MAX_MESSAGES):
        self._ttl = ttl
        self._max_messages = max_messages
        self._redis = None
        self._initialized = False

    async def _ensure_connected(self) -> bool:
        """Garantiza conexión Redis. Retorna False si no disponible."""
        if not self._initialized:
            self._redis = await get_redis_client()
            self._initialized = True
        return self._redis is not None and await self._redis.is_connected()

    def _make_key(self, phone_number: str) -> str:
        """Genera la clave Redis para el historial del usuario."""
        # Normalizar número: remover espacios y caracteres especiales
        normalized = "".join(c for c in phone_number if c.isdigit() or c == "+")
        return f"{MEMORY_KEY_PREFIX}{normalized}"

    async def load_history(self, phone_number: str) -> list[BaseMessage]:
        """
        Carga el historial de conversación del usuario desde Redis.

        Args:
            phone_number: Número de WhatsApp del usuario

        Returns:
            Lista de mensajes LangChain (HumanMessage / AIMessage)
        """
        if not await self._ensure_connected():
            return []

        try:
            key = self._make_key(phone_number)
            raw = await self._redis.client.get(key)
            if not raw:
                return []

            data = json.loads(raw)
            messages = [_deserialize_message(m) for m in data]
            print(f"[Memory] Historial cargado: {len(messages)} mensajes para {phone_number[-4:]}")
            return messages

        except Exception as e:
            print(f"[Memory] Error al cargar historial: {e}")
            return []

    async def save_messages(self, phone_number: str, new_messages: list[BaseMessage]) -> bool:
        """
        Agrega nuevos mensajes al historial y recorta a la ventana máxima.

        Solo persiste HumanMessage y AIMessage (ignora SystemMessage).

        Args:
            phone_number: Número de WhatsApp del usuario
            new_messages: Mensajes nuevos a agregar

        Returns:
            True si se guardó correctamente
        """
        if not await self._ensure_connected():
            return False

        try:
            key = self._make_key(phone_number)

            # Cargar historial actual
            existing: list[dict] = []
            raw = await self._redis.client.get(key)
            if raw:
                existing = json.loads(raw)

            # Serializar solo mensajes persistibles (no SystemMessage)
            new_serialized = [
                _serialize_message(m)
                for m in new_messages
                if isinstance(m, (HumanMessage, AIMessage))
            ]

            # Combinar y recortar a ventana máxima
            combined = existing + new_serialized
            if len(combined) > self._max_messages:
                combined = combined[-self._max_messages:]

            # Persistir con TTL renovado
            await self._redis.client.setex(key, self._ttl, json.dumps(combined, ensure_ascii=False))
            print(f"[Memory] Historial guardado: {len(combined)} mensajes para {phone_number[-4:]}")
            return True

        except Exception as e:
            print(f"[Memory] Error al guardar historial: {e}")
            return False

    async def clear(self, phone_number: str) -> bool:
        """
        Limpia el historial de conversación de un usuario.

        Args:
            phone_number: Número de WhatsApp del usuario

        Returns:
            True si se eliminó correctamente
        """
        if not await self._ensure_connected():
            return False

        try:
            key = self._make_key(phone_number)
            await self._redis.client.delete(key)
            print(f"[Memory] Historial limpiado para {phone_number[-4:]}")
            return True
        except Exception as e:
            print(f"[Memory] Error al limpiar historial: {e}")
            return False

    async def get_message_count(self, phone_number: str) -> int:
        """Retorna la cantidad de mensajes en el historial."""
        if not await self._ensure_connected():
            return 0
        try:
            key = self._make_key(phone_number)
            raw = await self._redis.client.get(key)
            if not raw:
                return 0
            return len(json.loads(raw))
        except Exception:
            return 0


# Singleton global
_memory_service: Optional[ConversationMemoryService] = None


async def get_memory_service() -> ConversationMemoryService:
    """Retorna la instancia singleton del servicio de memoria."""
    global _memory_service
    if _memory_service is None:
        _memory_service = ConversationMemoryService()
    return _memory_service
