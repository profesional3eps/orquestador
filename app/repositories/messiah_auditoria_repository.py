"""Trazabilidad Messiah: ss_autorizacion_estado y log_at_autorizacion_programacion."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.messiah_constants import SW_LIBERADA_NO
from app.core.zona_horaria import ahora_bogota

NEXT_SS_AUTORIZACION_ESTADO = """
SELECT COALESCE(MAX(e.consecutivo_estado), 0) + 1
FROM administrativo.ss_autorizacion_estado e
"""

NEXT_LOG_AUTORIZACION_PROGRAMACION = """
SELECT COALESCE(MAX(l.consecutivo_log), 0) + 1
FROM administrativo.log_at_autorizacion_programacion l
"""

INSERT_SS_AUTORIZACION_ESTADO = """
INSERT INTO administrativo.ss_autorizacion_estado (
    consecutivo_estado,
    consecutivo_autorizacion,
    estado,
    fecha_estado,
    usuario_grabado,
    fecha_grabado,
    sw_liberada
) VALUES (
    :consecutivo_estado,
    :consecutivo_autorizacion,
    :estado,
    :fecha_estado,
    :usuario_grabado,
    :fecha_grabado,
    :sw_liberada
)
"""

UPDATE_ESTADO_FLUJO_AUTORIZACION = """
UPDATE administrativo.ss_autorizacion
SET estado_flujo = :estado_flujo
WHERE consecutivo_autorizacion = :consecutivo_autorizacion
"""

INSERT_LOG_AUTORIZACION_PROGRAMACION = """
INSERT INTO administrativo.log_at_autorizacion_programacion (
    consecutivo_log,
    consecutivo_autorizacion,
    codigo_servicio,
    codigo_interno_servicio,
    tipo_servicio,
    valor_antes,
    valor_despues,
    usuario_grabado,
    fecha_grabado,
    motivo_antes,
    motivo_despues,
    tipo_accion
) VALUES (
    :consecutivo_log,
    :consecutivo_autorizacion,
    :codigo_servicio,
    :codigo_interno_servicio,
    :tipo_servicio,
    :valor_antes,
    :valor_despues,
    :usuario_grabado,
    :fecha_grabado,
    NULL,
    NULL,
    :tipo_accion
)
"""

MEDICAMENTOS_AUTORIZACION = """
SELECT
    am.secuencia,
    am.medicamento,
    am.codigo_propio,
    am.fecha_programacion,
    am.fecha_prestacion_servicio
FROM administrativo.ss_autorizacion_medicamento am
WHERE am.consecutivo_autorizacion = :consecutivo_autorizacion
  AND am.fecha_cancelacion IS NULL
ORDER BY am.secuencia
"""


def registrar_estado_flujo_autorizacion(
    db: Session,
    *,
    consecutivo_autorizacion: int,
    estado_flujo: int,
    fecha_estado: date | datetime,
    username: str,
    sw_liberada: int = SW_LIBERADA_NO,
) -> None:
    """Réplica registrarEstadoAutorizacion (estado_flujo + ss_autorizacion_estado)."""
    ahora = ahora_bogota()
    fecha_est = fecha_estado.date() if isinstance(fecha_estado, datetime) else fecha_estado
    consecutivo_estado = int(db.execute(text(NEXT_SS_AUTORIZACION_ESTADO)).scalar_one())
    db.execute(
        text(INSERT_SS_AUTORIZACION_ESTADO),
        {
            "consecutivo_estado": consecutivo_estado,
            "consecutivo_autorizacion": int(consecutivo_autorizacion),
            "estado": int(estado_flujo),
            "fecha_estado": fecha_est,
            "usuario_grabado": username[:100],
            "fecha_grabado": ahora,
            "sw_liberada": int(sw_liberada),
        },
    )
    db.execute(
        text(UPDATE_ESTADO_FLUJO_AUTORIZACION),
        {
            "consecutivo_autorizacion": int(consecutivo_autorizacion),
            "estado_flujo": int(estado_flujo),
        },
    )


def registrar_log_autorizacion_programacion(
    db: Session,
    *,
    consecutivo_autorizacion: int,
    codigo_servicio: int,
    codigo_interno: str,
    tipo_servicio: int,
    tipo_accion: int,
    valor_antes: str | None,
    valor_despues: str | None,
    username: str,
) -> None:
    """Réplica CostImplService.updateAutorizacionLog."""
    consecutivo_log = int(db.execute(text(NEXT_LOG_AUTORIZACION_PROGRAMACION)).scalar_one())
    db.execute(
        text(INSERT_LOG_AUTORIZACION_PROGRAMACION),
        {
            "consecutivo_log": consecutivo_log,
            "consecutivo_autorizacion": int(consecutivo_autorizacion),
            "codigo_servicio": int(codigo_servicio),
            "codigo_interno_servicio": (codigo_interno or "")[:50],
            "tipo_servicio": int(tipo_servicio),
            "valor_antes": valor_antes,
            "valor_despues": valor_despues,
            "usuario_grabado": username[:100],
            "fecha_grabado": ahora_bogota(),
            "tipo_accion": int(tipo_accion),
        },
    )


def fetch_medicamentos_autorizacion(
    db: Session,
    consecutivo_autorizacion: int,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(MEDICAMENTOS_AUTORIZACION),
        {"consecutivo_autorizacion": int(consecutivo_autorizacion)},
    ).mappings().all()
    return [dict(r) for r in rows]


def _fmt_fecha(val: date | datetime | None) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date().isoformat()
    return val.isoformat()


def registrar_logs_activacion_medicamentos(
    db: Session,
    *,
    consecutivo_autorizacion: int,
    username: str,
) -> None:
    from app.core.messiah_constants import (
        TIPO_ACCION_LOG_ACTIVACION,
        TIPO_SERVICIO_LOG_MEDICAMENTO,
    )

    for med in fetch_medicamentos_autorizacion(db, consecutivo_autorizacion):
        registrar_log_autorizacion_programacion(
            db,
            consecutivo_autorizacion=consecutivo_autorizacion,
            codigo_servicio=int(med["medicamento"]),
            codigo_interno=str(med.get("codigo_propio") or ""),
            tipo_servicio=TIPO_SERVICIO_LOG_MEDICAMENTO,
            tipo_accion=TIPO_ACCION_LOG_ACTIVACION,
            valor_antes=_fmt_fecha(med.get("fecha_programacion")),
            valor_despues=None,
            username=username,
        )


def registrar_logs_confirmacion_medicamentos(
    db: Session,
    *,
    consecutivo_autorizacion: int,
    username: str,
) -> None:
    from app.core.messiah_constants import (
        TIPO_ACCION_LOG_CONSULTA,
        TIPO_SERVICIO_LOG_MEDICAMENTO,
    )

    for med in fetch_medicamentos_autorizacion(db, consecutivo_autorizacion):
        registrar_log_autorizacion_programacion(
            db,
            consecutivo_autorizacion=consecutivo_autorizacion,
            codigo_servicio=int(med["medicamento"]),
            codigo_interno=str(med.get("codigo_propio") or ""),
            tipo_servicio=TIPO_SERVICIO_LOG_MEDICAMENTO,
            tipo_accion=TIPO_ACCION_LOG_CONSULTA,
            valor_antes=_fmt_fecha(med.get("fecha_programacion")),
            valor_despues=_fmt_fecha(med.get("fecha_prestacion_servicio")),
            username=username,
        )
