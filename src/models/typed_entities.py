"""
Typed Entities — Modelos Pydantic para entidades extraídas del NLP.

Cambios respecto a la versión anterior:
- `dia` (str) reemplazado por `dias` (list[str]) para soporte multi-día

  Ej: "fin de semana" → ["sabado", "domingo"]
- Nuevo campo `segmento`: inyectado desde user_profile, no del texto
- Nuevo campo `provincia`: extracción geográfica (futuro)
"""

from typing import Optional

from pydantic import BaseModel


class Entities(BaseModel):
    """
    Entidades extraídas del texto del usuario + contexto de user_profile.

    Attributes:
        ciudad:    Ciudad mencionada (ej: "corrientes", "9 de julio")
        tarjeta:   Tarjeta de crédito mencionada (ej: "visa", "mastercard")
        dias:      Días de la semana, soporta multi-día
                   (ej: ["sabado", "domingo"], ["lunes"])
        categoria: Categoría de comercio normalizada
                   (ej: "gastronomia", "supermercados", "bares")
        localidad: Localidad/provincia mencionada
        negocio:   Nombre de comercio específico (ej: "carrefour", "ypf")
        segmento:  Segmento bancario del usuario, inyectado desde user_profile
                   (ej: "black", "premium", "plan_sueldo")
        provincia: Provincia mencionada explícitamente por el usuario
    """

    ciudad:         Optional[str] = None
    tarjeta:        Optional[str] = None
    dias:           Optional[list[str]] = None
    categoria:      Optional[str] = None
    localidad:      Optional[str] = None
    negocio:        Optional[str] = None
    segmento:       Optional[str] = None
    provincia:      Optional[str] = None
    tipo_beneficio: Optional[str] = None   # "cuotas" | "descuento" | None

    class Config:
        extra = "forbid"
        validate_assignment = True
