import json
from typing import Any


class JsonSerializer:
    """Serializa datos como JSON. Comportamiento por defecto del proyecto."""

    @property
    def format_name(self) -> str:
        return "json"

    def serialize(self, data: Any) -> str:
        if isinstance(data, str):
            return data
        return json.dumps(data, ensure_ascii=False)

    def get_format_instruction(self) -> str:
        return ""
