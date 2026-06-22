from decimal import Decimal
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.zona_horaria import ahora_bogota

from app.models.dto import Service1ResultDTO

AFILIADO_RESUMEN_POR_DOCUMENTO = """
SELECT
    a.afiliado,
    a.estado_afiliado,
    TRIM(
        CONCAT_WS(
            ' ',
            NULLIF(TRIM(a.primer_nombre), ''),
            NULLIF(TRIM(COALESCE(a.segundo_nombre, '')), ''),
            NULLIF(TRIM(a.primer_apellido), ''),
            NULLIF(TRIM(COALESCE(a.segundo_apellido, '')), '')
        )
    ) AS nombre_completo
FROM administrativo.af_afiliado a
WHERE CAST(a.tipo_identificacion AS TEXT) = :tipo_doc
  AND TRIM(CAST(a.numero_identificacion AS TEXT)) = TRIM(:numero_doc)
LIMIT 1
"""

AFILIADO_DATOS_CERTIFICADO = """
SELECT
    a.afiliado,
    a.tipo_identificacion,
    a.numero_identificacion,
    a.primer_nombre,
    COALESCE(NULLIF(TRIM(COALESCE(a.segundo_nombre::text, '')), ''), '') AS segundo_nombre,
    a.primer_apellido,
    COALESCE(NULLIF(TRIM(COALESCE(a.segundo_apellido::text, '')), ''), '') AS segundo_apellido,
    a.estado_afiliado,
    CASE
        WHEN a.estado_afiliado = 1 THEN 'Activo'
        WHEN a.estado_afiliado = 2 THEN 'Retirado'
        WHEN a.estado_afiliado = 3 THEN 'Fallecido'
        WHEN a.estado_afiliado = 4 THEN 'Suspendido'
        WHEN a.estado_afiliado = 5 THEN 'Activo Carnetizado'
        WHEN a.estado_afiliado = 6 THEN 'Retirado Anulado'
        ELSE COALESCE(a.estado_afiliado::text, '')
    END AS nombre_estado_afiliado,
    a.fecha_afilia,
    a.fecha_afiliacion_entidad,
    a.fecha_afiliacion_inicial,
    a.fecha_afiliacion_sgsss,
    a.fecha_retiro,
    TRIM(
        CONCAT_WS(
            ' ',
            NULLIF(TRIM(a.primer_nombre), ''),
            NULLIF(TRIM(COALESCE(a.segundo_nombre::text, '')), ''),
            NULLIF(TRIM(a.primer_apellido), ''),
            NULLIF(TRIM(COALESCE(a.segundo_apellido::text, '')), '')
        )
    ) AS nombre_completo,
    CASE
        WHEN a.tipo_afiliado = 1 THEN 'Cotizante'
        WHEN a.tipo_afiliado = 2 THEN 'Beneficiario'
        WHEN a.tipo_afiliado = 3 THEN 'Cabeza de familia'
        ELSE COALESCE(a.tipo_afiliado::text, '')
    END AS tipo_afiliado_texto,
    (
        SELECT CASE
            WHEN ac.tipo_regimen = 1 THEN 'Contributivo'
            WHEN ac.tipo_regimen = 99 THEN 'Subsidiado'
            ELSE NULL
        END
        FROM administrativo.af_afiliado_complemento ac
        WHERE ac.afiliado = a.afiliado
        LIMIT 1
    ) AS des_tipo_reg
    ,
    COALESCE(i.razon_social, '') AS ips_primaria,
    COALESCE(ips_od.razon_social, '') AS ips_odontologica
FROM administrativo.af_afiliado a
LEFT JOIN administrativo.ct_ips i ON i.ips = a.ips
LEFT JOIN administrativo.tb_zonificacion_ips_encabezado z
    ON a.consecutivo_zonificacion_odontologia = z.consecutivo_zonificacion
LEFT JOIN administrativo.ct_ips ips_od ON z.consecutivo_ips = ips_od.ips
WHERE CAST(a.tipo_identificacion AS TEXT) = :tipo_doc
  AND TRIM(CAST(a.numero_identificacion AS TEXT)) = TRIM(:numero_doc)
LIMIT 1
"""

PORTABILIDADES_POR_AFILIADO = """
SELECT
    m.consecutivo_movilidad,
    m.estado AS estado_portabilidad_codigo,
    CASE
        WHEN m.estado IS NULL THEN NULL
        WHEN m.estado = 1 THEN 'Pendiente'
        WHEN m.estado = 2 THEN 'Anulada'
        WHEN m.estado = 3 THEN 'Revisada'
        WHEN m.estado = 4 THEN 'Negada'
        WHEN m.estado = 5 THEN 'Aprobada'
        WHEN m.estado = 6 THEN 'Terminada'
        ELSE 'Desconocido'
    END AS nombre_estado_portabilidad,
    COALESCE(mo.descripcion, m.municipio_actual::text, NULL) AS ciudad_origen,
    COALESCE(md.descripcion, m.municipio_receptor::text, NULL) AS ciudad_destino,
    m.fecha_inicio,
    m.fecha_fin
FROM administrativo.af_afiliado_movilidad m
LEFT JOIN administrativo.tb_municipio mo ON m.municipio_actual IS NOT DISTINCT FROM mo.municipio
LEFT JOIN administrativo.tb_municipio md ON m.municipio_receptor IS NOT DISTINCT FROM md.municipio
WHERE m.consecutivo_afiliado = :afiliado
ORDER BY m.consecutivo_movilidad ASC NULLS LAST, m.fecha_inicio ASC NULLS LAST
"""

PQR_RESUMEN_BASE = """
SELECT
    e.consecutivo_peticion,
    ts.descripcion AS tipo_solicitud,
    e.fecha_recepcion AS fecha_radicado,
    CASE
        WHEN e.estado IS NULL THEN 'Sin datos'
        WHEN e.estado = 1 THEN 'Registrado'
        WHEN e.estado = 2 THEN 'Leido'
        WHEN e.estado = 3 THEN 'Borrador'
        WHEN e.estado = 4 THEN 'Respondido'
        ELSE 'Otro (' || COALESCE(e.estado::text, '') || ')'
    END AS estado_pqr,
    NULLIF(TRIM(COALESCE(dep.descripcion::text, '')), '') AS area_responsable,
    CASE
        WHEN NULLIF(TRIM(COALESCE(e.respuesta::text, '')), '') IS NOT NULL
            THEN LEFT(TRIM(e.respuesta::text), 4000)
        WHEN NULLIF(TRIM(COALESCE(e.relato::text, '')), '') IS NOT NULL
            THEN LEFT(TRIM(e.relato::text), 2000)
        ELSE NULL
    END AS respuesta_resumen
FROM administrativo.pqr_peticion_encabezado e
LEFT JOIN administrativo.pqr_tipo_solicitud ts
    ON e.consecutivo_tipo_solicitud = ts.consecutivo_tipo_solicitud
LEFT JOIN administrativo.prb_dependencia dep
    ON dep.consecutivo_dependencia = e.consecutivo_dependencia
INNER JOIN administrativo.af_afiliado a ON e.consecutivo_afiliado = a.afiliado
"""

PQR_LISTA_POR_DOCUMENTO_AFILIADO = PQR_RESUMEN_BASE + """
WHERE CAST(a.tipo_identificacion AS TEXT) = :tipo_doc
  AND TRIM(CAST(a.numero_identificacion AS TEXT)) = TRIM(:numero_doc)
ORDER BY e.consecutivo_peticion DESC
"""

PQR_DETALLE_POR_CONSECUTIVO_Y_DOCUMENTO = PQR_RESUMEN_BASE + """
WHERE e.consecutivo_peticion = :consecutivo
  AND CAST(a.tipo_identificacion AS TEXT) = :tipo_doc
  AND TRIM(CAST(a.numero_identificacion AS TEXT)) = TRIM(:numero_doc)
LIMIT 1
"""

PORTABILIDAD_DETALLE_POR_DOCUMENTO = """
SELECT
    m.consecutivo_movilidad,
    m.estado AS estado_portabilidad,
    CASE
        WHEN m.estado IS NULL THEN NULL
        WHEN m.estado = 1 THEN 'Pendiente'
        WHEN m.estado = 2 THEN 'Anulada'
        WHEN m.estado = 3 THEN 'Revisada'
        WHEN m.estado = 4 THEN 'Negada'
        WHEN m.estado = 5 THEN 'Aprobada'
        WHEN m.estado = 6 THEN 'Terminada'
        ELSE 'Desconocido'
    END AS nombre_estado_portabilidad,
    CASE
        WHEN m.estado IS NULL THEN NULL
        ELSE m.estado::TEXT
    END AS "estadoPortabilidad",
    COALESCE(tm_act.descripcion, m.municipio_actual::text, 'No registra datos') AS municipio_actual,
    COALESCE(tm_rec.descripcion, m.municipio_receptor::text, 'No registra datos') AS municipio_receptor,
    m.fecha_inicio,
    m.fecha_fin,
    a.estado_afiliado,
    CASE
        WHEN a.estado_afiliado = 1 THEN 'Activo'
        WHEN a.estado_afiliado = 2 THEN 'Retirado'
        WHEN a.estado_afiliado = 3 THEN 'Fallecido'
        WHEN a.estado_afiliado = 4 THEN 'Suspendido'
        WHEN a.estado_afiliado = 5 THEN 'Activo Carnetizado'
        WHEN a.estado_afiliado = 6 THEN 'Retirado Anulado'
        ELSE COALESCE(a.estado_afiliado::text, '')
    END AS nombre_estado_afiliado,
    COALESCE(i.razon_social, 'No registra datos') AS ips_primaria,
    COALESCE(ips_od.razon_social, 'No registra datos') AS ips_odontologica
FROM administrativo.af_afiliado a
LEFT JOIN administrativo.ct_ips i ON i.ips = a.ips
LEFT JOIN administrativo.tb_zonificacion_ips_encabezado z
    ON a.consecutivo_zonificacion_odontologia = z.consecutivo_zonificacion
LEFT JOIN administrativo.ct_ips ips_od ON z.consecutivo_ips = ips_od.ips
LEFT JOIN administrativo.af_afiliado_movilidad m ON m.consecutivo_afiliado = a.afiliado
LEFT JOIN administrativo.tb_municipio tm_act ON m.municipio_actual IS NOT DISTINCT FROM tm_act.municipio
LEFT JOIN administrativo.tb_municipio tm_rec ON m.municipio_receptor IS NOT DISTINCT FROM tm_rec.municipio
WHERE CAST(a.tipo_identificacion AS TEXT) = :tipo_doc
  AND TRIM(CAST(a.numero_identificacion AS TEXT)) = TRIM(:numero_doc)
ORDER BY m.consecutivo_movilidad DESC NULLS LAST, m.fecha_inicio DESC NULLS LAST
"""

CONSULTA_AFILIADO_POR_DOCUMENTO = """
SELECT
    a.afiliado,
    CASE
        WHEN a.tipo_identificacion = 5 THEN 'Registro Civil'
        WHEN a.tipo_identificacion = 7 THEN 'Cedula de Extranjeria'
        WHEN a.tipo_identificacion = 12 THEN 'Salvo Conducto'
        WHEN a.tipo_identificacion = 11 THEN 'Certificado Nacido Vivo'
        WHEN a.tipo_identificacion = 8 THEN 'Pasaporte'
        WHEN a.tipo_identificacion = 13 THEN 'Permiso Especial de Permanencia'
        WHEN a.tipo_identificacion = 10 THEN 'Carnet Diplomatico'
        WHEN a.tipo_identificacion = 6 THEN 'Tarjeta de Identidad'
        WHEN a.tipo_identificacion = 14 THEN 'Permiso proteccion temporal'
        WHEN a.tipo_identificacion = 15 THEN 'DE'
        WHEN a.tipo_identificacion = 2 THEN 'RUT'
        WHEN a.tipo_identificacion = 3 THEN 'Cedula de Ciudadania'
        WHEN a.tipo_identificacion = 4 THEN 'Menor sin Identificacion'
        WHEN a.tipo_identificacion = 9 THEN 'Adulto sin Identificacion'
        WHEN a.tipo_identificacion = 1 THEN 'NIT'
    END AS nombre_tipo_identificacion,
    a.tipo_identificacion,
    CAST(a.numero_identificacion AS TEXT) AS numero_identificacion,
    a.primer_nombre,
    COALESCE(a.segundo_nombre, '') AS segundo_nombre,
    a.primer_apellido,
    COALESCE(a.segundo_apellido, '') AS segundo_apellido,
    a.fecha_nacimiento,
    CASE
        WHEN a.sexo = 1 THEN 'Masculino'
        WHEN a.sexo = 2 THEN 'Femenino'
    END AS nombre_sexo,
    a.sexo,
    COALESCE(a.telefono_1, 'No registra datos') AS telefono_1,
    COALESCE(a.celular, 'No registra datos') AS celular,
    COALESCE(a.correo_electronico, 'No registra datos') AS correo_electronico,
    COALESCE(a.direccion, 'No registra datos') AS direccion,
    a.departamento AS departamento_codigo,
    td.descripcion AS departamento,
    a.municipio AS municipio_codigo,
    tm.descripcion AS municipio,
    a.zona_afiliacion,
    a.estrato,
    CASE
        WHEN a.tipo_afiliado = 1 THEN 'Cabeza de Familia'
        WHEN a.tipo_afiliado = 2 THEN 'Beneficiario'
        WHEN a.tipo_afiliado = 3 THEN 'Cotizante'
        WHEN a.tipo_afiliado = 4 THEN 'Adicional'
    END AS nombre_tipo_afiliado,
    a.tipo_afiliado,
    a.estado_afiliado,
    CASE
        WHEN a.estado_afiliado = 1 THEN 'Activo'
        WHEN a.estado_afiliado = 2 THEN 'Retirado'
        WHEN a.estado_afiliado = 3 THEN 'Fallecido'
        WHEN a.estado_afiliado = 4 THEN 'Suspendido'
        WHEN a.estado_afiliado = 5 THEN 'Activo Carnetizado'
        WHEN a.estado_afiliado = 6 THEN 'Retirado Anulado'
    END AS nombre_estado_afiliado,
    CASE
        WHEN ac.tipo_regimen = 1 THEN 'Contributivo'
        WHEN ac.tipo_regimen = 99 THEN 'Subsidiado'
    END AS nombre_tipo_regimen,
    ac.tipo_regimen,
    COALESCE(i.razon_social, 'No registra datos') AS ips_primaria,
    COALESCE(i.direccion, 'No registra datos') AS ips_prim_dir,
    COALESCE(i.telefono, 'No registra datos') AS ips_prim_telf,
    COALESCE(i.correo_electronico, 'No registra datos') AS ips_prim_email,
    COALESCE(i.nit::TEXT, 'No registra datos') AS ips_prim_nit,
    COALESCE(m.consecutivo_movilidad, 0) AS consecutivo_movilidad,
    COALESCE(m.municipio_actual::TEXT, 'No registra datos') AS municipio_actual,
    COALESCE(m.municipio_receptor::TEXT, 'No registra datos') AS municipio_receptor,
    COALESCE(m.fecha_inicio, DATE '1900-01-01') AS fecha_inicio,
    COALESCE(m.fecha_fin, DATE '1900-01-01') AS fecha_fin,
    CASE
        WHEN m.estado IS NULL THEN 'NULL'
        ELSE m.estado::TEXT
    END AS estado_portabilidad,
    CASE
        WHEN m.estado = 1 THEN 'Pendiente'
        WHEN m.estado = 2 THEN 'Anulada'
        WHEN m.estado = 3 THEN 'Revisada'
        WHEN m.estado = 4 THEN 'Negada'
        WHEN m.estado = 5 THEN 'Aprobada'
        WHEN m.estado = 6 THEN 'Terminada'
        WHEN m.estado IS NULL THEN ''
        ELSE 'Desconocido'
    END AS nombre_estado_portabilidad,
    COALESCE(z.descripcion, 'No registra datos') AS ips_odontologica,
    COALESCE(ips_od.direccion, 'No registra datos') AS ips_odontologica_dir,
    COALESCE(ips_od.telefono, 'No registra datos') AS ips_odontologica_telf,
    COALESCE(ips_od.correo_electronico, 'No registra datos') AS ips_odontologica_email
FROM administrativo.af_afiliado a
LEFT JOIN administrativo.af_afiliado_complemento ac ON a.afiliado = ac.afiliado
LEFT JOIN administrativo.tb_municipio td ON a.departamento = td.municipio
LEFT JOIN administrativo.tb_municipio tm ON a.municipio = tm.municipio
LEFT JOIN administrativo.ct_ips i ON i.ips = a.ips
LEFT JOIN administrativo.tb_zonificacion_ips_encabezado z ON a.consecutivo_zonificacion_odontologia = z.consecutivo_zonificacion
LEFT JOIN administrativo.af_afiliado_movilidad m ON a.afiliado = m.consecutivo_afiliado
LEFT JOIN administrativo.ct_ips ips_od ON z.consecutivo_ips = ips_od.ips
WHERE CAST(a.tipo_identificacion AS TEXT) = :tipo_doc
  AND TRIM(CAST(a.numero_identificacion AS TEXT)) = TRIM(:numero_doc)
ORDER BY m.consecutivo_movilidad DESC NULLS LAST, m.fecha_inicio DESC NULLS LAST
"""

PQR_POR_AFILIADO_OPCIONAL_CONSECUTIVO = """
SELECT
    e.consecutivo_peticion AS consecutivo_pqr,
    e.estado AS estado_pqr,
    CASE
        WHEN e.estado IS NULL THEN 'Sin datos'
        WHEN e.estado = 1 THEN 'Registrado'
        WHEN e.estado = 2 THEN 'Leido'
        WHEN e.estado = 3 THEN 'Borrador'
        WHEN e.estado = 4 THEN 'Respondido'
        ELSE 'Desconocido'
    END AS nombre_estado_pqr,
    e.fecha_recepcion AS fecha_grabado_fecha_recepcion,
    CASE
        WHEN NULLIF(TRIM(COALESCE(e.respuesta::text, '')), '') IS NOT NULL
            THEN LEFT(TRIM(e.respuesta::text), 4000)
        WHEN NULLIF(TRIM(COALESCE(e.relato::text, '')), '') IS NOT NULL
            THEN LEFT(TRIM(e.relato::text), 2000)
        ELSE NULL
    END AS respuesta,
    NULLIF(TRIM(COALESCE(dep.descripcion::text, '')), '') AS arearesponsable
FROM administrativo.pqr_peticion_encabezado e
INNER JOIN administrativo.af_afiliado a ON e.consecutivo_afiliado = a.afiliado
LEFT JOIN administrativo.prb_dependencia dep
    ON dep.consecutivo_dependencia = e.consecutivo_dependencia
WHERE CAST(a.tipo_identificacion AS TEXT) = :tipo_doc
  AND TRIM(CAST(a.numero_identificacion AS TEXT)) = TRIM(:numero_doc)
  AND (:consecutivo_pqr IS NULL OR e.consecutivo_peticion = :consecutivo_pqr)
ORDER BY e.consecutivo_peticion DESC
"""

AFILIADO_ACTIVO_POR_DOCUMENTO = """
SELECT 1
FROM administrativo.af_afiliado a
WHERE CAST(a.tipo_identificacion AS TEXT) = :tipo_doc
  AND TRIM(CAST(a.numero_identificacion AS TEXT)) = TRIM(:numero_doc)
  AND a.estado_afiliado IN (1, 5)
LIMIT 1
"""

EXISTE_IPS_POR_NIT = """
SELECT 1
FROM administrativo.ct_ips
WHERE TRIM(CAST(nit AS TEXT)) = TRIM(:nit)
LIMIT 1
"""

EXISTE_CIE10_POR_SIMBOLO = """
SELECT 1
FROM administrativo.tb_cie10
WHERE simbolo = :simbolo
LIMIT 1
"""

EXISTE_CUP_POR_CODIGO_INTERNO = """
SELECT 1
FROM administrativo.tb_cup
WHERE codigo_interno = :codigo
LIMIT 1
"""

EXISTE_INSUMO_POR_CODIGO = """
SELECT 1
FROM administrativo.tb_insumo
WHERE codigo_interno = :codigo
LIMIT 1
"""

EXISTE_MEDICAMENTO_POR_CODIGO = """
SELECT 1
FROM administrativo.tb_medicamento
WHERE codigo_interno = :codigo
LIMIT 1
"""

IPS_POR_NIT = """
SELECT ips, nit
FROM administrativo.ct_ips
WHERE TRIM(CAST(nit AS TEXT)) = TRIM(:nit)
LIMIT 1
"""

CT_IPS_CONTRATO_COLUMNAS = """
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'administrativo'
  AND table_name = 'ct_ips_contrato'
"""

AFILIADO_FECHA_NACIMIENTO_VALIDA = """
SELECT 1
FROM administrativo.af_afiliado a
WHERE CAST(a.tipo_identificacion AS TEXT) = :tipo_doc
  AND TRIM(CAST(a.numero_identificacion AS TEXT)) = TRIM(:numero_doc)
  AND a.fecha_nacimiento = :fecha_nacimiento
LIMIT 1
"""

RELACIONES_LABORALES_CERTIFICADO = """
SELECT
    CAST(aa.numero_identificacion AS TEXT) AS numero_identificacion,
    aa.razon_social,
    aaic.sw_activo,
    aaic.fecha_ingreso_inicial AS fecha_ingreso,
    aaic.fecha_grabado_retiro,
    COALESCE(com.categoria_ibc::TEXT, '') AS nivel_ibc
FROM administrativo.af_afiliado af
INNER JOIN administrativo.af_afiliado_complemento com
    ON com.afiliado = af.afiliado
INNER JOIN administrativo.af_afiliado_ingreso_contributivo aaic
    ON af.afiliado = aaic.afiliado
INNER JOIN administrativo.af_aportante aa
    ON aa.consecutivo_aportante = aaic.consecutivo_aportante
WHERE af.afiliado = :afiliado
ORDER BY aaic.fecha_ingreso_inicial
"""

AFILIADO_PARA_ACTUALIZACION_DATOS = """
SELECT
    a.afiliado,
    a.tipo_identificacion,
    a.numero_identificacion,
    a.primer_nombre,
    a.segundo_nombre,
    a.primer_apellido,
    a.segundo_apellido,
    a.fecha_nacimiento,
    a.sexo,
    a.telefono_1,
    a.celular,
    a.direccion,
    a.correo_electronico,
    a.municipio_nacimiento,
    a.estado_afiliado,
    DATE_PART('year', AGE(CURRENT_DATE, a.fecha_nacimiento))::INT AS edad_anios
FROM administrativo.af_afiliado a
WHERE CAST(a.tipo_identificacion AS TEXT) = :tipo_doc
  AND TRIM(CAST(a.numero_identificacion AS TEXT)) = TRIM(:numero_doc)
LIMIT 1
"""

LOCK_CT_IPS_SS_SOLICITUD = "SELECT pg_advisory_xact_lock(hashtext('ct_ips_ss_solicitud'))"

NEXT_CT_IPS_SS_SOLICITUD = """
SELECT COALESCE(MAX(s.consecutivo_solicitud), 0) + 1 AS next_id
FROM administrativo.ct_ips_ss_solicitud s
"""

INSERT_CT_IPS_SS_SOLICITUD = """
INSERT INTO administrativo.ct_ips_ss_solicitud (
    consecutivo_solicitud,
    consecutivo_afiliado,
    consecutivo_ips,
    tipo_servicio,
    url_archivo,
    observacion,
    email,
    telefono,
    celular,
    tipo_identificacion_afiliado,
    numero_identificacion_afiliado,
    usuario_grabado,
    fecha_grabado,
    estado
) VALUES (
    :consecutivo_solicitud,
    :consecutivo_afiliado,
    :consecutivo_ips,
    :tipo_servicio,
    :url_archivo,
    :observacion,
    :email,
    :telefono,
    :celular,
    :tipo_identificacion_afiliado,
    :numero_identificacion_afiliado,
    :usuario_grabado,
    :fecha_grabado,
    :estado
)
"""

EXISTS_CIE10 = """
SELECT 1
FROM administrativo.tb_cie10 c
WHERE UPPER(TRIM(COALESCE(c.simbolo, ''))) = UPPER(TRIM(:codigo))
LIMIT 1
"""

EXISTS_IPS_BY_NAME = """
SELECT 1
FROM administrativo.ct_ips i
WHERE UPPER(TRIM(COALESCE(i.razon_social, ''))) = UPPER(TRIM(:nombre))
LIMIT 1
"""

EXISTS_MUNICIPIO_BY_NAME = """
SELECT 1
FROM administrativo.tb_municipio m
WHERE UPPER(TRIM(COALESCE(m.descripcion, ''))) = UPPER(TRIM(:nombre))
LIMIT 1
"""

EXISTS_ESPECIALIDAD_BY_NAME = """
SELECT 1
FROM administrativo.tb_especialidad e
WHERE UPPER(TRIM(COALESCE(e.descripcion, ''))) = UPPER(TRIM(:nombre))
LIMIT 1
"""

EXISTS_MEDICAMENTO_BY_CODIGO_INTERNO = """
SELECT 1
FROM administrativo.tb_medicamento m
WHERE UPPER(TRIM(COALESCE(m.codigo_interno, ''))) = UPPER(TRIM(:codigo))
LIMIT 1
"""

NEXT_TICKET_HEADER_ID = """
SELECT COALESCE(MAX(t.consecutivo), 0) + 1 AS next_id
FROM administrativo.af_ticket_administrativo_aseguramiento t
"""

NEXT_TICKET_DETAIL_ID = """
SELECT COALESCE(MAX(d.consecutivo), 0) + 1 AS next_id
FROM administrativo.af_ticket_administrativo_aseguramiento_detalle d
"""

INSERT_TICKET_HEADER = """
INSERT INTO administrativo.af_ticket_administrativo_aseguramiento (
    consecutivo,
    usuario_grabado,
    fecha_grabado,
    estado,
    tipo_proceso,
    consecutivo_afiliado,
    observacion,
    origen_solicitud,
    tipo_documento,
    numero_documento
) VALUES (
    :consecutivo,
    :usuario_grabado,
    NOW(),
    :estado,
    :tipo_proceso,
    :consecutivo_afiliado,
    :observacion,
    :origen_solicitud,
    :tipo_documento,
    :numero_documento
)
"""

INSERT_TICKET_DETAIL = """
INSERT INTO administrativo.af_ticket_administrativo_aseguramiento_detalle (
    consecutivo,
    consecutivo_encabezado,
    tipo_identificacion,
    numero_identificacion,
    primer_nombre,
    segundo_nombre,
    primer_apellido,
    segundo_apellido,
    fecha_nacimiento,
    sexo,
    telefono,
    celular,
    direccion,
    correo_electronico,
    municipio_nacimiento,
    proceso,
    campo,
    nuevo_valor,
    antiguo_valor
) VALUES (
    :consecutivo,
    :consecutivo_encabezado,
    :tipo_identificacion,
    :numero_identificacion,
    :primer_nombre,
    :segundo_nombre,
    :primer_apellido,
    :segundo_apellido,
    :fecha_nacimiento,
    :sexo,
    :telefono,
    :celular,
    :direccion,
    :correo_electronico,
    :municipio_nacimiento,
    :proceso,
    :campo,
    :nuevo_valor,
    :antiguo_valor
)
"""

LOCK_TICKET_HEADER = "SELECT pg_advisory_xact_lock(hashtext('af_ticket_administrativo_aseguramiento'))"
LOCK_TICKET_DETAIL = "SELECT pg_advisory_xact_lock(hashtext('af_ticket_administrativo_aseguramiento_detalle'))"
LOCK_TICKET_SUPPORT = "SELECT pg_advisory_xact_lock(hashtext('af_ticket_administrativo_aseguramiento_soporte'))"

NEXT_TICKET_SUPPORT_ID = """
SELECT COALESCE(MAX(s.consecutivo), 0) + 1 AS next_id
FROM administrativo.af_ticket_administrativo_aseguramiento_soporte s
"""

TIPO_SOPORTE_DOC_IDENTIDAD = """
SELECT ts.consecutivo_soporte
FROM administrativo.tb_tipo_soporte ts
WHERE UPPER(COALESCE(ts.descripcion, '')) LIKE '%IDENT%'
   OR UPPER(COALESCE(ts.descripcion, '')) LIKE '%REGISTRAD%'
ORDER BY ts.consecutivo_soporte
LIMIT 1
"""

INSERT_TICKET_SUPPORT = """
INSERT INTO administrativo.af_ticket_administrativo_aseguramiento_soporte (
    consecutivo,
    url,
    url_copia,
    consecutivo_soporte,
    consecutivo_encabezado
) VALUES (
    :consecutivo,
    :url,
    :url_copia,
    :consecutivo_soporte,
    :consecutivo_encabezado
)
"""

SERVICE1_QUERY = """
SELECT
    i.consecutivo_factura,
    i.documento_soporte_enlace_auxiliar,
    i.valor,
    i.saldo_factura,
    i.valor - i.saldo_factura AS delta
FROM (
    SELECT
        d.consecutivo_factura,
        d.documento_soporte_enlace_auxiliar,
        fe.saldo_factura,
        SUM(d.valor_credito - d.valor_debito) AS valor
    FROM administrativo.sc_saldo_encabezado a
    INNER JOIN administrativo.sc_saldo_detalle d
        ON a.consecutivo_saldo = d.consecutivo_saldo
    INNER JOIN administrativo.sc_cuenta c
        ON c.cuenta = d.cuenta
    INNER JOIN administrativo.sc_tercero t
        ON t.consecutivo_tercero = d.tercero
    INNER JOIN administrativo.sc_factura_encabezado fe
        ON d.consecutivo_factura = fe.consecutivo_factura
    WHERE a.estado = 1
      AND c.clase_b = 1
      AND EXISTS (
          SELECT 1
          FROM administrativo.ct_ips ips
          WHERE ips.nit IS NOT NULL
            AND TRIM(CAST(ips.nit AS TEXT)) = TRIM(CAST(t.nro_identificacion AS TEXT))
      )
      AND a.fecha >= DATE '2022-01-01'
      AND a.fecha <= DATE '2030-12-31'
      AND a.tipo_documento <> 99
    GROUP BY d.consecutivo_factura, d.documento_soporte_enlace_auxiliar, fe.saldo_factura
    ORDER BY d.consecutivo_factura, d.documento_soporte_enlace_auxiliar
) i
WHERE i.valor - i.saldo_factura <> 0
  AND i.saldo_factura > 0
  AND ABS(i.valor - i.saldo_factura) > 0
"""

BULK_UPDATE_SALDO_ENCABEZADO = """
UPDATE administrativo.sc_factura_encabezado AS fe
SET saldo_factura = src.valor
FROM unnest(
    CAST(:consecutivos AS text[]),
    CAST(:valores AS bigint[])
) AS src(consecutivo_factura, valor)
WHERE fe.consecutivo_factura::text = src.consecutivo_factura
"""

BULK_UPDATE_VALOR_POR_APLICAR = """
UPDATE administrativo.sc_factura_detalle_valor AS fdv
SET valor_por_aplicar = src.valor
FROM unnest(
    CAST(:consecutivos AS text[]),
    CAST(:valores AS bigint[])
) AS src(consecutivo_factura, valor)
WHERE fdv.consecutivo_factura::text = src.consecutivo_factura
"""

SALDO_VALOR_UPDATE_CHUNK_SIZE = 500


def monto_sin_decimales(value: Any) -> int:
    """Trunca un monto al entero (sin decimales) para persistir en saldo_factura / valor_por_aplicar."""
    if value is None:
        return 0
    return int(Decimal(str(value)))


class PostgresRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def fetch_service1_candidates(self) -> list[Service1ResultDTO]:
        rows = self.db.execute(text(SERVICE1_QUERY)).mappings().all()
        result: list[Service1ResultDTO] = []
        for row in rows:
            result.append(
                Service1ResultDTO(
                    consecutivo_factura=str(row["consecutivo_factura"]),
                    documento_soporte=row.get("documento_soporte_enlace_auxiliar"),
                    valor=Decimal(row["valor"]),
                    saldo_factura=Decimal(row["saldo_factura"]),
                    delta=Decimal(row["delta"]),
                )
            )
        return result

    def update_saldo_factura(self, consecutivo_factura: str, valor: int) -> None:
        self.db.execute(
            text(
                """
                update administrativo.sc_factura_encabezado
                set saldo_factura = :valor
                where consecutivo_factura = :consecutivo_factura
                """
            ),
            {"valor": valor, "consecutivo_factura": consecutivo_factura},
        )

    def update_valor_por_aplicar(self, consecutivo_factura: str, valor: int) -> None:
        self.db.execute(
            text(
                """
                update administrativo.sc_factura_detalle_valor
                set valor_por_aplicar = :valor
                where consecutivo_factura = :consecutivo_factura
                """
            ),
            {"valor": valor, "consecutivo_factura": consecutivo_factura},
        )

    def batch_update_saldo_factura(self, items: list[dict[str, int | str]]) -> None:
        if not items:
            return
        self.db.execute(
            text(
                """
                update administrativo.sc_factura_encabezado
                set saldo_factura = :valor
                where consecutivo_factura = :consecutivo_factura
                """
            ),
            items,
        )

    def batch_update_valor_por_aplicar(self, items: list[dict[str, int | str]]) -> None:
        if not items:
            return
        self.db.execute(
            text(
                """
                update administrativo.sc_factura_detalle_valor
                set valor_por_aplicar = :valor
                where consecutivo_factura = :consecutivo_factura
                """
            ),
            items,
        )

    def fetch_saldo_valor_update_payload(self) -> tuple[list[dict[str, int | str]], int]:
        """
        Candidatos del flujo unificado: un registro por consecutivo_factura.
        Usa int(valor) de la consulta (suma contable), no saldo_factura actual, para corregir el delta.
        """
        rows = self.db.execute(text(SERVICE1_QUERY)).mappings().all()
        query_rows = len(rows)
        by_consecutivo: dict[str, int] = {}
        for row in rows:
            consecutivo = str(row["consecutivo_factura"])
            monto = monto_sin_decimales(row["valor"])
            prev = by_consecutivo.get(consecutivo)
            if prev is None or abs(monto) > abs(prev):
                by_consecutivo[consecutivo] = monto
        payload = [{"consecutivo_factura": k, "valor": v} for k, v in by_consecutivo.items()]
        return payload, query_rows

    def count_saldo_valor_candidates(self) -> int:
        row = self.db.execute(
            text(
                f"""
                SELECT COUNT(*) AS total
                FROM ({SERVICE1_QUERY.strip().rstrip(';')}) AS candidatos
                """
            )
        ).mappings().first()
        return int(row["total"]) if row else 0

    def apply_saldo_valor_updates_batched(
        self,
        items: list[dict[str, int | str]],
        *,
        chunk_size: int = SALDO_VALOR_UPDATE_CHUNK_SIZE,
    ) -> int:
        """
        Por cada lote: (1) sc_factura_encabezado.saldo_factura,
        (2) sc_factura_detalle_valor.valor_por_aplicar, ambos con int(valor) de la consulta.
        """
        if not items:
            return 0
        processed = 0
        for offset in range(0, len(items), chunk_size):
            chunk = items[offset : offset + chunk_size]
            consecutivos = [str(item["consecutivo_factura"]) for item in chunk]
            valores = [int(item["valor"]) for item in chunk]
            params = {"consecutivos": consecutivos, "valores": valores}
            self.db.execute(text(BULK_UPDATE_SALDO_ENCABEZADO), params)
            self.db.execute(text(BULK_UPDATE_VALOR_POR_APLICAR), params)
            processed += len(chunk)
        return processed

    def fetch_afiliado_resumen_por_documento(self, tipo_identificacion: str, numero_identificacion: str) -> dict[str, Any] | None:
        row = self.db.execute(
            text(AFILIADO_RESUMEN_POR_DOCUMENTO),
            {"tipo_doc": tipo_identificacion.strip(), "numero_doc": numero_identificacion.strip()},
        ).mappings().first()
        if row is None:
            return None
        return dict(row)

    def fetch_afiliado_datos_certificado_por_documento(
        self, tipo_identificacion: str, numero_identificacion: str
    ) -> dict[str, Any] | None:
        row = self.db.execute(
            text(AFILIADO_DATOS_CERTIFICADO),
            {"tipo_doc": tipo_identificacion.strip(), "numero_doc": numero_identificacion.strip()},
        ).mappings().first()
        if row is None:
            return None
        return dict(row)

    def fetch_todas_portabilidades_por_afiliado(self, afiliado: Any) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(PORTABILIDADES_POR_AFILIADO),
            {"afiliado": afiliado},
        ).mappings().all()
        return [dict(r) for r in rows]

    def fetch_pqrs_resumen_por_documento_afiliado(self, tipo_identificacion: str, numero_identificacion: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(PQR_LISTA_POR_DOCUMENTO_AFILIADO),
            {"tipo_doc": tipo_identificacion.strip(), "numero_doc": numero_identificacion.strip()},
        ).mappings().all()
        return [dict(r) for r in rows]

    def fetch_pqr_resumen_por_consecutivo_y_documento(
        self,
        tipo_identificacion: str,
        numero_identificacion: str,
        consecutivo_peticion: int,
    ) -> dict[str, Any] | None:
        row = self.db.execute(
            text(PQR_DETALLE_POR_CONSECUTIVO_Y_DOCUMENTO),
            {
                "tipo_doc": tipo_identificacion.strip(),
                "numero_doc": numero_identificacion.strip(),
                "consecutivo": consecutivo_peticion,
            },
        ).mappings().first()
        if row is None:
            return None
        return dict(row)

    def fetch_portabilidad_detalle_por_documento(
        self,
        tipo_identificacion: str,
        numero_identificacion: str,
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(PORTABILIDAD_DETALLE_POR_DOCUMENTO),
            {"tipo_doc": tipo_identificacion.strip(), "numero_doc": numero_identificacion.strip()},
        ).mappings().all()
        return [dict(r) for r in rows]

    def fetch_consulta_afiliado_por_documento(
        self,
        tipo_identificacion: str,
        numero_identificacion: str,
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(CONSULTA_AFILIADO_POR_DOCUMENTO),
            {"tipo_doc": tipo_identificacion.strip(), "numero_doc": numero_identificacion.strip()},
        ).mappings().all()
        return [dict(r) for r in rows]

    def fetch_pqr_por_documento_con_opcional_consecutivo(
        self,
        tipo_identificacion: str,
        numero_identificacion: str,
        consecutivo_pqr: int | None,
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(PQR_POR_AFILIADO_OPCIONAL_CONSECUTIVO),
            {
                "tipo_doc": tipo_identificacion.strip(),
                "numero_doc": numero_identificacion.strip(),
                "consecutivo_pqr": consecutivo_pqr,
            },
        ).mappings().all()
        return [dict(r) for r in rows]

    def afiliado_activo_por_documento(self, tipo_identificacion: str, numero_identificacion: str) -> bool:
        row = self.db.execute(
            text(AFILIADO_ACTIVO_POR_DOCUMENTO),
            {"tipo_doc": tipo_identificacion.strip(), "numero_doc": numero_identificacion.strip()},
        ).first()
        return row is not None

    def existe_ips_por_nit(self, nit: str) -> bool:
        row = self.db.execute(text(EXISTE_IPS_POR_NIT), {"nit": nit.strip()}).first()
        return row is not None

    def existe_cie10(self, simbolo: str) -> bool:
        row = self.db.execute(text(EXISTE_CIE10_POR_SIMBOLO), {"simbolo": simbolo.strip()}).first()
        return row is not None

    def existe_cup(self, codigo_interno: str) -> bool:
        row = self.db.execute(text(EXISTE_CUP_POR_CODIGO_INTERNO), {"codigo": codigo_interno.strip()}).first()
        return row is not None

    def existe_insumo_o_medicamento(self, codigo_interno: str) -> bool:
        codigo = codigo_interno.strip()
        row_insumo = self.db.execute(text(EXISTE_INSUMO_POR_CODIGO), {"codigo": codigo}).first()
        if row_insumo is not None:
            return True
        row_medicamento = self.db.execute(text(EXISTE_MEDICAMENTO_POR_CODIGO), {"codigo": codigo}).first()
        return row_medicamento is not None

    def afiliado_fecha_nacimiento_coincide(
        self,
        tipo_identificacion: str,
        numero_identificacion: str,
        fecha_nacimiento,
    ) -> bool:
        row = self.db.execute(
            text(AFILIADO_FECHA_NACIMIENTO_VALIDA),
            {
                "tipo_doc": tipo_identificacion.strip(),
                "numero_doc": numero_identificacion.strip(),
                "fecha_nacimiento": fecha_nacimiento,
            },
        ).first()
        return row is not None

    def fetch_ips_por_nit(self, nit: str) -> dict[str, Any] | None:
        row = self.db.execute(text(IPS_POR_NIT), {"nit": nit.strip()}).mappings().first()
        return dict(row) if row else None

    def fetch_contrato_ips_por_departamento(self, ips: int, departamento_codigo: str) -> dict[str, Any] | None:
        cols = {
            str(r[0]).strip().lower()
            for r in self.db.execute(text(CT_IPS_CONTRATO_COLUMNAS)).all()
        }
        if not cols:
            return None

        estado_col: str | None = None
        for candidate in ("estado_contrato", "estado", "estatus", "estado_ctto"):
            if candidate in cols:
                estado_col = candidate
                break
        if estado_col is None:
            return None

        row = self.db.execute(
            text(
                f"""
                SELECT
                    c.numero_contrato,
                    c.{estado_col} AS estado_contrato
                FROM administrativo.ct_ips_contrato c
                WHERE c.ips = :ips
                  AND LEFT(TRIM(CAST(c.municipio_administracion AS TEXT)), 2) = :departamento_codigo
                ORDER BY c.numero_contrato DESC
                LIMIT 1
                """
            ),
            {"ips": int(ips), "departamento_codigo": departamento_codigo.strip()},
        ).mappings().first()
        return dict(row) if row else None

    def fetch_relaciones_laborales_certificado(self, afiliado: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(RELACIONES_LABORALES_CERTIFICADO),
            {"afiliado": afiliado},
        ).mappings().all()
        return [dict(r) for r in rows]

    def fetch_afiliado_para_actualizacion_datos(
        self,
        tipo_identificacion: str,
        numero_identificacion: str,
    ) -> dict[str, Any] | None:
        row = self.db.execute(
            text(AFILIADO_PARA_ACTUALIZACION_DATOS),
            {"tipo_doc": tipo_identificacion.strip(), "numero_doc": numero_identificacion.strip()},
        ).mappings().first()
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def validar_dato_contacto(valor: str | None, tipo: str) -> bool:
        if valor is None:
            return tipo == "telefono"
        val = valor.strip()
        if not val:
            return tipo == "telefono"
        if tipo == "telefono":
            return bool(re.fullmatch(r"\d{10}", val))
        if tipo == "celular":
            return bool(re.fullmatch(r"\d{10}", val))
        if tipo == "correo":
            return bool(re.fullmatch(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", val))
        return True

    @staticmethod
    def _tipo_documento_abreviatura(tipo_identificacion: str) -> str:
        mapa = {
            "3": "CC",
            "4": "MS",
            "5": "RC",
            "6": "TI",
            "7": "CE",
            "8": "PA",
            "9": "AS",
            "10": "CD",
            "11": "NV",
            "13": "PE",
            "14": "PT",
        }
        return mapa.get(tipo_identificacion.strip(), tipo_identificacion.strip())

    def create_ticket_actualizacion_datos_micrositio(
        self,
        *,
        afiliado_data: dict[str, Any],
        usuario_grabado: str,
        observacion: str,
        telefono: str | None,
        celular: str,
        direccion: str,
        correo_electronico: str,
        barrio: str,
        soporte_url: str | None = None,
    ) -> int:
        self.db.execute(text(LOCK_TICKET_HEADER))
        consecutivo_ticket = int(self.db.execute(text(NEXT_TICKET_HEADER_ID)).scalar_one())
        self.db.execute(text(LOCK_TICKET_DETAIL))
        consecutivo_detalle = int(self.db.execute(text(NEXT_TICKET_DETAIL_ID)).scalar_one())

        tipo_identificacion = str(afiliado_data["tipo_identificacion"]).strip()
        numero_identificacion = str(afiliado_data["numero_identificacion"]).strip()
        tipo_documento = self._tipo_documento_abreviatura(tipo_identificacion)

        self.db.execute(
            text(INSERT_TICKET_HEADER),
            {
                "consecutivo": consecutivo_ticket,
                "usuario_grabado": usuario_grabado,
                "estado": 1,  # REGISTRADO
                "tipo_proceso": 1,  # ACTUALIZACION_DATOS
                "consecutivo_afiliado": afiliado_data["afiliado"],
                "observacion": f"{observacion} - PROCESO ACTUALIZACION DATOS CONTACTO",
                "origen_solicitud": 2,  # MICROSITIO
                "tipo_documento": tipo_documento,
                "numero_documento": numero_identificacion,
            },
        )

        self.db.execute(
            text(INSERT_TICKET_DETAIL),
            {
                "consecutivo": consecutivo_detalle,
                "consecutivo_encabezado": consecutivo_ticket,
                "tipo_identificacion": int(tipo_identificacion),
                "numero_identificacion": numero_identificacion,
                "primer_nombre": afiliado_data.get("primer_nombre"),
                "segundo_nombre": afiliado_data.get("segundo_nombre"),
                "primer_apellido": afiliado_data.get("primer_apellido"),
                "segundo_apellido": afiliado_data.get("segundo_apellido"),
                "fecha_nacimiento": afiliado_data.get("fecha_nacimiento"),
                "sexo": afiliado_data.get("sexo"),
                "telefono": telefono,
                "celular": celular,
                "direccion": direccion,
                "correo_electronico": correo_electronico,
                "municipio_nacimiento": afiliado_data.get("municipio_nacimiento"),
                "proceso": 46,  # ACTUALIZACION_DATOS_CONTACTO
                "campo": 114,  # BARRIO
                "nuevo_valor": barrio,
                "antiguo_valor": "",
            },
        )

        if soporte_url:
            self.db.execute(text(LOCK_TICKET_SUPPORT))
            consecutivo_soporte_reg = int(self.db.execute(text(NEXT_TICKET_SUPPORT_ID)).scalar_one())
            consecutivo_tipo_soporte = self.db.execute(text(TIPO_SOPORTE_DOC_IDENTIDAD)).scalar_one()
            if consecutivo_tipo_soporte is None:
                raise ValueError(
                    "No se encontró un tipo de soporte válido para documento de identificación en administrativo.tb_tipo_soporte."
                )
            self.db.execute(
                text(INSERT_TICKET_SUPPORT),
                {
                    "consecutivo": consecutivo_soporte_reg,
                    "url": soporte_url,
                    "url_copia": None,
                    "consecutivo_soporte": int(consecutivo_tipo_soporte),
                    "consecutivo_encabezado": consecutivo_ticket,
                },
            )

        self.db.commit()
        return consecutivo_ticket

    def create_solicitud_autorizacion_orden_medica(
        self,
        *,
        afiliado_data: dict[str, Any],
        usuario_grabado: str,
        observacion: str,
        email: str,
        telefono: str | None,
        celular: str | None,
        soporte_url: str,
        consecutivo_ips: int | None = None,
        tipo_servicio: int = 1,
    ) -> int:
        self.db.execute(text(LOCK_CT_IPS_SS_SOLICITUD))
        consecutivo_solicitud = int(self.db.execute(text(NEXT_CT_IPS_SS_SOLICITUD)).scalar_one())

        tipo_identificacion = str(afiliado_data["tipo_identificacion"]).strip()
        numero_identificacion = str(afiliado_data["numero_identificacion"]).strip()
        tipo_documento = self._tipo_documento_abreviatura(tipo_identificacion)

        self.db.execute(
            text(INSERT_CT_IPS_SS_SOLICITUD),
            {
                "consecutivo_solicitud": consecutivo_solicitud,
                "consecutivo_afiliado": int(afiliado_data["afiliado"]),
                "consecutivo_ips": consecutivo_ips,
                "tipo_servicio": int(tipo_servicio),
                "url_archivo": soporte_url,
                "observacion": observacion,
                "email": email,
                "telefono": telefono,
                "celular": celular,
                "tipo_identificacion_afiliado": tipo_documento,
                "numero_identificacion_afiliado": numero_identificacion,
                "usuario_grabado": usuario_grabado[:100],
                "fecha_grabado": ahora_bogota(),
                "estado": 1,
            },
        )
        self.db.commit()
        return consecutivo_solicitud

    def exists_cie10(self, codigo: str) -> bool:
        return self.db.execute(text(EXISTS_CIE10), {"codigo": codigo}).first() is not None

    def exists_ips_by_name(self, nombre: str) -> bool:
        return self.db.execute(text(EXISTS_IPS_BY_NAME), {"nombre": nombre}).first() is not None

    def exists_municipio_by_name(self, nombre: str) -> bool:
        return self.db.execute(text(EXISTS_MUNICIPIO_BY_NAME), {"nombre": nombre}).first() is not None

    def exists_especialidad_by_name(self, nombre: str) -> bool:
        return self.db.execute(text(EXISTS_ESPECIALIDAD_BY_NAME), {"nombre": nombre}).first() is not None

    def exists_medicamento_by_codigo_interno(self, codigo: str) -> bool:
        return self.db.execute(text(EXISTS_MEDICAMENTO_BY_CODIGO_INTERNO), {"codigo": codigo}).first() is not None
