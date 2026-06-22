from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.models.dto import PortabilidadConsultaResponse, PortabilidadHistorialItem


def _normalize_sql_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        d = val.date()
    elif isinstance(val, date):
        d = val
    else:
        return None
    if d <= date(1900, 1, 1):
        return None
    return d


def _to_int_or_none(val: Any) -> int | None:
    if val is None:
        return None
    if isinstance(val, Decimal):
        return int(val)
    if isinstance(val, bool):
        return int(val)
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _fila_movilidad_a_item(row: dict[str, Any]) -> PortabilidadHistorialItem:
    return PortabilidadHistorialItem(
        consecutivoMovilidad=_to_int_or_none(row.get("consecutivo_movilidad")),
        estadoPortabilidad=row.get("nombre_estado_portabilidad"),
        estadoPortabilidadCodigo=_to_int_or_none(row.get("estado_portabilidad_codigo")),
        ciudadOrigen=row.get("ciudad_origen"),
        ciudadDestino=row.get("ciudad_destino"),
        fechaInicio=_normalize_sql_date(row.get("fecha_inicio")),
        fechaFin=_normalize_sql_date(row.get("fecha_fin")),
    )


def construir_respuesta_portabilidad(
    afiliado_row: dict[str, Any],
    filas_movilidad: list[dict[str, Any]],
    *,
    tipo_identificacion_codigo: str,
    tipo_identificacion_descripcion: str,
    numero_identificacion: str,
) -> PortabilidadConsultaResponse:
    nombre = (afiliado_row.get("nombre_completo") or "").strip() or "Sin nombre registrado"
    items = [_fila_movilidad_a_item(r) for r in filas_movilidad]
    return PortabilidadConsultaResponse(
        tipo_identificacion_codigo=tipo_identificacion_codigo,
        tipo_identificacion_descripcion=tipo_identificacion_descripcion,
        numero_identificacion=numero_identificacion,
        nombreCompleto=nombre,
        portabilidades=items,
    )


def estado_afiliado_es_activo_para_portabilidad(estado_afiliado: Any) -> bool:
    """CA1: afiliación activa — estado 1 (Activo) o 5 (Activo carnetizado)."""
    code = _to_int_or_none(estado_afiliado)
    return code in (1, 5)
