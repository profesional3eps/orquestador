from sqlalchemy import create_engine, text
from app.config.settings import get_settings

e = create_engine(get_settings().postgres_url)
with e.connect() as c:
    r = c.execute(
        text("SELECT * FROM administrativo.ss_solicitud WHERE consecutivo_solicitud = 2806998")
    ).mappings().first()
    if r:
        for k, v in r.items():
            if v is not None and v != "" and v != 0:
                print(f"{k}={v!r}")

    a = c.execute(
        text("SELECT * FROM administrativo.ss_autorizacion WHERE consecutivo_autorizacion = 2510334")
    ).mappings().first()
    if a:
        print("--- auth ---")
        for k, v in a.items():
            if v is not None and v != "" and v != 0:
                print(f"{k}={v!r}")
