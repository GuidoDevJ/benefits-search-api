"""
Benefits API Tool — Consulta y filtrado de beneficios TeVaBien.

Estrategia de caché:
  - Caché diario (24h): guarda TODOS los beneficios sin filtrar.
  - Cada request aplica filtros localmente sobre el caché.
  - Los resultados filtrados NO se cachean (son efímeros por usuario).

Pipeline de filtrado (todo Python, sin LLM):
  1. trade_ids  → campo r[]  (categoría/rubro)
  2. days       → campo a    (días válidos como string de dígitos)
  3. negocio    → campo b    (nombre del comercio, substring)
  4. product_ids→ campo pr[] (tarjetas habilitadas; pr vacío = todos)
  5. _prioritize → beneficios del segmento del usuario van primero
"""

from typing import Dict, List, Optional

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel

try:
    from .clasify_intent import build_filter_params
    from .normalizar import normalize_promo
    from ..cache import get_cache_service
    from ..config import CACHE_ENABLED
    from ..models.typed_entities import Entities
except ImportError:
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from src.tools.clasify_intent import build_filter_params
    from src.tools.normalizar import normalize_promo
    from src.cache import get_cache_service
    from src.config import CACHE_ENABLED
    from src.models.typed_entities import Entities


# ── Modelos ──────────────────────────────────────────────────────────────

class BenefitItem(BaseModel):
    i:   int              # id beneficio
    t:   int              # tipo (406=cuotas, 407=descuento, 409=ambos)
    c:   List[int]        # canal/segmento exclusivo (Black, PremPlat)
    d:   str              # descuento porcentaje
    q:   Optional[str]    # cuotas / condición
    a:   str              # días válidos ("1234567"=todos, "56"=sáb+dom)
    b:   str              # nombre del comercio
    ct:  str              # medio de pago (texto)
    cti: List[int]        # ids canal de pago
    m:   Optional[str]    # media/imagen
    r:   List[int]        # rubros: trade/category IDs solamente
    o:   List[int]        # productos requeridos (vacío = todos)
    f:   int              # fecha inicio
    e:   int              # fecha fin
    pr:  List[int]        # marca tarjeta (151=Visa, 152=MC; siempre presente)


class BenefitsAPIConfig(BaseModel):
    base_url: str = "https://www.tevabien.com/json/apps/benefits.aspx"
    default_pagesize: int = 500


class BenefitsResponse(BaseModel):
    success:    bool
    data:       Optional[List[BenefitItem]] = None
    error:      Optional[str] = None
    url:        str
    status_code: int


# ── Caché diario ─────────────────────────────────────────────────────────

CACHE_TTL_ALL_BENEFITS = 86400       # 24 horas
CACHE_KEY_ALL_BENEFITS = "all_benefits"


async def _fetch_all_benefits_from_api(
    config: BenefitsAPIConfig,
    headers: Dict[str, str],
    timeout: int = 10,
) -> Optional[List[dict]]:
    params = {"pagesize": config.default_pagesize, "allFields": ""}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                config.base_url, params=params, headers=headers
            )
            response.raise_for_status()
            data = response.json()
            print(f"[API] Beneficios obtenidos: {len(data)}")
            return data
    except Exception as e:
        print(f"[API] Error al obtener beneficios: {e}")
        return None


async def _get_all_benefits_cached(
    config: BenefitsAPIConfig,
    headers: Dict[str, str],
    timeout: int = 10,
) -> Optional[List[dict]]:
    if not CACHE_ENABLED:
        return await _fetch_all_benefits_from_api(config, headers, timeout)

    try:
        cache = await get_cache_service()
        cached_data = await cache.get(CACHE_KEY_ALL_BENEFITS)
        if cached_data is not None:
            print(f"[Cache] HIT: {CACHE_KEY_ALL_BENEFITS}")
            return cached_data

        print(f"[Cache] MISS: {CACHE_KEY_ALL_BENEFITS} — llamando API...")
        data = await _fetch_all_benefits_from_api(config, headers, timeout)

        if data:
            await cache.set(
                CACHE_KEY_ALL_BENEFITS, data, ttl=CACHE_TTL_ALL_BENEFITS
            )
            print(f"[Cache] SET: {CACHE_KEY_ALL_BENEFITS} (TTL: 24h)")

        return data
    except Exception as e:
        print(f"[Cache] Error: {e}")
        return await _fetch_all_benefits_from_api(config, headers, timeout)


# ── Filtrado (lógica pura, sin LLM) ──────────────────────────────────────────

def _apply_filters(data: List[dict], params: dict) -> List[dict]:
    """
    Aplica todos los filtros determinísticos sobre la lista completa.

    Cada filtro es inclusivo: si el parámetro no está presente,
    no se filtra por ese criterio (devuelve todos).

    Campos del beneficio usados:
      r[]  → trade IDs (categoría de rubro)
      a    → string de días "1234567"
      b    → nombre del comercio
      pr[] → product IDs requeridos (vacío = todos)
    """
    filtered = data

    # 1. Categoría: ANY(trade_id in item["r"])
    trade_ids = params.get("trade_ids", [])
    if trade_ids:
        trade_set = set(trade_ids)
        filtered = [
            item for item in filtered
            if trade_set & set(item.get("r", []))
        ]
        print(
            f"[Filter] trade_ids={trade_ids} -> {len(filtered)} beneficios"
        )

    # 2. Días: ANY(str(day) in item["a"])
    days = params.get("days", [])
    if days:
        day_strs = {str(d) for d in days}
        filtered = [
            item for item in filtered
            if any(d in str(item.get("a", "")) for d in day_strs)
        ]
        print(f"[Filter] days={days} -> {len(filtered)} beneficios")

    # 3. Negocio: substring case-insensitive en nombre del comercio
    negocio = params.get("negocio")
    if negocio:
        filtered = [
            item for item in filtered
            if negocio.lower() in item.get("b", "").lower()
        ]
        print(
            f"[Filter] negocio='{negocio}' -> {len(filtered)} beneficios"
        )

    # 4. Productos del usuario: o[] vacío (universal) o intersección
    product_ids = params.get("product_ids", [])
    if product_ids:
        pset = set(product_ids)
        filtered = [
            item for item in filtered
            if not item.get("o")               # o[] vacío = para todos
            or bool(pset & set(item.get("o", [])))
        ]
        print(
            f"[Filter] product_ids={product_ids} -> {len(filtered)} "
            "beneficios"
        )

    # 5. Tipo de beneficio: t=406 cuotas, t=407 descuento, t=409 ambos
    benefit_type = params.get("benefit_type")
    if benefit_type == "cuotas":
        before = len(filtered)
        filtered = [
            item for item in filtered
            if item.get("t") in (406, 409)
        ]
        print(
            f"[Filter] benefit_type=cuotas -> {len(filtered)} "
            f"(de {before})"
        )
    elif benefit_type == "descuento":
        before = len(filtered)
        filtered = [
            item for item in filtered
            if item.get("t") in (407, 409)
        ]
        print(
            f"[Filter] benefit_type=descuento -> {len(filtered)} "
            f"(de {before})"
        )

    return filtered


def _prioritize(data: List[dict], params: dict) -> List[dict]:
    """
    Reordena beneficios poniendo los exclusivos del segmento primero.

    No excluye beneficios generales — solo reordena para que el usuario
    vea sus beneficios exclusivos al inicio de la lista.

    Si is_exclusive_query=True (usuario pidió "mis beneficios black"),
    devuelve SOLO los del segmento sin los generales.
    """
    channel_ids = set(params.get("channel_ids", []))
    if not channel_ids:
        return data

    exclusive = [
        item for item in data
        if channel_ids & set(item.get("c", []))
    ]
    general = [
        item for item in data
        if not (channel_ids & set(item.get("c", [])))
    ]

    if params.get("is_exclusive_query") and exclusive:
        print(
            f"[Prioritize] Modo exclusivo: {len(exclusive)} beneficios "
            "del segmento"
        )
        return exclusive

    print(
        f"[Prioritize] {len(exclusive)} exclusivos + "
        f"{len(general)} generales"
    )
    return exclusive + general


# ── Función principal ────────────────────────────────────────────────────

async def fetch_benefits(
    entities: Entities,
    user_profile: Optional[dict] = None,
    config: Optional[BenefitsAPIConfig] = None,
    timeout: int = 10,
    headers: Optional[Dict[str, str]] = None,
) -> BenefitsResponse:
    """
    Obtiene beneficios filtrados y priorizados según entidades + perfil.

    Todo el filtrado es determinístico (Python puro).
    El LLM solo recibe los resultados ya procesados.

    Args:
        entities:     Entidades extraídas del NLP
        user_profile: Perfil del usuario de sofia-api-users (opcional)
        config:       Configuración de la API (opcional)
        timeout:      Timeout HTTP en segundos
        headers:      Headers HTTP personalizados (opcional)

    Returns:
        BenefitsResponse con lista filtrada y priorizada
    """
    if config is None:
        config = BenefitsAPIConfig()

    if headers is None:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "es-AR,es;q=0.9",
        }

    # Construir parámetros de filtro (toda la lógica de negocio aquí)
    filter_params = build_filter_params(entities, user_profile)
    print(f"[Benefits] filter_params={filter_params}")

    try:
        all_benefits = await _get_all_benefits_cached(
            config, headers, timeout
        )

        if all_benefits is None:
            return BenefitsResponse(
                success=False,
                error="No se pudieron obtener los beneficios",
                url=config.base_url,
                status_code=0,
            )

        # Filtrar → priorizar → limitar a top 10
        filtered = _apply_filters(all_benefits, filter_params)
        prioritized = _prioritize(filtered, filter_params)

        print(
            f"[Benefits] {len(all_benefits)} total -> "
            f"{len(filtered)} filtrados -> "
            f"{len(prioritized)} priorizados"
        )

        return BenefitsResponse(
            success=True,
            data=prioritized,
            url="(from-daily-cache)",
            status_code=200,
        )

    except Exception as e:
        return BenefitsResponse(
            success=False,
            error=f"Error inesperado: {str(e)}",
            url=config.base_url,
            status_code=0,
        )


# ── Tool LangChain ───────────────────────────────────────────────────────

@tool
async def search_benefits(
    query: str,
    categoria: Optional[str] = None,
    dia: Optional[str] = None,
    negocio: Optional[str] = None,
) -> dict:
    """
    Busca beneficios y descuentos TeVaBien con tarjeta Comafi.

    Args:
        query    : Consulta del usuario en lenguaje natural.
        categoria: Categoría del comercio. Opciones: gastronomia, bares,
                   moda, supermercados, belleza, salud, turismo, vehiculos,
                   combustible, librerias, entretenimiento, hogar_deco,
                   ecommerce, transporte, jugueterias, promos_del_mes,
                   vinotecas, mascotas, cercanos, modo, cine, deportes.
        dia      : Día(s): lunes, martes, miercoles, jueves, viernes,
                   sabado, domingo, fin de semana, lunes a viernes.
        negocio  : Nombre de un comercio específico (ej: carrefour, ypf).

    Returns:
        dict con lista de beneficios filtrados y priorizados.
    """
    # Convertir dia (str) a lista para soportar multi-día
    dias = None
    if dia:
        dias = [dia]

    entities = Entities(categoria=categoria, dias=dias, negocio=negocio)
    response = await fetch_benefits(entities, user_profile=None)

    # Serializar top 10 al LLM (reducir tokens)
    top = (response.data or [])[:5]
    datas_json = [normalize_promo(b.model_dump()) for b in top]

    result: dict = {"data": datas_json}
    if response.error:
        result["error"] = response.error
    return result


async def search_benefits_with_profile(
    query: str,
    entities: Entities,
    user_profile: Optional[dict] = None,
    offset: int = 0,
) -> dict:
    """
    Versión extendida de search_benefits con user_profile completo.

    Llamada directamente desde benefits_agent (no por el LLM).
    Aplica filtrado y priorización por segmento del usuario.

    Args:
        query:        Texto original del usuario (para logging)
        entities:     Entidades ya clasificadas
        user_profile: Perfil completo del usuario
        offset:       Posición inicial para paginación (ver_mas)

    Returns:
        dict con data[] (top 5 priorizados) y metadata de filtrado
    """
    print(
        f"[search_benefits_with_profile] query='{query}' | "
        f"categoria={entities.categoria}, dias={entities.dias}, "
        f"negocio={entities.negocio}, "
        f"segmento={entities.segmento or 'N/A'}, offset={offset}"
    )

    response = await fetch_benefits(entities, user_profile=user_profile)

    top = (response.data or [])[offset:offset + 5]
    datas_json = [normalize_promo(b.model_dump()) for b in top]

    result: dict = {
        "data": datas_json,
        "total_found": len(response.data or []),
    }
    if response.error:
        result["error"] = response.error
    return result


# ── Demo ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio

    async def main():
        query = "promociones en gastronomia"
        print(f"Query: {query}\n")
        result = await search_benefits.ainvoke({"query": query})
        print(f"Beneficios encontrados: {len(result.get('data', []))}")

    asyncio.run(main())
