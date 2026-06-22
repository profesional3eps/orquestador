import psycopg2
from psycopg2.extras import RealDictCursor

c = psycopg2.connect(
    "postgresql://postgres:Sup3r4dm1n7ami1i4rC0l@10.0.1.102:5432/base_sie_comfasucre"
)
cur = c.cursor(cursor_factory=RealDictCursor)

for sol_id in (2761552, 2761553):
    cur.execute(
        """
        SELECT consecutivo_solicitud, numero_solicitud, tipo_servicio_solicitado, servicio,
               prioridad_atencion, ubicacion_paciente, diagnostico_principal,
               usuario_grabado, fecha_grabado, fecha_solicitud, nombre_medico,
               registro_medico, justificacion_clinica, municipio_solicitante,
               estado_solicitud, sw_pendiente, sw_completa, sw_ingreso_hospitalario
        FROM administrativo.ss_solicitud WHERE consecutivo_solicitud = %s
        """,
        (sol_id,),
    )
    print("sol", cur.fetchone())
    cur.execute(
        "SELECT * FROM administrativo.ss_solicitud_atencion WHERE consecutivo_solicitud = %s",
        (sol_id,),
    )
    print("atencion", cur.fetchall())

cur.execute(
    """
    SELECT consecutivo_autorizacion, consecutivo_interno, consecutivo_contrato, consecutivo_solicitud
    FROM administrativo.ss_autorizacion WHERE consecutivo_interno = '1891554'
    """
)
print("auth", cur.fetchone())
