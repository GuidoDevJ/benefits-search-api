"""
User Profile Tool — Identifica al usuario por número de WhatsApp.

Llama directamente al microservicio sofia-api-users.
Cachea el perfil en Redis (TTL: 30 min).

Endpoint:
    GET {SOFIA_API_URL}/users/v2/{phone}/complements

Estructura de respuesta:
    response.data.consolidated_position.cliente_datos_personales
        → tipo_cliente (segmento), domicilios[].provincia, empleado
    response.data.consolidated_position.posicion_consolidada
        → productos_cliente.productos[].id_producto.definicion_producto.{grupo,subgrupo}
    response.data.client_information
        → nombre, apellido, tipo_documento, numero_documento

Variable de entorno requerida:
    SOFIA_API_URL: URL base de sofia-api-users
                   (ej: https://sofia-users-api-prod.apps.prod-ocp.bue299.comafi.com.ar)

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
SOFIA_API_URL = os.getenv("SOFIA_API_URL", "")
REQUEST_TIMEOUT = int(os.getenv("SOFIA_API_TIMEOUT", "10"))
USER_PROFILE_CACHE_KEY_PREFIX = "comafi:user_profile:"
USER_PROFILE_CACHE_TTL = int(os.getenv("USER_PROFILE_CACHE_TTL", "1800"))
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
    provincia: Optional[str] = None  # Provincia del domicilio principal
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
        if self.provincia:
            lines.append(f"- Provincia: {self.provincia}")
        return "Información del cliente identificado:\n" + "\n".join(lines)


def _normalize_phone(phone_number: str) -> str:
    """Normaliza el número de teléfono (solo dígitos) para usar como userId en la URL."""
    return "".join(c for c in phone_number if c.isdigit())


def _parse_profile(phone_number: str, raw: dict) -> UserProfile:
    """
    Parsea la respuesta de GET /users/v2/{phone}/complements al modelo UserProfile.

    Estructura esperada:
    {
      "response": {
        "type": "success",
        "data": {
          "consolidated_position": {
            "cliente_datos_personales": {
              "nombre": "...", "apellido": "...",
              "tipo_cliente": "COMAFI PREMIUM GOLD",
              "domicilios": [{"provincia": "CORDOBA", ...}]
            },
            "posicion_consolidada": {
              "productos_cliente": {
                "productos": [
                  {"id_producto": {"definicion_producto": {"grupo": "MASTERCARD", "subgrupo": "MASTER GOLD"}}}
                ]
              }
            }
          },
          "client_information": {
            "tipo_documento": "DNI",
            "numero_documento": "12506320",
            "nombre": "FERNANDO DANIEL",
            "apellido": "ESCALANTE"
          }
        }
      }
    }
    """
    # Desempaquetar response.data
    data = {}
    if "response" in raw:
        data = (raw.get("response") or {}).get("data") or {}
    else:
        data = raw

    if not data:
        return UserProfile(phone_number=phone_number, identificado=False)

    # ── client_information: nombre, apellido, documento ──────────────────
    client_info = data.get("client_information") or {}
    nombre = (client_info.get("nombre") or "").strip().title() or None
    apellido = (client_info.get("apellido") or "").strip().title() or None
    nombre_completo = (
        f"{nombre} {apellido}".strip() if (nombre or apellido) else None
    )
    nro_doc = str(client_info.get("numero_documento") or "").strip() or None
    tipo_doc = str(client_info.get("tipo_documento") or "").strip() or None

    # ── consolidated_position ────────────────────────────────────────────
    consolidated = data.get("consolidated_position") or {}
    cliente_dp = consolidated.get("cliente_datos_personales") or {}

    # segmento: tipo_cliente (ej: "COMAFI PREMIUM GOLD")
    segmento = str(cliente_dp.get("tipo_cliente") or "").strip() or None

    # provincia: primer domicilio con campo "provincia"
    provincia = None
    domicilios = cliente_dp.get("domicilios") or []
    for dom in domicilios:
        if isinstance(dom, dict) and dom.get("provincia"):
            provincia = str(dom["provincia"]).strip().title() or None
            break

    # productos: grupo + subgrupo de cada producto de la posición consolidada
    productos: list[str] = []
    posicion = consolidated.get("posicion_consolidada") or {}
    productos_cliente = posicion.get("productos_cliente") or {}
    raw_productos = productos_cliente.get("productos") or []
    for p in raw_productos:
        if not isinstance(p, dict):
            continue
        defprod = (
            (p.get("id_producto") or {})
            .get("definicion_producto") or {}
        )
        grupo = str(defprod.get("grupo") or "").strip()
        subgrupo = str(defprod.get("subgrupo") or "").strip()
        label = f"{grupo} {subgrupo}".strip()
        if label:
            productos.append(label)

    return UserProfile(
        phone_number=phone_number,
        nombre=nombre,
        apellido=apellido,
        nombre_completo=nombre_completo,
        segmento=segmento,
        nro_documento=nro_doc,
        tipo_documento=tipo_doc,
        productos=productos,
        provincia=provincia,
        identificado=True,
    )


async def _fetch_from_sofia(phone_number: str) -> Optional[dict]:
    """
    Llama a GET {SOFIA_API_URL}/users/v2/{phone}/complements.

    Retorna el dict completo de la respuesta, o None si el usuario
    no existe (404) o hay error de conexión.
    """
    url = f"{SOFIA_API_URL.rstrip('/')}/users/v2/{phone_number}/complements"
    print(f"[UserProfile] GET {url}")
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            verify=False,
        ) as client:
            response = await client.get(
                url,
                headers={"Content-Type": "application/json"},
            )

        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            print(f"[UserProfile] Usuario no encontrado: ...{phone_number[-4:]}")
            return None
        print(
            f"[UserProfile] sofia-api-users respondió "
            f"{response.status_code} para ...{phone_number[-4:]}"
        )
        return None

    except httpx.TimeoutException:
        print(f"[UserProfile] Timeout para ...{phone_number[-4:]}")
        return None
    except httpx.ConnectError:
        print(f"[UserProfile] No se pudo conectar a {SOFIA_API_URL}")
        return None
    except Exception as exc:
        print(f"[UserProfile] Error inesperado: {exc}")
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
        mock_raw = get_mock_profile(normalized)
        # mock_raw=None → usuario no identificado (mismo que 404 en producción)
        if mock_raw is None:
            profile = UserProfile(
                phone_number=normalized,
                identificado=False,
                error="Usuario no identificado en el sistema bancario",
            )
        else:
            # Usa el mismo _parse_profile que producción
            profile = _parse_profile(normalized, mock_raw)
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
