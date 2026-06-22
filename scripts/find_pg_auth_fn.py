from sqlalchemy import create_engine, text
from app.config.settings import get_settings

e = create_engine(get_settings().postgres_url)
with e.connect() as c:
    for row in c.execute(
        text(
            """
            SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid) AS args
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE p.proname ILIKE '%autoriz%medic%'
               OR p.proname ILIKE '%solicitud%medic%'
               OR p.proname ILIKE '%genera%pin%'
               OR p.proname ILIKE '%fn_ss%'
            ORDER BY 1, 2
            LIMIT 50
            """
        )
    ):
        print(f"{row[0]}.{row[1]}({row[2]})")
