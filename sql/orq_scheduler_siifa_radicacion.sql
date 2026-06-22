/*
  DEPRECADO: SIIFA ya no se programa en orq.scheduler_jobs.

  Use programación externa del endpoint HTTP:
    sql/siifa_radicacion_programacion_externa.sql
*/
USE [OrquestacionDB];
GO

UPDATE orq.scheduler_jobs
SET activo = 0,
    fecha_modificacion = SYSDATETIME()
WHERE nombre_servicio = N'siifa_radicacion_lote';

PRINT 'Job interno siifa_radicacion_lote desactivado. Use script externo (sql/siifa_radicacion_programacion_externa.sql).';
GO
