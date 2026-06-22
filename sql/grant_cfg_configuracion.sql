/*
  Permisos mínimos para el motor de configuración dinámica (usuario de aplicación).
  Sustituya [app_orquestador] por el usuario de SQLSERVER_URL.
*/
USE [OrquestacionDB];
GO

GRANT SELECT ON SCHEMA::cfg TO [app_orquestador];
GO

/* La aplicación no debe modificar configuración en runtime; solo lectura.
   Para cambios administrativos use un rol separado (cfg_admin). */
GO
