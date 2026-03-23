"""
User Profile Tool — Identifica al usuario por número de WhatsApp.

Llama al microservicio sofia-api-users para obtener el perfil del cliente
(nombre, segmento, productos, documentación) y lo cachea en Redis (TTL: 30 min).

Variables de entorno requeridas:
    SOFIA_API_URL: URL base del microservicio (ej: http://sofia-api-users:3000)
    SOFIA_API_TOKEN: Bearer token para autenticación (opcional)

Clave Redis: comafi:user_profile:{phone_number}
TTL: 1800 segundos (30 minutos)
"""

import json
import os
from typing import Optional

import httpx
from pydantic import BaseModel

try:
    from ..cache.redis_client import get_redis_client
    from .user_profile_mocks import get_mock_profile
except ImportError:
    from src.cache.redis_client import get_redis_client
    from src.tools.user_profile_mocks import get_mock_profile


# ── Configuración ─────────────────────────────────────────────────────────
SOFIA_API_URL = os.getenv("SOFIA_API_URL")
SOFIA_API_TOKEN = os.getenv("SOFIA_API_TOKEN", "")
USER_PROFILE_CACHE_KEY_PREFIX = "comafi:user_profile:"
USER_PROFILE_CACHE_TTL = int(os.getenv("USER_PROFILE_CACHE_TTL", "1800"))
REQUEST_TIMEOUT = int(os.getenv("SOFIA_API_TIMEOUT", "5"))
MOCK_ENABLED = os.getenv("MOCK_USER_PROFILE", "false").lower() == "true"


class UserProfile(BaseModel):
    """Perfil del cliente identificado por WhatsApp."""

    phone_number: str
    nombre: Optional[str] = None
    apellido: Optional[str] = None
    nombre_completo: Optional[str] = None
    segmento: Optional[str] = None  # MASIVO, PREMIUM, PYME, etc.
    nro_documento: Optional[str] = None
    tipo_documento: Optional[str] = None  # DNI, CUIL, CUIT
    productos: list[str] = []  # Tarjetas / productos activos
    identificado: bool = False  # True si se encontró en el banco
    error: Optional[str] = None  # Mensaje de error si falla

    @property
    def saludo(self) -> str:
        """Genera un saludo personalizado."""
        if self.nombre:
            return f"{self.nombre.title()}"
        return "Cliente"

    @property
    def contexto_agente(self) -> str:
        """Genera el bloque de contexto para inyectar en el system prompt del agente."""
        if not self.identificado:
            return ""
        lines = [f"- Nombre: {self.nombre_completo or self.nombre}"]
        if self.segmento:
            lines.append(f"- Segmento: {self.segmento}")
        if self.productos:
            lines.append(f"- Productos: {', '.join(self.productos)}")
        return "Información del cliente identificado:\n" + "\n".join(lines)


def _normalize_phone(phone_number: str) -> str:
    """Normaliza el número de teléfono para usar como userId."""
    return "".join(c for c in phone_number if c.isdigit() or c == "+")


def _parse_profile(phone_number: str, data: dict) -> UserProfile:
    """
    Parsea la respuesta de sofia-api-users al modelo UserProfile.

    La API retorna distintas estructuras según el endpoint:
    - /users/:userId      → { cliente: { nombre, apellido, segmento, ... } }
    - /users/.../complements → estructura similar con pos-cons
    """
    cliente = data.get("cliente") or data.get("data") or data

    if not cliente:
        return UserProfile(phone_number=phone_number, identificado=False)

    nombre = (cliente.get("nombre") or "").strip().title() or None
    apellido = (cliente.get("apellido") or "").strip().title() or None
    nombre_completo = f"{nombre} {apellido}".strip() if (nombre or apellido) else None

    # Extraer productos (tarjetas / cuentas)
    productos: list[str] = []
    raw_products = cliente.get("productos") or cliente.get("tarjetas") or []
    if isinstance(raw_products, list):
        for p in raw_products:
            if isinstance(p, dict):
                label = p.get("descripcion") or p.get("nombre") or p.get("tipo")
                if label:
                    productos.append(str(label))
            elif isinstance(p, str):
                productos.append(p)

    return UserProfile(
        phone_number=phone_number,
        nombre=nombre,
        apellido=apellido,
        nombre_completo=nombre_completo,
        segmento=cliente.get("segmento") or cliente.get("segment"),
        nro_documento=str(
            cliente.get("nroDocumento") or cliente.get("nro_documento") or ""
        ),
        tipo_documento=cliente.get("tipoDocumento") or cliente.get("tipo_documento"),
        productos=productos,
        identificado=True,
    )


async def _fetch_from_sofia(phone_number: str) -> Optional[dict]:
    """
    Llama a sofia-api-users GET /users/:userId para identificar al usuario.

    Retorna el dict de respuesta o None si falla.
    """
    url = f"{SOFIA_API_URL.rstrip('/')}/users/{phone_number}"
    headers: dict = {"Content-Type": "application/json"}
    if SOFIA_API_TOKEN:
        headers["Authorization"] = f"Bearer {SOFIA_API_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                print(
                    f"[UserProfile] Usuario no encontrado en sofia: {phone_number[-4:]}"
                )
                return None
            else:
                print(
                    f"[UserProfile] sofia-api-users respondió {response.status_code} "
                    f"para {phone_number[-4:]}"
                )
                return None
    except httpx.TimeoutException:
        print(
            f"[UserProfile] Timeout al llamar sofia-api-users para {phone_number[-4:]}"
        )
        return None
    except httpx.ConnectError:
        print(f"[UserProfile] No se pudo conectar a sofia-api-users ({SOFIA_API_URL})")
        return None
    except Exception as e:
        print(f"[UserProfile] Error inesperado: {e}")
        return None


async def fetch_user_profile(phone_number: str) -> UserProfile:
    """
    Obtiene el perfil del usuario por número de WhatsApp.

    Estrategia (producción):
    1. Busca en Redis (TTL 30 min)
    2. Si no hay caché, llama a sofia-api-users
    3. Guarda resultado en Redis
    4. Siempre retorna un UserProfile (identificado=False si no encontrado)

    Estrategia (mock activo — MOCK_USER_PROFILE=true):
    - Devuelve directamente desde el dict de perfiles de prueba.
    - Saltea Redis y la llamada HTTP.

    Args:
        phone_number: Número WhatsApp (ej: "+5491112345678")

    Returns:
        UserProfile con los datos del cliente
    """
    normalized = _normalize_phone(phone_number)

    # ── Mock: retorno inmediato sin Redis ni HTTP ─────────────────────
    if MOCK_ENABLED:
        mock_data = get_mock_profile(normalized)
        profile = UserProfile(**mock_data)
        status = "identificado" if profile.identificado else "no identificado"
        print(
            f"[UserProfile][MOCK] {status}: "
            f"{profile.nombre_completo or normalized[-4:]} "
            f"({profile.segmento or 'sin segmento'})"
        )
        return profile

    cache_key = f"{USER_PROFILE_CACHE_KEY_PREFIX}{normalized}"

    # ── 1. Intentar desde Redis ────────────────────────────────────────────────
    try:
        redis = await get_redis_client()
        if await redis.is_connected():
            raw = await redis.client.get(cache_key)
            if raw:
                cached = json.loads(raw)
                print(f"[UserProfile] Cache HIT para {normalized[-4:]}")
                return UserProfile(**cached)
    except Exception as e:
        print(f"[UserProfile] Error leyendo caché: {e}")

    # ── 2. Llamar a sofia-api-users ────────────────────────────────────────────
    print(f"[UserProfile] Cache MISS — llamando sofia-api-users para {normalized[-4:]}")
    sofia_data = await _fetch_from_sofia(normalized)

    if sofia_data is None:
        profile = UserProfile(
            phone_number=normalized,
            identificado=False,
            error="Usuario no identificado en el sistema bancario",
        )
    else:
        profile = _parse_profile(normalized, sofia_data)

    # ── 3. Guardar en Redis (aunque no se haya identificado, para evitar spam) ──
    try:
        redis = await get_redis_client()
        if await redis.is_connected():
            await redis.client.setex(
                cache_key,
                USER_PROFILE_CACHE_TTL,
                json.dumps(profile.model_dump(), ensure_ascii=False),
            )
            status = "identificado" if profile.identificado else "no identificado"
            print(f"[UserProfile] Guardado en caché ({status}) para {normalized[-4:]}")
    except Exception as e:
        print(f"[UserProfile] Error guardando caché: {e}")

    return profile


async def invalidate_user_profile_cache(phone_number: str) -> bool:
    """
    Invalida el caché del perfil de un usuario.

    Útil cuando se sabe que los datos del usuario cambiaron.
    """
    normalized = _normalize_phone(phone_number)
    cache_key = f"{USER_PROFILE_CACHE_KEY_PREFIX}{normalized}"
    try:
        redis = await get_redis_client()
        if await redis.is_connected():
            await redis.client.delete(cache_key)
            print(f"[UserProfile] Caché invalidado para {normalized[-4:]}")
            return True
    except Exception as e:
        print(f"[UserProfile] Error invalidando caché: {e}")
    return False
