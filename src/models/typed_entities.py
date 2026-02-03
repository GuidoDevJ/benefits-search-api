"""
Typed Entities - Modelos Pydantic para entidades.

Define los modelos de datos que representan las entidades
extraídas del procesamiento NLP.
"""

from typing import Optional

from pydantic import BaseModel


class Entities(BaseModel):
    """
    Modelo de entidades extraídas del texto.

    Attributes:
        ciudad: Ciudad mencionada (ej: "corrientes", "9 de julio")
        tarjeta: Tarjeta de crédito (ej: "visa", "mastercard", "modo")
        dia: Día de la semana (ej: "lunes", "viernes")
        categoria: Categoría de comercio (ej: "Gastronomía", "Supermercados")
        localidad: Localidad/provincia (ej: "corrientes", "buenos aires")
        negocio: Nombre del negocio/local (ej: "Freddo", "McDonald's")
    """

    ciudad: Optional[str] = None
    tarjeta: Optional[str] = None
    dia: Optional[str] = None
    categoria: Optional[str] = None
    localidad: Optional[str] = None
    negocio: Optional[str] = None

    class Config:
        """Configuración de Pydantic."""

        # Permitir validación de campos extra
        extra = "forbid"
        # Usar valores por defecto
        validate_assignment = True
