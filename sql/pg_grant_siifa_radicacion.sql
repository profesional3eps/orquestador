/*
  Permisos PostgreSQL (ERP Messiah) para radicación SIIFA.
  Ejecutar en: base_sie_comfasucre (o la base ERP configurada).

  Sustituya app_orquestador por el usuario que usa el orquestador para conectar al ERP.
  Si el usuario ya es superuser o tiene permisos amplios, este script es opcional.
*/

-- Usuario de la aplicación (ajustar)
-- CREATE USER app_orquestador WITH PASSWORD '...';

GRANT USAGE ON SCHEMA administrativo TO app_orquestador;

GRANT SELECT ON administrativo.rips_af TO app_orquestador;
GRANT SELECT ON administrativo.rips_resumen TO app_orquestador;
GRANT UPDATE (
    radicado_siifa,
    fecha_rad_siifa,
    idfactura_siifa
) ON administrativo.rips_af TO app_orquestador;

-- Verificación
SELECT has_table_privilege('app_orquestador', 'administrativo.rips_af', 'SELECT') AS puede_select_af,
       has_table_privilege('app_orquestador', 'administrativo.rips_af', 'UPDATE') AS puede_update_af,
       has_table_privilege('app_orquestador', 'administrativo.rips_resumen', 'SELECT') AS puede_select_resumen;
