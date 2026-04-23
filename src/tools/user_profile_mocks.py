"""
User Profile Mocks — Perfiles de prueba para desarrollo local.

Activo cuando MOCK_USER_PROFILE=true en .env.
Los mocks replican la estructura EXACTA de GET /users/v2/{phone}/complements
para que el path de mock ejercite el mismo _parse_profile que producción.

Números de prueba:
  +5491100000001  →  COMAFI UNICO BLACK      (Mastercard Black + Visa Platinum)
  +5491100000002  →  COMAFI PREMIUM          (Visa Platinum + Mastercard Gold)
  +5491100000003  →  COMAFI PREMIUM PLATINUM
                        (Visa Signature + Mastercard Platinum)
  +5491100000004  →  PLAN SUELDO             (Visa clásica + débito)
  +5491100000005  →  PYME                    (Mastercard PYME)
  +5491100000006  →  MASIVO / STANDARD       (Visa clásica)
  +5491100000007  →  No identificado         (no existe en el banco)
"""

# Estructura idéntica a la respuesta real de /users/v2/{phone}/complements
_MOCK_PROFILES: dict[str, dict] = {

    # ── COMAFI UNICO BLACK ─────────────────────────────────────────────
    "5491100000001": {
        "response": {
            "type": "success",
            "data": {
                "consolidated_position": {
                    "cliente_datos_personales": {
                        "nombre": "CARLOS",
                        "apellido": "SANCHEZ RODRIGUEZ",
                        "tipo_cliente": "COMAFI UNICO BLACK",
                        "sub_banca": "PREMIUM",
                        "empleado": "NO",
                        "domicilios": [
                            {
                                "provincia": "BUENOS AIRES",
                                "localidad": "PALERMO",
                            }
                        ],
                        "segmentos_cliente": [
                            {"clase_codigo": "1", "segmento_codigo": "01"}
                        ],
                        "cliente": {
                            "tipo_documento": "DNI",
                            "numero_documento": "28547891",
                        },
                    },
                    "posicion_consolidada": {
                        "productos_cliente": {
                            "productos": [
                                {
                                    "id_producto": {
                                        "definicion_producto": {
                                            "grupo": "MASTERCARD",
                                            "subgrupo": "MASTERCARD BLACK",
                                        }
                                    }
                                },
                                {
                                    "id_producto": {
                                        "definicion_producto": {
                                            "grupo": "VISA",
                                            "subgrupo": "VISA PLATINUM",
                                        }
                                    }
                                },
                            ]
                        }
                    },
                },
                "client_information": {
                    "tipo_documento": "DNI",
                    "numero_documento": "28547891",
                    "nombre": "CARLOS",
                    "apellido": "SANCHEZ RODRIGUEZ",
                    "saludo": "buen día",
                },
            },
        }
    },

    # ── COMAFI PREMIUM ─────────────────────────────────────────────────
    "5491100000002": {
        "response": {
            "type": "success",
            "data": {
                "consolidated_position": {
                    "cliente_datos_personales": {
                        "nombre": "VALENTINA",
                        "apellido": "TORRES HERRERA",
                        "tipo_cliente": "COMAFI PREMIUM",
                        "sub_banca": "PREMIUM",
                        "empleado": "NO",
                        "domicilios": [
                            {"provincia": "CABA", "localidad": "RECOLETA"}
                        ],
                        "segmentos_cliente": [
                            {"clase_codigo": "1", "segmento_codigo": "02"}
                        ],
                        "cliente": {
                            "tipo_documento": "DNI",
                            "numero_documento": "31204567",
                        },
                    },
                    "posicion_consolidada": {
                        "productos_cliente": {
                            "productos": [
                                {
                                    "id_producto": {
                                        "definicion_producto": {
                                            "grupo": "VISA",
                                            "subgrupo": "VISA PLATINUM",
                                        }
                                    }
                                },
                                {
                                    "id_producto": {
                                        "definicion_producto": {
                                            "grupo": "MASTERCARD",
                                            "subgrupo": "MASTER GOLD",
                                        }
                                    }
                                },
                            ]
                        }
                    },
                },
                "client_information": {
                    "tipo_documento": "DNI",
                    "numero_documento": "31204567",
                    "nombre": "VALENTINA",
                    "apellido": "TORRES HERRERA",
                    "saludo": "buenas tardes",
                },
            },
        }
    },

    # ── COMAFI PREMIUM PLATINUM ────────────────────────────────────────
    "5491100000003": {
        "response": {
            "type": "success",
            "data": {
                "consolidated_position": {
                    "cliente_datos_personales": {
                        "nombre": "MARTIN",
                        "apellido": "IBANEZ PEREYRA",
                        "tipo_cliente": "COMAFI PREMIUM PLATINUM",
                        "sub_banca": "PREMIUM",
                        "empleado": "NO",
                        "domicilios": [
                            {
                                "provincia": "CORDOBA",
                                "localidad": "NUEVA CORDOBA",
                            }
                        ],
                        "segmentos_cliente": [
                            {"clase_codigo": "1", "segmento_codigo": "03"}
                        ],
                        "cliente": {
                            "tipo_documento": "DNI",
                            "numero_documento": "24891034",
                        },
                    },
                    "posicion_consolidada": {
                        "productos_cliente": {
                            "productos": [
                                {
                                    "id_producto": {
                                        "definicion_producto": {
                                            "grupo": "VISA",
                                            "subgrupo": "VISA SIGNATURE",
                                        }
                                    }
                                },
                                {
                                    "id_producto": {
                                        "definicion_producto": {
                                            "grupo": "MASTERCARD",
                                            "subgrupo": "MASTERCARD PLATINUM",
                                        }
                                    }
                                },
                            ]
                        }
                    },
                },
                "client_information": {
                    "tipo_documento": "DNI",
                    "numero_documento": "24891034",
                    "nombre": "MARTIN",
                    "apellido": "IBANEZ PEREYRA",
                    "saludo": "buenas noches",
                },
            },
        }
    },

    # ── PLAN SUELDO ────────────────────────────────────────────────────
    "5491100000004": {
        "response": {
            "type": "success",
            "data": {
                "consolidated_position": {
                    "cliente_datos_personales": {
                        "nombre": "LUCIA",
                        "apellido": "FERNANDEZ GOMEZ",
                        "tipo_cliente": "PLAN SUELDO",
                        "sub_banca": "MASIVO",
                        "empleado": "NO",
                        "domicilios": [
                            {"provincia": "SANTA FE", "localidad": "ROSARIO"}
                        ],
                        "segmentos_cliente": [
                            {"clase_codigo": "1", "segmento_codigo": "04"}
                        ],
                        "cliente": {
                            "tipo_documento": "DNI",
                            "numero_documento": "38129045",
                        },
                    },
                    "posicion_consolidada": {
                        "productos_cliente": {
                            "productos": [
                                {
                                    "id_producto": {
                                        "definicion_producto": {
                                            "grupo": "VISA",
                                            "subgrupo": "VISA CLASICA",
                                        }
                                    }
                                },
                                {
                                    "id_producto": {
                                        "definicion_producto": {
                                            "grupo": "VISA",
                                            "subgrupo": "VISA DEBITO",
                                        }
                                    }
                                },
                            ]
                        }
                    },
                },
                "client_information": {
                    "tipo_documento": "DNI",
                    "numero_documento": "38129045",
                    "nombre": "LUCIA",
                    "apellido": "FERNANDEZ GOMEZ",
                    "saludo": "buen día",
                },
            },
        }
    },

    # ── PYME ───────────────────────────────────────────────────────────
    "5491100000005": {
        "response": {
            "type": "success",
            "data": {
                "consolidated_position": {
                    "cliente_datos_personales": {
                        "nombre": "ROBERTO",
                        "apellido": "GIMENEZ CASTILLO",
                        "tipo_cliente": "PYME",
                        "sub_banca": "NEGS Y PROFESIONALES",
                        "empleado": "NO",
                        "domicilios": [
                            {"provincia": "MENDOZA", "localidad": "GODOY CRUZ"}
                        ],
                        "segmentos_cliente": [
                            {"clase_codigo": "1", "segmento_codigo": "05"}
                        ],
                        "cliente": {
                            "tipo_documento": "CUIT",
                            "numero_documento": "20789123",
                        },
                    },
                    "posicion_consolidada": {
                        "productos_cliente": {
                            "productos": [
                                {
                                    "id_producto": {
                                        "definicion_producto": {
                                            "grupo": "MASTERCARD",
                                            "subgrupo": "MASTERCARD PYME",
                                        }
                                    }
                                },
                            ]
                        }
                    },
                },
                "client_information": {
                    "tipo_documento": "CUIT",
                    "numero_documento": "20789123",
                    "nombre": "ROBERTO",
                    "apellido": "GIMENEZ CASTILLO",
                    "saludo": "buenas tardes",
                },
            },
        }
    },

    # ── MASIVO / STANDARD ──────────────────────────────────────────────
    "5491100000006": {
        "response": {
            "type": "success",
            "data": {
                "consolidated_position": {
                    "cliente_datos_personales": {
                        "nombre": "ANA",
                        "apellido": "LOPEZ DIAZ",
                        "tipo_cliente": "MASIVO",
                        "sub_banca": "MASIVO",
                        "empleado": "NO",
                        "domicilios": [
                            {"provincia": "TUCUMAN", "localidad": "SAN MIGUEL"}
                        ],
                        "segmentos_cliente": [
                            {"clase_codigo": "1", "segmento_codigo": "06"}
                        ],
                        "cliente": {
                            "tipo_documento": "DNI",
                            "numero_documento": "42310987",
                        },
                    },
                    "posicion_consolidada": {
                        "productos_cliente": {
                            "productos": [
                                {
                                    "id_producto": {
                                        "definicion_producto": {
                                            "grupo": "VISA",
                                            "subgrupo": "VISA CLASICA",
                                        }
                                    }
                                },
                            ]
                        }
                    },
                },
                "client_information": {
                    "tipo_documento": "DNI",
                    "numero_documento": "42310987",
                    "nombre": "ANA",
                    "apellido": "LOPEZ DIAZ",
                    "saludo": "buen día",
                },
            },
        }
    },

    # ── No identificado ────────────────────────────────────────────────
    "5491100000007": None,  # None → _parse_profile retorna identificado=False
}


def get_mock_profile(phone_number: str) -> dict | None:
    """
    Retorna el dict con estructura real de /users/v2/{phone}/complements.

    Retorna None si el número no existe → fetch_user_profile lo trata
    como usuario no identificado.

    El número se normaliza (solo dígitos) antes de buscar.
    Si el número no está en los mocks, retorna None (no identificado genérico).
    """
    normalized = "".join(c for c in phone_number if c.isdigit())
    if normalized not in _MOCK_PROFILES:
        return None
    # puede ser None explícito (ej: 5491100000007)
    return _MOCK_PROFILES[normalized]


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
                "provincia": None,
            })
        else:
            inner_data = data["response"]["data"]
            client_info = inner_data.get("client_information", {})
            consolidated = inner_data.get("consolidated_position", {})
            cliente_dp = consolidated.get("cliente_datos_personales", {})
            posicion = consolidated.get("posicion_consolidada", {})

            nombre = (
                f"{client_info.get('nombre', '')} "
                f"{client_info.get('apellido', '')}"
            ).strip()
            segmento = cliente_dp.get("tipo_cliente", "")

            productos = []
            prods_raw = (
                (posicion.get("productos_cliente") or {})
                .get("productos", [])
            )
            for p in prods_raw:
                defprod = (
                    (p.get("id_producto") or {})
                    .get("definicion_producto") or {}
                )
                grupo = defprod.get("grupo", "")
                subgrupo = defprod.get("subgrupo", "")
                label = f"{grupo} {subgrupo}".strip()
                if label:
                    productos.append(label)

            provincia = None
            for dom in cliente_dp.get("domicilios", []):
                if isinstance(dom, dict) and dom.get("provincia"):
                    provincia = dom["provincia"]
                    break

            result.append({
                "phone": f"+{phone}",
                "nombre": nombre,
                "segmento": segmento,
                "productos": productos,
                "provincia": provincia,
            })
    return result
