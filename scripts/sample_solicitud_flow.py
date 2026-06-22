from sqlalchemy import create_engine, text

from app.config.settings import get_settings

engine = create_engine(get_settings().postgres_url)
with engine.connect() as conn:
    q = """
    SELECT s.consecutivo_solicitud, s.estado_solicitud, s.sw_automatico, s.consecutivo_autorizacion,
           a.pin, a.tipo_cobro, a.valor_cobro, a.razon_social_prestador, a.municipio_prestador
    FROM administrativo.ss_solicitud s
    LEFT JOIN administrativo.ss_autorizacion a ON a.consecutivo_autorizacion = s.consecutivo_autorizacion
    WHERE s.tipo_servicio_solicitado = 1
    ORDER BY s.fecha_grabado DESC
    LIMIT 5
    """
    for r in conn.execute(text(q)).mappings():
        print(dict(r))

    med = conn.execute(
        text(
            """
            SELECT m.consecutivo_medicamento, med.codigo_interno, med.sw_automatico, med.sw_activo,
                   i.tipo_autoriza, i.sw_autorizacion_masiva, i.sw_habilitada
            FROM administrativo.tb_medicamento med
            CROSS JOIN administrativo.ct_ips i
            WHERE TRIM(i.nit::text) = '901483168'
            LIMIT 3
            """
        )
    ).mappings().all()
    for r in med:
        print("med+ips", dict(r))
