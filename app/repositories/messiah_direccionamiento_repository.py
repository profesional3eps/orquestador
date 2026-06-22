"""Persistencia y consultas Messiah para solicitud/autorización de medicamentos (direccionamiento)."""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.core.messiah_constants import (
    ESTADO_FLUJO_ACTIVA,
    ESTADO_FLUJO_CONFIRMADA,
    ESTADO_FLUJO_DIRECCIONADA,
    SERVICIO_SOLICITUD_MEDICAMENTOS,
    TIPO_SERVICIO_SOLICITADO_MEDICAMENTOS,
)
from app.repositories.messiah_auditoria_repository import (
    registrar_estado_flujo_autorizacion,
    registrar_logs_activacion_medicamentos,
    registrar_logs_confirmacion_medicamentos,
)
from app.services.messiah_preferencia_service import calcular_vigencias_autorizacion_medicamentos
from app.core.zona_horaria import ahora_bogota, hoy_bogota

LOCK_DIRECCIONAMIENTO = "SELECT pg_advisory_xact_lock(hashtext('orq_direccionamiento'))"

NEXT_SS_SOLICITUD = """
SELECT COALESCE(MAX(s.consecutivo_solicitud), 0) + 1 AS next_id
FROM administrativo.ss_solicitud s
"""

NEXT_SS_AUTORIZACION = """
SELECT COALESCE(MAX(a.consecutivo_autorizacion), 0) + 1 AS next_id
FROM administrativo.ss_autorizacion a
"""

NEXT_INTERNO_ACTIVACION = """
SELECT COALESCE(MAX(CAST(NULLIF(regexp_replace(consecutivo_interno_base, '[^0-9]', '', 'g'), '') AS BIGINT)), 0) + 1
FROM administrativo.ss_autorizacion
"""

NEXT_NUMERO_SOLICITUD = """
UPDATE administrativo.tb_parametro
SET numero_autorizacion = COALESCE(numero_autorizacion, 0) + 1
WHERE consecutivo_parametro = 1
RETURNING numero_autorizacion
"""

IPS_DETALLE_POR_NIT = """
SELECT
    i.ips,
    i.nit,
    i.digito_verificacion,
    i.razon_social,
    i.sigla,
    i.codigo_prestador,
    i.municipio,
    tm.descripcion AS municipio_descripcion,
    i.direccion,
    i.telefono,
    i.correo_electronico,
    i.sw_habilitada,
    i.sw_autorizacion_masiva,
    i.tipo_autoriza,
    i.tipo_medicamento,
    i.estado_ips
FROM administrativo.ct_ips i
LEFT JOIN administrativo.tb_municipio tm ON tm.municipio = i.municipio
WHERE TRIM(CAST(i.nit AS TEXT)) = TRIM(:nit)
LIMIT 1
"""

IPS_DETALLE_POR_CONSECUTIVO = """
SELECT
    i.ips,
    i.nit,
    i.digito_verificacion,
    i.razon_social,
    i.sigla,
    i.codigo_prestador,
    i.municipio,
    tm.descripcion AS municipio_descripcion,
    i.direccion,
    i.telefono,
    i.correo_electronico,
    i.sw_habilitada,
    i.sw_autorizacion_masiva,
    i.tipo_autoriza,
    i.tipo_medicamento,
    i.estado_ips
FROM administrativo.ct_ips i
LEFT JOIN administrativo.tb_municipio tm ON tm.municipio = i.municipio
WHERE i.ips = :ips
LIMIT 1
"""

IPS_DETALLE_POR_NOMBRE = """
SELECT
    i.ips,
    i.nit,
    i.digito_verificacion,
    i.razon_social,
    i.sigla,
    i.codigo_prestador,
    i.municipio,
    tm.descripcion AS municipio_descripcion,
    i.direccion,
    i.telefono,
    i.correo_electronico,
    i.sw_habilitada,
    i.sw_autorizacion_masiva,
    i.tipo_autoriza,
    i.tipo_medicamento,
    i.estado_ips
FROM administrativo.ct_ips i
LEFT JOIN administrativo.tb_municipio tm ON tm.municipio = i.municipio
WHERE UPPER(TRIM(COALESCE(i.razon_social, ''))) = UPPER(TRIM(:nombre))
LIMIT 1
"""

MEDICAMENTO_POR_CODIGO = """
SELECT
    m.medicamento,
    m.codigo_interno,
    m.descripcion,
    m.concentracion,
    m.forma_farmaceutica,
    m.posologia AS posologia_catalogo,
    um.descripcion AS unidad_medida,
    m.sw_automatico,
    m.sw_activo,
    m.sw_pos,
    COALESCE(m.valor, 0) AS valor,
    (
        SELECT mn.consecutivo_concepto
        FROM administrativo.tb_medicamento_nota_tecnica mn
        INNER JOIN administrativo.tb_concepto_nota_tecnica c
            ON c.consecutivo_concepto = mn.consecutivo_concepto
        WHERE mn.consecutivo_medicamento = m.medicamento
          AND COALESCE(c.sw_activo, 0) = 1
        ORDER BY c.consecutivo_nivel DESC, mn.consecutivo_concepto
        LIMIT 1
    ) AS consecutivo_concepto
FROM administrativo.tb_medicamento m
LEFT JOIN administrativo.tb_unidad_medida um ON um.consecutivo_unidad_medida = m.consecutivo_unidad_medida
WHERE UPPER(TRIM(COALESCE(m.codigo_interno, ''))) = UPPER(TRIM(:codigo))
LIMIT 1
"""

CONTRATO_IPS_MUNICIPIO_AFILIADO = """
SELECT
    c.consecutivo_contrato,
    c.numero_contrato,
    c.consecutivo_tarifario_medicamento,
    c.estado
FROM administrativo.ct_ips_contrato c
LEFT JOIN administrativo.ct_ips_contrato_cobertura cob
    ON cob.contrato_ips = c.consecutivo_contrato
WHERE c.ips = :ips
  AND COALESCE(c.sw_bloqueado, 0) = 0
  AND c.consecutivo_contrato_base IS NULL
  AND c.estado = 3
  AND c.tipo_red = 1
  AND (timezone('America/Bogota', now()))::date BETWEEN c.fecha_inicio AND c.fecha_terminacion
  AND c.consecutivo_tarifario_medicamento IS NOT NULL
  AND (
    :municipio_afiliado = ''
    OR cob.municipio IS NULL
    OR TRIM(CAST(cob.municipio AS TEXT)) = TRIM(:municipio_afiliado)
  )
ORDER BY c.numero_contrato DESC
LIMIT 1
"""

CONTRATO_IPS_MUNICIPIO_EXISTE = """
SELECT 1
FROM administrativo.ct_ips_contrato c
LEFT JOIN administrativo.ct_ips_contrato_cobertura cob
    ON cob.contrato_ips = c.consecutivo_contrato
WHERE c.ips = :ips
  AND COALESCE(c.sw_bloqueado, 0) = 0
  AND c.consecutivo_contrato_base IS NULL
  AND c.estado = 3
  AND c.tipo_red = 1
  AND (timezone('America/Bogota', now()))::date BETWEEN c.fecha_inicio AND c.fecha_terminacion
  AND c.consecutivo_tarifario_medicamento IS NOT NULL
  AND (
    :municipio_afiliado = ''
    OR cob.municipio IS NULL
    OR TRIM(CAST(cob.municipio AS TEXT)) = TRIM(:municipio_afiliado)
  )
LIMIT 1
"""

TARIFARIO_MEDICAMENTO_CONTRATOS_MUNICIPIO = """
SELECT
    d.consecutivo_tarifa,
    d.secuencia,
    d.codigo_tarifa,
    d.codigo_propio,
    d.descripcion,
    COALESCE(d.valor_servicio, d.valor, 0) AS valor_servicio,
    d.sw_activo,
    d.sw_automatico,
    c.consecutivo_contrato,
    c.numero_contrato,
    c.consecutivo_tarifario_medicamento
FROM administrativo.ct_ips_contrato c
INNER JOIN administrativo.tb_tarifario_propio_detalle d
    ON d.consecutivo_tarifa = c.consecutivo_tarifario_medicamento
LEFT JOIN administrativo.ct_ips_contrato_cobertura cob
    ON cob.contrato_ips = c.consecutivo_contrato
WHERE c.ips = :ips
  AND COALESCE(c.sw_bloqueado, 0) = 0
  AND c.consecutivo_contrato_base IS NULL
  AND c.estado = 3
  AND c.tipo_red = 1
  AND (timezone('America/Bogota', now()))::date BETWEEN c.fecha_inicio AND c.fecha_terminacion
  AND c.consecutivo_tarifario_medicamento IS NOT NULL
  AND COALESCE(d.sw_principal, 0) = 1
  AND (
    :municipio_afiliado = ''
    OR cob.municipio IS NULL
    OR TRIM(CAST(cob.municipio AS TEXT)) = TRIM(:municipio_afiliado)
  )
  AND (
    UPPER(TRIM(COALESCE(d.codigo_tarifa, ''))) = UPPER(TRIM(:codigo))
    OR UPPER(TRIM(COALESCE(d.codigo_propio, ''))) = UPPER(TRIM(:codigo))
  )
ORDER BY COALESCE(d.valor_servicio, d.valor, 0) ASC
LIMIT 1
"""

TARIFARIO_MEDICAMENTO = """
SELECT
    d.consecutivo_tarifa,
    d.secuencia,
    d.codigo_tarifa,
    d.codigo_propio,
    d.descripcion,
    COALESCE(d.valor_servicio, d.valor, 0) AS valor_servicio,
    d.sw_activo,
    d.sw_automatico
FROM administrativo.tb_tarifario_propio_detalle d
WHERE d.consecutivo_tarifa = :consecutivo_tarifario
  AND COALESCE(d.sw_principal, 0) = 1
  AND (
    UPPER(TRIM(COALESCE(d.codigo_tarifa, ''))) = UPPER(TRIM(:codigo))
    OR UPPER(TRIM(COALESCE(d.codigo_propio, ''))) = UPPER(TRIM(:codigo))
  )
ORDER BY COALESCE(d.valor_servicio, d.valor, 0) ASC
LIMIT 1
"""

MEDICAMENTO_POR_CUM = """
SELECT
    m.medicamento,
    m.codigo_interno,
    m.consecutivo,
    m.descripcion,
    m.concentracion,
    m.forma_farmaceutica,
    m.posologia AS posologia_catalogo,
    um.descripcion AS unidad_medida,
    m.sw_automatico,
    m.sw_activo,
    m.sw_pos,
    COALESCE(m.edad_minima, 0) AS edad_minima,
    COALESCE(m.edad_maxima, 0) AS edad_maxima,
    COALESCE(m.maximo_veces_dias, 0) AS maximo_veces_dias,
    COALESCE(m.maximo_veces_mes, 0) AS maximo_veces_mes,
    COALESCE(m.maximo_veces_ano, 0) AS maximo_veces_ano,
    COALESCE(m.maximo_veces_vida, 0) AS maximo_veces_vida,
    COALESCE(m.tiempo_limite_dias, 0) AS tiempo_limite_dias,
    COALESCE(m.valor, 0) AS valor,
    (
        SELECT mn.consecutivo_concepto
        FROM administrativo.tb_medicamento_nota_tecnica mn
        INNER JOIN administrativo.tb_concepto_nota_tecnica c
            ON c.consecutivo_concepto = mn.consecutivo_concepto
        WHERE mn.consecutivo_medicamento = m.medicamento
          AND COALESCE(c.sw_activo, 0) = 1
        ORDER BY c.consecutivo_nivel DESC, mn.consecutivo_concepto
        LIMIT 1
    ) AS consecutivo_concepto
FROM administrativo.tb_medicamento m
LEFT JOIN administrativo.tb_unidad_medida um ON um.consecutivo_unidad_medida = m.consecutivo_unidad_medida
WHERE UPPER(TRIM(COALESCE(m.codigo_interno, ''))) = UPPER(TRIM(:cum))
   OR UPPER(TRIM(COALESCE(m.consecutivo, ''))) = UPPER(TRIM(:cum))
LIMIT 1
"""

SUM_AUTORIZACION_MEDICAMENTO = """
SELECT
    am.medicamento,
    COALESCE(SUM(am.cantidad), 0) AS total
FROM administrativo.ss_autorizacion_medicamento am
INNER JOIN administrativo.ss_autorizacion a
    ON a.consecutivo_autorizacion = am.consecutivo_autorizacion
WHERE a.afiliado = :afiliado
  AND COALESCE(a.sw_orden_servicio, 0) = 0
  AND COALESCE(a.sw_activo, 0) <> 0
  AND am.medicamento IN :medicamentos
{fecha_filtro}
GROUP BY am.medicamento
"""

MEDICO_SOLICITANTE_POR_REGISTRO = """
SELECT
    ms.consecutivo_medico,
    ms.registro_medico,
    ms.nombre,
    ms.cargo,
    ms.consecutivo_especialidad,
    ms.tipo_identificacion,
    ms.numero_identificacion,
    e.descripcion AS especialidad_descripcion
FROM administrativo.tb_medico_solicitante ms
LEFT JOIN administrativo.tb_especialidad e ON e.consecutivo_especialidad = ms.consecutivo_especialidad
WHERE UPPER(TRIM(COALESCE(ms.registro_medico, ''))) = UPPER(TRIM(:registro))
LIMIT 1
"""

IPS_SEDE_POR_CONSECUTIVO = """
SELECT
    s.consecutivo_sede_ips,
    s.ips,
    s.nombre_sede,
    s.numero_sede,
    s.municipio,
    tm.descripcion AS municipio_descripcion,
    s.telefono,
    s.correo_electronico,
    s.direccion,
    s.codigo_prestador,
    COALESCE(s.sw_habitlitada, 0) AS sw_habilitada
FROM administrativo.ct_ips_sede s
LEFT JOIN administrativo.tb_municipio tm ON tm.municipio = s.municipio
WHERE s.consecutivo_sede_ips = :consecutivo_sede
  AND s.ips = :ips
LIMIT 1
"""

IPS_SEDE_DEFAULT_POR_IPS = """
SELECT
    s.consecutivo_sede_ips,
    s.ips,
    s.nombre_sede,
    s.numero_sede,
    s.municipio,
    tm.descripcion AS municipio_descripcion,
    s.telefono,
    s.correo_electronico,
    s.direccion,
    s.codigo_prestador,
    COALESCE(s.sw_habitlitada, 0) AS sw_habilitada
FROM administrativo.ct_ips_sede s
LEFT JOIN administrativo.tb_municipio tm ON tm.municipio = s.municipio
WHERE s.ips = :ips
ORDER BY COALESCE(s.sw_habitlitada, 0) DESC, s.consecutivo_sede_ips
LIMIT 1
"""

MODALIDAD_AMBULATORIA = """
SELECT m.consecutivo_modalidad, m.descripcion, m.codigo
FROM administrativo.tb_modalidad_servicio_salud m
WHERE UPPER(TRIM(COALESCE(m.descripcion, ''))) LIKE '%AMBULATOR%'
   OR UPPER(TRIM(COALESCE(m.codigo, ''))) LIKE '%AMBULATOR%'
ORDER BY m.consecutivo_modalidad
LIMIT 1
"""

CIE10_POR_SIMBOLO = """
SELECT c.cie_10 AS consecutivo_cie10, c.simbolo
FROM administrativo.tb_cie10 c
WHERE UPPER(TRIM(COALESCE(c.simbolo, ''))) = UPPER(TRIM(:simbolo))
LIMIT 1
"""

ESPECIALIDAD_POR_NOMBRE = """
SELECT e.consecutivo_especialidad
FROM administrativo.tb_especialidad e
WHERE UPPER(TRIM(COALESCE(e.descripcion, ''))) = UPPER(TRIM(:nombre))
LIMIT 1
"""

COUNT_DIRECCIONAMIENTO_IPS = """
SELECT COUNT(*) AS total
FROM administrativo.tb_direccionamiento_autorizacion d
WHERE d.consecutivo_ips = :ips
  AND COALESCE(d.sw_activo, 0) = 1
"""

MEDICAMENTO_EN_DIRECCIONAMIENTO = """
SELECT 1
FROM administrativo.tb_direccionamiento_autorizacion d
INNER JOIN administrativo.tb_direccionamiento_autorizacion_medicamento dm
    ON dm.consecutivo_direccionamiento = d.consecutivo_direccionamiento
WHERE d.consecutivo_ips = :ips
  AND COALESCE(d.sw_activo, 0) = 1
  AND dm.medicamento = :medicamento
LIMIT 1
"""

AFILIADO_DIRECCIONAMIENTO = """
SELECT
    a.afiliado,
    a.tipo_identificacion,
    a.numero_identificacion,
    a.primer_nombre,
    a.segundo_nombre,
    a.primer_apellido,
    a.segundo_apellido,
    a.fecha_nacimiento,
    a.estado_afiliado,
    COALESCE(a.estado_traslado, 0) AS estado_traslado,
    COALESCE(ac.tipo_regimen, 99) AS tipo_regimen,
    a.correo_electronico,
    a.telefono_1,
    a.celular,
    a.direccion,
    CAST(tm.municipio AS TEXT) AS municipio_codigo,
    tm.descripcion AS municipio_descripcion,
    LEFT(CAST(tm.municipio AS TEXT), 2) AS departamento_codigo
FROM administrativo.af_afiliado a
LEFT JOIN administrativo.af_afiliado_complemento ac ON ac.afiliado = a.afiliado
LEFT JOIN administrativo.tb_municipio tm ON tm.municipio = a.municipio
WHERE CAST(a.tipo_identificacion AS TEXT) = :tipo_doc
  AND TRIM(CAST(a.numero_identificacion AS TEXT)) = TRIM(:numero_doc)
LIMIT 1
"""

INSERT_SS_SOLICITUD = """
INSERT INTO administrativo.ss_solicitud (
    consecutivo_solicitud, afiliado, tipo_servicio_solicitado, prioridad_atencion,
    ubicacion_paciente, sw_internacion, diagnostico_principal, ips_solicitante,
    fecha_solicitud_medico, nombre_medico, cargo, fecha_solicitud,
    tipo_identificacion_afiliado, numero_identificacion_afiliado, estado_afiliado, estado_traslado,
    tipo_regimen_afiliado, municipio_afiliado, departamento_afiliado,
    nit_ips, digito_verificacion_ips, razon_social_ips, codigo_prestador,
    usuario_grabado, fecha_grabado, estado_solicitud, municipio_solicitante,
    numero_solicitud, primer_nombre, segundo_nombre, primer_apellido, segundo_apellido,
    indicador_solicitud, sw_pos, email, consecutivo_especialidad, sw_finalizado,
    consecutivo_ips_registro, fecha_solicitud_proceso, sw_automatico, sw_restringido,
    sw_completa, clasificacion_triaje, sw_remitido, destino_paciente, sw_aiu,
    tipo_entrada, ips_origen, sw_sin_tope, tipo_proceso, sw_pendiente, sw_sucesiva,
    cantidad_total, cantidad_unitaria, sw_embebido, sw_alto_costo, sw_ingreso_hospitalario,
    sw_terminada_hospitalaria, servicio, justificacion_clinica, registro_medico,
    sede_solicitante, diagnostico_relacionado_1, diagnostico_relacionado_2,
    consecutivo_modalidad, telefono_institucional_indicativo,
    telefono_institucional_telefono, telefono_institucional_extension,
    celular_institucional, consecutivo_medico
) VALUES (
    :consecutivo_solicitud, :afiliado, :tipo_servicio_solicitado, :prioridad_atencion,
    :ubicacion_paciente, :sw_internacion, :diagnostico_principal, :ips_solicitante,
    :fecha_solicitud_medico, :nombre_medico, :cargo, :fecha_solicitud,
    :tipo_identificacion_afiliado, :numero_identificacion_afiliado, :estado_afiliado, :estado_traslado,
    :tipo_regimen_afiliado, :municipio_afiliado, :departamento_afiliado,
    :nit_ips, :digito_verificacion_ips, :razon_social_ips, :codigo_prestador,
    :usuario_grabado, :fecha_grabado, :estado_solicitud, :municipio_solicitante,
    :numero_solicitud, :primer_nombre, :segundo_nombre, :primer_apellido, :segundo_apellido,
    1, 1, :email, :consecutivo_especialidad, 0,
    :consecutivo_ips_registro, :fecha_solicitud_proceso, :sw_automatico, 0,
    1, 0, 0, 0, 0,
    2, :ips_origen, 0, 1, 1, 0,
    :cantidad_total, 1, 0, 0, 0,
    1, :servicio, :justificacion_clinica, :registro_medico,
    :sede_solicitante, :diagnostico_relacionado1, :diagnostico_relacionado2,
    :consecutivo_modalidad, :telefono_institucional_indicativo,
    :telefono_institucional_telefono, :telefono_institucional_extension,
    :celular_institucional, :consecutivo_medico
)
"""

INSERT_SS_SOLICITUD_ATENCION = """
INSERT INTO administrativo.ss_solicitud_atencion (consecutivo_solicitud, secuencia, atencion)
VALUES (:consecutivo_solicitud, :secuencia, :atencion)
"""

INSERT_SS_SOLICITUD_MEDICAMENTO = """
INSERT INTO administrativo.ss_solicitud_medicamento (
    consecutivo_solicitud, secuencia, consecutivo_medicamento, cantidad,
    posologia, dias, observacion, estado_aprobado, sw_restringido, sw_aprobado_eps,
    usuario_grabado, fecha_grabado
) VALUES (
    :consecutivo_solicitud, :secuencia, :consecutivo_medicamento, :cantidad,
    :posologia, :dias, :observacion, :estado_aprobado, 0, 0,
    :usuario_grabado, :fecha_grabado
)
"""

INSERT_SS_AUTORIZACION = """
INSERT INTO administrativo.ss_autorizacion (
    consecutivo_autorizacion, consecutivo_interno, consecutivo_interno_base,
    consecutivo_solicitud,
    valor_autorizacion_propio, valor_autorizacion_contrato, valor_base_cobro, valor_cobro,
    saldo_presupuestal,
    consecutivo_ips, municipio_prestador, codigo_prestador, razon_social_prestador,
    nit_prestador, digito_verificacion_prestador, afiliado,
    tipo_identificacion_afiliado, numero_identificacion_afiliado, fecha_nacimiento_afiliado,
    municipio_afiliado, tipo_regimen_afiliado, estado_afiliado, valor_base_aplica,
    origen_servicio, vigencia, fecha_fin_vigencia, fecha_fin_vigencia_servicio,
    fecha_autorizacion_reserva,
    sw_activo, usuario_grabado, fecha_grabado,
    tipo_cobro, primer_nombre_afiliado, segundo_nombre_afiliado, primer_apellido_afiliado,
    segundo_apellido_afiliado, pin, estado_trazabilidad, estado_flujo, tipo_servicio_solicitado, tipo_proceso,
    diagnostico_principal, concepto_autorizacion, consecutivo_ambito, consecutivo_nivel, consecutivo_contrato
) VALUES (
    :consecutivo_autorizacion, :consecutivo_interno, :consecutivo_interno_base,
    :consecutivo_solicitud,
    :valor_autorizacion, :valor_autorizacion, :valor_base_cobro, :valor_cobro,
    :saldo_presupuestal,
    :consecutivo_ips, :municipio_prestador, :codigo_prestador, :razon_social_prestador,
    :nit_prestador, :digito_verificacion_prestador, :afiliado,
    :tipo_identificacion_afiliado, :numero_identificacion_afiliado, :fecha_nacimiento_afiliado,
    :municipio_afiliado, :tipo_regimen_afiliado, :estado_afiliado, :valor_base_aplica,
    1, :vigencia, :fecha_fin_vigencia, :fecha_fin_vigencia_servicio,
    :fecha_autorizacion_reserva,
    1, :usuario_grabado, :fecha_grabado,
    :tipo_cobro, :primer_nombre, :segundo_nombre, :primer_apellido, :segundo_apellido,
    :pin, 1, :estado_flujo, :tipo_servicio_solicitado, 1,
    :diagnostico_principal, 1, :consecutivo_ambito, :consecutivo_nivel, :consecutivo_contrato
)
"""

INSERT_SS_AUTORIZACION_MEDICAMENTO = """
INSERT INTO administrativo.ss_autorizacion_medicamento (
    consecutivo_autorizacion, secuencia, medicamento, cantidad,
    valor_unitario_propio, valor_unitario_contratado, valor_tarifario,
    sw_regulado, valor_regulado, valor_servicio, valor_autoriza, codigo_propio,
    descripcion_codigo_propio, consecutivo_concepto
) VALUES (
    :consecutivo_autorizacion, :secuencia, :medicamento, :cantidad,
    :valor_unitario, :valor_unitario, :valor_tarifario,
    0, 0, :valor_servicio, :valor_autoriza, :codigo_propio,
    :descripcion, :consecutivo_concepto
)
"""

UPDATE_SOLICITUD_AUTORIZADA = """
UPDATE administrativo.ss_solicitud
SET consecutivo_autorizacion = :consecutivo_autorizacion,
    estado_solicitud = 2,
    fecha_respuesta = :fecha_grabado,
    sw_finalizado = 1,
    sw_terminada_hospitalaria = 1,
    ips_autorizada = :consecutivo_ips
WHERE consecutivo_solicitud = :consecutivo_solicitud
"""

UPDATE_SOL_MED_AUTORIZADO = """
UPDATE administrativo.ss_solicitud_medicamento
SET consecutivo_autorizacion = :consecutivo_autorizacion,
    estado_aprobado = 1,
    usuario_aprobado = :usuario,
    fecha_aprobado = :fecha_grabado
WHERE consecutivo_solicitud = :consecutivo_solicitud
  AND secuencia = :secuencia
"""

UPDATE_CT_IPS_SS_SOLICITUD = """
UPDATE administrativo.ct_ips_ss_solicitud
SET solicitud = :consecutivo_solicitud,
    estado = :estado,
    usuario_cierre = CASE WHEN :estado = 2 THEN :usuario ELSE usuario_cierre END,
    fecha_cierre = CASE WHEN :estado = 2 THEN :fecha_grabado ELSE fecha_cierre END
WHERE consecutivo_solicitud = :consecutivo_ips_ss
"""

INSERT_CT_IPS_SS_AUTORIZACION = """
INSERT INTO administrativo.ct_ips_ss_solicitud_autorizacion (
    consecutivo_solicitud, consecutivo_autorizacion, fecha_autorizacion
) VALUES (
    :consecutivo_ips_ss, :consecutivo_autorizacion, :fecha_autorizacion
)
"""

AUTORIZACION_ACTIVADA_POR_PIN = """
SELECT
    a.consecutivo_autorizacion,
    a.consecutivo_interno,
    a.consecutivo_saldo,
    a.consecutivo_solicitud,
    a.pin,
    a.consecutivo_ips,
    a.fecha_fin_vigencia,
    a.fecha_fin_vigencia_servicio,
    a.fecha_grabado,
    a.valor_autorizacion_propio AS valor_autorizacion,
    a.estado_trazabilidad,
    a.sw_activo,
    a.fecha_real_prestacion_servicio,
    s.numero_solicitud
FROM administrativo.ss_autorizacion a
INNER JOIN administrativo.ss_solicitud s ON s.consecutivo_solicitud = a.consecutivo_solicitud
WHERE UPPER(TRIM(COALESCE(a.tipo_identificacion_afiliado, ''))) = UPPER(TRIM(:tipo_doc))
  AND TRIM(CAST(a.numero_identificacion_afiliado AS TEXT)) = TRIM(:numero_doc)
  AND UPPER(TRIM(COALESCE(a.pin, ''))) = UPPER(TRIM(:pin))
  AND a.consecutivo_ips = :consecutivo_ips
  AND a.fecha_activacion IS NOT NULL
  AND a.fecha_anula IS NULL
ORDER BY a.consecutivo_autorizacion DESC
LIMIT 1
"""

AUTORIZACION_PENDIENTE_ACTIVACION = """
SELECT
    a.consecutivo_autorizacion,
    a.consecutivo_interno,
    a.consecutivo_saldo,
    a.consecutivo_solicitud,
    a.pin,
    a.consecutivo_ips,
    a.fecha_fin_vigencia,
    a.fecha_fin_vigencia_servicio,
    a.fecha_grabado,
    a.valor_autorizacion_propio AS valor_autorizacion,
    a.estado_trazabilidad,
    a.sw_activo,
    a.fecha_real_prestacion_servicio,
    s.numero_solicitud
FROM administrativo.ss_autorizacion a
INNER JOIN administrativo.ss_solicitud s ON s.consecutivo_solicitud = a.consecutivo_solicitud
WHERE UPPER(TRIM(COALESCE(a.tipo_identificacion_afiliado, ''))) = UPPER(TRIM(:tipo_doc))
  AND TRIM(CAST(a.numero_identificacion_afiliado AS TEXT)) = TRIM(:numero_doc)
  AND UPPER(TRIM(COALESCE(a.pin, ''))) = UPPER(TRIM(:pin))
  AND a.consecutivo_ips = :consecutivo_ips
  AND a.fecha_activacion IS NULL
  AND a.fecha_anula IS NULL
ORDER BY a.consecutivo_autorizacion DESC
LIMIT 1
"""

UPDATE_CT_IPS_SS_URL_ARCHIVO = """
UPDATE administrativo.ct_ips_ss_solicitud
SET url_archivo = :url_archivo
WHERE consecutivo_solicitud = :consecutivo_ips_ss
"""

TIPO_SOPORTE_ORDEN_MEDICA = """
SELECT ts.consecutivo_soporte
FROM administrativo.tb_tipo_soporte ts
WHERE COALESCE(ts.sw_activo, 0) = 1
  AND (
    UPPER(COALESCE(ts.descripcion, '')) LIKE '%ORDEN%'
    OR UPPER(COALESCE(ts.descripcion, '')) LIKE '%MEDICA%'
    OR UPPER(COALESCE(ts.descripcion, '')) LIKE '%SOLICITUD%'
  )
ORDER BY ts.consecutivo_soporte
LIMIT 1
"""

UPSERT_SS_SOLICITUD_SOPORTE = """
INSERT INTO administrativo.ss_solicitud_soporte (
    consecutivo_solicitud, consecutivo_soporte, url
) VALUES (
    :consecutivo_solicitud, :consecutivo_soporte, :url
)
ON CONFLICT (consecutivo_solicitud, consecutivo_soporte)
DO UPDATE SET url = EXCLUDED.url
"""

ACTIVAR_SS_AUTORIZACION = """
UPDATE administrativo.ss_autorizacion
SET fecha_activacion = :fecha_activacion,
    fecha_real_autorizacion = :fecha_real_autorizacion,
    usuario_activacion = :usuario,
    estado_trazabilidad = 2,
    estado_flujo = :estado_flujo,
    sw_activo = 1,
    consecutivo_interno_base = COALESCE(NULLIF(consecutivo_interno_base, '0'), :consecutivo_interno_base),
    consecutivo_interno = COALESCE(NULLIF(consecutivo_interno, '0'), :consecutivo_interno),
    fecha_autorizacion_reserva = COALESCE(fecha_autorizacion_reserva, :fecha_autorizacion_reserva),
    fecha_fin_vigencia_servicio = :fecha_fin_vigencia_servicio
WHERE consecutivo_autorizacion = :consecutivo_autorizacion
  AND fecha_activacion IS NULL
  AND fecha_anula IS NULL
"""

AUTORIZACION_ACTIVADA_SIN_CONFIRMAR = """
SELECT
    a.consecutivo_autorizacion,
    a.consecutivo_interno,
    a.consecutivo_saldo,
    a.consecutivo_solicitud,
    a.pin,
    a.consecutivo_ips,
    a.fecha_fin_vigencia,
    a.fecha_fin_vigencia_servicio,
    a.fecha_grabado,
    a.valor_autorizacion_propio AS valor_autorizacion,
    a.estado_trazabilidad,
    a.sw_activo,
    a.fecha_real_prestacion_servicio,
    s.numero_solicitud
FROM administrativo.ss_autorizacion a
INNER JOIN administrativo.ss_solicitud s ON s.consecutivo_solicitud = a.consecutivo_solicitud
WHERE UPPER(TRIM(COALESCE(a.tipo_identificacion_afiliado, ''))) = UPPER(TRIM(:tipo_doc))
  AND TRIM(CAST(a.numero_identificacion_afiliado AS TEXT)) = TRIM(:numero_doc)
  AND UPPER(TRIM(COALESCE(a.pin, ''))) = UPPER(TRIM(:pin))
  AND a.consecutivo_ips = :consecutivo_ips
  AND a.fecha_activacion IS NOT NULL
  AND a.fecha_real_prestacion_servicio IS NULL
  AND COALESCE(a.estado_trazabilidad, 0) < 3
  AND a.fecha_anula IS NULL
ORDER BY a.consecutivo_autorizacion DESC
LIMIT 1
"""

AUTORIZACION_COMPLETADA_POR_PIN = """
SELECT
    a.consecutivo_autorizacion,
    a.consecutivo_interno,
    a.consecutivo_saldo,
    a.consecutivo_solicitud,
    a.pin,
    a.consecutivo_ips,
    a.fecha_fin_vigencia,
    a.fecha_fin_vigencia_servicio,
    a.fecha_grabado,
    a.valor_autorizacion_propio AS valor_autorizacion,
    a.estado_trazabilidad,
    a.sw_activo,
    a.fecha_real_prestacion_servicio,
    s.numero_solicitud
FROM administrativo.ss_autorizacion a
INNER JOIN administrativo.ss_solicitud s ON s.consecutivo_solicitud = a.consecutivo_solicitud
WHERE UPPER(TRIM(COALESCE(a.tipo_identificacion_afiliado, ''))) = UPPER(TRIM(:tipo_doc))
  AND TRIM(CAST(a.numero_identificacion_afiliado AS TEXT)) = TRIM(:numero_doc)
  AND UPPER(TRIM(COALESCE(a.pin, ''))) = UPPER(TRIM(:pin))
  AND a.consecutivo_ips = :consecutivo_ips
  AND a.fecha_real_prestacion_servicio IS NOT NULL
  AND a.fecha_anula IS NULL
ORDER BY a.consecutivo_autorizacion DESC
LIMIT 1
"""

UPDATE_MEDICAMENTOS_FECHA_PROGRAMACION = """
UPDATE administrativo.ss_autorizacion_medicamento
SET fecha_programacion = :fecha_programacion
WHERE consecutivo_autorizacion = :consecutivo_autorizacion
  AND fecha_cancelacion IS NULL
"""

UPDATE_MEDICAMENTOS_FECHA_PRESTACION = """
UPDATE administrativo.ss_autorizacion_medicamento
SET fecha_prestacion_servicio = :fecha_prestacion
WHERE consecutivo_autorizacion = :consecutivo_autorizacion
  AND fecha_cancelacion IS NULL
"""

CONFIRMAR_SS_AUTORIZACION = """
UPDATE administrativo.ss_autorizacion
SET fecha_real_prestacion_servicio = :fecha_real_prestacion,
    estado_trazabilidad = 3,
    estado_flujo = :estado_flujo,
    fecha_fin_vigencia_servicio = NULL,
    url_activacion = COALESCE(:url_activacion, url_activacion),
    sw_activo = 1
WHERE consecutivo_autorizacion = :consecutivo_autorizacion
  AND fecha_activacion IS NOT NULL
  AND fecha_real_prestacion_servicio IS NULL
  AND fecha_anula IS NULL
"""

SOLICITUD_PENDIENTE_AUTORIZACION = """
SELECT
    s.consecutivo_solicitud,
    s.numero_solicitud,
    s.afiliado,
    s.ips_solicitante,
    s.ips_origen,
    s.diagnostico_principal,
    s.diagnostico_relacionado_1 AS diagnostico_relacionado1,
    s.diagnostico_relacionado_2 AS diagnostico_relacionado2,
    s.consecutivo_especialidad,
    s.consecutivo_modalidad,
    s.fecha_solicitud_proceso,
    s.fecha_solicitud_medico,
    s.nombre_medico,
    s.cargo,
    s.email,
    s.registro_medico,
    s.consecutivo_medico,
    s.justificacion_clinica,
    s.tipo_identificacion_afiliado,
    s.numero_identificacion_afiliado,
    s.sw_automatico,
    c.consecutivo_solicitud AS consecutivo_ips_ss,
    c.estado AS estado_ips_ss
FROM administrativo.ss_solicitud s
INNER JOIN administrativo.ct_ips_ss_solicitud c ON c.solicitud = s.consecutivo_solicitud
WHERE s.consecutivo_solicitud = :consecutivo_solicitud
  AND TRIM(CAST(s.numero_identificacion_afiliado AS TEXT)) = TRIM(:numero_doc)
  AND UPPER(TRIM(COALESCE(s.tipo_identificacion_afiliado, ''))) = UPPER(TRIM(:tipo_doc))
  AND s.consecutivo_autorizacion IS NULL
  AND s.estado_solicitud = 1
  AND c.estado = 1
LIMIT 1
"""

MEDICAMENTOS_SOLICITUD_PARA_EVAL = """
SELECT
    sm.secuencia,
    sm.cantidad,
    sm.dias,
    sm.posologia,
    sm.observacion,
    m.medicamento,
    m.codigo_interno AS cum,
    m.descripcion
FROM administrativo.ss_solicitud_medicamento sm
INNER JOIN administrativo.tb_medicamento m ON m.medicamento = sm.consecutivo_medicamento
WHERE sm.consecutivo_solicitud = :consecutivo_solicitud
ORDER BY sm.secuencia
"""

AUTORIZACION_POR_SOLICITUD = """
SELECT
    a.consecutivo_autorizacion,
    a.consecutivo_interno,
    a.consecutivo_saldo,
    a.consecutivo_solicitud,
    a.pin,
    a.consecutivo_ips,
    a.fecha_fin_vigencia,
    a.fecha_grabado,
    a.valor_autorizacion_propio AS valor_autorizacion,
    a.estado_trazabilidad,
    a.sw_activo,
    a.fecha_real_prestacion_servicio,
    a.fecha_activacion,
    s.numero_solicitud
FROM administrativo.ss_autorizacion a
INNER JOIN administrativo.ss_solicitud s ON s.consecutivo_solicitud = a.consecutivo_solicitud
WHERE a.consecutivo_solicitud = :consecutivo_solicitud
  AND a.fecha_anula IS NULL
ORDER BY a.consecutivo_autorizacion DESC
LIMIT 1
"""

TIPO_COBRO_DESCRIPCION = {
    0: "EXENTO",
    1: "COPAGO SUBSIDIADO",
    2: "CUOTA MODERADORA",
    3: "COPAGO",
}


class MessiahDireccionamientoRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def fetch_ips_por_nit(self, nit: str) -> dict[str, Any] | None:
        row = self.db.execute(text(IPS_DETALLE_POR_NIT), {"nit": nit.strip()}).mappings().first()
        return dict(row) if row else None

    def fetch_ips_por_consecutivo(self, ips: int) -> dict[str, Any] | None:
        row = self.db.execute(text(IPS_DETALLE_POR_CONSECUTIVO), {"ips": int(ips)}).mappings().first()
        return dict(row) if row else None

    def fetch_ips_por_nombre(self, nombre: str) -> dict[str, Any] | None:
        row = self.db.execute(text(IPS_DETALLE_POR_NOMBRE), {"nombre": nombre.strip()}).mappings().first()
        return dict(row) if row else None

    def fetch_medicamento(self, codigo_interno: str) -> dict[str, Any] | None:
        return self.fetch_medicamento_por_cum(codigo_interno)

    def fetch_medicamento_por_cum(self, cum: str) -> dict[str, Any] | None:
        clave = cum.strip()
        if not clave:
            return None
        row = self.db.execute(text(MEDICAMENTO_POR_CUM), {"cum": clave}).mappings().first()
        return dict(row) if row else None

    def fetch_concepto_nota_tecnica_medicamento(self, medicamento: int) -> int | None:
        row = self.db.execute(
            text(
                """
                SELECT mn.consecutivo_concepto
                FROM administrativo.tb_medicamento_nota_tecnica mn
                INNER JOIN administrativo.tb_concepto_nota_tecnica c
                    ON c.consecutivo_concepto = mn.consecutivo_concepto
                WHERE mn.consecutivo_medicamento = :medicamento
                  AND COALESCE(c.sw_activo, 0) = 1
                ORDER BY c.consecutivo_nivel DESC, mn.consecutivo_concepto
                LIMIT 1
                """
            ),
            {"medicamento": int(medicamento)},
        ).mappings().first()
        if row:
            return int(row["consecutivo_concepto"])
        fallback = self.db.execute(
            text(
                """
                SELECT mn.consecutivo_concepto
                FROM administrativo.tb_medicamento_nota_tecnica mn
                WHERE mn.consecutivo_medicamento = :medicamento
                ORDER BY mn.consecutivo_concepto
                LIMIT 1
                """
            ),
            {"medicamento": int(medicamento)},
        ).scalar_one_or_none()
        return int(fallback) if fallback is not None else None

    def resolver_concepto_medicamento_obligatorio(
        self,
        med_row: dict[str, Any],
    ) -> int:
        """
        Messiah exige consecutivo_concepto en ss_autorizacion_medicamento:
        getGeneralAuthorizationMedicines hace INNER JOIN tb_concepto_nota_tecnica.
        """
        concepto = med_row.get("consecutivo_concepto")
        if concepto is not None:
            return int(concepto)
        medicamento_id = med_row.get("medicamento")
        if medicamento_id is None:
            raise ValueError("Medicamento sin id de catálogo para resolver concepto nota técnica.")
        concepto = self.fetch_concepto_nota_tecnica_medicamento(int(medicamento_id))
        if concepto is None:
            codigo = str(med_row.get("codigo_interno") or "").strip()
            raise ValueError(
                f"Medicamento {codigo or medicamento_id} sin concepto nota técnica en tb_medicamento_nota_tecnica."
            )
        return int(concepto)

    def fetch_medico_solicitante(self, registro_medico: str) -> dict[str, Any] | None:
        row = self.db.execute(
            text(MEDICO_SOLICITANTE_POR_REGISTRO),
            {"registro": registro_medico.strip()},
        ).mappings().first()
        return dict(row) if row else None

    def fetch_ips_sede(self, ips: int, consecutivo_sede: int | None) -> dict[str, Any] | None:
        if consecutivo_sede is not None:
            row = self.db.execute(
                text(IPS_SEDE_POR_CONSECUTIVO),
                {"ips": int(ips), "consecutivo_sede": int(consecutivo_sede)},
            ).mappings().first()
            return dict(row) if row else None
        row = self.db.execute(text(IPS_SEDE_DEFAULT_POR_IPS), {"ips": int(ips)}).mappings().first()
        return dict(row) if row else None

    def fetch_modalidad_ambulatoria(self) -> dict[str, Any] | None:
        """Busca modalidad ambulatoria por descripcion/codigo (columna modalidad es smallint, no texto)."""
        row = self.db.execute(text(MODALIDAD_AMBULATORIA)).mappings().first()
        return dict(row) if row else None

    def fetch_consecutivo_modalidad_ambulatoria(self) -> int:
        row = self.fetch_modalidad_ambulatoria()
        return int(row["consecutivo_modalidad"]) if row else 1

    def registrar_origenes_atencion(self, consecutivo_solicitud: int, origenes: list[int]) -> None:
        for seq, origen in enumerate(origenes):
            self.db.execute(
                text(INSERT_SS_SOLICITUD_ATENCION),
                {
                    "consecutivo_solicitud": int(consecutivo_solicitud),
                    "secuencia": seq,
                    "atencion": int(origen),
                },
            )

    def fetch_cie10(self, simbolo: str) -> dict[str, Any] | None:
        row = self.db.execute(text(CIE10_POR_SIMBOLO), {"simbolo": simbolo.strip()}).mappings().first()
        return dict(row) if row else None

    def fetch_especialidad(self, nombre: str) -> int | None:
        row = self.db.execute(text(ESPECIALIDAD_POR_NOMBRE), {"nombre": nombre.strip()}).mappings().first()
        return int(row["consecutivo_especialidad"]) if row else None

    def fetch_afiliado(self, tipo_doc: str, numero_doc: str) -> dict[str, Any] | None:
        row = self.db.execute(
            text(AFILIADO_DIRECCIONAMIENTO),
            {"tipo_doc": tipo_doc.strip(), "numero_doc": numero_doc.strip()},
        ).mappings().first()
        return dict(row) if row else None

    def ips_requiere_direccionamiento(self, ips: int) -> bool:
        total = self.db.execute(text(COUNT_DIRECCIONAMIENTO_IPS), {"ips": ips}).scalar_one()
        return int(total) > 0

    def medicamento_en_direccionamiento(self, ips: int, medicamento: int) -> bool:
        return (
            self.db.execute(
                text(MEDICAMENTO_EN_DIRECCIONAMIENTO),
                {"ips": ips, "medicamento": medicamento},
            ).first()
            is not None
        )

    def fetch_contrato_ips_municipio_afiliado(self, ips: int, municipio_afiliado: str) -> dict[str, Any] | None:
        row = self.db.execute(
            text(CONTRATO_IPS_MUNICIPIO_AFILIADO),
            {"ips": int(ips), "municipio_afiliado": (municipio_afiliado or "").strip()},
        ).mappings().first()
        return dict(row) if row else None

    def ips_tiene_contrato_medicamento_municipio(self, ips: int, municipio_afiliado: str) -> bool:
        """True si existe al menos un contrato activo con tarifario de medicamentos y cobertura del municipio."""
        return (
            self.db.execute(
                text(CONTRATO_IPS_MUNICIPIO_EXISTE),
                {"ips": int(ips), "municipio_afiliado": (municipio_afiliado or "").strip()},
            ).first()
            is not None
        )

    def fetch_tarifario_medicamento_contratos_municipio(
        self,
        ips: int,
        municipio_afiliado: str,
        codigo_interno: str,
    ) -> dict[str, Any] | None:
        """
        Busca el CUM/código en todos los tarifarios de contratos activos de la IPS
        con cobertura sobre el municipio del afiliado (mismo criterio que Messiah).
        Devuelve la línea de menor valor cuando hay varias coincidencias.
        """
        row = self.db.execute(
            text(TARIFARIO_MEDICAMENTO_CONTRATOS_MUNICIPIO),
            {
                "ips": int(ips),
                "municipio_afiliado": (municipio_afiliado or "").strip(),
                "codigo": codigo_interno.strip(),
            },
        ).mappings().first()
        return dict(row) if row else None

    def fetch_tarifario_medicamento(self, consecutivo_tarifario: int, codigo_interno: str) -> dict[str, Any] | None:
        row = self.db.execute(
            text(TARIFARIO_MEDICAMENTO),
            {"consecutivo_tarifario": int(consecutivo_tarifario), "codigo": codigo_interno.strip()},
        ).mappings().first()
        return dict(row) if row else None

    def sum_cantidades_autorizacion_medicamento(
        self,
        afiliado_id: int,
        medicamento_ids: list[int],
        *,
        fecha_inicio: datetime | None = None,
        fecha_fin: datetime | None = None,
    ) -> dict[int, float]:
        """Suma cantidades autorizadas (Messiah: sw_orden_servicio=0, sw_activo<>0)."""
        if not medicamento_ids:
            return {}
        if fecha_inicio is not None and fecha_fin is not None:
            fecha_filtro = (
                " AND a.fecha_grabado >= :fecha_inicio AND a.fecha_grabado <= :fecha_fin "
            )
        else:
            fecha_filtro = ""
        sql = SUM_AUTORIZACION_MEDICAMENTO.format(fecha_filtro=fecha_filtro)
        stmt = text(sql).bindparams(bindparam("medicamentos", expanding=True))
        params: dict[str, Any] = {
            "afiliado": int(afiliado_id),
            "medicamentos": [int(m) for m in medicamento_ids],
        }
        if fecha_inicio is not None and fecha_fin is not None:
            params["fecha_inicio"] = fecha_inicio
            params["fecha_fin"] = fecha_fin
        rows = self.db.execute(stmt, params).mappings().all()
        return {int(r["medicamento"]): float(r["total"]) for r in rows}

    @staticmethod
    def generar_pin() -> str:
        return secrets.token_hex(4).upper()

    def fetch_autorizacion_pendiente_activacion(
        self,
        *,
        tipo_doc_abrev: str,
        numero_identificacion: str,
        pin: str,
        consecutivo_ips: int,
    ) -> dict[str, Any] | None:
        """Messiah getSsAutorizacionXActivacion (confirma=false): fecha_activacion IS NULL."""
        row = self.db.execute(
            text(AUTORIZACION_PENDIENTE_ACTIVACION),
            {
                "tipo_doc": tipo_doc_abrev.strip(),
                "numero_doc": numero_identificacion.strip(),
                "pin": pin.strip(),
                "consecutivo_ips": int(consecutivo_ips),
            },
        ).mappings().first()
        return dict(row) if row else None

    def fetch_autorizacion_activada_por_pin(
        self,
        *,
        tipo_doc_abrev: str,
        numero_identificacion: str,
        pin: str,
        consecutivo_ips: int,
    ) -> dict[str, Any] | None:
        """Autorización ya activada (Messiah o API) — misma búsqueda que activación con fecha_activacion IS NOT NULL."""
        row = self.db.execute(
            text(AUTORIZACION_ACTIVADA_POR_PIN),
            {
                "tipo_doc": tipo_doc_abrev.strip(),
                "numero_doc": numero_identificacion.strip(),
                "pin": pin.strip(),
                "consecutivo_ips": int(consecutivo_ips),
            },
        ).mappings().first()
        return dict(row) if row else None

    def actualizar_url_archivo_ct_ips_ss(
        self,
        consecutivo_ips_ss: int,
        url_archivo: str,
    ) -> None:
        self.db.execute(
            text(UPDATE_CT_IPS_SS_URL_ARCHIVO),
            {
                "consecutivo_ips_ss": int(consecutivo_ips_ss),
                "url_archivo": url_archivo.strip(),
            },
        )

    def registrar_soporte_ss_solicitud(
        self,
        *,
        consecutivo_solicitud: int,
        url: str,
    ) -> bool:
        """
        Registra soporte en ss_solicitud_soporte (vista SsSolicitud en Messiah).
        Retorna False si no hay tipo de soporte parametrizado.
        """
        tipo = self.db.execute(text(TIPO_SOPORTE_ORDEN_MEDICA)).scalar()
        if tipo is None:
            return False
        self.db.execute(
            text(UPSERT_SS_SOLICITUD_SOPORTE),
            {
                "consecutivo_solicitud": int(consecutivo_solicitud),
                "consecutivo_soporte": int(tipo),
                "url": url.strip(),
            },
        )
        return True

    def fetch_autorizacion_activada_sin_confirmar(
        self,
        *,
        tipo_doc_abrev: str,
        numero_identificacion: str,
        pin: str,
        consecutivo_ips: int,
    ) -> dict[str, Any] | None:
        """Messiah enviarBuscarConfirmar: fecha_activacion NOT NULL y sin prestación confirmada."""
        row = self.db.execute(
            text(AUTORIZACION_ACTIVADA_SIN_CONFIRMAR),
            {
                "tipo_doc": tipo_doc_abrev.strip(),
                "numero_doc": numero_identificacion.strip(),
                "pin": pin.strip(),
                "consecutivo_ips": int(consecutivo_ips),
            },
        ).mappings().first()
        return dict(row) if row else None

    def fetch_autorizacion_completada_por_pin(
        self,
        *,
        tipo_doc_abrev: str,
        numero_identificacion: str,
        pin: str,
        consecutivo_ips: int,
    ) -> dict[str, Any] | None:
        row = self.db.execute(
            text(AUTORIZACION_COMPLETADA_POR_PIN),
            {
                "tipo_doc": tipo_doc_abrev.strip(),
                "numero_doc": numero_identificacion.strip(),
                "pin": pin.strip(),
                "consecutivo_ips": int(consecutivo_ips),
            },
        ).mappings().first()
        return dict(row) if row else None

    def fetch_solicitud_pendiente_autorizacion(
        self,
        *,
        consecutivo_solicitud: int,
        tipo_doc_abrev: str,
        numero_identificacion: str,
    ) -> dict[str, Any] | None:
        row = self.db.execute(
            text(SOLICITUD_PENDIENTE_AUTORIZACION),
            {
                "consecutivo_solicitud": int(consecutivo_solicitud),
                "tipo_doc": tipo_doc_abrev,
                "numero_doc": numero_identificacion,
            },
        ).mappings().first()
        return dict(row) if row else None

    def fetch_medicamentos_solicitud_para_evaluacion(
        self,
        consecutivo_solicitud: int,
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(MEDICAMENTOS_SOLICITUD_PARA_EVAL),
            {"consecutivo_solicitud": int(consecutivo_solicitud)},
        ).mappings().all()
        return [dict(r) for r in rows]

    def fetch_autorizacion_por_solicitud(
        self,
        consecutivo_solicitud: int,
    ) -> dict[str, Any] | None:
        row = self.db.execute(
            text(AUTORIZACION_POR_SOLICITUD),
            {"consecutivo_solicitud": int(consecutivo_solicitud)},
        ).mappings().first()
        return dict(row) if row else None

    def activar_autorizacion(
        self,
        consecutivo_autorizacion: int,
        username: str,
        *,
        fecha_real_autorizacion: date | None = None,
        consecutivo_interno_base: str | None = None,
    ) -> bool:
        """Activa autorización (estado trazabilidad ACTIVADA=2). Retorna False si ya estaba activada."""
        fecha_real = fecha_real_autorizacion or hoy_bogota()
        fecha_activacion = ahora_bogota()
        interno_base = consecutivo_interno_base or str(
            self.db.execute(text(NEXT_INTERNO_ACTIVACION)).scalar_one()
        )
        _, _, fecha_fin_vigencia_servicio = calcular_vigencias_autorizacion_medicamentos(
            self.db, fecha_real
        )
        result = self.db.execute(
            text(ACTIVAR_SS_AUTORIZACION),
            {
                "consecutivo_autorizacion": int(consecutivo_autorizacion),
                "usuario": username[:100],
                "fecha_real_autorizacion": fecha_real,
                "fecha_activacion": fecha_activacion,
                "estado_flujo": ESTADO_FLUJO_ACTIVA,
                "consecutivo_interno_base": interno_base,
                "consecutivo_interno": interno_base,
                "fecha_autorizacion_reserva": fecha_activacion,
                "fecha_fin_vigencia_servicio": fecha_fin_vigencia_servicio,
            },
        )
        if (result.rowcount or 0) > 0:
            registrar_estado_flujo_autorizacion(
                self.db,
                consecutivo_autorizacion=int(consecutivo_autorizacion),
                estado_flujo=ESTADO_FLUJO_ACTIVA,
                fecha_estado=fecha_activacion,
                username=username,
            )
            registrar_logs_activacion_medicamentos(
                self.db,
                consecutivo_autorizacion=int(consecutivo_autorizacion),
                username=username,
            )
        self.db.commit()
        return (result.rowcount or 0) > 0

    def programar_medicamentos_autorizacion(
        self,
        consecutivo_autorizacion: int,
        fecha_programacion: date,
    ) -> None:
        self.db.execute(
            text(UPDATE_MEDICAMENTOS_FECHA_PROGRAMACION),
            {
                "consecutivo_autorizacion": int(consecutivo_autorizacion),
                "fecha_programacion": fecha_programacion,
            },
        )

    def confirmar_prestacion_autorizacion(
        self,
        consecutivo_autorizacion: int,
        *,
        fecha_real_prestacion: date,
        fecha_prestacion_lineas: date,
        username: str,
        url_activacion: str | None = None,
    ) -> bool:
        """Confirma prestación (estado trazabilidad COMPLETADA=3). Retorna False si ya estaba confirmada."""
        self.db.execute(
            text(UPDATE_MEDICAMENTOS_FECHA_PRESTACION),
            {
                "consecutivo_autorizacion": int(consecutivo_autorizacion),
                "fecha_prestacion": fecha_prestacion_lineas,
            },
        )
        result = self.db.execute(
            text(CONFIRMAR_SS_AUTORIZACION),
            {
                "consecutivo_autorizacion": int(consecutivo_autorizacion),
                "fecha_real_prestacion": fecha_real_prestacion,
                "url_activacion": url_activacion,
                "estado_flujo": ESTADO_FLUJO_CONFIRMADA,
            },
        )
        if (result.rowcount or 0) > 0:
            registrar_estado_flujo_autorizacion(
                self.db,
                consecutivo_autorizacion=int(consecutivo_autorizacion),
                estado_flujo=ESTADO_FLUJO_CONFIRMADA,
                fecha_estado=fecha_real_prestacion,
                username=username,
            )
            registrar_logs_confirmacion_medicamentos(
                self.db,
                consecutivo_autorizacion=int(consecutivo_autorizacion),
                username=username,
            )
        self.db.commit()
        return (result.rowcount or 0) > 0

    def crear_solicitud_orden_medica(
        self,
        *,
        afiliado: dict[str, Any],
        ips_solicitante: dict[str, Any],
        sede: dict[str, Any] | None,
        ips_ss_consecutivo: int,
        diagnostico_cie10: int,
        diagnostico_relacionado1: int | None,
        diagnostico_relacionado2: int | None,
        consecutivo_especialidad: int | None,
        origenes_atencion: list[int],
        form: dict[str, Any],
        medicamentos: list[dict[str, Any]],
        username: str,
        tipo_doc_abrev: str,
    ) -> dict[str, Any]:
        """Crea ss_solicitud y líneas de medicamento sin autorización (Messiah: solo solicitud)."""
        self.db.execute(text(LOCK_DIRECCIONAMIENTO))
        consecutivo_solicitud = int(self.db.execute(text(NEXT_SS_SOLICITUD)).scalar_one())
        numero_solicitud = str(self.db.execute(text(NEXT_NUMERO_SOLICITUD)).scalar_one())

        cantidad_total = sum(int(m["cantidad"]) for m in medicamentos)
        fecha_hoy = hoy_bogota()
        fecha_grabado = ahora_bogota()
        fecha_solicitud = fecha_grabado
        sede_row = sede or {}
        municipio_solicitante = str(
            sede_row.get("municipio") or ips_solicitante.get("municipio") or ""
        )
        codigo_prestador = str(
            sede_row.get("codigo_prestador") or ips_solicitante.get("codigo_prestador") or ""
        )
        telefono_inst = str(sede_row.get("telefono") or ips_solicitante.get("telefono") or "")
        email_inst = str(form.get("email_institucional") or sede_row.get("correo_electronico") or form["email"])[:200]
        consecutivo_modalidad = int(form.get("consecutivo_modalidad") or 1)
        sede_id = sede_row.get("consecutivo_sede_ips")

        self.db.execute(
            text(INSERT_SS_SOLICITUD),
            {
                "consecutivo_solicitud": consecutivo_solicitud,
                "afiliado": int(afiliado["afiliado"]),
                "tipo_servicio_solicitado": TIPO_SERVICIO_SOLICITADO_MEDICAMENTOS,
                "prioridad_atencion": int(form.get("prioridad_atencion") or 1),
                "ubicacion_paciente": int(form.get("ubicacion_paciente_codigo") or 1),
                "sw_internacion": int(form.get("sw_internacion") or 0),
                "diagnostico_principal": diagnostico_cie10,
                "ips_solicitante": int(ips_solicitante["ips"]),
                "fecha_solicitud_medico": form["fecha_solicitud_medico"],
                "nombre_medico": form["nombre_profesional_solicitante"][:120],
                "cargo": form["cargo_actividad"][:80],
                "fecha_solicitud": fecha_solicitud,
                "tipo_identificacion_afiliado": tipo_doc_abrev,
                "numero_identificacion_afiliado": str(afiliado["numero_identificacion"]),
                "estado_afiliado": int(afiliado.get("estado_afiliado") or 1),
                "estado_traslado": int(afiliado.get("estado_traslado") or 0),
                "tipo_regimen_afiliado": int(afiliado.get("tipo_regimen") or 99),
                "municipio_afiliado": str(afiliado.get("municipio_codigo") or ""),
                "departamento_afiliado": str(afiliado.get("departamento_codigo") or "")[:2] or None,
                "nit_ips": str(ips_solicitante["nit"]),
                "digito_verificacion_ips": str(ips_solicitante.get("digito_verificacion") or ""),
                "razon_social_ips": str(ips_solicitante["razon_social"]),
                "codigo_prestador": codigo_prestador,
                "usuario_grabado": username[:100],
                "fecha_grabado": fecha_grabado,
                "estado_solicitud": 1,
                "municipio_solicitante": municipio_solicitante,
                "numero_solicitud": numero_solicitud,
                "primer_nombre": str(afiliado.get("primer_nombre") or "")[:60],
                "segundo_nombre": (afiliado.get("segundo_nombre") or None),
                "primer_apellido": str(afiliado.get("primer_apellido") or "")[:60],
                "segundo_apellido": (afiliado.get("segundo_apellido") or None),
                "email": email_inst,
                "consecutivo_especialidad": consecutivo_especialidad,
                "consecutivo_ips_registro": int(ips_solicitante["ips"]),
                "fecha_solicitud_proceso": form["fecha_solicitud_proceso"],
                "sw_automatico": 0,
                "cantidad_total": cantidad_total,
                "justificacion_clinica": (form.get("justificacion_clinica") or form.get("observacion") or "")[:4000],
                "registro_medico": form.get("registro_profesional") or "",
                "ips_origen": int(ips_solicitante["ips"]),
                "sede_solicitante": int(sede_id) if sede_id is not None else None,
                "diagnostico_relacionado1": diagnostico_relacionado1,
                "diagnostico_relacionado2": diagnostico_relacionado2,
                "consecutivo_modalidad": consecutivo_modalidad,
                "telefono_institucional_indicativo": str(form.get("telefono_institucional_indicativo") or "57")[:10],
                "telefono_institucional_telefono": telefono_inst[:30] if telefono_inst else None,
                "telefono_institucional_extension": (form.get("telefono_institucional_extension") or None),
                "celular_institucional": telefono_inst[:30] if telefono_inst else None,
                "consecutivo_medico": form.get("consecutivo_medico"),
                "servicio": str(form.get("servicio") or SERVICIO_SOLICITUD_MEDICAMENTOS)[:200],
            },
        )
        if origenes_atencion:
            self.registrar_origenes_atencion(consecutivo_solicitud, origenes_atencion)

        for med in medicamentos:
            obs = str(med.get("observacion") or "").strip()
            self.db.execute(
                text(INSERT_SS_SOLICITUD_MEDICAMENTO),
                {
                    "consecutivo_solicitud": consecutivo_solicitud,
                    "secuencia": int(med["secuencia"]),
                    "consecutivo_medicamento": int(med["medicamento"]),
                    "cantidad": med["cantidad"],
                    "posologia": (med.get("posologia") or "NA")[:500],
                    "dias": med.get("dias") or 1,
                    "observacion": obs[:500] if obs else None,
                    "estado_aprobado": 0,
                    "usuario_grabado": username[:100],
                    "fecha_grabado": fecha_grabado,
                },
            )

        self.db.execute(
            text(UPDATE_CT_IPS_SS_SOLICITUD),
            {
                "consecutivo_solicitud": consecutivo_solicitud,
                "consecutivo_ips_ss": ips_ss_consecutivo,
                "estado": 1,
                "usuario": username[:100],
                "fecha_grabado": fecha_grabado,
            },
        )
        self.db.commit()
        return {
            "consecutivo_solicitud": consecutivo_solicitud,
            "numero_solicitud": numero_solicitud,
            "consecutivo_autorizacion": None,
            "consecutivo_interno": None,
            "pin": None,
            "autorizacion_activa": False,
            "pendiente_activacion": False,
            "estado_trazabilidad": None,
            "valor_autorizacion": None,
            "fecha_fin_vigencia": None,
            "tipo_cobro": 1,
            "valor_cobro": Decimal("0"),
            "tipo_cobro_descripcion": TIPO_COBRO_DESCRIPCION.get(1, "COPAGO SUBSIDIADO"),
        }

    def crear_autorizacion_desde_solicitud(
        self,
        *,
        consecutivo_solicitud: int,
        afiliado: dict[str, Any],
        ips_direccionamiento: dict[str, Any],
        ips_ss_consecutivo: int,
        diagnostico_cie10: int,
        form: dict[str, Any],
        autorizaciones: list[dict[str, Any]],
        username: str,
        tipo_doc_abrev: str,
    ) -> dict[str, Any]:
        """Emite ss_autorizacion EMITIDA para una solicitud existente (paso 2)."""
        if not autorizaciones:
            raise ValueError("No hay medicamentos autorizados para emitir autorización.")

        self.db.execute(text(LOCK_DIRECCIONAMIENTO))
        fecha_hoy = hoy_bogota()
        fecha_grabado = ahora_bogota()
        valor_cobro = Decimal("0")
        tipo_cobro = 1

        consecutivo_autorizacion = int(self.db.execute(text(NEXT_SS_AUTORIZACION)).scalar_one())
        pin = self.generar_pin()
        valor_total = sum(Decimal(str(a["valor_total"])) for a in autorizaciones)
        vigencia, fecha_fin_vigencia, fecha_fin_vigencia_servicio = (
            calcular_vigencias_autorizacion_medicamentos(self.db, fecha_hoy)
        )

        contrato = self.fetch_contrato_ips_municipio_afiliado(
            int(ips_direccionamiento["ips"]),
            str(afiliado.get("municipio_codigo") or ""),
        )
        consecutivo_contrato = int(contrato["consecutivo_contrato"]) if contrato else None

        self.db.execute(
            text(INSERT_SS_AUTORIZACION),
            {
                "consecutivo_autorizacion": consecutivo_autorizacion,
                "consecutivo_interno": "0",
                "consecutivo_interno_base": "0",
                "consecutivo_solicitud": int(consecutivo_solicitud),
                "valor_autorizacion": valor_total,
                "valor_base_cobro": valor_total,
                "valor_cobro": valor_cobro,
                "saldo_presupuestal": valor_total,
                "consecutivo_ips": int(ips_direccionamiento["ips"]),
                "municipio_prestador": str(ips_direccionamiento.get("municipio") or ""),
                "codigo_prestador": str(ips_direccionamiento.get("codigo_prestador") or ""),
                "razon_social_prestador": str(ips_direccionamiento["razon_social"]),
                "nit_prestador": str(ips_direccionamiento["nit"]),
                "digito_verificacion_prestador": str(ips_direccionamiento.get("digito_verificacion") or ""),
                "afiliado": int(afiliado["afiliado"]),
                "tipo_identificacion_afiliado": tipo_doc_abrev,
                "numero_identificacion_afiliado": str(afiliado["numero_identificacion"]),
                "fecha_nacimiento_afiliado": afiliado["fecha_nacimiento"],
                "municipio_afiliado": str(afiliado.get("municipio_codigo") or ""),
                "tipo_regimen_afiliado": int(afiliado.get("tipo_regimen") or 99),
                "estado_afiliado": int(afiliado.get("estado_afiliado") or 1),
                "valor_base_aplica": valor_total,
                "vigencia": vigencia,
                "fecha_fin_vigencia": fecha_fin_vigencia,
                "fecha_fin_vigencia_servicio": fecha_fin_vigencia_servicio,
                "fecha_autorizacion_reserva": fecha_grabado,
                "usuario_grabado": username[:100],
                "tipo_cobro": tipo_cobro,
                "primer_nombre": str(afiliado.get("primer_nombre") or ""),
                "segundo_nombre": afiliado.get("segundo_nombre"),
                "primer_apellido": str(afiliado.get("primer_apellido") or ""),
                "segundo_apellido": afiliado.get("segundo_apellido"),
                "pin": pin,
                "estado_flujo": ESTADO_FLUJO_DIRECCIONADA,
                "diagnostico_principal": diagnostico_cie10,
                "consecutivo_ambito": int(form.get("consecutivo_ambito") or 23),
                "consecutivo_nivel": int(form.get("consecutivo_nivel") or 2),
                "fecha_grabado": fecha_grabado,
                "tipo_servicio_solicitado": TIPO_SERVICIO_SOLICITADO_MEDICAMENTOS,
                "consecutivo_contrato": consecutivo_contrato,
            },
        )

        for auth in autorizaciones:
            secuencia = int(auth["secuencia"])
            med_row = auth["med_row"]
            cantidad = Decimal(str(auth["cantidad"]))
            valor_unit = Decimal(str(med_row.get("valor") or 0))
            valor_linea = valor_unit * cantidad
            consecutivo_concepto = self.resolver_concepto_medicamento_obligatorio(med_row)
            self.db.execute(
                text(INSERT_SS_AUTORIZACION_MEDICAMENTO),
                {
                    "consecutivo_autorizacion": consecutivo_autorizacion,
                    "secuencia": secuencia,
                    "medicamento": int(med_row["medicamento"]),
                    "cantidad": cantidad,
                    "valor_unitario": valor_unit,
                    "valor_tarifario": valor_unit,
                    "valor_servicio": valor_linea,
                    "valor_autoriza": valor_linea,
                    "codigo_propio": str(med_row.get("codigo_interno") or ""),
                    "descripcion": str(med_row.get("descripcion") or "")[:500],
                    "consecutivo_concepto": consecutivo_concepto,
                },
            )
            self.db.execute(
                text(UPDATE_SOL_MED_AUTORIZADO),
                {
                    "consecutivo_autorizacion": consecutivo_autorizacion,
                    "consecutivo_solicitud": int(consecutivo_solicitud),
                    "secuencia": secuencia,
                    "usuario": username[:100],
                    "fecha_grabado": fecha_grabado,
                },
            )

        self.db.execute(
            text(UPDATE_SOLICITUD_AUTORIZADA),
            {
                "consecutivo_autorizacion": consecutivo_autorizacion,
                "consecutivo_solicitud": int(consecutivo_solicitud),
                "consecutivo_ips": int(ips_direccionamiento["ips"]),
                "fecha_grabado": fecha_grabado,
            },
        )
        self.db.execute(
            text(
                "UPDATE administrativo.ss_solicitud SET sw_automatico = 1 "
                "WHERE consecutivo_solicitud = :consecutivo_solicitud"
            ),
            {"consecutivo_solicitud": int(consecutivo_solicitud)},
        )
        self.db.execute(
            text(INSERT_CT_IPS_SS_AUTORIZACION),
            {
                "consecutivo_ips_ss": ips_ss_consecutivo,
                "consecutivo_autorizacion": consecutivo_autorizacion,
                "fecha_autorizacion": fecha_hoy,
            },
        )
        self.db.execute(
            text(UPDATE_CT_IPS_SS_SOLICITUD),
            {
                "consecutivo_solicitud": int(consecutivo_solicitud),
                "consecutivo_ips_ss": ips_ss_consecutivo,
                "estado": 2,
                "usuario": username[:100],
                "fecha_grabado": fecha_grabado,
            },
        )
        registrar_estado_flujo_autorizacion(
            self.db,
            consecutivo_autorizacion=consecutivo_autorizacion,
            estado_flujo=ESTADO_FLUJO_DIRECCIONADA,
            fecha_estado=fecha_grabado,
            username=username,
        )
        self.db.commit()
        return {
            "consecutivo_solicitud": int(consecutivo_solicitud),
            "consecutivo_autorizacion": consecutivo_autorizacion,
            "consecutivo_interno": "0",
            "pin": pin,
            "autorizacion_activa": False,
            "pendiente_activacion": True,
            "estado_trazabilidad": 1,
            "valor_autorizacion": float(valor_total),
            "fecha_fin_vigencia": fecha_fin_vigencia,
            "tipo_cobro": tipo_cobro,
            "valor_cobro": valor_cobro,
            "tipo_cobro_descripcion": TIPO_COBRO_DESCRIPCION.get(tipo_cobro, "COPAGO SUBSIDIADO"),
        }

    def crear_solicitud_y_autorizar(
        self,
        *,
        afiliado: dict[str, Any],
        ips_solicitante: dict[str, Any],
        ips_direccionamiento: dict[str, Any],
        sede: dict[str, Any] | None,
        ips_ss_consecutivo: int,
        diagnostico_cie10: int,
        diagnostico_relacionado1: int | None,
        diagnostico_relacionado2: int | None,
        consecutivo_especialidad: int | None,
        origenes_atencion: list[int],
        form: dict[str, Any],
        medicamentos: list[dict[str, Any]],
        autorizaciones: list[dict[str, Any]],
        username: str,
        tipo_doc_abrev: str,
    ) -> dict[str, Any]:
        """Compatibilidad: solo crea solicitud (la autorización se emite en paso 2)."""
        del ips_direccionamiento, autorizaciones
        return self.crear_solicitud_orden_medica(
            afiliado=afiliado,
            ips_solicitante=ips_solicitante,
            sede=sede,
            ips_ss_consecutivo=ips_ss_consecutivo,
            diagnostico_cie10=diagnostico_cie10,
            diagnostico_relacionado1=diagnostico_relacionado1,
            diagnostico_relacionado2=diagnostico_relacionado2,
            consecutivo_especialidad=consecutivo_especialidad,
            origenes_atencion=origenes_atencion,
            form=form,
            medicamentos=medicamentos,
            username=username,
            tipo_doc_abrev=tipo_doc_abrev,
        )
