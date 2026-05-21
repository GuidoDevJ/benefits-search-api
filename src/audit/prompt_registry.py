"""
PromptRegistry — Carga prompts desde AWS Bedrock Prompt Management.

Cada prompt se almacena en Bedrock y se recupera por su ID (o ARN) y versión.
La versión puede ser un número entero como string ("1", "2") o "DRAFT".

Mapeo nombre → ID de Bedrock:
  - "benefits"  → BEDROCK_PROMPT_BENEFITS_ID
  - "tienda"    → BEDROCK_PROMPT_TIENDA_ID

La versión activa se controla con BEDROCK_PROMPT_VERSION (default: "DRAFT").

Los prompts se cachean en memoria durante la vida del proceso; para refrescar
en runtime reiniciar el servicio o llamar a reset_prompt_registry().
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from src.config import (
    AWS_REGION,
    BEDROCK_PROMPT_BENEFITS_ID,
    BEDROCK_PROMPT_TIENDA_ID,
    BEDROCK_PROMPT_VERSION,
)

logger = logging.getLogger(__name__)

# Mapeo de nombre lógico → variable de entorno con el ID del prompt en Bedrock
_PROMPT_ID_MAP: dict[str, str] = {
    "benefits": BEDROCK_PROMPT_BENEFITS_ID,
    "tienda": BEDROCK_PROMPT_TIENDA_ID,
}


class PromptVersion:
    """Una versión específica de un prompt."""

    def __init__(self, version: str, content: str, name: str = "") -> None:
        self.version = version
        self.content = content.strip()
        self.name = name
        self.hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:16]

    def render(self, **kwargs: str) -> str:
        """Formatea el prompt con los kwargs provistos (para templates con {placeholders})."""
        if not kwargs:
            return self.content
        return self.content.format(**kwargs)

    def __repr__(self) -> str:
        return f"<PromptVersion name={self.name} v={self.version} hash={self.hash}>"


def _extract_text_from_bedrock_response(response: dict) -> str:
    """Extrae el texto del prompt de la respuesta de bedrock-agent.get_prompt()."""
    variants = response.get("variants", [])
    if not variants:
        raise ValueError("La respuesta de Bedrock no contiene variants.")

    # Usar el primer variant disponible (o el que tenga inferenceConfiguration)
    variant = variants[0]
    template_config = variant.get("templateConfiguration", {})

    # Soporte para TEXT templates (el tipo más común)
    if "text" in template_config:
        text = template_config["text"].get("text", "")
        if text:
            return text

    raise ValueError(
        f"No se pudo extraer texto del variant '{variant.get('name', 'unknown')}'. "
        f"templateType={variant.get('templateType')}"
    )


class PromptRegistry:
    """
    Recupera prompts desde AWS Bedrock Prompt Management.

    Singleton a nivel de módulo — usar get_prompt_registry().
    """

    def __init__(self) -> None:
        self._client = boto3.client("bedrock-agent", region_name=AWS_REGION)
        self._cache: dict[str, PromptVersion] = {}
        self._version = BEDROCK_PROMPT_VERSION

    def _fetch(self, name: str, version: Optional[str] = None) -> PromptVersion:
        prompt_id = _PROMPT_ID_MAP.get(name, "")
        if not prompt_id:
            raise KeyError(
                f"Prompt '{name}' no tiene ID configurado en variables de entorno. "
                f"Prompts disponibles: {[k for k, v in _PROMPT_ID_MAP.items() if v]}"
            )

        target_version = version or self._version
        cache_key = f"{name}:{target_version}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            response = self._client.get_prompt(
                promptIdentifier=prompt_id,
                promptVersion=target_version,
            )
        except ClientError as e:
            raise RuntimeError(
                f"Error al obtener prompt '{name}' (id={prompt_id}, "
                f"version={target_version}) desde Bedrock: {e}"
            ) from e

        bedrock_version = response.get("version", target_version)
        content = _extract_text_from_bedrock_response(response)
        prompt_version = PromptVersion(version=bedrock_version, content=content, name=name)

        self._cache[cache_key] = prompt_version
        logger.info("Prompt '%s' v%s cargado desde Bedrock (hash=%s)", name, bedrock_version, prompt_version.hash)
        return prompt_version

    def get(self, name: str) -> PromptVersion:
        """Retorna la versión activa del prompt `name`."""
        return self._fetch(name)

    def get_version(self, name: str, version: str) -> PromptVersion:
        """Retorna una versión específica de un prompt (para replay histórico)."""
        return self._fetch(name, version=version)

    def get_all_current_versions(self) -> dict[str, str]:
        """Retorna {nombre_prompt: version_actual} para todos los prompts configurados."""
        result = {}
        for name, prompt_id in _PROMPT_ID_MAP.items():
            if not prompt_id:
                continue
            try:
                pv = self._fetch(name)
                result[name] = pv.version
            except Exception as e:
                logger.warning("No se pudo obtener versión de '%s': %s", name, e)
        return result

    def get_version_metadata(self, name: str) -> dict[str, str]:
        """Retorna {version, hash} para usar en AuditRecord."""
        v = self.get(name)
        return {"version": v.version, "hash": v.hash}

    def list_prompts(self) -> list[dict]:
        """Retorna metadata de todos los prompts configurados."""
        result = []
        for name, prompt_id in _PROMPT_ID_MAP.items():
            if not prompt_id:
                continue
            try:
                pv = self._fetch(name)
                result.append({
                    "name": name,
                    "prompt_id": prompt_id,
                    "current_version": pv.version,
                    "current_hash": pv.hash,
                })
            except Exception as e:
                result.append({"name": name, "prompt_id": prompt_id, "error": str(e)})
        return result


# --------------------------------------------------------------------------
# Singleton a nivel de módulo
# --------------------------------------------------------------------------

_registry: Optional[PromptRegistry] = None


def get_prompt_registry() -> PromptRegistry:
    """Retorna el singleton de PromptRegistry, inicializándolo si es necesario."""
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry


def reset_prompt_registry() -> None:
    """Reinicia el singleton para forzar recarga de prompts desde Bedrock."""
    global _registry
    _registry = None
