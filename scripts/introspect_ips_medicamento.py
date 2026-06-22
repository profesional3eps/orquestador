from sqlalchemy import create_engine, text

from app.config.settings import get_settings

engine = create_engine(get_settings().postgres_url)
sql = text(
    """
    SELECT column_name
    FROM information_schema.columns
    WHERE table_schema = 'administrativo'
      AND table_name = :table
      AND (
          column_name ILIKE '%autor%'
          OR column_name ILIKE '%automatic%'
          OR column_name ILIKE '%direccion%'
          OR column_name ILIKE '%pin%'
          OR column_name ILIKE '%sw_%'
      )
    ORDER BY column_name
    """
)
for table in ("ct_ips", "tb_medicamento", "tb_insumo", "tb_cup", "tb_parametro"):
    print(f"\n=== {table} (filtro) ===")
    with engine.connect() as conn:
        for row in conn.execute(sql, {"table": table}):
            print(" ", row[0])
