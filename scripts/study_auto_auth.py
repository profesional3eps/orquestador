"""Estudia solicitudes con autorización automática y PIN."""
from sqlalchemy import create_engine, text

from app.config.settings import get_settings

e = create_engine(get_settings().postgres_url)
with e.connect() as c:
    row = c.execute(
        text(
            """
            SELECT s.consecutivo_solicitud, s.sw_automatico, s.consecutivo_autorizacion,
                   s.tipo_servicio_solicitado, s.indicador_solicitud, s.tipo_proceso,
                   a.pin, a.tipo_cobro, a.valor_cobro, a.consecutivo_ips, a.razon_social_prestador,
                   a.municipio_prestador, a.sw_masiva
            FROM administrativo.ss_solicitud s
            JOIN administrativo.ss_autorizacion a ON a.consecutivo_autorizacion = s.consecutivo_autorizacion
            WHERE a.pin IS NOT NULL
              AND s.tipo_servicio_solicitado = 1
            ORDER BY s.fecha_grabado DESC
            LIMIT 3
            """
        )
    ).mappings().all()
    for r in row:
        print("solicitud+auth", dict(r))
        sid = r["consecutivo_solicitud"]
        meds = c.execute(
            text(
                """
                SELECT sm.secuencia, sm.consecutivo_medicamento, sm.cantidad, sm.dias, sm.posologia,
                       sm.estado_aprobado, sm.consecutivo_autorizacion
                FROM administrativo.ss_solicitud_medicamento sm
                WHERE sm.consecutivo_solicitud = :sid
                ORDER BY sm.secuencia
                """
            ),
            {"sid": sid},
        ).mappings().all()
        for m in meds:
            print("  med", dict(m))
        ips_sol = c.execute(
            text("SELECT * FROM administrativo.ct_ips_ss_solicitud WHERE solicitud = :sid LIMIT 1"),
            {"sid": sid},
        ).mappings().first()
        if ips_sol:
            print("  ct_ips_ss", {k: ips_sol[k] for k in ips_sol.keys() if k in ips_sol})
