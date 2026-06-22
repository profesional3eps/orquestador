from sqlalchemy import create_engine, text

from app.config.settings import get_settings

engine = create_engine(get_settings().postgres_url)
sql = text(
    """
    SELECT ips, nit, razon_social, tipo_autoriza, sw_autorizacion_masiva, sw_habilitada, direccion
    FROM administrativo.ct_ips
    WHERE UPPER(razon_social) LIKE '%GLOBAL HEALTH%'
    LIMIT 5
    """
)
with engine.connect() as conn:
    for r in conn.execute(sql).mappings():
        print(dict(r))
