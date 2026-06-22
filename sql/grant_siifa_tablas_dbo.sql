/*
  Obsoleto como única fuente: use el script consolidado en la raíz del repo TIC:
  OrquestacionDB_siifa_app_post_deploy.sql (permisos seg + orq + dbo SIIFA + seed módulo).

  Permisos mínimos solo tablas SIIFA (ORQUESTADORDB):
  la API lee/inserta/actualiza dbo.terceros, dbo.factura y dbo.factura_tercero.

  Error típico sin estos permisos:
  "The SELECT permission was denied on the object 'terceros' ... schema 'dbo'"

  Uso:
  1) Conéctese a la base OrquestacionDB con una cuenta que pueda conceder permisos (p. ej. sa / dbo).
  2) Sustituya [UsuarioApp] por el USUARIO DE BASE DE DATOS de la aplicación (el de SQLSERVER_URL,
     por ejemplo el mapeado desde el login app_orquestador).
  3) Ejecute el bloque GRANT.

  El nombre debe ser el del usuario en la base actual (sys.database_principals), no obligatoriamente el login.
*/

USE [OrquestacionDB];
GO

-- Compruebe el nombre exacto:
-- SELECT name, type_desc FROM sys.database_principals WHERE type IN ('S','U') ORDER BY name;

GRANT SELECT, INSERT, UPDATE ON OBJECT::dbo.terceros        TO [UsuarioApp];
GRANT SELECT, INSERT, UPDATE ON OBJECT::dbo.factura         TO [UsuarioApp];
GRANT SELECT, INSERT, UPDATE ON OBJECT::dbo.factura_tercero TO [UsuarioApp];
GO

/*
  Esquema recomendado: dbo.factura.id_factura BIGINT PK **sin** IDENTITY
  (el id viene de SIIFA). Si la columna quedó como IDENTITY, hace falta
  permiso ALTER (o dbo) para SET IDENTITY_INSERT; mejor quitar IDENTITY
  del diseño o recrear la columna según política del DBA.
*/
