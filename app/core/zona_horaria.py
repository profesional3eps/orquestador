"""Fechas y horas de negocio Colombia (America/Bogota, UTC-5)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

ZONA_BOGOTA = ZoneInfo("America/Bogota")


def ahora_bogota() -> datetime:
    """Timestamp naive en hora de Bogotá (columnas timestamp sin tz de Messiah)."""
    return datetime.now(ZONA_BOGOTA).replace(tzinfo=None)


def hoy_bogota() -> date:
    """Fecha calendario actual en Bogotá."""
    return datetime.now(ZONA_BOGOTA).date()
