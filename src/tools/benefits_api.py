"""
Benefits API Tool - Herramienta async para consultar beneficios de TeVaBien.

Esta herramienta realiza peticiones a la API de TeVaBien con filtros
extraídos del procesamiento NLP. Incluye caché diario con Redis (24hs).
"""

# Standard library imports
import asyncio
from typing import Any, Dict, List, Optional

# Third-party imports
import httpx
from langchain_core.tools import tool
from pydantic import BaseModel

# Local imports
try:
    from .clasify_intent import get_filter
    from .nlp_processor import Entities, nlp_pipeline
    from .normalizar import normalize_promo
    from .cloudwatch_unhandled_queries import get_cw_service
    from .push_notifications import send_push_notification
    from ..cache import get_cache_service
    from ..config import CACHE_ENABLED
except ImportError:
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from src.tools.clasify_intent import get_filter
    from src.tools.nlp_processor import Entities, nlp_pipeline
    from src.tools.normalizar import normalize_promo
    from src.tools.cloudwatch_unhandled_queries import get_cw_service
    from src.tools.push_notifications import send_push_notification
    from src.cache import get_cache_service
    from src.config import CACHE_ENABLED


class BenefitItem(BaseModel):
    i: int  # id beneficio
    t: int  # tipo
    c: List[int]  # categorias
    d: str  # descuento
    q: Optional[str]  # query / condición
    a: str  # código comercio
    b: str  # nombre comercio
    ct: str  # canal (MODO)
    cti: List[int]  # ids canal
    m: Optional[str]  # media
    r: List[int]  # regiones
    o: List[int]  # operaciones
    f: int  # fecha inicio
    e: int  # fecha fin
    pr: List[int]  # productos


class BenefitsAPIConfig(BaseModel):
    """Configuración para la API de beneficios"""
    base_url: str = "https://www.tevabien.com/json/apps/benefits.aspx"
    default_pagesize: int = 500
    default_sortcolumn: int = 2
    default_sortdesc: bool = True
    default_t: int = 44


class BenefitsResponse(BaseModel):
    """Respuesta de la API de beneficios"""
    success: bool
    data: Optional[List[BenefitItem]] = None
    error: Optional[str] = None
    url: str
    status_code: int


def build_query_params(
    pagesize: int = 500,
) -> Dict[str, Any]:
    """Construye los parámetros de query basándose en las entidades."""
    params = {"pagesize": pagesize, "allFields": ""}
    return params


# TTL para caché diario de todos los beneficios (24 horas)
CACHE_TTL_ALL_BENEFITS = 86400
CACHE_KEY_ALL_BENEFITS = "all_benefits"


async def _fetch_all_benefits_from_api(
    config: BenefitsAPIConfig,
    headers: Dict[str, str],
    timeout: int = 10,
) -> Optional[List[dict]]:
    """
    Obtiene TODOS los beneficios de la API (sin filtros).
    Esta función solo se llama una vez por día.
    """
    params = {"pagesize": 500, "allFields": ""}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                config.base_url,
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            print(f"[API] Beneficios obtenidos de la API: {len(data)}")
            return data
    except Exception as e:
        print(f"[API] Error al obtener beneficios: {e}")
        return None


async def _get_all_benefits_cached(
    config: BenefitsAPIConfig,
    headers: Dict[str, str],
    timeout: int = 10,
) -> Optional[List[dict]]:
    """
    Obtiene todos los beneficios, usando caché diario.
    Solo hace una llamada real a la API por día.
    """
    if not CACHE_ENABLED:
        return await _fetch_all_benefits_from_api(config, headers, timeout)

    try:
        cache = await get_cache_service()

        # Intentar obtener del caché
        cached_data = await cache.get(CACHE_KEY_ALL_BENEFITS)
        if cached_data is not None:
            print(f"[Cache] HIT: {CACHE_KEY_ALL_BENEFITS} (caché diario)")
            return cached_data

        # MISS - hacer llamada a la API
        print(f"[Cache] MISS: {CACHE_KEY_ALL_BENEFITS} - llamando a la API...")
        data = await _fetch_all_benefits_from_api(config, headers, timeout)

        if data:
            # Guardar en caché por 24 horas
            await cache.set(CACHE_KEY_ALL_BENEFITS, data, ttl=CACHE_TTL_ALL_BENEFITS)
            print(f"[Cache] SET: {CACHE_KEY_ALL_BENEFITS} (TTL: 24h)")

        return data

    except Exception as e:
        print(f"[Cache] Error: {e}")
        return await _fetch_all_benefits_from_api(config, headers, timeout)


def _apply_filters(data: List[dict], params: Dict[str, Any]) -> List[dict]:
    """Aplica filtros localmente a los datos."""
    filtered_data = data

    # Filtro por categoría (trade)
    trade = params.get("trade")
    if trade:
        print(f"Filtrando por categoría (trade): {trade}")
        filtered_data = [
            item for item in filtered_data if trade in item.get("r", [])
        ]

    # Filtro por nombre de negocio
    negocio = params.get("negocio")
    if negocio:
        print(f"Filtrando por negocio: {negocio}")
        negocio_lower = negocio.lower()
        filtered_data = [
            item for item in filtered_data
            if negocio_lower in item.get("b", "").lower()
        ]

    # Filtro por día
    # El campo 'a' contiene los días como string: "1234567" = todos, "5" = viernes, etc.
    day = params.get("day")
    if day:
        day_str = str(day)
        print(f"Filtrando por día: {day_str}")
        filtered_data = [
            item for item in filtered_data
            if day_str in str(item.get("a", ""))
        ]

    return filtered_data


async def fetch_benefits(
    entities: Entities,
    config: Optional[BenefitsAPIConfig] = None,
    timeout: int = 10,
    headers: Optional[Dict[str, str]] = None,
) -> BenefitsResponse:
    """
    Obtiene beneficios filtrados usando caché diario.

    Estrategia de caché:
    - Caché diario (24hs): Guarda TODOS los beneficios de la API
    - Cada query aplica filtros localmente sobre el caché diario
    - No se cachean resultados filtrados (son efímeros)

    Args:
        entities: Entidades extraídas del NLP
        config: Configuración de la API (opcional)
        timeout: Timeout en segundos (default: 10)
        headers: Headers HTTP personalizados (opcional)

    Returns:
        BenefitsResponse con el resultado de la petición
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

    filter_entities = get_filter(entities)

    try:
        # Obtener TODOS los beneficios del caché diario (o API si expiró)
        all_benefits = await _get_all_benefits_cached(config, headers, timeout)

        if all_benefits is None:
            return BenefitsResponse(
                success=False,
                error="No se pudieron obtener los beneficios",
                url=config.base_url,
                status_code=0,
            )

        # Aplicar filtros localmente (sin cachear resultados filtrados)
        filtered_data = _apply_filters(all_benefits, filter_entities)
        print(f"Beneficios filtrados: {len(filtered_data)} de {len(all_benefits)}")

        return BenefitsResponse(
            success=True,
            data=filtered_data,
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


async def search_benefits_async(query: str) -> dict:
    """
    Busca beneficios.

    Args:
        query: Consulta en lenguaje natural

    Returns:
        dict con el resultado de la búsqueda
    """
    nlp_result = await asyncio.to_thread(nlp_pipeline, query)

    entities_dict = nlp_result.entities.model_dump(exclude_none=True)
    if not entities_dict:
        print("[search_benefits] No entities detected - returning empty")

        # Guardar en S3
        try:
            s3_service = await get_cw_service()
            await s3_service.save_unhandled_query(
                query=query,
                detected_intent=nlp_result.intent,
                entities=entities_dict,
                reason="no_entities_detected",
            )
        except Exception as s3_error:
            print(f"[CW] Error guardando query sin entidades: {s3_error}")

        # Enviar push notification
        try:
            await send_push_notification(
                f"Query sin entidades detectadas: {query}"
            )
        except Exception as push_error:
            print(f"[Push] Error enviando notificación: {push_error}")

        return {
            "data": [],
            "error": "No se detectaron entidades en la consulta",
        }

    response = await fetch_benefits(nlp_result.entities)

    # Limitar a máximo 5 beneficios para reducir tokens enviados al LLM
    datas_json = []
    for benefit in (response.data or [])[:5]:
        datas_json.append(normalize_promo(benefit.model_dump()))

    result: dict = {"data": datas_json}
    if response.error:
        result["error"] = response.error
    return result


@tool
def search_benefits(query: str) -> dict:
    """
    Busca beneficios en la API de TeVaBien basándose en una consulta.

    Esta herramienta:
    1. Procesa la consulta con NLP para extraer entidades
    2. Construye los parámetros de filtro
    3. Realiza la petición a la API
    4. Retorna los beneficios encontrados

    Args:
        query: Consulta en lenguaje natural (ej: "descuentos en supermercados")

    Returns:
        dict con el resultado de la búsqueda
    """
    # Wrapper sync que ejecuta la versión async
    return asyncio.run(search_benefits_async(query))


# Demo
if __name__ == "__main__":
    async def main():
        query = "promociones en moda"
        print(f"Query: {query}\n")
        result = await search_benefits_async(query)
        print(f"Beneficios encontrados: {len(result.get('success', []))}")

    asyncio.run(main())
