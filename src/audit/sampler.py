from __future__ import annotations

import os
import random

from src.audit.models import AuditEvent


class SamplingPolicy:
    """Decide si un evento debe ser registrado basado en su tipo y status."""

    def __init__(
        self,
        success_rate: float = 0.10,
        error_rate: float = 1.00,
        slow_threshold_ms: float = 1500.0,
    ):
        self.success_rate = success_rate
        self.error_rate = error_rate
        self.slow_threshold_ms = slow_threshold_ms

    def should_record(self, event: AuditEvent) -> bool:
        if event.status in ("error", "timeout"):
            return True

        if event.latency_ms is not None and event.latency_ms > self.slow_threshold_ms:
            return True

        if os.getenv("AUDIT_DEBUG", "false").lower() == "true":
            return True

        return random.random() < self.success_rate
