from sqlalchemy import create_engine, text

from app.config.settings import get_settings

engine = create_engine(get_settings().postgres_url)
with engine.connect() as conn:
    for row in conn.execute(
        text(
            """
            SELECT n.nspname, p.proname
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname IN ('administrativo', 'public')
              AND (
                p.proname ILIKE '%autoriz%'
                OR p.proname ILIKE '%solicitud%'
                OR p.proname ILIKE '%orden%med%'
                OR p.proname ILIKE '%medic%'
              )
            ORDER BY 1, 2
            LIMIT 80
            """
        )
    ):
        print(f"{row[0]}.{row[1]}")
