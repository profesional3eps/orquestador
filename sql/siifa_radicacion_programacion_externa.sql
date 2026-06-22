/*
  Programación EXTERNA de radicación SIIFA por lotes.

  La radicación ya no se expone vía API; ejecútela con script standalone o CLI:

    export/siifa_radicacion_sin_seguimiento_api.py
    scripts/siifa_radicacion_sync.py

  Checkpoint en dbo.SIIFA_LoteCheckpoint. El orquestador interno (APScheduler)
  NO ejecuta SIIFA; programe el script desde Task Scheduler, cron, etc.
*/
USE [OrquestacionDB];
GO

-- Desactivar job interno si existía
UPDATE orq.scheduler_jobs
SET activo = 0,
    fecha_modificacion = SYSDATETIME()
WHERE nombre_servicio = N'siifa_radicacion_lote';
GO

/*
  ── Flujo (script standalone) ──
  1. python export/siifa_radicacion_sin_seguimiento_api.py --nit-adquiriente <NIT> --hasta-completar
  2. O: python scripts/siifa_radicacion_sync.py (usa RadicacionService vía CLI)

  Variables .env relevantes:
    SIIFA_API_PAGINAS_POR_LOTE=50
    SIIFA_API_REPROCESAR_FALLIDOS=false
    SIIFA_REGISTROS_POR_PAGINA=500
*/
GO

/*
  ── cron (Linux) ──

  0 */2 * * * cd /ruta/ORQUESTADORDB/export && python3 -u siifa_radicacion_sin_seguimiento_api.py \
      --nit-adquiriente 901543761 --hasta-completar --sin-verificar >> /var/log/siifa_radicacion.log 2>&1
*/
GO

-- Monitoreo checkpoint
SELECT TOP 1 *
FROM dbo.SIIFA_LoteCheckpoint
ORDER BY Id DESC;
GO
