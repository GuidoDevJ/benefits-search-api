"""
Context utilities — lógica de enriquecimiento y clarificación de contexto.

Funciones puras (sin I/O, sin LLM) que combinan clasificación NLP con
preferencias del usuario para construir el contexto final del grafo.

Extraído de ui/chat_interface.py para romper la dependencia
services → ui.
"""

from typing import Optional

_DIAS_DISPLAY = {
    "lunes":    "los lunes",
    "martes":   "los martes",
    "miercoles": "los miércoles",
    "jueves":   "los jueves",
    "viernes":  "los viernes",
    "sabado":   "los sábados",
    "domingo":  "los domingos",
}

_WEEKDAY_KEY = [
    "lunes", "martes", "miercoles", "jueves",
    "viernes", "sabado", "domingo",
]


def _format_dias(dias: list[str]) -> str:
    """Convierte lista de días a texto legible en español."""
    if set(dias) >= {"sabado", "domingo"}:
        return "el fin de semana"
    parts = [_DIAS_DISPLAY.get(d, d) for d in dias]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " y " + parts[-1]


def _get_top_from_prefs(user_prefs: dict) -> tuple:
    """
    Lee contadores de uso y retorna (top_categoria, top_dias).

    Umbral: >= 2 usos para considerar preferencia estable.
    """
    cat_counts = user_prefs.get("cat_counts", {})
    top_cat = None
    if cat_counts:
        best = max(cat_counts, key=cat_counts.get)
        if cat_counts[best] >= 2:
            top_cat = best

    day_counts = user_prefs.get("day_counts", {})
    top_dias = [d for d, c in day_counts.items() if c >= 2] or None
    return top_cat, top_dias


def _autofill_today(merged_clf: dict, user_prefs: dict) -> dict:
    """
    Si el usuario no especificó día y hoy coincide con uno de sus días
    habituales (day_counts >= 2), lo inyecta automáticamente.

    Ej: hoy es sábado, el usuario suele buscar sábados
    → merged_clf["dias"] = ["sabado"]
    """
    if merged_clf.get("dias") or merged_clf.get("dia"):
        return merged_clf

    from datetime import datetime
    hoy_key = _WEEKDAY_KEY[datetime.now().weekday()]

    day_counts = user_prefs.get("day_counts", {})
    if day_counts.get(hoy_key, 0) >= 2:
        merged_clf = dict(merged_clf)
        merged_clf["dias"] = [hoy_key]
        merged_clf["dia"] = hoy_key
        print(f"[Prefs] Auto-fill dia={hoy_key} (habitual del usuario)")

    return merged_clf


def _needs_clarification(
    clf: dict,
    gathering: dict,
    user_prefs: Optional[dict] = None,
) -> tuple[bool, str]:
    """
    Determina si falta información para hacer una búsqueda útil.

    Considera las preferencias guardadas del usuario como contexto implícito.
    Cuando falta info, pregunta categoría + días en un solo turno.

    Retorna (True, pregunta) si falta contexto.
    Retorna (False, "") si se puede buscar.
    """
    merged = {**gathering, **{k: v for k, v in clf.items() if v is not None}}
    up = user_prefs or {}
    top_cat, _ = _get_top_from_prefs(up)

    has_categoria = (
        merged.get("categoria_benefits")
        or merged.get("negocio")
        or merged.get("segmento")
        or top_cat
    )
    has_tipo = merged.get("tipo_beneficio")

    if has_categoria or has_tipo:
        return False, ""

    provincia = merged.get("provincia")
    prefix = (
        "Los beneficios Comafi aplican en todo el país. "
        if provincia else ""
    )
    known_dias = merged.get("dias") or (
        [merged["dia"]] if merged.get("dia") else None
    )

    if known_dias:
        dias_str = _format_dias(known_dias)
        return True, (
            f"{prefix}¿Qué tipo de beneficio buscás para {dias_str}?\n\n"
            "Por ejemplo: gastronomía, supermercados, moda, "
            "entretenimiento, combustible, turismo, cine, salud, belleza..."
        )

    return True, (
        f"{prefix}¿Qué tipo de comercio te interesa y para cuándo?\n\n"
        "Por ejemplo: _gastronomía los sábados_, "
        "_supermercados los lunes_, _cine este fin de semana_..."
    )


def _merge_context(gathering: dict, clf: dict) -> dict:
    """
    Combina el contexto acumulado con la nueva clasificación.

    Los valores nuevos (no-None) sobreescriben los anteriores.
    """
    merged = dict(gathering)
    for key, val in clf.items():
        if val is not None and key != "gathering":
            merged[key] = val
    if "intent" not in merged:
        merged["intent"] = "benefits"
    return merged
