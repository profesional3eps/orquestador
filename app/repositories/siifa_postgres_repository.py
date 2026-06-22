"""Consultas parametrizadas PostgreSQL (ERP Messiah) para radicación SIIFA."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.models.siifa_radicacion import RipsAfMatch, RipsResumenRadicado

# Columnas reales en Messiah: numero_identificacion (NIT prestador), radica_rips en resumen.
BUSCAR_RIPS_AF = """
SELECT
    consecutivo_rips_af,
    consecutivo_rips,
    numero_factura,
    numero_identificacion,
    radicado_siifa,
    idfactura_siifa
FROM administrativo.rips_af
WHERE TRIM(numero_factura) = TRIM(:numero_factura)
  AND TRIM(numero_identificacion) = TRIM(:nit_emisor)
LIMIT 1
"""

BUSCAR_RIPS_RESUMEN = """
SELECT
    consecutivo_rips,
    estado,
    radica_rips,
    fecha_radica
FROM administrativo.rips_resumen
WHERE consecutivo_rips = :consecutivo_rips
LIMIT 1
"""

ACTUALIZAR_RIPS_AF_SIIFA = """
UPDATE administrativo.rips_af
SET
    radicado_siifa = :radicado_siifa,
    fecha_rad_siifa = :fecha_rad_siifa,
    idfactura_siifa = :idfactura_siifa
WHERE consecutivo_rips_af = :consecutivo_rips_af
"""


class PostgreSQLRepository:
    """Acceso de solo lectura/actualización puntual a tablas RIPS del ERP."""

    ESTADO_RADICADO_ERP = 5

    def __init__(self, session: Session) -> None:
        self._session = session

    def buscar_rips_af(self, numero_factura: str, nit_emisor: str) -> RipsAfMatch | None:
        row = self._session.execute(
            text(BUSCAR_RIPS_AF),
            {"numero_factura": numero_factura, "nit_emisor": nit_emisor},
        ).mappings().first()
        if not row:
            return None
        return RipsAfMatch(
            consecutivo_rips_af=int(row["consecutivo_rips_af"]),
            consecutivo_rips=int(row["consecutivo_rips"]),
            numero_factura=str(row["numero_factura"] or ""),
            numero_identificacion=str(row["numero_identificacion"] or ""),
            radicado_siifa=int(row["radicado_siifa"]) if row["radicado_siifa"] is not None else None,
            idfactura_siifa=str(row["idfactura_siifa"]) if row["idfactura_siifa"] else None,
        )

    @staticmethod
    def _parse_fecha_radica(value: Any) -> datetime | None:
        """Normaliza fecha_radica del ERP (DATE/TIMESTAMP/str) a datetime naive."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
            except ValueError:
                pass
            for fmt, size in (
                ("%Y-%m-%d %H:%M:%S.%f", 26),
                ("%Y-%m-%d %H:%M:%S", 19),
                ("%Y-%m-%d", 10),
            ):
                try:
                    return datetime.strptime(raw[:size], fmt)
                except ValueError:
                    continue
            return None
        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
            try:
                return datetime(int(value.year), int(value.month), int(value.day))
            except (TypeError, ValueError):
                return None
        return None

    def buscar_rips_resumen(self, consecutivo_rips: int) -> RipsResumenRadicado | None:
        row = self._session.execute(
            text(BUSCAR_RIPS_RESUMEN),
            {"consecutivo_rips": consecutivo_rips},
        ).mappings().first()
        if not row:
            return None
        fecha_raw = row["fecha_radica"]
        fecha = self._parse_fecha_radica(fecha_raw)
        if fecha is None and fecha_raw is not None:
            logger.warning(
                "fecha_radica_no_parseada consecutivo_rips=%s tipo=%s valor=%r",
                consecutivo_rips,
                type(fecha_raw).__name__,
                fecha_raw,
            )
        return RipsResumenRadicado(
            consecutivo_rips=int(row["consecutivo_rips"]),
            estado=int(row["estado"]),
            radica_rips=str(row["radica_rips"] or "").strip(),
            fecha_radica=fecha,
        )

    def actualizar_radicado_siifa(
        self,
        *,
        consecutivo_rips_af: int,
        id_factura_siifa: int,
        id_factura_radicado_siifa: int,
    ) -> None:
        """Actualiza rips_af tras radicación exitosa en SIIFA."""
        radicado_valor = self._valor_radicado_siifa(id_factura_radicado_siifa)
        self._session.execute(
            text(ACTUALIZAR_RIPS_AF_SIIFA),
            {
                "consecutivo_rips_af": consecutivo_rips_af,
                "radicado_siifa": radicado_valor,
                "fecha_rad_siifa": datetime.now(timezone.utc).replace(tzinfo=None),
                "idfactura_siifa": str(id_factura_siifa),
            },
        )

    def actualizar_siifa_erp_desde_seguimiento(
        self,
        *,
        consecutivo_rips_af: int,
        id_factura_siifa: int,
        radicado_siifa: int,
        fecha_rad_siifa: datetime,
    ) -> int:
        """Actualiza rips_af con valores explícitos (backfill desde CSV con seguimiento)."""
        valor = self._valor_radicado_siifa(radicado_siifa)
        self._session.execute(
            text(ACTUALIZAR_RIPS_AF_SIIFA),
            {
                "consecutivo_rips_af": consecutivo_rips_af,
                "radicado_siifa": valor,
                "fecha_rad_siifa": fecha_rad_siifa.replace(tzinfo=None)
                if fecha_rad_siifa.tzinfo
                else fecha_rad_siifa,
                "idfactura_siifa": str(id_factura_siifa),
            },
        )
        return valor

    @staticmethod
    def _valor_radicado_siifa(id_factura_radicado: int) -> int:
        """rips_af.radicado_siifa es SMALLINT: usa 1 como bandera si el id excede el rango."""
        if id_factura_radicado <= 32767:
            return id_factura_radicado
        return 1

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()
