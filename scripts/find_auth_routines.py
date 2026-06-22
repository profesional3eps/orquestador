from sqlalchemy import create_engine, text

from app.config.settings import get_settings

engine = create_engine(get_settings().postgres_url)
with engine.connect() as conn:
    for row in conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'administrativo' AND table_name = 'ss_autorizacion'
            ORDER BY ordinal_position
            """
        )
    ):
        print(row[0])
    print("--- routines ---")
    for row in conn.execute(
        text(
            """
            SELECT routine_name
            FROM information_schema.routines
            WHERE routine_schema = 'administrativo'
              AND (
                routine_name ILIKE '%autoriz%'
                OR routine_name ILIKE '%solicitud%med%'
                OR routine_name ILIKE '%direccion%'
                OR routine_name ILIKE '%pin%'
              )
            ORDER BY routine_name
            LIMIT 60
            """
        )
    ):
        print(row[0])
