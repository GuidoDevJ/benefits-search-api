# Local imports
try:
    # Import relativo (cuando se usa como módulo)
    from ..models.queries_types import TRADE, STATE, CITY, DAYS_OF_THE_WEEK
    from ..models.typed_entities import Entities
except ImportError:
    # Fallback para ejecución directa
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from src.models.queries_types import TRADE, STATE, CITY, DAYS_OF_THE_WEEK
    from src.models.typed_entities import Entities


def get_filter(entity: Entities) -> dict:
    filters = {}

    if entity.categoria:
        # Normalizar a minúsculas para búsqueda case-insensitive
        cat_id = TRADE.get(entity.categoria.lower())
        if cat_id:
            filters["trade"] = cat_id

    # NOTA: Los filtros de localidad/ciudad/state no son soportados por la API de TeVaBien
    # Se detectan las entidades pero no se envían como parámetros de filtro
    # porque la API retorna 0 resultados con estos parámetros.
    # Las entidades localidad/ciudad se mantienen para informar al usuario.

    if entity.dia:
        # Normalizar a minúsculas para búsqueda case-insensitive
        day_id = DAYS_OF_THE_WEEK.get(entity.dia.lower())
        if day_id:
            filters["day"] = day_id

    # El negocio se pasa como string (ya normalizado) para filtrado
    if entity.negocio:
        filters["negocio"] = entity.negocio

    return filters
