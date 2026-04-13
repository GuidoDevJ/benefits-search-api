"""
UserPrefsService — Preferencias persistentes del usuario en Redis.

Almacena datos que trascienden sesiones individuales, como la ciudad/provincia
del usuario para filtrar beneficios por zona.

Clave Redis: comafi:prefs:{phone}
TTL: 30 días (renovable en cada acceso)

Estructura del dict:
    {
        "ciudad":          "corrientes",      # clave normalizada
        "ciudad_display":  "Corrientes",      # nombre para mostrar
        "location_asked":  true,              # ya se le preguntó la ubicación
    }
"""

import json
from typing import Optional

try:
    from ..cache.redis_client import get_redis_client
except ImportError:
    from src.cache.redis_client import get_redis_client


PREFS_KEY_PREFIX = "comafi:prefs:"
PREFS_TTL = 30 * 24 * 3600  # 30 días

# Fallback en memoria cuando Redis no está disponible (ej: dev local).
# Persiste mientras el proceso esté vivo; se pierde al reiniciar.
_memory_fallback: dict[str, dict] = {}


class UserPrefsService:
    """Preferencias persistentes del usuario almacenadas en Redis."""

    def __init__(self):
        self._redis = None
        self._initialized = False

    async def _ensure_connected(self) -> bool:
        if not self._initialized:
            self._redis = await get_redis_client()
            self._initialized = True
        return self._redis is not None and await self._redis.is_connected()

    def _make_key(self, phone: str) -> str:
        normalized = "".join(c for c in phone if c.isdigit() or c == "+")
        return f"{PREFS_KEY_PREFIX}{normalized}"

    async def load(self, phone: str) -> dict:
        """Carga las preferencias del usuario. Retorna {} si no existen."""
        key = self._make_key(phone)
        if not await self._ensure_connected():
            return dict(_memory_fallback.get(key, {}))
        try:
            raw = await self._redis.client.get(key)
            if not raw:
                return {}
            return json.loads(raw)
        except Exception as e:
            print(f"[Prefs] Error al cargar preferencias: {e}")
            return dict(_memory_fallback.get(key, {}))

    async def save(self, phone: str, prefs: dict) -> bool:
        """Persiste el dict completo de preferencias (sobrescribe)."""
        key = self._make_key(phone)
        if not await self._ensure_connected():
            _memory_fallback[key] = dict(prefs)
            return True
        try:
            await self._redis.client.setex(
                key, PREFS_TTL,
                json.dumps(prefs, ensure_ascii=False),
            )
            return True
        except Exception as e:
            print(f"[Prefs] Error al guardar preferencias: {e}")
            _memory_fallback[key] = dict(prefs)
            return True

    async def update(self, phone: str, **kwargs) -> bool:
        """Actualiza uno o más campos sin pisar el resto."""
        prefs = await self.load(phone)
        prefs.update(kwargs)
        return await self.save(phone, prefs)

    async def set_location(
        self, phone: str, ciudad_key: str, ciudad_display: str
    ) -> bool:
        """Persiste ciudad/provincia y marca que ya fue registrada."""
        return await self.update(
            phone,
            ciudad=ciudad_key,
            ciudad_display=ciudad_display,
            location_asked=False,  # se respondió → ya no preguntar de nuevo
        )

    async def save_search_context(
        self,
        phone: str,
        context: dict,
        gathering: bool = False,
    ) -> bool:
        """
        Persiste el contexto de búsqueda actual del usuario.

        gathering=True  → todavía acumulando filtros, no buscar aún
        gathering=False → contexto de la última búsqueda ejecutada
        """
        ctx = dict(context)
        ctx["gathering"] = gathering
        return await self.update(phone, search_context=ctx)

    async def load_search_context(self, phone: str) -> dict:
        """Carga el contexto de búsqueda guardado."""
        prefs = await self.load(phone)
        return prefs.get("search_context") or {}

    async def clear_search_context(self, phone: str) -> bool:
        """Elimina el contexto de búsqueda."""
        prefs = await self.load(phone)
        prefs.pop("search_context", None)
        return await self.save(phone, prefs)

    async def update_search_prefs(
        self,
        phone: str,
        categoria: Optional[str],
        dias: Optional[list],
    ) -> bool:
        """
        Incrementa contadores de categoría y días, y guarda recencia.

        Umbral de preferencia: >= 2 usos → se considera favorito.
        Persiste también last_categoria y last_searched_at para
        permitir continuidad entre sesiones.
        """
        if not categoria and not dias:
            return True

        from datetime import datetime, timezone
        prefs = await self.load(phone)

        if categoria:
            counts = prefs.get("cat_counts", {})
            counts[categoria] = counts.get(categoria, 0) + 1
            prefs["cat_counts"] = counts
            prefs["last_categoria"] = categoria

        if dias:
            day_counts = prefs.get("day_counts", {})
            for dia in dias:
                day_counts[dia] = day_counts.get(dia, 0) + 1
            prefs["day_counts"] = day_counts

        prefs["last_searched_at"] = (
            datetime.now(timezone.utc).isoformat()
        )
        return await self.save(phone, prefs)

    @staticmethod
    def extract_top_prefs(prefs: dict) -> dict:
        """
        Extrae categoría y días favoritos del usuario a partir de contadores.

        Umbral: ≥ 2 usos para considerar algo como preferencia estable.
        Retorna {"top_categoria": str|None, "top_dias": list|None}
        """
        cat_counts = prefs.get("cat_counts", {})
        top_cat: Optional[str] = None
        if cat_counts:
            best = max(cat_counts, key=cat_counts.get)
            if cat_counts[best] >= 2:
                top_cat = best

        day_counts = prefs.get("day_counts", {})
        top_dias = [d for d, c in day_counts.items() if c >= 2] or None

        return {"top_categoria": top_cat, "top_dias": top_dias}

    async def clear(self, phone: str) -> bool:
        """Borra todas las preferencias del usuario."""
        if not await self._ensure_connected():
            return False
        try:
            await self._redis.client.delete(self._make_key(phone))
            return True
        except Exception as e:
            print(f"[Prefs] Error al borrar preferencias: {e}")
            return False


_prefs_service: Optional[UserPrefsService] = None


async def get_prefs_service() -> UserPrefsService:
    global _prefs_service
    if _prefs_service is None:
        _prefs_service = UserPrefsService()
    return _prefs_service
