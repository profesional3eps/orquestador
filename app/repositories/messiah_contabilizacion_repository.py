"""Contabilización de autorizaciones (réplica AccountantImplService.recopilarInformacionContableAutorizacionV2)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.messiah_constants import (
    CONTABILIZA_AUTORIZACIONES,
    PREFIJO_NOTA_AUTORIZACION,
    REGIMEN_CONTABILIZA_RC,
    REGIMEN_CONTABILIZA_RS,
    TERCERO_EPS,
    TERCERO_PRESTADOR,
    TIPO_ASIENTO_AUTORIZACION,
    TIPO_DOCUMENTO_NOTA_AUTORIZACION,
    VALOR_CONTABILIZA_CERO,
    VALOR_CONTABILIZA_TOTAL_MEDICAMENTOS,
)
from app.core.zona_horaria import ahora_bogota

LOCK_CONTABILIZACION = "SELECT pg_advisory_xact_lock(hashtext('orq_contabilizacion_autorizacion'))"

SALDO_AUTORIZACION_VALIDO_SQL = """
SELECT 1
FROM administrativo.sc_saldo_encabezado e
WHERE e.consecutivo_saldo = :consecutivo_saldo
  AND e.tipo_documento = :tipo_documento
  AND e.documento = :consecutivo_autorizacion
  AND e.documento_nota LIKE :prefijo || '%'
LIMIT 1
"""

DESVINCULAR_SALDO_INVALIDO_SQL = """
UPDATE administrativo.ss_autorizacion
SET consecutivo_saldo = NULL
WHERE consecutivo_autorizacion = :consecutivo_autorizacion
  AND consecutivo_saldo = :consecutivo_saldo
"""

AUTORIZACION_CONTABILIZAR_SQL = """
SELECT
    a.consecutivo_autorizacion,
    a.consecutivo_interno,
    a.consecutivo_interno_base,
    a.pin,
    a.consecutivo_ips,
    a.consecutivo_saldo,
    a.tipo_regimen_afiliado,
    a.municipio_afiliado,
    a.nit_prestador,
    a.tipo_identificacion_afiliado,
    a.numero_identificacion_afiliado,
    a.fecha_autorizacion_reserva,
    a.fecha_grabado,
    i.consecutivo_tercero AS ips_consecutivo_tercero
FROM administrativo.ss_autorizacion a
INNER JOIN administrativo.ct_ips i ON i.ips = a.consecutivo_ips
WHERE a.consecutivo_autorizacion = :consecutivo_autorizacion
  AND a.fecha_anula IS NULL
LIMIT 1
"""

SUM_MEDICAMENTOS_SQL = """
SELECT COALESCE(SUM(am.valor_servicio), 0) AS total
FROM administrativo.ss_autorizacion_medicamento am
WHERE am.consecutivo_autorizacion = :consecutivo_autorizacion
  AND am.fecha_cancelacion IS NULL
"""

SC_CONTABILIZA_SQL = """
SELECT
    c.contabiliza,
    c.secuencia,
    c.cuenta,
    c.valor_debito,
    c.valor_credito,
    c.tipo_regimen,
    c.tipo_tercero,
    c.consecutivo_centro_costo,
    c.vigencia
FROM administrativo.sc_contabiliza c
WHERE c.contabiliza = :contabiliza
  AND COALESCE(c.sw_activo, 0) = 1
ORDER BY c.secuencia
"""

EMPRESA_TERCERO_SQL = """
SELECT consecutivo_tercero_empresa
FROM administrativo.tb_empresa
ORDER BY consecutivo_empresa
LIMIT 1
"""

CENTRO_COSTO_MUNICIPIO_SQL = """
SELECT consecutivo_centro_costo
FROM administrativo.sc_centro_costo
WHERE municipio = :municipio
  AND TRIM(COALESCE(codigo_interno, '')) = TRIM(:municipio)
LIMIT 1
"""

NEXT_SALDO_ENCABEZADO = """
SELECT COALESCE(MAX(consecutivo_saldo), 0) + 1 AS next_id
FROM administrativo.sc_saldo_encabezado
"""

NEXT_NOTA_AUTORIZACION = """
SELECT COALESCE(
    MAX(CAST(NULLIF(regexp_replace(documento_nota, '[^0-9]', '', 'g'), '') AS BIGINT)),
    0
) + 1 AS next_id
FROM administrativo.sc_saldo_encabezado
WHERE tipo_documento = :tipo_documento
  AND documento_nota LIKE :prefijo || '%'
"""

INSERT_SALDO_ENCABEZADO = """
INSERT INTO administrativo.sc_saldo_encabezado (
    consecutivo_saldo, documento, documento_soporte, documento_nota,
    estado, fecha, tipo_documento, descripcion_tipo_documento,
    tipo_documento_auxiliar, usuario_grabado, fecha_grabado, observacion, tercero
) VALUES (
    :consecutivo_saldo, :documento, :documento_soporte, :documento_nota,
    1, :fecha, :tipo_documento, :descripcion_tipo_documento,
    :tipo_documento, :usuario_grabado, :fecha_grabado, :observacion, :tercero
)
"""

INSERT_SALDO_ENCABEZADO_DOCUMENTO = """
INSERT INTO administrativo.sc_saldo_encabezado_documento (
    consecutivo_saldo, secuencia, llave, tipo_documento,
    tipo_documento_auxiliar, documento_nota, documento_soporte
) VALUES (
    :consecutivo_saldo, 0, :llave, :tipo_documento,
    0, :documento_nota, :documento_soporte
)
"""

INSERT_SALDO_DETALLE = """
INSERT INTO administrativo.sc_saldo_detalle (
    consecutivo_saldo, secuencia, tercero, centro_costo, cuenta,
    documento, documento_consecutivo, documento_nota, observacion,
    valor_credito, valor_debito, tipo_asiento, descripcion_cuenta,
    documento_soporte_enlace_auxiliar
) VALUES (
    :consecutivo_saldo, :secuencia, :tercero, :centro_costo, :cuenta,
    :documento, :documento_consecutivo, :documento_nota, :observacion,
    :valor_credito, :valor_debito, :tipo_asiento, :descripcion_cuenta,
    :documento_soporte_enlace_auxiliar
)
"""

UPDATE_AUTORIZACION_SALDO = """
UPDATE administrativo.ss_autorizacion
SET consecutivo_saldo = :consecutivo_saldo
WHERE consecutivo_autorizacion = :consecutivo_autorizacion
  AND consecutivo_saldo IS NULL
"""

DESCRIPCION_TIPO_NOTA_AUTORIZACION = "Nota contabilidad autorización"


class MessiahContabilizacionError(Exception):
    def __init__(self, mensaje: str) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje


def _interno_base(auth: dict[str, Any]) -> str:
    base = auth.get("consecutivo_interno_base")
    if base:
        return str(base)
    interno = str(auth.get("consecutivo_interno") or "")
    if interno and interno != "0":
        return interno
    return str(auth["consecutivo_autorizacion"])


def _valor_regla(regla: int, *, total_medicamentos: float) -> float:
    if regla == VALOR_CONTABILIZA_CERO:
        return 0.0
    if regla == VALOR_CONTABILIZA_TOTAL_MEDICAMENTOS:
        return total_medicamentos
    return 0.0


def _regimen_contabiliza(tipo_regimen_afiliado: int) -> int:
    if int(tipo_regimen_afiliado) in {2, 99}:
        return REGIMEN_CONTABILIZA_RS
    return REGIMEN_CONTABILIZA_RC


def _filtrar_reglas(rows: list[dict[str, Any]], tipo_regimen_afiliado: int) -> list[dict[str, Any]]:
    regimen = _regimen_contabiliza(tipo_regimen_afiliado)
    out: list[dict[str, Any]] = []
    for row in rows:
        tr = int(row.get("tipo_regimen") or 0)
        if tr in {0, regimen}:
            out.append(row)
    return out


def _observacion_autorizacion(auth: dict[str, Any]) -> str:
    interno = str(auth.get("consecutivo_interno") or auth["consecutivo_autorizacion"])
    pin = str(auth.get("pin") or "")
    if interno == "0":
        return (
            f"Autorización PIN {pin} "
            f"{auth.get('tipo_identificacion_afiliado')} {auth.get('numero_identificacion_afiliado')}"
        )
    pin_txt = f" ({pin})" if pin else ""
    return (
        f"Autorización {interno}{pin_txt} "
        f"{auth.get('tipo_identificacion_afiliado')} {auth.get('numero_identificacion_afiliado')}"
    )


def _saldo_vinculo_es_nota_autorizacion(
    db: Session,
    *,
    consecutivo_saldo: int,
    consecutivo_autorizacion: int,
) -> bool:
    """True si el saldo es nota NC-AT de esta autorización (no facturación u otro tipo)."""
    return (
        db.execute(
            text(SALDO_AUTORIZACION_VALIDO_SQL),
            {
                "consecutivo_saldo": int(consecutivo_saldo),
                "tipo_documento": TIPO_DOCUMENTO_NOTA_AUTORIZACION,
                "consecutivo_autorizacion": int(consecutivo_autorizacion),
                "prefijo": PREFIJO_NOTA_AUTORIZACION,
            },
        ).first()
        is not None
    )


def contabilizar_autorizacion_medicamentos(
    db: Session,
    *,
    consecutivo_autorizacion: int,
    username: str,
    fecha_asiento: date | None = None,
) -> int | None:
    """Genera sc_saldo y asigna ss_autorizacion.consecutivo_saldo."""
    db.execute(text(LOCK_CONTABILIZACION))
    auth = db.execute(
        text(AUTORIZACION_CONTABILIZAR_SQL),
        {"consecutivo_autorizacion": int(consecutivo_autorizacion)},
    ).mappings().first()
    if auth is None:
        raise MessiahContabilizacionError(
            f"No existe autorización {consecutivo_autorizacion} para contabilizar."
        )
    auth = dict(auth)
    saldo_vinculado = auth.get("consecutivo_saldo")
    if saldo_vinculado is not None:
        if _saldo_vinculo_es_nota_autorizacion(
            db,
            consecutivo_saldo=int(saldo_vinculado),
            consecutivo_autorizacion=int(consecutivo_autorizacion),
        ):
            return int(saldo_vinculado)
        db.execute(
            text(DESVINCULAR_SALDO_INVALIDO_SQL),
            {
                "consecutivo_autorizacion": int(consecutivo_autorizacion),
                "consecutivo_saldo": int(saldo_vinculado),
            },
        )

    total_med = float(
        db.execute(
            text(SUM_MEDICAMENTOS_SQL),
            {"consecutivo_autorizacion": int(consecutivo_autorizacion)},
        ).scalar_one()
        or 0
    )
    if total_med <= 0:
        raise MessiahContabilizacionError(
            "La autorización no tiene valor de medicamentos para contabilizar."
        )

    reglas = db.execute(
        text(SC_CONTABILIZA_SQL),
        {"contabiliza": CONTABILIZA_AUTORIZACIONES},
    ).mappings().all()
    reglas = _filtrar_reglas([dict(r) for r in reglas], int(auth.get("tipo_regimen_afiliado") or 99))
    if not reglas:
        raise MessiahContabilizacionError(
            "No hay reglas sc_contabiliza activas para autorizaciones del régimen del afiliado."
        )

    empresa = db.execute(text(EMPRESA_TERCERO_SQL)).mappings().first()
    tercero_eps = int(empresa["consecutivo_tercero_empresa"]) if empresa and empresa.get("consecutivo_tercero_empresa") else 0
    if not tercero_eps:
        raise MessiahContabilizacionError("tb_empresa.consecutivo_tercero_empresa no configurado.")

    centro_costo = None
    municipio = str(auth.get("municipio_afiliado") or "").strip()
    if municipio:
        cc = db.execute(
            text(CENTRO_COSTO_MUNICIPIO_SQL),
            {"municipio": municipio},
        ).scalar_one_or_none()
        if cc is not None:
            centro_costo = int(cc)

    fecha = fecha_asiento
    if fecha is None:
        reserva = auth.get("fecha_autorizacion_reserva") or auth.get("fecha_grabado")
        if isinstance(reserva, datetime):
            fecha = reserva.date()
        elif isinstance(reserva, date):
            fecha = reserva
        else:
            fecha = ahora_bogota().date()

    consecutivo_saldo = int(db.execute(text(NEXT_SALDO_ENCABEZADO)).scalar_one())
    nota_seq = int(
        db.execute(
            text(NEXT_NOTA_AUTORIZACION),
            {
                "tipo_documento": TIPO_DOCUMENTO_NOTA_AUTORIZACION,
                "prefijo": PREFIJO_NOTA_AUTORIZACION,
            },
        ).scalar_one()
    )
    documento_nota = f"{PREFIJO_NOTA_AUTORIZACION} {nota_seq}"
    documento = str(consecutivo_autorizacion)
    observacion = _observacion_autorizacion(auth)
    grabado = ahora_bogota()
    interno_detalle = _interno_base(auth)

    detalles: list[dict[str, Any]] = []
    secuencia = 0
    tercero_prestador = auth.get("ips_consecutivo_tercero")

    for regla in reglas:
        vigencia = int(regla.get("vigencia") or 0)
        if vigencia not in {0, 1}:
            continue

        valor_debito = _valor_regla(int(regla["valor_debito"]), total_medicamentos=total_med)
        valor_credito = _valor_regla(int(regla["valor_credito"]), total_medicamentos=total_med)
        if valor_debito == 0 and valor_credito == 0:
            continue

        tipo_tercero = int(regla.get("tipo_tercero") or 0)
        if tipo_tercero == TERCERO_EPS:
            tercero = tercero_eps
        elif tipo_tercero == TERCERO_PRESTADOR:
            if not tercero_prestador:
                raise MessiahContabilizacionError(
                    "La IPS de direccionamiento no tiene consecutivo_tercero para contabilizar."
                )
            tercero = int(tercero_prestador)
        else:
            tercero = tercero_eps

        cuenta = int(regla["cuenta"])
        desc_cuenta = db.execute(
            text("SELECT descripcion FROM administrativo.sc_cuenta WHERE cuenta = :cuenta LIMIT 1"),
            {"cuenta": cuenta},
        ).scalar_one_or_none()
        cc = centro_costo if centro_costo is not None else regla.get("consecutivo_centro_costo")

        detalles.append(
            {
                "consecutivo_saldo": consecutivo_saldo,
                "secuencia": secuencia,
                "tercero": tercero,
                "centro_costo": cc,
                "cuenta": cuenta,
                "documento": documento,
                "documento_consecutivo": documento,
                "documento_nota": documento_nota,
                "observacion": observacion,
                "valor_credito": valor_credito,
                "valor_debito": valor_debito,
                "tipo_asiento": TIPO_ASIENTO_AUTORIZACION,
                "descripcion_cuenta": str(desc_cuenta or "")[:200],
                "documento_soporte_enlace_auxiliar": interno_detalle,
            }
        )
        secuencia += 1

    if not detalles:
        raise MessiahContabilizacionError("No se generaron líneas de saldo para la autorización.")

    suma = sum(d["valor_credito"] - d["valor_debito"] for d in detalles)
    if abs(suma) > 1:
        raise MessiahContabilizacionError(f"El asiento contable no cuadra (diferencia {suma:.2f}).")

    # db.execute(
    #     text(INSERT_SALDO_ENCABEZADO),
    #     {
    #         "consecutivo_saldo": consecutivo_saldo,
    #         "documento": int(consecutivo_autorizacion),
    #         "documento_soporte": documento,
    #         "documento_nota": documento_nota,
    #         "tipo_documento": TIPO_DOCUMENTO_NOTA_AUTORIZACION,
    #         "descripcion_tipo_documento": DESCRIPCION_TIPO_NOTA_AUTORIZACION,
    #         "usuario_grabado": username[:100],
    #         "fecha_grabado": grabado,
    #         "observacion": observacion[:500],
    #         "fecha": fecha,
    #         "tercero": tercero_eps,
    #     },
    # )
    # db.execute(
    #     text(INSERT_SALDO_ENCABEZADO_DOCUMENTO),
    #     {
    #         "consecutivo_saldo": consecutivo_saldo,
    #         "llave": int(consecutivo_autorizacion),
    #         "tipo_documento": TIPO_DOCUMENTO_NOTA_AUTORIZACION,
    #         "documento_nota": documento_nota,
    #         "documento_soporte": documento,
    #     },
    # )
    # for det in detalles:
    #     db.execute(text(INSERT_SALDO_DETALLE), det)

    # updated = db.execute(
    #     text(UPDATE_AUTORIZACION_SALDO),
    #     {
    #         "consecutivo_saldo": consecutivo_saldo,
    #         "consecutivo_autorizacion": int(consecutivo_autorizacion),
    #     },
    # )
    # if (updated.rowcount or 0) == 0:
    #     raise MessiahContabilizacionError("La autorización ya fue contabilizada por otro proceso.")
    # db.commit()
    # return consecutivo_saldo
