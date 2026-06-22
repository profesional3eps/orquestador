from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.models.dto import PqrResumenItem


def _to_int_consecutivo(val: Any) -> int:
    if val is None:
        return 0
    if isinstance(val, Decimal):
        return int(val)
    if isinstance(val, bool):
        return int(val)
    return int(val)


def _to_fecha_radicado(val: Any) -> datetime | date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return val
    return None


def fila_encabezado_a_resumen(row: dict[str, Any]) -> PqrResumenItem:
    return PqrResumenItem(
        consecutivoPeticion=_to_int_consecutivo(row.get("consecutivo_peticion")),
        tipoSolicitud=row.get("tipo_solicitud"),
        fechaRadicado=_to_fecha_radicado(row.get("fecha_radicado")),
        estadopqr=row.get("estado_pqr"),
        areaResponsable=row.get("area_responsable"),
        respuestaResumen=row.get("respuesta_resumen"),
    )
