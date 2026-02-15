from datetime import datetime

DAYS_MAP = {
    "1": "Lunes",
    "2": "Martes",
    "3": "Miércoles",
    "4": "Jueves",
    "5": "Viernes",
    "6": "Sábado",
    "7": "Domingo",
}

BENEFIT_TYPE = {406: "cuotas", 407: "descuento", 409: "descuento_y_cuotas"}


def parse_date(date_int: int) -> str:
    return datetime.strptime(str(date_int), "%y%m%d").strftime("%d/%m/%Y")


def parse_days(raw: str) -> str:
    if raw == "1234567":
        return "Todos los días"
    return ", ".join(DAYS_MAP[d] for d in raw)


def normalize_promo(promo: any) -> any:
    """
    Normaliza un beneficio a formato legible.
    Solo incluye campos esenciales para reducir tokens enviados al LLM.
    """
    benefit_type = BENEFIT_TYPE.get(promo["t"])

    if benefit_type == "descuento":
        beneficio = f'{promo["d"]}%'
    elif benefit_type == "cuotas":
        beneficio = f'{promo["q"]}c'
    else:
        beneficio = f'{promo["d"]}%+{promo["q"]}c'

    return {
        "nom": promo["b"],
        "ben": beneficio,
        "pago": promo["ct"],
        "dias": parse_days(promo["a"]),
    }
