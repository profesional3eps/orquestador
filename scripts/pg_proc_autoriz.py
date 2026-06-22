from sqlalchemy import create_engine, text
from app.config.settings import get_settings

e = create_engine(get_settings().postgres_url)
with e.connect() as c:
    n = c.execute(
        text(
            "SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE p.proname ILIKE '%autoriz%'"
        )
    ).scalar()
    print("count", n)
    for row in c.execute(
        text(
            """
            SELECT n.nspname, p.proname
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE p.proname ILIKE '%autoriz%'
            ORDER BY 1,2 LIMIT 30
            """
        )
    ):
        print(row[0], row[1])
