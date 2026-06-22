/*
  Verificación post-implementación SIIFA radicación.
  Ejecutar en OrquestacionDB (SQL Server).
*/
USE [OrquestacionDB];
GO

PRINT '=== Tablas SIIFA ===';
SELECT name AS Tabla
FROM sys.tables
WHERE name LIKE 'SIIFA_%'
ORDER BY name;
GO

PRINT '=== Columnas clave SIIFA_Factura ===';
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'SIIFA_Factura'
  AND COLUMN_NAME IN ('EstadoProceso', 'PaginaOrigen', 'IdEjecucion')
ORDER BY COLUMN_NAME;
GO

PRINT '=== Permisos app_orquestador sobre tablas SIIFA ===';
SELECT
    OBJECT_NAME(p.major_id) AS Tabla,
    p.permission_name,
    p.state_desc
FROM sys.database_permissions p
JOIN sys.database_principals dp ON p.grantee_principal_id = dp.principal_id
WHERE dp.name = 'app_orquestador'
  AND OBJECT_NAME(p.major_id) LIKE 'SIIFA_%'
ORDER BY Tabla, permission_name;
GO

PRINT '=== Módulo y permiso API ===';
SELECT m.nombre AS Modulo, a.nombre AS Accion, p.permitido, pf.nombre AS Perfil
FROM seg.permisos p
JOIN seg.modulos m ON m.id_modulo = p.id_modulo
JOIN seg.acciones a ON a.id_accion = p.id_accion
JOIN seg.perfiles pf ON pf.id_perfil = p.id_perfil
WHERE m.nombre = 'SIIFA facturas';
GO

PRINT '=== Parámetros SIIFA en cfg ===';
IF OBJECT_ID('cfg.CFG_Parametro') IS NOT NULL
    SELECT Nombre, Valor FROM cfg.CFG_Parametro WHERE Nombre LIKE 'SIIFA_%' ORDER BY Nombre;
ELSE
    PRINT 'Esquema cfg no desplegado — parámetros SIIFA pueden estar solo en .env';
GO

PRINT '=== Conexión ERP (PostgreSQL) en cfg ===';
IF OBJECT_ID('cfg.CFG_EndpointBaseDatos') IS NOT NULL
BEGIN
    SELECT e.Nombre AS Endpoint, a.Nombre AS Ambiente, bd.NombreConexion, bd.Motor, bd.Host, bd.BaseDatos
    FROM cfg.CFG_Endpoint e
    INNER JOIN cfg.CFG_EndpointBaseDatos r ON r.IdEndpoint = e.IdEndpoint
    INNER JOIN cfg.CFG_BaseDatos bd ON bd.IdBaseDatos = r.IdBaseDatos
    INNER JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = 'ErpSiifaRadicacion';
END
GO
