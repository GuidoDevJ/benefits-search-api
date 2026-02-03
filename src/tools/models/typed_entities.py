from typing import Optional

from pydantic import BaseModel


class Entities(BaseModel):
    ciudad: Optional[str] = None
    tarjeta: Optional[str] = None
    dia: Optional[str] = None
    categoria: Optional[str] = None
    localidad: Optional[str] = None


class NLPResult(BaseModel):
    intent: str
    entities: Entities
