from sqlalchemy import create_engine, text
from app.config.settings import get_settings

e = create_engine(get_settings().postgres_url)
with e.connect() as c:
    rows = c.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='administrativo' AND table_name='tb_medicamento' ORDER BY ordinal_position"
        )
    ).fetchall()
    print([r[0] for r in rows[:30]])
    row = c.execute(
        text(
            """
            SELECT med.medicamento, med.codigo_interno, med.descripcion, med.sw_automatico, med.sw_activo,
                   ips.ips, ips.tipo_autoriza, ips.sw_autorizacion_masiva, ips.sw_habilitada, ips.razon_social
            FROM administrativo.tb_medicamento med
            CROSS JOIN administrativo.ct_ips ips
            WHERE TRIM(ips.nit::text) = '901483168'
              AND med.sw_automatico = 1
            LIMIT 2
            """
        )
    ).mappings().first()
    print("sample", dict(row) if row else None)
