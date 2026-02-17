from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from src.audit.models import AuditEvent

_logger = logging.getLogger("audit.exporter.jsonfile")


class JsonFileExporter:
    """Escribe eventos como JSONL con rotación diaria."""

    def __init__(self, log_dir: str = "logs"):
        self._log_dir = Path(log_dir)

    def _get_filepath(self) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._log_dir / f"audit-{date_str}.jsonl"

    async def export(self, event: AuditEvent) -> None:
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            filepath = self._get_filepath()
            line = event.model_dump_json() + "\n"
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            _logger.warning("Failed to write audit event: %s", e)

    async def flush(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass
