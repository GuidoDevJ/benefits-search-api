"""
NLP Processor — Validación básica de consultas con spaCy.

Responsabilidad reducida: detectar texto sin sentido (gibberish)
antes de enviarlo al LLM classifier.

La clasificación de intención y extracción de entidades fue
migrada a src/tools/llm_classifier.py.
"""

import re

import spacy

nlp = spacy.load("es_core_news_sm")

# Palabras vacías que por sí solas no constituyen una consulta válida
_STOP_ONLY = {"hola", "ok", "si", "no", "ola", "hey", "hi", "bye"}


def is_valid_query(text: str) -> bool:
    """
    Devuelve True si el texto parece una consulta real en lenguaje natural.
    Devuelve False para:
      - Texto muy corto (< 3 caracteres)
      - Solo números o símbolos
      - Saludos/palabras sueltas sin contenido
      - Secuencias aleatorias de caracteres (gibberish)

    Args:
        text: Texto ingresado por el usuario.

    Returns:
        bool — True si vale la pena procesarlo con el LLM.
    """
    stripped = text.strip()

    # Muy corto
    if len(stripped) < 3:
        return False

    # Solo números, símbolos o espacios
    if re.fullmatch(r"[\d\s\W]+", stripped):
        return False

    text_lower = stripped.lower()

    # Palabra suelta que es solo saludo/vacía
    tokens_alpha = [t for t in text_lower.split() if t.isalpha()]
    if len(tokens_alpha) == 1 and tokens_alpha[0] in _STOP_ONLY:
        return False

    # spaCy: necesita al menos un token con 3+ caracteres alfabéticos
    doc = nlp(stripped)
    meaningful = [
        tok for tok in doc
        if tok.is_alpha and len(tok.text) >= 3 and not tok.is_space
    ]
    return len(meaningful) >= 1
