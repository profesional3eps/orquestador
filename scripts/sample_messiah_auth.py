from sqlalchemy import create_engine, text

from app.config.settings import get_settings

engine = create_engine(get_settings().postgres_url)
queries = [
    ("tipo_autoriza ct_ips", "SELECT DISTINCT tipo_autoriza, sw_autorizacion_masiva FROM administrativo.ct_ips LIMIT 20"),
    ("tb_parametro cols", "SELECT column_name FROM information_schema.columns WHERE table_schema='administrativo' AND table_name='tb_parametro' ORDER BY 1"),
    ("parametro sample", "SELECT * FROM administrativo.tb_parametro LIMIT 3"),
    ("ss_autorizacion pin sample", """
        SELECT pin, tipo_cobro, valor_cobro, razon_social_prestador, municipio_prestador, consecutivo_solicitud
        FROM administrativo.ss_autorizacion
        WHERE pin IS NOT NULL
        ORDER BY fecha_grabado DESC LIMIT 3
    """),
]
with engine.connect() as conn:
    for title, sql in queries:
        print(f"\n=== {title} ===")
        try:
            rows = conn.execute(text(sql)).mappings().all()
            for r in rows:
                print(dict(r))
        except Exception as e:
            print("ERROR", e)
