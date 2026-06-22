"""Parámetros del job programado SIIFA (orq.scheduler_jobs.parametros JSON)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config.settings import Settings, get_settings

SERVICE_SIIFA_RADICACION_LOTE = "siifa_radicacion_lote"


@dataclass(frozen=True)
class SiifaSchedulerParams:
    usuario: str
    max_paginas: int | None
    reprocesar_fallidos: bool | None
    reiniciar_lote: bool
    modo_lote: bool | None

    def to_execution_kwargs(self) -> dict[str, Any]:
        return {
            "usuario": self.usuario,
            "max_paginas": self.max_paginas,
            "reprocesar_fallidos": self.reprocesar_fallidos,
            "reiniciar_lote": self.reiniciar_lote,
            "modo_lote": self.modo_lote,
        }


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "si", "sí", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(value)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def parse_siifa_scheduler_params(
    raw: dict[str, Any] | None,
    settings: Settings | None = None,
) -> SiifaSchedulerParams:
    """
    Interpreta el JSON de orq.scheduler_jobs.parametros.

    Claves soportadas:
      - usuario (str)
      - max_paginas | paginas_por_lote | paginas (int)
      - reprocesar_fallidos (bool)
      - sin_reproceso (bool): si true, fuerza reprocesar_fallidos=false
      - reiniciar_lote (bool)
      - modo_lote (bool)
    """
    cfg = settings or get_settings()
    data = raw or {}

    usuario = str(data.get("usuario") or "scheduler").strip() or "scheduler"

    max_paginas = _coerce_int(
        data.get("max_paginas", data.get("paginas_por_lote", data.get("paginas")))
    )

    reprocesar: bool | None
    if _coerce_bool(data.get("sin_reproceso")) is True:
        reprocesar = False
    elif "reprocesar_fallidos" in data:
        reprocesar = _coerce_bool(data.get("reprocesar_fallidos"))
    else:
        reprocesar = None

    reiniciar_lote = _coerce_bool(data.get("reiniciar_lote")) is True
    modo_lote = _coerce_bool(data.get("modo_lote"))
    if modo_lote is None and cfg.siifa_modo_lote:
        modo_lote = True

    return SiifaSchedulerParams(
        usuario=usuario,
        max_paginas=max_paginas,
        reprocesar_fallidos=reprocesar,
        reiniciar_lote=reiniciar_lote,
        modo_lote=modo_lote,
    )
