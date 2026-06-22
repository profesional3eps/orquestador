/* Edite hosts/bases abajo. Contraseñas: docker compose --profile tools run --rm cfg-encrypt-to-sql --password "..." --all-ambientes */
USE [OrquestacionDB];
GO

/* ═══ Ambientes ═══ */
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'Desarrollo')
    INSERT INTO cfg.CFG_Ambiente (Nombre, Activo) VALUES (N'Desarrollo', 1);
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'QA')
    INSERT INTO cfg.CFG_Ambiente (Nombre, Activo) VALUES (N'QA', 1);
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'Produccion')
    INSERT INTO cfg.CFG_Ambiente (Nombre, Activo) VALUES (N'Produccion', 1);
GO

/* ═══ PostgreSQL por ambiente (credenciales en SQL Server) ═══ */
DECLARE @PgHostDev     VARCHAR(255) = '10.0.1.102';
DECLARE @PgHostQa      VARCHAR(255) = '10.0.1.102';
DECLARE @PgHostProd    VARCHAR(255) = '10.0.1.102';
DECLARE @PgPuerto      INT          = 5432;
DECLARE @PgUser        VARCHAR(128) = 'postgres';
DECLARE @PgBaseDev     VARCHAR(128) = 'base_sie_comfasucre_dev';
DECLARE @PgBaseQa      VARCHAR(128) = 'base_sie_comfasucre_qa';
DECLARE @PgBaseProd    VARCHAR(128) = 'base_sie_comfasucre';
DECLARE @PgPwdDev      VARBINARY(MAX) = 0x00;
DECLARE @PgPwdQa       VARBINARY(MAX) = 0x00;
DECLARE @PgPwdProd     VARBINARY(MAX) = 0x00;

IF NOT EXISTS (SELECT 1 FROM cfg.CFG_BaseDatos bd INNER JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente WHERE a.Nombre = N'Desarrollo' AND bd.NombreConexion = N'PostgreSQL Desarrollo')
    INSERT INTO cfg.CFG_BaseDatos (IdAmbiente, NombreConexion, Motor, Host, Puerto, BaseDatos, Usuario, PasswordEncriptado, Activa)
    SELECT IdAmbiente, N'PostgreSQL Desarrollo', N'POSTGRESQL', @PgHostDev, @PgPuerto, @PgBaseDev, @PgUser, @PgPwdDev, 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'Desarrollo';

IF NOT EXISTS (SELECT 1 FROM cfg.CFG_BaseDatos bd INNER JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente WHERE a.Nombre = N'QA' AND bd.NombreConexion = N'PostgreSQL QA')
    INSERT INTO cfg.CFG_BaseDatos (IdAmbiente, NombreConexion, Motor, Host, Puerto, BaseDatos, Usuario, PasswordEncriptado, Activa)
    SELECT IdAmbiente, N'PostgreSQL QA', N'POSTGRESQL', @PgHostQa, @PgPuerto, @PgBaseQa, @PgUser, @PgPwdQa, 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'QA';

IF NOT EXISTS (SELECT 1 FROM cfg.CFG_BaseDatos bd INNER JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente WHERE a.Nombre = N'Produccion' AND bd.NombreConexion = N'PostgreSQL Produccion')
    INSERT INTO cfg.CFG_BaseDatos (IdAmbiente, NombreConexion, Motor, Host, Puerto, BaseDatos, Usuario, PasswordEncriptado, Activa)
    SELECT IdAmbiente, N'PostgreSQL Produccion', N'POSTGRESQL', @PgHostProd, @PgPuerto, @PgBaseProd, @PgUser, @PgPwdProd, 1 FROM cfg.CFG_Ambiente WHERE Nombre = N'Produccion';
GO

/* ═══ Endpoints ═══ */
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Endpoint WHERE Nombre = N'AuthLogin')
    INSERT INTO cfg.CFG_Endpoint (Nombre, Metodo, Url, Modulo, Activo) VALUES (N'AuthLogin', N'POST', N'/api/Auth/login', N'SIIFA', 1);
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Endpoint WHERE Nombre = N'ConsultarFacturas')
    INSERT INTO cfg.CFG_Endpoint (Nombre, Metodo, Url, Modulo, Activo) VALUES (N'ConsultarFacturas', N'GET', N'/api/Factura', N'SIIFA', 1);
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Endpoint WHERE Nombre = N'FacturaRadicado')
    INSERT INTO cfg.CFG_Endpoint (Nombre, Metodo, Url, Modulo, Activo) VALUES (N'FacturaRadicado', N'POST', N'/api/FacturaRadicado', N'SIIFA', 1);
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Endpoint WHERE Nombre = N'ErpDefault')
    INSERT INTO cfg.CFG_Endpoint (Nombre, Metodo, Url, Modulo, Activo) VALUES (N'ErpDefault', N'POST', N'/consultas/afiliado', N'ERP', 1);
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Endpoint WHERE Nombre = N'ErpConsultas')
    INSERT INTO cfg.CFG_Endpoint (Nombre, Metodo, Url, Modulo, Activo) VALUES (N'ErpConsultas', N'POST', N'/consultas/portabilidad', N'ERP', 1);
GO

/* ═══ Endpoint → Base de datos ═══ */
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r INNER JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = N'ConsultarFacturas')
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e
    CROSS JOIN cfg.CFG_BaseDatos bd
    INNER JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = N'ConsultarFacturas' AND a.Nombre = N'Produccion' AND bd.NombreConexion = N'PostgreSQL Produccion';

IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r INNER JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = N'FacturaRadicado')
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e
    CROSS JOIN cfg.CFG_BaseDatos bd
    INNER JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = N'FacturaRadicado' AND a.Nombre = N'Produccion' AND bd.NombreConexion = N'PostgreSQL Produccion';

IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r INNER JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = N'PruebasMasivas')
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e
    CROSS JOIN cfg.CFG_BaseDatos bd
    INNER JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = N'PruebasMasivas' AND a.Nombre = N'QA' AND bd.NombreConexion = N'PostgreSQL QA';

IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r INNER JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = N'ErpDefault')
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e
    CROSS JOIN cfg.CFG_BaseDatos bd
    INNER JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = N'ErpDefault' AND a.Nombre = N'Produccion' AND bd.NombreConexion = N'PostgreSQL Produccion';

IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r INNER JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = N'ErpConsultas')
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e
    CROSS JOIN cfg.CFG_BaseDatos bd
    INNER JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = N'ErpConsultas' AND a.Nombre = N'Produccion' AND bd.NombreConexion = N'PostgreSQL Produccion';

IF NOT EXISTS (SELECT 1 FROM cfg.CFG_EndpointBaseDatos r INNER JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint WHERE e.Nombre = N'ErpSiifaRadicacion')
    INSERT INTO cfg.CFG_EndpointBaseDatos (IdEndpoint, IdBaseDatos)
    SELECT e.IdEndpoint, bd.IdBaseDatos FROM cfg.CFG_Endpoint e
    CROSS JOIN cfg.CFG_BaseDatos bd
    INNER JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
    WHERE e.Nombre = N'ErpSiifaRadicacion' AND a.Nombre = N'Produccion' AND bd.NombreConexion = N'PostgreSQL Produccion';
GO

/* ═══ Parámetros base (operación / Docker) ═══ */
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Parametro WHERE Nombre = N'AMBIENTE_ACTIVO')
    INSERT INTO cfg.CFG_Parametro (Nombre, Valor, Descripcion) VALUES (N'AMBIENTE_ACTIVO', N'Produccion', N'Ambiente activo (nombre o IdAmbiente)');
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Parametro WHERE Nombre = N'CONFIG_CACHE_TTL_SECONDS')
    INSERT INTO cfg.CFG_Parametro (Nombre, Valor, Descripcion) VALUES (N'CONFIG_CACHE_TTL_SECONDS', N'300', N'TTL caché configuración');
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Parametro WHERE Nombre = N'LOG_LEVEL')
    INSERT INTO cfg.CFG_Parametro (Nombre, Valor, Descripcion) VALUES (N'LOG_LEVEL', N'INFO', N'Nivel de log');
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Parametro WHERE Nombre = N'LIBREOFFICE_SOFFICE_PATH')
    INSERT INTO cfg.CFG_Parametro (Nombre, Valor, Descripcion) VALUES (N'LIBREOFFICE_SOFFICE_PATH', N'/usr/bin/soffice', N'LibreOffice en contenedor Docker');
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Parametro WHERE Nombre = N'SIIFA_SEGURIDAD_BASE_URL')
    INSERT INTO cfg.CFG_Parametro (Nombre, Valor, Descripcion) VALUES (N'SIIFA_SEGURIDAD_BASE_URL', N'https://siifa.sispro.gov.co/siifa-seguridad', N'URL SIIFA seguridad');
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Parametro WHERE Nombre = N'SIIFA_FACTURA_BASE_URL')
    INSERT INTO cfg.CFG_Parametro (Nombre, Valor, Descripcion) VALUES (N'SIIFA_FACTURA_BASE_URL', N'https://siifa.sispro.gov.co/siifa-factura', N'URL SIIFA factura');
GO

PRINT 'Seed cfg.* completado. Verifique PasswordEncriptado <> 0x00 y ejecute parametrizar.ps1 para JWT/Messiah desde .env.migration.';
GO

/* Verificación */
SELECT a.Nombre AS Ambiente, bd.NombreConexion, bd.Host, bd.BaseDatos, bd.Usuario,
       CASE WHEN bd.PasswordEncriptado = 0x00 THEN '*** PENDIENTE ***' ELSE 'OK' END AS Password
FROM cfg.CFG_BaseDatos bd
JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
ORDER BY a.IdAmbiente;
GO
