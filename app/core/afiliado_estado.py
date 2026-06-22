"""Estados de afiliado alineados con Messiah EStatusAffiliate."""

from __future__ import annotations

from typing import Any

# Messiah: com.creandosoft.sie.publico.enumerations.EStatusAffiliate
ESTADOS_AFILIADO_MESSIAH: dict[int, str] = {
    0: "No aplica",
    1: "Activo",
    2: "Retirado",
    3: "Fallecido",
    4: "Suspendido",
    5: "Activo Carnetizado",
    6: "Retirado Anulado",
}

# authorization_request_ips (continueRequest): solo EStatusAffiliate.ACTIVE (1)
ESTADOS_PERMITIDOS_AUTORIZACION_IPS: frozenset[int] = frozenset({1})

MENSAJE_ESTADO_NO_PERMITIDO_AUTORIZACION_IPS = (
    "El afiliado no cumple con condiciones de estado que permitan continuar con el proceso."
)


def codigo_estado_afiliado(estado: Any) -> int | None:
    try:
        return int(estado)
    except (TypeError, ValueError):
        return None


def nombre_estado_afiliado(estado: Any) -> str:
    code = codigo_estado_afiliado(estado)
    if code is None:
        return "Desconocido"
    return ESTADOS_AFILIADO_MESSIAH.get(code, f"Estado {code}")


def estado_afiliado_permite_autorizacion_ips(estado: Any) -> bool:
    code = codigo_estado_afiliado(estado)
    return code is not None and code in ESTADOS_PERMITIDOS_AUTORIZACION_IPS
