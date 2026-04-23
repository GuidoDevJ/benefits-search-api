"""
Filter Builder — Convierte entidades NLP + user_profile a parámetros de filtro.

Toda la lógica de negocio es determinística (sin LLM).
El LLM solo recibe los resultados ya filtrados.

Función principal: build_filter_params(entity, user_profile) → dict

Estructura del dict de retorno:
    trade_ids:   list[int]  IDs de categoría → filtra campo r[]
    days:        list[int]  Días válidos (1-7) → filtra campo a
    negocio:     str        Nombre de comercio → filtra campo b
    product_ids: list[int]  Tarjetas del usuario → filtra campo pr[]
    channel_ids: list[int]  Segmento del usuario → prioriza campo r[]
    is_exclusive_query: bool  True si el usuario pregunta por beneficios
                               exclusivos de su segmento
"""

from typing import Optional

try:
    from ..models.queries_types import (
        CHANNELS,
        PRODUCTS,
        SEGMENT_TO_PRODUCTS,
        normalize_product_name,
        normalize_segment,
        resolve_days,
        resolve_trade_ids,
    )
    from ..models.typed_entities import Entities
except ImportError:
    import sys
    from pathlib import Path

    _root = Path(__file__).resolve().parent.parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from src.models.queries_types import (
        CHANNELS,
        PRODUCTS,
        SEGMENT_TO_PRODUCTS,
        normalize_product_name,
        normalize_segment,
        resolve_days,
        resolve_trade_ids,
    )
    from src.models.typed_entities import Entities

# Palabras que indican que el usuario quiere ver SUS beneficios exclusivos
_EXCLUSIVE_KEYWORDS = {
    "exclusivo", "exclusivos", "exclusiva", "exclusivas",
    "mi beneficio", "mis beneficios", "para mi",
    "black", "premium", "platinum",
}


def _resolve_product_ids_from_profile(
    user_profile: dict,
) -> list[int]:
    """
    Deriva los product IDs del usuario a partir de su perfil.

    Prioridad:
    1. Si el segmento está en SEGMENT_TO_PRODUCTS → usa ese set
    2. Si tiene productos listados → mapea cada uno a IDs de PRODUCTS
    3. Fallback: productos estándar
    """
    segmento_raw = (user_profile.get("segmento") or "").strip()
    segmento_key = normalize_segment(segmento_raw)

    # Base desde el segmento
    default = [161, 162, 164]
    ids: list[int] = list(SEGMENT_TO_PRODUCTS.get(segmento_key, default))

    # Enriquecer con productos específicos del usuario si los tiene
    for prod_name in (user_profile.get("productos") or []):
        key = normalize_product_name(prod_name)
        if key and key in PRODUCTS:
            for pid in PRODUCTS[key]:
                if pid not in ids:
                    ids.append(pid)

    return ids


def build_filter_params(
    entity: Entities,
    user_profile: Optional[dict] = None,
) -> dict:
    """
    Construye el dict de parámetros de filtro a partir de entidades + perfil.

    No toma ninguna decisión de negocio mediante LLM.
    Todo es lookup determinístico en los dicts de queries_types.

    Args:
        entity:       Entidades extraídas por fast_classify o llm_classify
        user_profile: Perfil del usuario desde sofia-api-users (opcional)

    Returns:
        dict con trade_ids, days, negocio, product_ids, channel_ids,
        is_exclusive_query
    """
    params: dict = {}

    # ── 1. Categoría → trade_ids ──────────────────────────────────────────
    if entity.categoria:
        ids = resolve_trade_ids(entity.categoria)
        if ids:
            params["trade_ids"] = ids

    # ── 2. Días → days (lista de ints, soporta multi-día) ─────────────────
    if entity.dias:
        day_nums: list[int] = []
        for dia in entity.dias:
            day_nums.extend(resolve_days(dia))
        if day_nums:
            params["days"] = list(set(day_nums))

    # ── 3. Negocio ────────────────────────────────────────────────────────
    if entity.negocio:
        params["negocio"] = entity.negocio.lower().strip()

    # ── 4. Perfil de usuario → product_ids + channel_ids ─────────────────
    if user_profile and user_profile.get("identificado"):
        segmento_raw = (user_profile.get("segmento") or "").strip()
        segmento_key = normalize_segment(segmento_raw)

        # Channel IDs para priorización (beneficios exclusivos del segmento)
        channel_ids = CHANNELS.get(segmento_key, [])
        if channel_ids:
            params["channel_ids"] = channel_ids

        # Product IDs para filtrar beneficios incompatibles con las tarjetas
        product_ids = _resolve_product_ids_from_profile(user_profile)
        if product_ids:
            params["product_ids"] = product_ids

    # ── 5. Segmento inyectado por el agente (override) ────────────────────
    # entity.segmento se inyecta cuando la query es explícitamente sobre
    # beneficios de segmento ("mis beneficios black", "beneficios premium")
    if entity.segmento:
        seg_key = normalize_segment(entity.segmento)
        ch_ids = CHANNELS.get(seg_key, [])
        if ch_ids:
            params["channel_ids"] = ch_ids
            params["is_exclusive_query"] = True

    # ── 6. Tipo de beneficio (cuotas vs descuento) ────────────────────────
    if entity.tipo_beneficio:
        params["benefit_type"] = entity.tipo_beneficio

    return params
