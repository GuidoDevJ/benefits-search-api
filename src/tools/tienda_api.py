"""
Tienda Comafi API Tool — Búsqueda en el catálogo scrapeado de Tienda Comafi.

Estrategia de datos:
  - Fuente primaria : Redis (caché 24h)
  - Fuente fallback : JSON más reciente en data/tienda_comafi/
  - Si ninguna disponible: lista vacía con mensaje de error

Búsqueda:
  - Tokenización de la query en palabras clave
  - Score ponderado: nombre (3x) > marca (2x) > categoría (1x)
  - Filtros opcionales: precio_max, categoria
  - Top 5 resultados para no saturar el contexto del LLM
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Optional

from langchain_core.tools import tool

try:
    from ..cache import get_cache_service
    from ..config import CACHE_ENABLED
except ImportError:
    from src.cache import get_cache_service
    from src.config import CACHE_ENABLED

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

CACHE_KEY_TIENDA   = "tienda_catalog"
CACHE_TTL_TIENDA   = 86400          # 24 horas
DATA_DIR           = Path("data/tienda_comafi")
SAMPLE_FILE        = DATA_DIR / "productos_sample.json"
MAX_RESULTS        = 5

# Pesos de scoring por campo
SCORE_WEIGHTS = {
    "name":        3,
    "brand":       2,
    "subcategory": 1,
    "category":    1,
}

# ---------------------------------------------------------------------------
# Carga del catálogo
# ---------------------------------------------------------------------------

def _find_latest_json() -> Optional[Path]:
    """Encuentra el JSON más reciente en data/tienda_comafi/."""
    if not DATA_DIR.exists():
        return None
    # Excluir el sample y buscar los generados por el scraper
    files = sorted(
        [f for f in DATA_DIR.glob("productos_2*.json")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if files:
        return files[0]
    # Fallback al sample
    return SAMPLE_FILE if SAMPLE_FILE.exists() else None


def _load_from_disk() -> list[dict]:
    path = _find_latest_json()
    if not path:
        print("[TiendaAPI] No se encontró ningún JSON de catálogo en disco.")
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        print(f"[TiendaAPI] Catálogo cargado desde disco: {path.name} ({len(data)} productos)")
        return data
    except Exception as exc:
        print(f"[TiendaAPI] Error leyendo {path}: {exc}")
        return []


async def _get_catalog_cached() -> list[dict]:
    """Obtiene el catálogo desde Redis (hit) o disco (miss) con caché 24h."""
    if not CACHE_ENABLED:
        return _load_from_disk()

    try:
        cache = await get_cache_service()
        cached = await cache.get(CACHE_KEY_TIENDA)
        if cached is not None:
            print(f"[Cache] HIT: {CACHE_KEY_TIENDA} ({len(cached)} productos)")
            return cached

        print(f"[Cache] MISS: {CACHE_KEY_TIENDA} — cargando desde disco...")
        data = _load_from_disk()
        if data:
            await cache.set(CACHE_KEY_TIENDA, data, ttl=CACHE_TTL_TIENDA)
            print(f"[Cache] SET: {CACHE_KEY_TIENDA} (TTL: 24h)")
        return data

    except Exception as exc:
        print(f"[Cache] Error: {exc}")
        return _load_from_disk()

# ---------------------------------------------------------------------------
# Motor de búsqueda
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    """Convierte texto a tokens normalizados para matching."""
    if not text:
        return set()
    text = text.lower()
    # Normalizar tildes
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")]:
        text = text.replace(a, b)
    return {t for t in re.split(r"\W+", text) if len(t) > 2}


def _score_product(product: dict, query_tokens: set[str]) -> float:
    """Calcula un score de relevancia para un producto dado una query."""
    score = 0.0
    for field, weight in SCORE_WEIGHTS.items():
        field_val = product.get(field) or ""
        field_tokens = _tokenize(str(field_val))
        matches = query_tokens & field_tokens
        score += len(matches) * weight
    return score


def _filter_products(
    catalog: list[dict],
    query_tokens: set[str],
    precio_max: Optional[float],
    categoria: Optional[str],
) -> list[dict]:
    """Aplica filtros y scoring, retorna los mejores resultados."""
    results = []

    for p in catalog:
        # Filtro de categoría exacta
        if categoria:
            cat_norm = _tokenize(categoria)
            prod_cat = _tokenize(f"{p.get('category','')} {p.get('subcategory','')}")
            if not cat_norm & prod_cat:
                continue

        # Filtro de precio máximo
        price = p.get("price")
        if precio_max and price and price > precio_max:
            continue

        # Score de relevancia
        if query_tokens:
            score = _score_product(p, query_tokens)
            if score == 0:
                continue
        else:
            score = 1.0  # Sin query → devolver todo (limitado luego)

        results.append((score, p))

    results.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in results[:MAX_RESULTS]]


def _normalize_product(p: dict) -> dict:
    """Reduce el producto a los campos relevantes para el LLM."""
    price = p.get("price")
    points = p.get("price_points")
    return {
        "nombre":    p.get("name", ""),
        "marca":     p.get("brand") or "",
        "categoria": f"{p.get('category','')} > {p.get('subcategory','')}".strip(" >"),
        "precio":    f"${price:,.0f}".replace(",", ".") if price else "Consultar",
        "puntos":    f"{points:,}".replace(",", ".") if points else None,
        "cuotas":    p.get("best_installment") or "",
        "url":       p.get("url", ""),
        "imagen":    p.get("image_url") or "",
    }

# ---------------------------------------------------------------------------
# Función async principal
# ---------------------------------------------------------------------------

async def search_tienda_async(
    query: str,
    precio_max: Optional[float] = None,
    categoria: Optional[str] = None,
) -> dict:
    """
    Busca productos en el catálogo de Tienda Comafi.

    Args:
        query      : Descripción del producto buscado (ej: "televisor samsung 55")
        precio_max : Precio máximo en ARS (opcional)
        categoria  : Filtrar por categoría (ej: "Tecnología", "Electrodomésticos")

    Returns:
        dict con clave "data" (lista de productos) y "total"
    """
    catalog = await _get_catalog_cached()
    if not catalog:
        return {"data": [], "total": 0, "error": "Catálogo no disponible. Intentá más tarde."}

    query_tokens = _tokenize(query)
    matched = _filter_products(catalog, query_tokens, precio_max, categoria)

    print(f"[TiendaAPI] Query='{query}' | Filtros: precio_max={precio_max}, cat={categoria} "
          f"| Resultados: {len(matched)}/{len(catalog)}")

    return {
        "data":  [_normalize_product(p) for p in matched],
        "total": len(matched),
    }


@tool
def search_tienda(query: str, precio_max: Optional[float] = None, categoria: Optional[str] = None) -> dict:
    """
    Busca productos en Tienda Comafi para comprar con tarjeta o puntos Comafi.

    Usar cuando el usuario quiere COMPRAR un producto (no buscar descuentos/beneficios).
    Ejemplos: "quiero comprar un televisor", "buscame una notebook", "qué auriculares tienen".

    Args:
        query      : Producto a buscar en lenguaje natural (ej: "televisor samsung 4k")
        precio_max : Presupuesto máximo en pesos ARS (opcional)
        categoria  : Categoría del producto (opcional): Tecnología, Electrodomésticos,
                     Hogar, Deporte, Belleza, Infantiles, Mascotas, Moda, Bebidas

    Returns:
        dict con lista de productos: nombre, precio ARS, puntos, cuotas, URL
    """
    return asyncio.run(search_tienda_async(query, precio_max, categoria))
