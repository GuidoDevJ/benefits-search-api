"""
LLM Classifier — Clasificador de intención y extracción de entidades.

Fallback cuando fast_classify no puede resolver la consulta (~15% casos).

Retorna:
  intent            : "benefits" | "tienda" | "unknown"
  categoria_benefits: clave normalizada de TRADES o None
  dia               : string de día simple (compat.) o None
  dias              : list[str] con uno o más días o None
  negocio           : nombre de comercio específico o None
  categoria_tienda  : categoría de Tienda Comafi o None
  provincia         : provincia argentina mencionada o None
  segmento          : segmento detectado o None
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


# ── Categorías y provincias válidas ──────────────────────────────────────

_BENEFITS_CATEGORIES = (
    "gastronomia, bares, moda, supermercados, belleza, salud, turismo, "
    "vehiculos, combustible, librerias, entretenimiento, cine, deportes, "
    "hogar_deco, ecommerce, transporte, jugueterias, promos_del_mes, "
    "vinotecas, mascotas, cercanos, modo, imperdibles, perfumeria"
)

_TIENDA_CATEGORIES = (
    "Tecnología, Electrodomésticos, Hogar, Deporte, "
    "Belleza, Infantiles, Mascotas, Moda, Bebidas"
)  # noqa: E501 — names fixed by external API

_PROVINCES = (
    "buenos aires, caba, catamarca, chaco, chubut, cordoba, corrientes, "
    "entre rios, formosa, jujuy, la pampa, la rioja, mendoza, misiones, "
    "neuquen, rio negro, salta, san juan, san luis, santa cruz, "
    "santa fe, santiago del estero, tierra del fuego, tucuman"
)

_SEGMENTS = "black, premium, premium_platinum, plan_sueldo, pyme"

_SYSTEM_PROMPT = f"""Sos un clasificador de consultas para el asistente \
del Banco Comafi.

Devolvé ÚNICAMENTE un JSON con este formato exacto (sin texto adicional):
{{
  "intent": "benefits" | "tienda" | "unknown",
  "categoria_benefits": <string o null>,
  "dias": <list[string] o null>,
  "negocio": <string o null>,
  "categoria_tienda": <string o null>,
  "provincia": <string o null>,
  "segmento": <string o null>,
  "tipo_beneficio": <"cuotas" | "descuento" | null>
}}

INTENT:
- "benefits" : descuentos, promos, beneficios, cuotas sin interés, reintegros
- "tienda"   : el usuario quiere COMPRAR algo (producto, precio, modelo)
- "location" : el usuario solo indica su ciudad/provincia (ej: "soy de Corrientes", "Mendoza", "vivo en Santa Fe")
- "unknown"  : saludos, preguntas sin sentido, temas irrelevantes al banco

CATEGORIA_BENEFITS (solo si intent=benefits, si aplica):
Usá exactamente una de estas claves: {_BENEFITS_CATEGORIES}
Reglas de mapeo:
- "resto", "comida", "comer", "almuerzo", "cena" → gastronomia
- "bar", "pub", "cerveceria", "tragos" → bares
- "super", "chango", "mercado" → supermercados
- "cine", "cinema", "pelicula" → cine
- "gym", "gimnasio", "deporte" → entretenimiento
- "farmacia", "optica", "medico" → salud
- "hogar", "deco", "mueble", "ferreteria" → hogar_deco
- "online", "web", "mercadolibre" → ecommerce
- Si no es claro → null

DIAS (solo si intent=benefits y se mencionan días):
Lista de strings. Valores permitidos: lunes, martes, miercoles, jueves,
viernes, sabado, domingo.
Expresiones especiales:
- "fin de semana" / "finde" → ["sabado", "domingo"]
- "lunes a viernes" / "entre semana" → ["lunes","martes","miercoles",
  "jueves","viernes"]
- "todos los días" → ["lunes","martes","miercoles","jueves","viernes",
  "sabado","domingo"]
- Un solo día → lista de un elemento: ["viernes"]
- Sin mención de día → null

NEGOCIO (solo si intent=benefits y se menciona un comercio específico):
Nombre normalizado del local (ej: mcdonalds, carrefour, ypf, starbucks)

PROVINCIA (solo si intent=benefits y se menciona una provincia/región):
Nombre normalizado (sin acentos, minúsculas) de una de estas:
{_PROVINCES}
Si no se menciona ubicación → null

SEGMENTO (solo si el usuario menciona explícitamente su segmento):
Uno de: {_SEGMENTS}
Ejemplos: "mis beneficios black" → "black", "soy premium" → "premium"
Si no se menciona → null

TIPO_BENEFICIO (solo si intent=benefits y el usuario especifica preferencia):
- "cuotas": "cuotas sin interés", "cuotas", "sin interés"
- "descuento": "descuento", "descuentos", "porcentaje", "% off"
- null si no se especifica

Tolerá errores de tipeo y jerga argentina:
"kiero"→quiero, "mc"→mcdonalds, "supermer"→supermercados,
"celu"→Tecnología, "tele"→Tecnología, "ropa"→moda,
"zapatillas"→moda, "heladera"→Electrodomésticos

Respondé SOLO el JSON, sin explicaciones ni markdown."""


# ── Modelo de salida ──────────────────────────────────────────────────────

class Classification(BaseModel):
    intent:             Literal[
        "benefits", "tienda", "location", "unknown", "ver_mas"
    ]
    categoria_benefits: Optional[str] = None
    # dia mantiene compat con fast_classify (string simple)
    dia:                Optional[str] = None
    # dias es la versión completa (lista, soporte multi-día)
    dias:               Optional[list[str]] = None
    negocio:            Optional[str] = None
    categoria_tienda:   Optional[str] = None
    provincia:          Optional[str] = None
    segmento:           Optional[str] = None
    tipo_beneficio:     Optional[str] = None   # "cuotas" | "descuento" | None

    def model_post_init(self, __context) -> None:
        """Sincroniza dia ↔ dias para compatibilidad con código existente."""
        if self.dias and not self.dia and len(self.dias) == 1:
            object.__setattr__(self, "dia", self.dias[0])
        elif self.dia and not self.dias:
            object.__setattr__(self, "dias", [self.dia])


# ── Clasificador ──────────────────────────────────────────────────────────

async def classify_query(query: str) -> Classification:
    """
    Clasifica la consulta del usuario y extrae entidades.

    Fallback del fast_classify. Consume tokens de Bedrock.

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
