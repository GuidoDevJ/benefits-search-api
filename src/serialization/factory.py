from src.serialization.base import LLMSerializer
from src.serialization.json_serializer import JsonSerializer
from src.serialization.toon_serializer import ToonSerializer

_SERIALIZERS: dict[str, type] = {
    "json": JsonSerializer,
    "toon": ToonSerializer,
}


def get_serializer(format_name: str | None = None) -> LLMSerializer:
    """Retorna el serializer configurado.

    Args:
        format_name: Forzar un formato específico. Si es None,
                     usa SERIALIZATION_FORMAT de config.py.

    Returns:
        Instancia de LLMSerializer (JsonSerializer o ToonSerializer).
    """
    if format_name is None:
        from src.config import SERIALIZATION_FORMAT

        format_name = SERIALIZATION_FORMAT

    serializer_cls = _SERIALIZERS.get(format_name, JsonSerializer)
    return serializer_cls()
