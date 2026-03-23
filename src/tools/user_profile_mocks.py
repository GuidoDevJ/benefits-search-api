"""
User Profile Mocks — Perfiles de prueba para desarrollo local.

Activo cuando MOCK_USER_PROFILE=true en .env.
Cubre todos los segmentos posibles para testear filtrado y priorización.

Números de prueba:
  +5491100000001  →  UNICO BLACK      (Mastercard Black)
  +5491100000002  →  PREMIUM          (Visa Platinum)
  +5491100000003  →  PREMIUM PLATINUM (Visa Signature)
  +5491100000004  →  PLAN SUELDO      (Visa clásica)
  +5491100000005  →  PYME             (Mastercard)
  +5491100000006  →  MASIVO/STANDARD  (Visa clásica)
  +5491100000007  →  No identificado  (no existe en el banco)
"""

# Estructura plana — misma que UserProfile.model_dump()
_MOCK_PROFILES: dict[str, dict] = {

    # ── Black ─────────────────────────────────────────────────────────
    "5491100000001": {
        "phone_number":    "5491100000001",
        "nombre":          "Carlos",
        "apellido":        "Sánchez",
        "nombre_completo": "Carlos Sánchez",
        "segmento":        "UNICO BLACK",
        "nro_documento":   "28547891",
        "tipo_documento":  "DNI",
        "productos":       ["Mastercard Black", "Visa Platinum"],
        "identificado":    True,
        "error":           None,
    },

    # ── Premium ───────────────────────────────────────────────────────
    "5491100000002": {
        "phone_number":    "5491100000002",
        "nombre":          "Valentina",
        "apellido":        "Torres",
        "nombre_completo": "Valentina Torres",
        "segmento":        "PREMIUM",
        "nro_documento":   "31204567",
        "tipo_documento":  "DNI",
        "productos":       ["Visa Platinum", "Mastercard Gold"],
        "identificado":    True,
        "error":           None,
    },

    # ── Premium Platinum ──────────────────────────────────────────────
    "5491100000003": {
        "phone_number":    "5491100000003",
        "nombre":          "Martín",
        "apellido":        "Ibáñez",
        "nombre_completo": "Martín Ibáñez",
        "segmento":        "PREMIUM PLATINUM",
        "nro_documento":   "24891034",
        "tipo_documento":  "DNI",
        "productos":       ["Visa Signature", "Mastercard Platinum"],
        "identificado":    True,
        "error":           None,
    },

    # ── Plan Sueldo ───────────────────────────────────────────────────
    "5491100000004": {
        "phone_number":    "5491100000004",
        "nombre":          "Lucía",
        "apellido":        "Fernández",
        "nombre_completo": "Lucía Fernández",
        "segmento":        "PLAN SUELDO",
        "nro_documento":   "38129045",
        "tipo_documento":  "DNI",
        "productos":       ["Tarjeta de crédito Visa", "Tarjeta de débito Visa"],
        "identificado":    True,
        "error":           None,
    },

    # ── Pyme ──────────────────────────────────────────────────────────
    "5491100000005": {
        "phone_number":    "5491100000005",
        "nombre":          "Roberto",
        "apellido":        "Giménez",
        "nombre_completo": "Roberto Giménez",
        "segmento":        "PYME",
        "nro_documento":   "20789123",
        "tipo_documento":  "CUIT",
        "productos":       ["Tarjeta de crédito Mastercard"],
        "identificado":    True,
        "error":           None,
    },

    # ── Masivo / Standard ─────────────────────────────────────────────
    "5491100000006": {
        "phone_number":    "5491100000006",
        "nombre":          "Ana",
        "apellido":        "López",
        "nombre_completo": "Ana López",
        "segmento":        "MASIVO",
        "nro_documento":   "42310987",
        "tipo_documento":  "DNI",
        "productos":       ["Tarjeta de crédito Visa"],
        "identificado":    True,
        "error":           None,
    },

    # ── No identificado ───────────────────────────────────────────────
    "5491100000007": {
        "phone_number":    "5491100000007",
        "nombre":          None,
        "apellido":        None,
        "nombre_completo": None,
        "segmento":        None,
        "nro_documento":   None,
        "tipo_documento":  None,
        "productos":       [],
        "identificado":    False,
        "error":           "Usuario no identificado en el sistema bancario",
    },
}


def get_mock_profile(phone_number: str) -> dict | None:
    """
    Retorna el perfil mock para el número dado, o None si no está en el dict.

    El número se normaliza (solo dígitos) antes de buscar.
    Si el número no está en los mocks, retorna un perfil genérico
    de "no identificado" para no romper el flujo.
    """
    normalized = "".join(c for c in phone_number if c.isdigit())
    # Buscar con y sin prefijo 549 (Argentina)
    profile = _MOCK_PROFILES.get(normalized)
    if profile is None:
        # Fallback: usuario no identificado genérico
        profile = {
            "phone_number":    normalized,
            "nombre":          None,
            "apellido":        None,
            "nombre_completo": None,
            "segmento":        None,
            "nro_documento":   None,
            "tipo_documento":  None,
            "productos":       [],
            "identificado":    False,
            "error":           "Número no encontrado en mocks de prueba",
        }
    return profile


def list_mock_phones() -> list[dict]:
    """Lista todos los teléfonos de prueba disponibles con su segmento."""
    return [
        {
            "phone": f"+{phone}",
            "nombre": p["nombre_completo"],
            "segmento": p["segmento"] or "No identificado",
            "productos": p["productos"],
        }
        for phone, p in _MOCK_PROFILES.items()
    ]
