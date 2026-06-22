from sqlalchemy import create_engine, text

from app.config.settings import get_settings

engine = create_engine(get_settings().postgres_url)
sql = text(
    """
    SELECT routine_name
    FROM information_schema.routines
    WHERE routine_schema = 'administrativo'
      AND (
          routine_name ILIKE '%autoriz%'
          OR routine_name ILIKE '%solicitud%'
          OR routine_name ILIKE '%ips_ss%'
          OR routine_name ILIKE '%direccion%'
      )
    ORDER BY 1
    LIMIT 80
    """
)
with engine.connect() as conn:
    for row in conn.execute(sql):
        print(row[0])
