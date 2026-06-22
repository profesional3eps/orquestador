"""Columnas de tablas clave para direccionamiento."""
from sqlalchemy import create_engine, text

from app.config.settings import get_settings

TABLES = [
    "ct_ips_ss_solicitud",
    "ct_ips_ss_solicitud_autorizacion",
    "ss_solicitud",
    "ss_solicitud_medicamento",
    "ss_solicitud_insumo",
    "ss_solicitud_cup",
    "ss_autorizacion",
    "ss_autorizacion_medicamento",
    "ss_autorizacion_insumo",
    "ss_autorizacion_cup",
    "ct_ips",
    "tb_medicamento",
    "tb_parametro",
    "tb_direccionamiento_autorizacion",
    "tb_direccionamiento_autorizacion_medicamento",
]

engine = create_engine(get_settings().postgres_url)
sql = text(
    """
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema = 'administrativo' AND table_name = :table
    ORDER BY ordinal_position
    """
)
with engine.connect() as conn:
    for table in TABLES:
        print(f"\n=== {table} ===")
        rows = conn.execute(sql, {"table": table}).fetchall()
        if not rows:
            print("  (no existe)")
            continue
        for col, dtype, nullable in rows:
            print(f"  {col}: {dtype} nullable={nullable}")
