"""Job batch para radicación SIIFA (invocable desde scheduler o CLI)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config.settings import Settings, get_settings
from app.jobs.siifa_scheduler_params import (
    SERVICE_SIIFA_RADICACION_LOTE,
    SiifaSchedulerParams,
    parse_siifa_scheduler_params,
)
from app.services.radicacion_service import RadicacionService

logger = logging.getLogger(__name__)

__all__ = [
    "SERVICE_SIIFA_RADICACION_LOTE",
    "SiifaSchedulerParams",
    "ejecutar_radicacion_siifa",
    "ejecutar_radicacion_siifa_desde_csv",
    "ejecutar_radicacion_siifa_desde_scheduler",
    "parse_siifa_scheduler_params",
]


def ejecutar_radicacion_siifa(
    settings: Settings | None = None,
    *,
    usuario: str | None = "scheduler",
    reprocesar_fallidos: bool | None = None,
    reiniciar_lote: bool = False,
    max_paginas: int | None = None,
    modo_lote: bool | None = None,
) -> dict[str, Any]:
    """Punto de entrada para CLI o llamadas directas."""
    cfg = settings or get_settings()
    service = RadicacionService(cfg)
    resultado = service.ejecutar_sincronizacion(
        tipo_ejecucion="BATCH",
        usuario=usuario,
        reprocesar_fallidos=reprocesar_fallidos,
        reiniciar_lote=reiniciar_lote,
        max_paginas=max_paginas,
        modo_lote=modo_lote,
    )
    logger.info("siifa_radicacion_job_fin resultado=%s", resultado)
    return resultado


def ejecutar_radicacion_siifa_desde_csv(
    csv_path: Path | str,
    settings: Settings | None = None,
    *,
    usuario: str | None = "cli_csv",
    reprocesar_fallidos: bool | None = None,
    reiniciar_lote: bool = False,
    max_filas: int | None = None,
) -> dict[str, Any]:
    """Radica facturas listadas en CSV contra ERP y SIIFA (misma lógica que el endpoint)."""
    cfg = settings or get_settings()
    service = RadicacionService(cfg)
    resultado = service.ejecutar_desde_csv(
        csv_path,
        tipo_ejecucion="CSV_BATCH",
        usuario=usuario,
        reprocesar_fallidos=reprocesar_fallidos,
        reiniciar_lote=reiniciar_lote,
        max_filas=max_filas,
        un_solo_lote=True,
    )
    logger.info("siifa_radicacion_csv_job_fin resultado=%s", resultado)
    return resultado


def ejecutar_radicacion_siifa_desde_scheduler(
    raw_params: dict[str, Any] | None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Punto de entrada para APScheduler (parametros JSON de orq.scheduler_jobs)."""
    cfg = settings or get_settings()
    params = parse_siifa_scheduler_params(raw_params, cfg)
    logger.info("siifa_scheduler_params %s", params)
    return ejecutar_radicacion_siifa(cfg, **params.to_execution_kwargs())
