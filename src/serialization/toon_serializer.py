from typing import Any

import toons


class ToonSerializer:
    """Serializa datos como TOON (Token-Oriented Object Notation).

    Reduce tokens un 30-60% vs JSON para arrays de objetos uniformes.
    Ideal para listas de beneficios donde todos los items comparten
    los mismos campos (comercio, beneficio, medio_pago, dias).
    """

    @property
    def format_name(self) -> str:
        return "toon"

    def serialize(self, data: Any) -> str:
        if isinstance(data, str):
            return data
        return toons.dumps(data)

    def get_format_instruction(self) -> str:
        return "Tool data uses TOON format (CSV-like with headers). Read it as structured data."
