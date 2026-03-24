"""
User Profile Mocks — Perfiles de prueba para desarrollo local.

Activo cuando MOCK_USER_PROFILE=true en .env.
Los mocks replican la estructura EXACTA de la API sofia-users
(response.data wrapper, segmento objeto, paquetes lista) para que
el path de mock ejercite el mismo _parse_profile que producción.

Números de prueba:
  +5491100000001  →  COMAFI UNICO BLACK      (Mastercard Black + Visa Platinum)
  +5491100000002  →  COMAFI PREMIUM          (Visa Platinum + Mastercard Gold)
  +5491100000003  →  COMAFI PREMIUM PLATINUM (Visa Signature + Mastercard Platinum)
  +5491100000004  →  PLAN SUELDO             (Visa clásica + débito)
  +5491100000005  →  PYME                    (Mastercard PYME)
  +5491100000006  →  MASIVO / STANDARD       (Visa clásica)
  +5491100000007  →  No identificado         (no existe en el banco)
"""

# Estructura idéntica a la respuesta real de sofia-users ?type=values
_MOCK_PROFILES: dict[str, dict] = {

    # ── COMAFI UNICO BLACK ─────────────────────────────────────────────
    "5491100000001": {
        "response": {
            "type": "success",
            "data": {
                "nombre": "Carlos",
                "apellidos": "Sánchez Rodríguez",
                "nro_documento": "28547891",
                "tipo_documento": "96",
                "saludo": "buen día",
                "foreign_phone_number": False,
                "empleado": "NO",
                "paquetes": [
                    {"descripcion": "Mastercard Black", "tipo": "TC"},
                    {"descripcion": "Visa Platinum", "tipo": "TC"},
                ],
                "segmento": {
                    "tipo": "01",
                    "descripcion": "COMAFI UNICO BLACK",
                },
            },
        }
    },

    # ── COMAFI PREMIUM ─────────────────────────────────────────────────
    "5491100000002": {
        "response": {
            "type": "success",
            "data": {
                "nombre": "Valentina",
                "apellidos": "Torres Herrera",
                "nro_documento": "31204567",
                "tipo_documento": "96",
                "saludo": "buenas tardes",
                "foreign_phone_number": False,
                "empleado": "NO",
                "paquetes": [
                    {"descripcion": "Visa Platinum", "tipo": "TC"},
                    {"descripcion": "Mastercard Gold", "tipo": "TC"},
                ],
                "segmento": {
                    "tipo": "02",
                    "descripcion": "COMAFI PREMIUM",
                },
            },
        }
    },

    # ── COMAFI PREMIUM PLATINUM ────────────────────────────────────────
    "5491100000003": {
        "response": {
            "type": "success",
            "data": {
                "nombre": "Martín",
                "apellidos": "Ibáñez Pereyra",
                "nro_documento": "24891034",
                "tipo_documento": "96",
                "saludo": "buenas noches",
                "foreign_phone_number": False,
                "empleado": "NO",
                "paquetes": [
                    {"descripcion": "Visa Signature", "tipo": "TC"},
                    {"descripcion": "Mastercard Platinum", "tipo": "TC"},
                ],
                "segmento": {
                    "tipo": "03",
                    "descripcion": "COMAFI PREMIUM PLATINUM",
                },
            },
        }
    },

    # ── PLAN SUELDO ────────────────────────────────────────────────────
    "5491100000004": {
        "response": {
            "type": "success",
            "data": {
                "nombre": "Lucía",
                "apellidos": "Fernández Gómez",
                "nro_documento": "38129045",
                "tipo_documento": "96",
                "saludo": "buen día",
                "foreign_phone_number": False,
                "empleado": "NO",
                "paquetes": [
                    {"descripcion": "Tarjeta de crédito Visa", "tipo": "TC"},
                    {"descripcion": "Tarjeta de débito Visa", "tipo": "TD"},
                ],
                "segmento": {
                    "tipo": "04",
                    "descripcion": "PLAN SUELDO",
                },
            },
        }
    },

    # ── PYME ───────────────────────────────────────────────────────────
    "5491100000005": {
        "response": {
            "type": "success",
            "data": {
                "nombre": "Roberto",
                "apellidos": "Giménez Castillo",
                "nro_documento": "20789123",
                "tipo_documento": "80",
                "saludo": "buenas tardes",
                "foreign_phone_number": False,
                "empleado": "NO",
                "paquetes": [
                    {"descripcion": "Mastercard PYME", "tipo": "TC"},
                ],
                "segmento": {
                    "tipo": "05",
                    "descripcion": "PYME",
                },
            },
        }
    },

    # ── MASIVO / STANDARD ──────────────────────────────────────────────
    "5491100000006": {
        "response": {
            "type": "success",
            "data": {
                "nombre": "Ana",
                "apellidos": "López Díaz",
                "nro_documento": "42310987",
                "tipo_documento": "96",
                "saludo": "buen día",
                "foreign_phone_number": False,
                "empleado": "NO",
                "paquetes": [
                    {"descripcion": "Tarjeta de crédito Visa", "tipo": "TC"},
                ],
                "segmento": {
                    "tipo": "06",
                    "descripcion": "MASIVO",
                },
            },
        }
    },

    # ── No identificado ────────────────────────────────────────────────
    "5491100000007": None,  # None → _parse_profile retorna identificado=False
}


def get_mock_profile(phone_number: str) -> dict | None:
    """
    Retorna el dict con estructura real de sofia-users para el número dado.

    Retorna None si el número no existe → fetch_user_profile lo trata
    como usuario no identificado.

    El número se normaliza (solo dígitos) antes de buscar.
    Si el número no está en los mocks, retorna None (no identificado genérico).
    """
    normalized = "".join(c for c in phone_number if c.isdigit())
    profile = _MOCK_PROFILES.get(normalized)
    if normalized not in _MOCK_PROFILES:
        # Número desconocido → no identificado
        return None
    return profile  # puede ser None explícito (ej: 5491100000007)


def list_mock_phones() -> list[dict]:
    """Lista todos los teléfonos de prueba disponibles con su segmento."""
    result = []
    for phone, data in _MOCK_PROFILES.items():
        if data is None:
            result.append({
                "phone": f"+{phone}",
                "nombre": None,
                "segmento": "No identificado",
                "productos": [],
            })
        else:
            inner = data["response"]["data"]
            seg = inner["segmento"]["descripcion"]
            nombre = f"{inner['nombre']} {inner['apellidos']}"
            prods = [p["descripcion"] for p in inner.get("paquetes", [])]
            result.append({
                "phone": f"+{phone}",
                "nombre": nombre,
                "segmento": seg,
                "productos": prods,
            })
    return result
