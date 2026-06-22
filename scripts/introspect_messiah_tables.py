"""Lista tablas administrativo relacionadas con solicitud/autorización IPS."""
from sqlalchemy import create_engine, text

from app.config.settings import get_settings

engine = create_engine(get_settings().postgres_url)
sql = text(
    """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'administrativo'
      AND (
          table_name ILIKE '%ips_ss%'
          OR table_name ILIKE '%autoriz%'
          OR table_name ILIKE '%direccion%'
          OR table_name ILIKE '%solicitud%'
          OR table_name ILIKE '%param%'
      )
    ORDER BY 1
    """
)
with engine.connect() as conn:
    for row in conn.execute(sql):
        print(row[0])
