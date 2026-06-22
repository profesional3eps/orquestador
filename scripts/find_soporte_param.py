from sqlalchemy import create_engine, text
from app.config.settings import get_settings

e = create_engine(get_settings().postgres_url)
with e.connect() as c:
    for row in c.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'administrativo'
              AND (column_name ILIKE '%soporte%' OR column_name ILIKE '%obligat%')
            ORDER BY 1, 2
            LIMIT 40
            """
        )
    ):
        print(row[0], row[1])
