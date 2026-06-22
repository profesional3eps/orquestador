/*
  Configuración COMFASUCRE — PostgreSQL + mapeo endpoint → ambiente
  Ejecutar DESPUÉS de cfg_configuracion_dinamica.sql y grant_cfg_configuracion.sql

  1. Genere CONFIG_ENCRYPTION_KEY (.env) con parametrizar.ps1
  2. docker compose --profile tools run --rm cfg-encrypt-to-sql --password "SU_PASSWORD" --all-ambientes
  3. Pegue los hex en @PgPwdDev / @PgPwdQa / @PgPwdProd
  4. Ajuste nombres de base Dev/QA si son distintos
*/
USE [OrquestacionDB];
GO

DECLARE @PgHostDev     VARCHAR(255) = '10.0.1.102';
DECLARE @PgHostQa      VARCHAR(255) = '10.0.1.102';
DECLARE @PgHostProd    VARCHAR(255) = '10.0.1.102';
DECLARE @PgPuerto      INT          = 5432;
DECLARE @PgUser        VARCHAR(128) = 'postgres';
DECLARE @PgBaseDev     VARCHAR(128) = 'base_sie_comfasucre';  -- ajustar si tiene BD dev separada
DECLARE @PgBaseQa      VARCHAR(128) = 'base_sie_comfasucre';  -- ajustar si tiene BD QA separada
DECLARE @PgBaseProd    VARCHAR(128) = 'base_sie_comfasucre';
DECLARE @PgPwdDev      VARBINARY(MAX) = 0x00;  -- pegar hex cfg-encrypt-to-sql
DECLARE @PgPwdQa       VARBINARY(MAX) = 0x00;
DECLARE @PgPwdProd     VARBINARY(MAX) = 0x00;

-- Ambientes
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'Desarrollo')
    INSERT INTO cfg.CFG_Ambiente (Nombre, Activo) VALUES (N'Desarrollo', 1);
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'QA')
    INSERT INTO cfg.CFG_Ambiente (Nombre, Activo) VALUES (N'QA', 1);
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'Produccion')
    INSERT INTO cfg.CFG_Ambiente (Nombre, Activo) VALUES (N'Produccion', 1);

-- PostgreSQL por ambiente
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_BaseDatos bd JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente WHERE a.Nombre = N'Desarrollo' AND bd.NombreConexion = N'PostgreSQL Desarrollo')
    INSERT INTO cfg.CFG_BaseDatos (IdAmbiente, NombreConexion, Motor, Host, Puerto, BaseDatos, Usuario, PasswordEncriptado, Activa)
    SELECT IdAmbiente, N'PostgreSQL Desarrollo', N'POSTGRESQL', @PgHostDev, @PgPuerto, @PgBaseDev, @PgUser, @PgPwdDev, 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'Desarrollo';

IF NOT EXISTS (SELECT 1 FROM cfg.CFG_BaseDatos bd JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente WHERE a.Nombre = N'QA' AND bd.NombreConexion = N'PostgreSQL QA')
    INSERT INTO cfg.CFG_BaseDatos (IdAmbiente, NombreConexion, Motor, Host, Puerto, BaseDatos, Usuario, PasswordEncriptado, Activa)
    SELECT IdAmbiente, N'PostgreSQL QA', N'POSTGRESQL', @PgHostQa, @PgPuerto, @PgBaseQa, @PgUser, @PgPwdQa, 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'QA';

IF NOT EXISTS (SELECT 1 FROM cfg.CFG_BaseDatos bd JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente WHERE a.Nombre = N'Produccion' AND bd.NombreConexion = N'PostgreSQL Produccion')
    INSERT INTO cfg.CFG_BaseDatos (IdAmbiente, NombreConexion, Motor, Host, Puerto, BaseDatos, Usuario, PasswordEncriptado, Activa)
    SELECT IdAmbiente, N'PostgreSQL Produccion', N'POSTGRESQL', @PgHostProd, @PgPuerto, @PgBaseProd, @PgUser, @PgPwdProd, 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'Produccion';
GO

/*
  Mapeo endpoint → PostgreSQL (cada uno con ambiente fijo)
  ┌─────────────────────┬─────────────┬──────────────────────────┐
  │ Endpoint            │ Ambiente    │ Uso                      │
  ├─────────────────────┼─────────────┼──────────────────────────┤
  │ ConsultarFacturas   │ Produccion  │ GET facturas SIIFA       │
  │ FacturaRadicado     │ Produccion  │ POST radicado            │
  │ ErpDefault          │ Produccion  │ Consultas afiliado ERP   │
  │ ErpConsultas        │ Produccion  │ Portabilidad, PQR        │
  │ ErpSiifaRadicacion  │ Produccion  │ Radicación batch SIIFA   │
  │ PruebasMasivas      │ QA          │ Pruebas integración      │
  └─────────────────────┴─────────────┴──────────────────────────┘
*/
-- Endpoints (si no existen)
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Endpoint WHERE Nombre = N'ConsultarFacturas')
    INSERT INTO cfg.CFG_Endpoint (Nombre, Metodo, Url, Modulo, Activo) VALUES (N'ConsultarFacturas', N'GET', N'/api/Factura', N'SIIFA', 1);
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Endpoint WHERE Nombre = N'FacturaRadicado')
    INSERT INTO cfg.CFG_Endpoint (Nombre, Metodo, Url, Modulo, Activo) VALUES (N'FacturaRadicado', N'POST', N'/api/FacturaRadicado', N'SIIFA', 1);
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Endpoint WHERE Nombre = N'ErpDefault')
    INSERT INTO cfg.CFG_Endpoint (Nombre, Metodo, Url, Modulo, Activo) VALUES (N'ErpDefault', N'POST', N'/consultas/afiliado', N'ERP', 1);
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Endpoint WHERE Nombre = N'ErpConsultas')
    INSERT INTO cfg.CFG_Endpoint (Nombre, Metodo, Url, Modulo, Activo) VALUES (N'ErpConsultas', N'POST', N'/consultas/portabilidad', N'ERP', 1);
GO

-- Helper: reemplazar mapeo de un endpoint
-- DELETE FROM cfg.CFG_EndpointBaseDatos WHERE IdEndpoint = (SELECT IdEndpoint FROM cfg.CFG_Endpoint WHERE Nombre = N'PruebasMasivas');

DECLARE @Ep NVARCHAR(100), @Amb NVARCHAR(50), @Conn NVARCHAR(100);

-- Produccion
SET @Ep = N'ConsultarFacturas';   SET @Amb = N'Produccion'; SET @Conn = N'PostgreSQL Produccion';
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = @Ep)
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e CROSS JOIN cfg.CFG_BaseDatos bd
    JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = @Ep AND a.Nombre = @Amb AND bd.NombreConexion = @Conn;

SET @Ep = N'FacturaRadicado';
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = @Ep)
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e CROSS JOIN cfg.CFG_BaseDatos bd
    JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = @Ep AND a.Nombre = @Amb AND bd.NombreConexion = @Conn;

SET @Ep = N'ErpDefault';
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = @Ep)
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e CROSS JOIN cfg.CFG_BaseDatos bd
    JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = @Ep AND a.Nombre = @Amb AND bd.NombreConexion = @Conn;

SET @Ep = N'ErpConsultas';
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = @Ep)
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e CROSS JOIN cfg.CFG_BaseDatos bd
    JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = @Ep AND a.Nombre = @Amb AND bd.NombreConexion = @Conn;

SET @Ep = N'ErpSiifaRadicacion';
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = @Ep)
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e CROSS JOIN cfg.CFG_BaseDatos bd
    JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = @Ep AND a.Nombre = @Amb AND bd.NombreConexion = @Conn;

-- QA (Pruebas)
SET @Ep = N'PruebasMasivas'; SET @Amb = N'QA'; SET @Conn = N'PostgreSQL QA';
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = @Ep)
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e CROSS JOIN cfg.CFG_BaseDatos bd
    JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = @Ep AND a.Nombre = @Amb AND bd.NombreConexion = @Conn;
GO

IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Parametro WHERE Nombre = N'AMBIENTE_ACTIVO')
    INSERT INTO cfg.CFG_Parametro (Nombre, Valor, Descripcion) VALUES (N'AMBIENTE_ACTIVO', N'Produccion', N'Ambiente por defecto');
GO

SELECT e.Nombre AS Endpoint, a.Nombre AS AmbientePostgreSQL, bd.Host, bd.BaseDatos
FROM cfg.CFG_EndpointBaseDatos r
JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint
JOIN cfg.CFG_BaseDatos bd ON bd.IdBaseDatos = r.IdBaseDatos
JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
ORDER BY e.Nombre;
GO
