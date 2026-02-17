from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMSerializer(Protocol):
    """Interfaz para serializar datos estructurados antes de enviarlos al LLM.

    Implementaciones concretas: JsonSerializer, ToonSerializer.
    Totalmente agnóstico al modelo — funciona con cualquier LLM.
    """

    @property
    def format_name(self) -> str:
        """Identificador del formato ('json', 'toon', etc.)."""
        ...

    def serialize(self, data: Any) -> str:
        """Convierte un dict/list de Python a string optimizado para el LLM."""
        ...

    def get_format_instruction(self) -> str:
        """Instrucción para agregar al system prompt del LLM.

        Retorna string vacío si el formato no necesita instrucción
        (ej: JSON es entendido por default por todos los modelos).
        """
        ...
