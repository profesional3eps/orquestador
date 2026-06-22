import psycopg2
from psycopg2.extras import RealDictCursor

c = psycopg2.connect(
    "postgresql://postgres:Sup3r4dm1n7ami1i4rC0l@10.0.1.102:5432/base_sie_comfasucre"
)
cur = c.cursor(cursor_factory=RealDictCursor)

cur.execute(
    """
    SELECT consecutivo_autorizacion, consecutivo_interno, consecutivo_solicitud,
           consecutivo_saldo, saldo_presupuestal, estado_flujo, diagnostico_principal,
           consecutivo_ips, tipo_proceso, fecha_activacion, fecha_real_prestacion_servicio
    FROM administrativo.ss_autorizacion
    WHERE consecutivo_interno = '1891553' OR consecutivo_autorizacion = 2463408
    """
)
print("auth:", cur.fetchone())

cur.execute(
    "SELECT COUNT(*) AS c FROM administrativo.ss_autorizacion_medicamento WHERE consecutivo_autorizacion = 2463408"
)
print("auth_med count:", cur.fetchone())

cur.execute(
    """
    SELECT secuencia, medicamento, cantidad, valor_servicio, consecutivo_concepto, fecha_cancelacion
    FROM administrativo.ss_autorizacion_medicamento WHERE consecutivo_autorizacion = 2463408
    """
)
print("meds:", cur.fetchall())

cur.execute(
    """
    SELECT consecutivo_solicitud, numero_solicitud, diagnostico_principal, ips_solicitante,
           consecutivo_autorizacion, sw_automatico, servicio, ubicacion_paciente, indicador_solicitud
    FROM administrativo.ss_solicitud WHERE consecutivo_solicitud = 2761552
    """
)
print("sol:", cur.fetchone())

cur.execute(
    """
    SELECT secuencia, consecutivo_medicamento, cantidad, consecutivo_autorizacion, estado_aprobado
    FROM administrativo.ss_solicitud_medicamento WHERE consecutivo_solicitud = 2761552
    """
)
print("sol_meds:", cur.fetchall())

cur.execute(
    """
    SELECT tipo_servicio_solicitado, servicio, nombre_medico, registro_medico, justificacion_clinica
    FROM administrativo.ss_solicitud WHERE consecutivo_solicitud = 2761552
    """
)
print("sol_detail:", cur.fetchone())

cur.execute(
    """
    SELECT mn.consecutivo_medicamento, mn.consecutivo_concepto
    FROM administrativo.tb_medicamento_nota_tecnica mn
    WHERE mn.consecutivo_medicamento IN (123574, 126385)
    """
)
print("conceptos:", cur.fetchall())

cur.execute(
    "SELECT consecutivo_saldo FROM administrativo.ss_autorizacion WHERE consecutivo_autorizacion = 2463408"
)
print("auth_saldo:", cur.fetchone())

cur.execute(
    "SELECT COUNT(*) AS c FROM administrativo.sc_saldo_encabezado WHERE documento = '2463408'"
)
print("saldo_by_doc:", cur.fetchone())

cur.execute(
    """
    SELECT consecutivo_saldo, documento_nota, estado, observacion
    FROM administrativo.sc_saldo_encabezado WHERE consecutivo_saldo = 1003323544
    """
)
print("saldo:", cur.fetchone())

cur.execute(
    """
    SELECT secuencia, cuenta, valor_debito, valor_credito
    FROM administrativo.sc_saldo_detalle WHERE consecutivo_saldo = 1003323544
    """
)
print("saldo_det:", cur.fetchall())

cur.execute(
    "SELECT tipo_preferencia, valor, valor_texto FROM administrativo.tb_preferencia WHERE tipo_preferencia IN (288, 390)"
)
print("prefs:", cur.fetchall())
