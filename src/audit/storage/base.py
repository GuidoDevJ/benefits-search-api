"""
BaseAuditStorage — Interfaz abstracta para backends de persistencia.

Strategy Pattern: el AuditService habla con esta interfaz,
no con ninguna implementación concreta.
"""

from abc import ABC, abstractmethod
from typing import Optional

from ..models import AuditRecord, SessionSummary


class BaseAuditStorage(ABC):

    @abstractmethod
    async def initialize(self) -> None:
        """Inicializa el backend (crea tablas, abre conexiones, etc.)."""
        ...

    @abstractmethod
    async def save_record(self, record: AuditRecord) -> None:
        """Persiste un único registro de auditoría."""
        ...

    @abstractmethod
    async def upsert_session(self, summary: SessionSummary) -> None:
        """Crea o actualiza el resumen de una sesión."""
        ...

    @abstractmethod
    async def get_session_records(self, session_id: str) -> list[AuditRecord]:
        """Retorna todos los registros de una sesión, ordenados por sequence_num."""
        ...

    @abstractmethod
    async def get_session_summary(self, session_id: str) -> Optional[SessionSummary]:
        """Retorna el resumen de una sesión, o None si no existe."""
        ...

    @abstractmethod
    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        has_error: Optional[bool] = None,
    ) -> list[SessionSummary]:
        """Lista sesiones con filtrado y paginación opcionales."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Cierra conexiones y libera recursos."""
        ...
