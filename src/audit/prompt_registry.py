"""
PromptRegistry — Versionado de prompts con semver + integridad SHA256.

Carga el registry.yaml como fuente de verdad.
Cada PromptVersion tiene:
  - version  : semver string (ej: "1.2.0")
  - content  : texto del prompt (puede contener {placeholders})
  - changelog: descripción del cambio
  - hash     : SHA256[:16] del content (detección de tampering)

El hash se recalcula en runtime sobre el content cargado.
Si alguien edita el YAML sin hacer un bump de versión, el hash diferirá
del que quedó grabado en los AuditRecords previos → inconsistencia detectable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import yaml


class PromptVersion:
    """Una versión específica de un prompt."""

    def __init__(self, version: str, content: str, changelog: str = "") -> None:
        self.version = version
        self.content = content.strip()
        self.changelog = changelog
        # Hash corto (16 hex chars) para mostrar en UI; el content completo
        # está en input_payload de los AuditRecords de tipo LLM_CALL.
        self.hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:16]

    def render(self, **kwargs: str) -> str:
        """Formatea el prompt con los kwargs provistos (para templates con {placeholders})."""
        if not kwargs:
            return self.content
        return self.content.format(**kwargs)

    def __repr__(self) -> str:
        return f"<PromptVersion name=? v={self.version} hash={self.hash}>"


class PromptEntry:
    """Todas las versiones de un prompt con puntero a la current."""

    def __init__(self, name: str, data: dict) -> None:
        self.name = name
        self.description = data.get("description", "")
        self._current_key: str = str(data["current_version"])
        self.versions: dict[str, PromptVersion] = {
            str(v): PromptVersion(str(v), d["content"], d.get("changelog", ""))
            for v, d in data["versions"].items()
        }
        if self._current_key not in self.versions:
            raise KeyError(
                f"Prompt '{name}': current_version='{self._current_key}' "
                f"no existe en versions. Versiones disponibles: "
                f"{list(self.versions.keys())}"
            )

    @property
    def current(self) -> PromptVersion:
        return self.versions[self._current_key]

    @property
    def current_version(self) -> str:
        return self._current_key


class PromptRegistry:
    """
    Carga y expone todos los prompts versionados desde registry.yaml.

    Singleton a nivel de módulo — usar get_prompt_registry().
    """

    def __init__(self, registry_path: Optional[str] = None) -> None:
        if registry_path is None:
            registry_path = str(
                Path(__file__).parent.parent / "prompts" / "registry.yaml"
            )
        self._path = Path(registry_path)
        self._prompts: dict[str, PromptEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            raise FileNotFoundError(
                f"Prompt registry no encontrado en: {self._path}\n"
                "Asegurate de que src/prompts/registry.yaml exista."
            )
        with open(self._path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data:
            raise ValueError("El registry.yaml está vacío.")
        for name, entry_data in data.items():
            self._prompts[name] = PromptEntry(name, entry_data)

    def get(self, name: str) -> PromptVersion:
        """Retorna la versión actual del prompt `name`."""
        if name not in self._prompts:
            raise KeyError(
                f"Prompt '{name}' no encontrado en registry. "
                f"Disponibles: {list(self._prompts.keys())}"
            )
        return self._prompts[name].current

    def get_version(self, name: str, version: str) -> PromptVersion:
        """Retorna una versión específica de un prompt (para replay histórico)."""
        if name not in self._prompts:
            raise KeyError(f"Prompt '{name}' no encontrado en registry.")
        entry = self._prompts[name]
        if version not in entry.versions:
            raise KeyError(
                f"Versión '{version}' de prompt '{name}' no existe. "
                f"Disponibles: {list(entry.versions.keys())}"
            )
        return entry.versions[version]

    def get_all_current_versions(self) -> dict[str, str]:
        """Retorna {nombre_prompt: version_actual} para todos los prompts."""
        return {name: entry.current_version for name, entry in self._prompts.items()}

    def get_version_metadata(self, name: str) -> dict[str, str]:
        """Retorna {version, hash} para usar en AuditRecord."""
        v = self.get(name)
        return {"version": v.version, "hash": v.hash}

    def list_prompts(self) -> list[dict]:
        """Retorna metadata de todos los prompts para inspección."""
        result = []
        for name, entry in self._prompts.items():
            result.append({
                "name": name,
                "description": entry.description,
                "current_version": entry.current_version,
                "current_hash": entry.current.hash,
                "available_versions": list(entry.versions.keys()),
            })
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
