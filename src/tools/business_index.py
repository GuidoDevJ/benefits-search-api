"""
Business Name Index — Mini-RAG para resolución de nombres de comercios.

Construye un índice invertido sobre el campo `b` (nombre del comercio)
de todos los beneficios en caché diario. Permite detectar y resolver
nombres de comercios que el fast_classifier no conoce estáticamente.

Redis key : comafi:business_index
TTL       : 86400s (24h, alineado con all_benefits)

Estructura almacenada:
    {
        "token_map": {
            "mostaza":   ["MOSTAZA PALERMO SRL", "MOSTAZA BELGRANO"],
            "ypf":       ["YPF LITORAL SRL", "YPF FLORES"],
            "carrefour": ["CARREFOUR EXPRESS", "CARREFOUR HIPERMERCADO"],
            ...
        },
        "all_names": ["MOSTAZA PALERMO SRL", "YPF LITORAL SRL", ...]
    }

Algoritmo de resolución (resolve_negocio):
    1. Tokenizar el candidato, descartar stop-tokens.
    2. Para cada token útil, buscar en token_map → lista de business names.
    3. Puntuar cada business name: +1 por cada token del candidato que
       aparece en el índice de ese nombre.
    4. Retornar el token con mayor cobertura en el índice (para usarlo
       como substring en _apply_filters). Si empate, el más corto.
    5. Si ningún token hace match → None (sin validación posible).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

try:
    from ..cache import get_cache_service
except ImportError:
    from src.cache import get_cache_service


# ── Configuración ─────────────────────────────────────────────────────────

BUSINESS_INDEX_CACHE_KEY = "business_index"
BUSINESS_INDEX_TTL = 86400  # 24h — alineado con all_benefits

# Stop tokens que se descartan al tokenizar nombres de negocios.
# Incluye artículos, preposiciones, formas jurídicas y stopwords de negocios.
_STOP_TOKENS: frozenset[str] = frozenset({
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "en", "y", "e", "o", "a", "al", "con",
    "por", "para", "los", "las", "su", "sus",
    # formas jurídicas (no aportan al nombre del comercio)
    "srl", "sa", "sas", "sl", "ltda", "sociedad", "anonima",
    "limitada", "cia", "compania",
    # palabras genéricas de negocios
    "sucursal", "local", "store", "shop", "express", "center",
    "centre", "punto", "casa",
})


# ── Normalización ─────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Minúsculas, sin acentos, solo alfanuméricos y espacios."""
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> list[str]:
    """Normaliza y retorna tokens útiles (sin stop-tokens ni tokens cortos)."""
    tokens = _normalize(text).split()
    return [t for t in tokens if t not in _STOP_TOKENS and len(t) > 1]


# ── Construcción del índice ────────────────────────────────────────────────

def build_index(benefits: list[dict]) -> dict:
    """
    Construye el índice invertido desde la lista cruda de beneficios.

    Extrae el campo `b` de cada beneficio, normaliza, tokeniza y construye:
      - token_map: {token → [b_values que contienen ese token]}
      - all_names: lista de todos los nombres únicos (originales, no norm.)

    Args:
        benefits: Lista de dicts crudos de TeVaBien (campo `b` requerido).

    Returns:
        dict con "token_map" y "all_names".
    """
    token_map: dict[str, list[str]] = {}
    seen_names: set[str] = set()
    all_names: list[str] = []

    for item in benefits:
        b_raw = item.get("b", "")
        if not b_raw or not isinstance(b_raw, str):
            continue

        b_raw = b_raw.strip()
        if b_raw in seen_names:
            continue

        seen_names.add(b_raw)
        all_names.append(b_raw)

        tokens = _tokenize(b_raw)
        for token in tokens:
            if token not in token_map:
                token_map[token] = []
            # Evitar duplicados en la lista del token
            if b_raw not in token_map[token]:
                token_map[token].append(b_raw)

    print(
        f"[BusinessIndex] Índice construido: "
        f"{len(all_names)} negocios únicos, "
        f"{len(token_map)} tokens"
    )
    return {"token_map": token_map, "all_names": all_names}


# ── Persistencia en Redis ─────────────────────────────────────────────────

async def store_index(index: dict) -> bool:
    """
    Persiste el índice en Redis.

    Args:
        index: Dict con "token_map" y "all_names".

    Returns:
        True si se guardó correctamente.
    """
    try:
        cache = await get_cache_service()
        ok = await cache.set(
            BUSINESS_INDEX_CACHE_KEY,
            index,
            ttl=BUSINESS_INDEX_TTL,
        )
        if ok:
            print(
                f"[BusinessIndex] Guardado en Redis "
                f"({len(index.get('all_names', []))} negocios, TTL=24h)"
            )
        return ok
    except Exception as exc:
        print(f"[BusinessIndex] Error guardando índice: {exc}")
        return False


async def load_index() -> Optional[dict]:
    """
    Carga el índice desde Redis.

    Returns:
        Dict con "token_map" y "all_names", o None si no existe/error.
    """
    try:
        cache = await get_cache_service()
        data = await cache.get(BUSINESS_INDEX_CACHE_KEY)
        if data:
            print(
                f"[BusinessIndex] Cache HIT "
                f"({len(data.get('all_names', []))} negocios)"
            )
        return data
    except Exception as exc:
        print(f"[BusinessIndex] Error cargando índice: {exc}")
        return None


# ── Sincronización ────────────────────────────────────────────────────────

async def build_and_sync() -> int:
    """
    Entry point del endpoint POST /sync/business-index.

    Estrategia:
      1. Intentar cargar all_benefits desde Redis (cache diario).
      2. Si no está en cache, llamar a la API de TeVaBien directamente.
      3. Construir el índice invertido.
      4. Guardarlo en Redis bajo comafi:business_index.

    Returns:
        Cantidad de negocios únicos indexados.

    Raises:
        RuntimeError: Si no se pueden obtener los beneficios.
    """
    # Import lazy para evitar circular entre business_index ↔ benefits_api
    try:
        from ..cache import get_cache_service as _get_cache
        from ..tools.benefits_api import (
            _fetch_all_benefits_from_api,
            BenefitsAPIConfig,
        )
    except ImportError:
        from src.cache import get_cache_service as _get_cache
        from src.tools.benefits_api import (
            _fetch_all_benefits_from_api,
            BenefitsAPIConfig,
        )

    benefits: Optional[list[dict]] = None

    # 1. Intentar desde Redis (reutiliza el cache de all_benefits)
    try:
        cache = await _get_cache()
        benefits = await cache.get("all_benefits:global")
        if benefits:
            print(
                f"[BusinessIndex] Usando all_benefits cacheados "
                f"({len(benefits)} items)"
            )
    except Exception as exc:
        print(f"[BusinessIndex] Error leyendo all_benefits de Redis: {exc}")

    # 2. Si no hay cache, llamar a la API
    if not benefits:
        print("[BusinessIndex] Cache miss — llamando API TeVaBien...")
        config = BenefitsAPIConfig()
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
            "Accept": "application/json",
        }
        benefits = await _fetch_all_benefits_from_api(config, headers)

    if not benefits:
        raise RuntimeError(
            "No se pudieron obtener los beneficios para construir el índice."
        )

    # 3. Construir índice
    index = build_index(benefits)

    # 4. Persistir
    await store_index(index)

    return len(index.get("all_names", []))


# ── Resolución de candidatos ──────────────────────────────────────────────

async def resolve_negocio(candidate: str) -> Optional[str]:
    """
    Dado un string candidato (extraído de la query del usuario),
    retorna el token más representativo que existe en el índice.

    El token retornado se usa como filtro substring en _apply_filters:
        negocio.lower() in item["b"].lower()
    Por eso retornamos el TOKEN (no el nombre completo del negocio),
    ya que el substring match es más flexible y cubre variantes
    como "MOSTAZA PALERMO SRL" si el token es "mostaza".

    Algoritmo:
        1. Tokenizar el candidato (sin stop-tokens).
        2. Para cada token, contar cuántos b_names están indexados bajo él.
        3. Retornar el token con mayor cobertura (más b_names asociados).
           Si hay empate, preferir el más corto (más específico como substring).
        4. Si ningún token tiene match → None.

    Ejemplos:
        "mostaza palermo"    → "mostaza"   (token en el índice)
        "mc donalds"         → "mcdonalds" (si existe en token_map)
        "mosataza" (typo)    → None        (no en token_map)
        "la cantina"         → "cantina"   (si existe)
        "carrefour express"  → "carrefour" (mayor cobertura)

    Args:
        candidate: String extraído de la query (puede ser multi-word).

    Returns:
        Token string para usar como filtro substring, o None si sin match.
    """
    index = await load_index()
    if not index:
        print("[BusinessIndex] Índice no disponible — resolve sin efecto")
        return None

    token_map: dict = index.get("token_map", {})
    if not token_map:
        return None

    candidate_tokens = _tokenize(candidate)
    if not candidate_tokens:
        return None

    # Puntuar cada token candidato: cuántos b_names tiene indexados
    # (más b_names = token más presente en el catálogo = más confiable)
    token_scores: dict[str, int] = {}
    for token in candidate_tokens:
        matches = token_map.get(token, [])
        if matches:
            token_scores[token] = len(matches)

    if not token_scores:
        print(
            f"[BusinessIndex] '{candidate}' → sin match en índice "
            f"(tokens: {candidate_tokens})"
        )
        return None

    # Token con mayor cobertura; en empate, el más corto
    best_token = max(
        token_scores,
        key=lambda t: (token_scores[t], -len(t)),
    )

    print(
        f"[BusinessIndex] '{candidate}' → '{best_token}' "
        f"({token_scores[best_token]} negocios indexados)"
    )
    return best_token
