from sqlalchemy import create_engine, text
from app.config.settings import get_settings

e = create_engine(get_settings().postgres_url)
with e.connect() as c:
    cols = c.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='administrativo' AND table_name='tb_cie10' ORDER BY 1 LIMIT 8"
        )
    ).fetchall()
    print("cie cols", cols)
    cie = c.execute(
        text("SELECT * FROM administrativo.tb_cie10 WHERE UPPER(TRIM(simbolo)) = 'Z000' LIMIT 1")
    ).mappings().first()
    print("cie10", dict(cie) if cie else None)
    ips = c.execute(
        text(
            """
            SELECT ips, nit, razon_social, municipio, codigo_prestador, digito_verificacion,
                   tipo_autoriza, sw_autorizacion_masiva, sw_habilitada, sigla
            FROM administrativo.ct_ips WHERE TRIM(nit::text) = '901483168' LIMIT 1
            """
        )
    ).mappings().first()
    print("ips", dict(ips) if ips else None)
