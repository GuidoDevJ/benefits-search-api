from src.serialization.toon_serializer import ToonSerializer

_serializer = ToonSerializer()


def get_serializer(format_name: str | None = None) -> ToonSerializer:
    """Retorna el serializer singleton (ToonSerializer)."""
    return _serializer
