"""
LLM Classifier — Clasificador de intención y extracción de entidades.
Retorna:
  - intent          : "benefits" | "tienda" | "unknown"
  - categoria_benefits: categoría de TeVaBien o None
  - dia             : día de la semana en español o None
  - negocio         : nombre de comercio específico o None
  - categoria_tienda: categoría de Tienda Comafi o None
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

try:
    from ..config import AWS_REGION, BEDROCK_MODEL_ID
except ImportError:
    from src.config import AWS_REGION, BEDROCK_MODEL_ID

# Singleton — una sola instancia para toda la vida del proceso
_llm = ChatBedrock(model_id=BEDROCK_MODEL_ID, region_name=AWS_REGION)


# ---------------------------------------------------------------------------
# Categorías válidas (exactamente las que usan las tools)
# ---------------------------------------------------------------------------

_BENEFITS_CATEGORIES = (
    "belleza, vehiculos, supermercados, librerias, combustible, moda, "
    "turismo, vinotecas, hogar/deco, promos del mes, e-commerce, "
    "gastronomia, salud, transporte, jugueterias, entretenimiento"
)

_TIENDA_CATEGORIES = (
    "Tecnología, Electrodomésticos, Hogar, Deporte, "
    "Belleza, Infantiles, Mascotas, Moda, Bebidas"
)

_SYSTEM_PROMPT = f"""Sos un clasificador de consultas para el asistente del Banco Comafi.

Devolvé ÚNICAMENTE un JSON con este formato exacto (sin texto adicional):
{{
  "intent": "benefits" | "tienda" | "unknown",
  "categoria_benefits": <string o null>,
  "dia": <string o null>,
  "negocio": <string o null>,
  "categoria_tienda": <string o null>
}}

INTENT:
- "benefits": el usuario pregunta por descuentos, promos, beneficios,
  cuotas sin interés, reintegros
- "tienda"  : el usuario quiere COMPRAR algo (producto, precio, modelo)
- "unknown" : saludos, preguntas sin sentido, temas irrelevantes al banco

CATEGORIA_BENEFITS (solo si intent=benefits, si aplica):
Una de estas exactas: {_BENEFITS_CATEGORIES}
Si el texto menciona "resto", "comida", "comer" → gastronomia
Si menciona "super", "chango", "mercado" → supermercados
Si no es claro → null

DIA (solo si intent=benefits y se menciona un día):
lunes, martes, miercoles, jueves, viernes, sabado, domingo

NEGOCIO (solo si intent=benefits y se menciona un comercio específico):
Nombre del local (ej: mcdonalds, carrefour, ypf, starbucks, cinemark)

CATEGORIA_TIENDA (solo si intent=tienda, si aplica):
Una de estas exactas: {_TIENDA_CATEGORIES}
Si no está claro → null

Tolerá errores de tipeo y jerga argentina:
"kiero"→quiero, "cuota"→cuotas, "mc"→mcdonalds, "supermer"→supermercados,
"celu"→celular→Tecnología, "tele"→televisor→Tecnología,
"ropa"→Moda, "zapatillas"→Moda, "heladera"→Electrodomésticos

Inputs con emojis o formato estructurado (ej: "6c | 💳 | 📅 todos los días"):
Interpretá "Xc" o "X cuotas" como cuotas sin interés → intent=benefits.
💳 indica medio de pago con tarjeta, 📅 indica días de validez.
Estos inputs son consultas sobre beneficios aunque no tengan forma de pregunta.

Respondé SOLO el JSON, sin explicaciones ni markdown."""


# ---------------------------------------------------------------------------
# Modelo de salida
# ---------------------------------------------------------------------------

class Classification(BaseModel):
    intent: Literal["benefits", "tienda", "unknown"]
    categoria_benefits: Optional[str] = None
    dia: Optional[str] = None
    negocio: Optional[str] = None
    categoria_tienda: Optional[str] = None


# ---------------------------------------------------------------------------
# Clasificador
# ---------------------------------------------------------------------------

async def classify_query(query: str) -> Classification:
    """
    Clasifica la consulta del usuario y extrae las entidades necesarias
    para las tools de benefits y tienda.

    Args:
        query: Texto del usuario.

    Returns:
        Classification con intent y entidades relevantes.
    """
    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=f"Consulta: {query}"),
    ]

    try:
        response = await _llm.ainvoke(messages)
        content = response.content.strip()
        # Limpiar posible markdown ```json ... ```
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        data = json.loads(content)
        return Classification(**data)
    except Exception as exc:
        print(f"[LLMClassifier] Error parseando respuesta: {exc}")
        return Classification(intent="unknown")
