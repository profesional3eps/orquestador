"""Lectura de tb_preferencia Messiah (reserva técnica medicamentos)."""

from __future__ import annotations

import threading
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from datetime import date, timedelta

from app.core.messiah_constants import (
    PREF_RESERVA_MEDICAMENTO_ACTIVACION,
    PREF_RESERVA_MEDICAMENTO_CONFIRMACION,
    PREF_VIGENCIA_MEDICAMENTOS,
)

PREFERENCIA_ACTIVA_SQL = """
SELECT consecutivo_preferencia, tipo_preferencia, valor, valor_texto
FROM administrativo.tb_preferencia
WHERE tipo_preferencia = :tipo_preferencia
LIMIT 1
"""

_PREF_CACHE_TTL_SEC = 60.0
_pref_cache: dict[int, tuple[float, dict | None]] = {}
_pref_cache_lock = threading.Lock()


def _preferencia_activa(row: dict | None) -> bool:
    if not row:
        return False
    valor = row.get("valor")
    if valor is not None and float(valor) == 1.0:
        return True
    texto = str(row.get("valor_texto") or "").strip().upper()
    return texto in {"1", "TRUE", "SI", "S"}


def fetch_preferencia(db: Session, tipo_preferencia: int) -> dict | None:
    key = int(tipo_preferencia)
    now = time.monotonic()
    with _pref_cache_lock:
        cached = _pref_cache.get(key)
        if cached is not None and now - cached[0] < _PREF_CACHE_TTL_SEC:
            return cached[1]
    row = db.execute(
        text(PREFERENCIA_ACTIVA_SQL),
        {"tipo_preferencia": key},
    ).mappings().first()
    value = dict(row) if row else None
    with _pref_cache_lock:
        _pref_cache[key] = (now, value)
    return value


def reserva_tecnica_medicamentos_activa(db: Session) -> tuple[bool, bool]:
    """Retorna (activacion_390, confirmacion_288) según tb_preferencia."""
    p_act = fetch_preferencia(db, PREF_RESERVA_MEDICAMENTO_ACTIVACION)
    p_conf = fetch_preferencia(db, PREF_RESERVA_MEDICAMENTO_CONFIRMACION)
    return _preferencia_activa(p_act), _preferencia_activa(p_conf)


def debe_contabilizar_reserva_medicamentos(db: Session) -> bool:
    """True si tipo_preferencia 288 o 390 tiene valor=1 o valor_texto activo."""
    act, conf = reserva_tecnica_medicamentos_activa(db)
    return act or conf


def debe_contabilizar_en_activacion(db: Session, *, solo_medicamento: bool) -> bool:
    """
    Réplica CostImplService.updateSsAutorizacionConDetalles (!fin):
    con 288 en confirmación, el asiento se genera en activación si 390 está activo
    o si 288 está inactivo; con 288 activo el asiento va en confirmación.
    """
    if not solo_medicamento:
        return debe_contabilizar_reserva_medicamentos(db)
    act, conf = reserva_tecnica_medicamentos_activa(db)
    if not act and not conf:
        return False
    if conf:
        return False
    return True


def calcular_vigencias_autorizacion_medicamentos(
    db: Session,
    fecha_base: date,
) -> tuple[int, date, date]:
    """
    Vigencia Messiah (tb_preferencia tipo 6): valor=días fin vigencia, valor_texto=días fin vigencia servicio.
    """
    row = fetch_preferencia(db, PREF_VIGENCIA_MEDICAMENTOS)
    if not row:
        fin = fecha_base + timedelta(days=30)
        return 10, fin, fin
    consecutivo_pref = int(row.get("consecutivo_preferencia") or 10)
    dias_vigencia = int(float(row.get("valor") or 30))
    try:
        dias_servicio = int(str(row.get("valor_texto") or "30").strip())
    except ValueError:
        dias_servicio = 30
    return (
        consecutivo_pref,
        fecha_base + timedelta(days=dias_vigencia),
        fecha_base + timedelta(days=dias_servicio),
    )


def debe_contabilizar_en_confirmacion(db: Session, *, solo_medicamento: bool) -> bool:
    """Réplica lógica Messiah cuando fin=true (confirmación prestación)."""
    if not solo_medicamento:
        return debe_contabilizar_reserva_medicamentos(db)
    act, conf = reserva_tecnica_medicamentos_activa(db)
    if not act and not conf:
        return False
    return conf or not act
