/*
  Seed SIIFA radicación — ajustado a OrquestacionDB en 150.136.57.32

  Hallazgos de auditoría:
    - Perfil operativo: ADMIN (id_perfil=1)
    - Módulo existente: SIIFA facturas (id_modulo=4)
    - Acción: EJECUTAR (id_accion=6)
    - Usuario admin tiene tipo=1 (bypass permisos y endpoints)
    - seg.usuario_perfil está vacío (se vincula admin por consistencia)

  Ejecutar DESPUÉS de siifa_integracion_tablas.sql y grant_siifa_integracion.sql
*/
USE [OrquestacionDB];
GO

/* ── 1. Permiso ADMIN → SIIFA facturas → EJECUTAR ── */
IF NOT EXISTS (
    SELECT 1 FROM seg.permisos
    WHERE id_perfil = 1 AND id_modulo = 4 AND id_accion = 6
)
    INSERT INTO seg.permisos (id_perfil, id_modulo, id_accion, permitido)
    VALUES (1, 4, 6, 1);
GO

/* ── 2. Vincular usuario admin al perfil ADMIN ── */
IF NOT EXISTS (
    SELECT 1 FROM seg.usuario_perfil WHERE id_usuario = 1 AND id_perfil = 1
)
    INSERT INTO seg.usuario_perfil (id_usuario, id_perfil)
    VALUES (1, 1);
GO

PRINT 'Seed SIIFA completado (perfil ADMIN, usuario admin). Radicación vía script externo, no API.';
GO
