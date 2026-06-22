/*
  Dashboard de seguimiento — integración SIIFA radicación.
  Ejecutar en OrquestacionDB (SQL Server).
*/

-- 1) Resumen últimas 10 ejecuciones
SELECT TOP 10
    IdEjecucion,
    TipoEjecucion,
    FechaInicio,
    FechaFin,
    DuracionMs,
    Estado,
    TotalRegistrosSIIFA,
    TotalPaginas,
    Procesadas,
    Radicadas,
    NoEncontradasERP,
    NoRadicadasERP,
    Errores,
    Omitidas,
    Workers,
    Usuario
FROM dbo.SIIFA_IntegracionLog
ORDER BY FechaInicio DESC;

-- 2) Facturas por estado de proceso
SELECT
    EstadoProceso,
    COUNT(*) AS Cantidad,
    MIN(FechaConsulta) AS PrimeraConsulta,
    MAX(FechaConsulta) AS UltimaConsulta
FROM dbo.SIIFA_Factura
GROUP BY EstadoProceso
ORDER BY Cantidad DESC;

-- 3) Radicados exitosos vs fallidos (últimos 7 días)
SELECT
    CAST(FechaRegistro AS DATE) AS Fecha,
    SUM(CASE WHEN Estado = 'EXITOSO' THEN 1 ELSE 0 END) AS Exitosos,
    SUM(CASE WHEN Estado = 'ERROR' THEN 1 ELSE 0 END) AS Errores,
    SUM(CASE WHEN Estado = 'PENDIENTE' THEN 1 ELSE 0 END) AS Pendientes
FROM dbo.SIIFA_Radicado
WHERE FechaRegistro >= DATEADD(DAY, -7, GETDATE())
GROUP BY CAST(FechaRegistro AS DATE)
ORDER BY Fecha DESC;

-- 4) Cola de reintentos pendientes
SELECT
    r.IdReintento,
    r.IdFacturaSIIFA,
    f.NumeroFactura,
    f.NitEmisor,
    r.Motivo,
    r.Intentos,
    r.MaxIntentos,
    r.ProximoIntento,
    r.UltimoError,
    r.FechaCreacion
FROM dbo.SIIFA_Reintento r
INNER JOIN dbo.SIIFA_Factura f ON f.IdFacturaSIIFA = r.IdFacturaSIIFA
WHERE r.Estado = 'PENDIENTE'
  AND (r.ProximoIntento IS NULL OR r.ProximoIntento <= GETDATE())
ORDER BY r.ProximoIntento, r.FechaCreacion;

-- 5) Facturas NO ENCONTRADAS en ERP
SELECT TOP 100
    f.IdFacturaSIIFA,
    f.NumeroFactura,
    f.NitEmisor,
    f.FechaConsulta,
    f.Observacion
FROM dbo.SIIFA_Factura f
WHERE f.EstadoProceso = 'NO_ENCONTRADA_ERP'
ORDER BY f.FechaConsulta DESC;

-- 6) Facturas encontradas pero no radicadas en ERP (estado <> 5)
SELECT TOP 100
    erp.IdRelacion,
    f.IdFacturaSIIFA,
    f.NumeroFactura,
    f.NitEmisor,
    erp.ConsecutivoRips,
    erp.EstadoERP,
    erp.Mensaje,
    erp.FechaRelacion
FROM dbo.SIIFA_FacturaERP erp
INNER JOIN dbo.SIIFA_Factura f ON f.IdFacturaSIIFA = erp.IdFacturaSIIFA
WHERE erp.Resultado = 'NO_RADICADA_ERP'
ORDER BY erp.FechaRelacion DESC;

-- 7) Tasa de éxito por ejecución (último mes)
SELECT
    IdEjecucion,
    FechaInicio,
    Procesadas,
    Radicadas,
    CAST(100.0 * Radicadas / NULLIF(Procesadas, 0) AS DECIMAL(5,2)) AS PctExito,
    Errores,
    NoEncontradasERP
FROM dbo.SIIFA_IntegracionLog
WHERE FechaInicio >= DATEADD(MONTH, -1, GETDATE())
ORDER BY FechaInicio DESC;

-- 8) Traza reciente de errores
SELECT TOP 50
    t.IdTraza,
    t.IdEjecucion,
    t.IdFacturaSIIFA,
    t.NumeroFactura,
    t.Paso,
    t.Resultado,
    t.Mensaje,
    t.Fecha
FROM dbo.SIIFA_FacturaTraza t
WHERE t.Resultado IN ('ERROR', 'NO_ENCONTRADA', 'NO_RADICADA_ERP')
ORDER BY t.Fecha DESC;

-- 9) Checkpoint de lote (reanudación)
SELECT
    Proceso,
    UltimaPaginaProcesada,
    TotalPaginasSiifa,
    TotalRegistrosSiifa,
    LoteCompletado,
    FechaActualizacion,
    IdEjecucionUltima,
    CASE
        WHEN LoteCompletado = 1 THEN 1
        ELSE UltimaPaginaProcesada + 1
    END AS ProximaPagina
FROM dbo.SIIFA_LoteCheckpoint
WHERE Proceso = 'RADICACION';
