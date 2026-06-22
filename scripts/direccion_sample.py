from sqlalchemy import create_engine, text
from app.config.settings import get_settings

e = create_engine(get_settings().postgres_url)
with e.connect() as c:
    rows = c.execute(
        text(
            """
            SELECT d.consecutivo_direccionamiento, d.consecutivo_ips, d.sw_activo,
                   dm.medicamento, m.codigo_interno, m.sw_automatico
            FROM administrativo.tb_direccionamiento_autorizacion d
            JOIN administrativo.tb_direccionamiento_autorizacion_medicamento dm
              ON dm.consecutivo_direccionamiento = d.consecutivo_direccionamiento
            JOIN administrativo.tb_medicamento m ON m.medicamento = dm.medicamento
            WHERE d.consecutivo_ips = 317
            LIMIT 5
            """
        )
    ).mappings().all()
    for r in rows:
        print(dict(r))

    auto = c.execute(
        text(
            """
            SELECT ips, nit, tipo_autoriza, sw_autorizacion_masiva, sw_habilitada
            FROM administrativo.ct_ips
            WHERE sw_autorizacion_masiva = 1 AND sw_habilitada = 1
            LIMIT 5
            """
        )
    ).mappings().all()
    print("ips auto", [dict(x) for x in auto])
