from __future__ import annotations

import copy
import re
from typing import Any

PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "card": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    "jwt": re.compile(r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"),
    "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
}

SENSITIVE_KEYS = frozenset(
    {"password", "secret", "token", "api_key", "authorization", "cookie", "credentials"}
)


def _redact_string(value: str) -> str:
    for name, pattern in PATTERNS.items():
        value = pattern.sub(f"[REDACTED_{name.upper()}]", value)
    return value


def sanitize(data: Any) -> Any:
    """Deep-clone y redacta PII/secrets. Nunca muta el input original."""
    if data is None:
        return None

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if isinstance(key, str) and key.lower() in SENSITIVE_KEYS:
                result[key] = "[REDACTED]"
            else:
                result[key] = sanitize(value)
        return result

    if isinstance(data, list):
        return [sanitize(item) for item in data]

    if isinstance(data, str):
        return _redact_string(data)

    return copy.deepcopy(data)
