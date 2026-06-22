/*
  Permisos mínimos para la integración SIIFA radicación (usuario de aplicación).
  Sustituya [app_orquestador] por el usuario de SQLSERVER_URL.
*/
USE [OrquestacionDB];
GO

DECLARE @user SYSNAME = N'app_orquestador';

GRANT SELECT, INSERT, UPDATE ON dbo.SIIFA_Factura          TO [app_orquestador];
GRANT SELECT, INSERT, UPDATE ON dbo.SIIFA_FacturaERP       TO [app_orquestador];
GRANT SELECT, INSERT, UPDATE ON dbo.SIIFA_Radicado         TO [app_orquestador];
GRANT SELECT, INSERT, UPDATE ON dbo.SIIFA_IntegracionLog   TO [app_orquestador];
GRANT SELECT, INSERT, UPDATE ON dbo.SIIFA_Reintento         TO [app_orquestador];
GRANT SELECT, INSERT, UPDATE ON dbo.SIIFA_FacturaTraza     TO [app_orquestador];
GRANT SELECT, INSERT, UPDATE ON dbo.SIIFA_LoteCheckpoint    TO [app_orquestador];
GO
