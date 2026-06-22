/*
  Parametrización manual post-migración — OrquestacionDB
  Ejecutar DESPUÉS de:
    1. sql/cfg_configuracion_dinamica.sql
    2. sql/grant_cfg_configuracion.sql
    3. python scripts/cfg_migrate_from_env.py

  IMPORTANTE — contraseñas PostgreSQL:
    python scripts/cfg_encrypt_password.py "su_password_postgres"
    → copie el hex 0x... resultante en los UPDATE de abajo.
*/
USE [OrquestacionDB];
GO

/* ═══════════════════════════════════════════════════════════════
   PASO A — Verificar que la migración cargó correctamente
   ═══════════════════════════════════════════════════════════════ */
SELECT * FROM cfg.CFG_Ambiente ORDER BY IdAmbiente;
SELECT IdBaseDatos, IdAmbiente, NombreConexion, Motor, Host, Puerto, BaseDatos, Usuario, Activa
FROM cfg.CFG_BaseDatos ORDER BY IdAmbiente, NombreConexion;
SELECT * FROM cfg.CFG_Endpoint ORDER BY IdEndpoint;
SELECT e.Nombre AS Endpoint, a.Nombre AS Ambiente, bd.NombreConexion, bd.Host, bd.BaseDatos
FROM cfg.CFG_EndpointBaseDatos r
JOIN cfg.CFG_Endpoint e ON e.IdEndpoint = r.IdEndpoint
JOIN cfg.CFG_BaseDatos bd ON bd.IdBaseDatos = r.IdBaseDatos
JOIN cfg.CFG_Ambiente a ON a.IdAmbiente = bd.IdAmbiente
ORDER BY e.Nombre, a.Nombre;
SELECT Nombre, LEFT(Valor, 80) AS Valor, Descripcion FROM cfg.CFG_Parametro ORDER BY Nombre;
GO

/* ═══════════════════════════════════════════════════════════════
   PASO B — Ajustar PostgreSQL por ambiente
   (sustituya host, puerto, base, usuario y PasswordEncriptado)
   ═══════════════════════════════════════════════════════════════ */

-- Desarrollo
UPDATE cfg.CFG_BaseDatos SET
    Host = '10.0.1.XXX',
    Puerto = 5432,
    BaseDatos = 'base_sie_comfasucre_dev',
    Usuario = 'postgres',
    PasswordEncriptado = 0x_REEMPLACE_CON_HEX_DE_cfg_encrypt_password_
WHERE NombreConexion = 'PostgreSQL Desarrollo';

-- QA
UPDATE cfg.CFG_BaseDatos SET
    Host = '10.0.1.XXX',
    Puerto = 5432,
    BaseDatos = 'base_sie_comfasucre_qa',
    Usuario = 'postgres',
    PasswordEncriptado = 0x_REEMPLACE_CON_HEX_DE_cfg_encrypt_password_
WHERE NombreConexion = 'PostgreSQL QA';

-- Producción
UPDATE cfg.CFG_BaseDatos SET
    Host = '10.0.1.102',
    Puerto = 5432,
    BaseDatos = 'base_sie_comfasucre',
    Usuario = 'postgres',
    PasswordEncriptado = 0x_REEMPLACE_CON_HEX_DE_cfg_encrypt_password_
WHERE NombreConexion = 'PostgreSQL Produccion';
GO

/* ═══════════════════════════════════════════════════════════════
   PASO C — Ambiente activo de la instancia
   Valor = nombre (Desarrollo|QA|Produccion) o IdAmbiente (1|2|3)
   ═══════════════════════════════════════════════════════════════ */
UPDATE cfg.CFG_Parametro SET Valor = 'Produccion' WHERE Nombre = 'AMBIENTE_ACTIVO';
-- UPDATE cfg.CFG_Parametro SET Valor = '2' WHERE Nombre = 'AMBIENTE_ACTIVO';  -- QA
-- UPDATE cfg.CFG_Parametro SET Valor = '1' WHERE Nombre = 'AMBIENTE_ACTIVO';  -- Desarrollo
GO

/* ═══════════════════════════════════════════════════════════════
   PASO D — Parámetros operativos (ejemplos frecuentes)
   ═══════════════════════════════════════════════════════════════ */
UPDATE cfg.CFG_Parametro SET Valor = 'INFO'                    WHERE Nombre = 'LOG_LEVEL';
UPDATE cfg.CFG_Parametro SET Valor = '60'                      WHERE Nombre = 'JOB_RELOAD_INTERVAL_SECONDS';
UPDATE cfg.CFG_Parametro SET Valor = '300'                     WHERE Nombre = 'CONFIG_CACHE_TTL_SECONDS';
UPDATE cfg.CFG_Parametro SET Valor = '10'                      WHERE Nombre = 'SIIFA_WORKERS';
UPDATE cfg.CFG_Parametro SET Valor = '100'                     WHERE Nombre = 'SIIFA_REGISTROS_POR_PAGINA';
UPDATE cfg.CFG_Parametro SET Valor = '5'                       WHERE Nombre = 'SIIFA_RETRY_MAX_ATTEMPTS';
UPDATE cfg.CFG_Parametro SET Valor = 'true'                    WHERE Nombre = 'SIIFA_REPROCESAR_FALLIDOS';
UPDATE cfg.CFG_Parametro SET Valor = '/execute/facturas_decimales'
    WHERE Nombre = 'SWAGGER_HIDDEN_PATHS';
GO

/* Parámetro que NO existe tras migración: insertar si falta */
IF NOT EXISTS (SELECT 1 FROM cfg.CFG_Parametro WHERE Nombre = 'MESSIAH_SFTP_HOST')
    INSERT INTO cfg.CFG_Parametro (Nombre, Valor, Descripcion)
    VALUES ('MESSIAH_SFTP_HOST', '10.0.1.129', 'Host SFTP Messiah');
GO

/* ═══════════════════════════════════════════════════════════════
   PASO E — Registrar endpoint admin para recargar config sin reiniciar
   (ajuste id_usuario según orq.usuarios)
   ═══════════════════════════════════════════════════════════════ */
/*
INSERT INTO seg.usuario_endpoints (id_usuario, metodo_http, endpoint, permitido, activo)
SELECT u.id, 'POST', '/admin/config/reload', 1, 1
FROM orq.usuarios u
WHERE u.username = 'admin_orquestador'
  AND NOT EXISTS (
      SELECT 1 FROM seg.usuario_endpoints ue
      WHERE ue.id_usuario = u.id AND ue.metodo_http = 'POST' AND ue.endpoint = '/admin/config/reload'
  );
*/
GO

/* ═══════════════════════════════════════════════════════════════
   PASO F — Consultar auditoría de cambios
   ═══════════════════════════════════════════════════════════════ */
SELECT TOP 50 IdHistorial, TablaAfectada, Registro, Usuario, FechaCambio
FROM cfg.CFG_HistorialConfiguracion
ORDER BY IdHistorial DESC;
GO
