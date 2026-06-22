"""Job CLI: backfill ERP desde CSV SIIFA con seguimiento."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config.settings import Settings, get_settings
from app.services.seguimiento_erp_service import SeguimientoErpService

logger = logging.getLogger(__name__)


def ejecutar_seguimiento_erp_desde_csv(
    csv_path: Path | str,
    settings: Settings | None = None,
    *,
    fecha_rad_siifa: str | date | datetime = "2026-04-10",
    usuario: str | None = "cli_seguimiento_erp",
    reiniciar_lote: bool = False,
    max_filas: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    service = SeguimientoErpService(cfg)
    resultado = service.ejecutar_desde_csv(
        csv_path,
        fecha_rad_siifa=fecha_rad_siifa,
        usuario=usuario,
        reiniciar_lote=reiniciar_lote,
        max_filas=max_filas,
        dry_run=dry_run,
    )
    logger.info("siifa_seguimiento_erp_job_fin resultado=%s", resultado)
    return resultado
