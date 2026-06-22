"""Prueba la consulta principal de reAutorizacion.jrxml contra Postgres."""
import re
import sys

import psycopg2

DSN = "postgresql://postgres:Sup3r4dm1n7ami1i4rC0l@10.0.1.102:5432/base_sie_comfasucre"
JRXML = "app/reports/messiah/reAutorizacion.jrxml"
AUTH = int(sys.argv[1]) if len(sys.argv) > 1 else 2463410

with open(JRXML, encoding="utf-8") as f:
    content = f.read()
query = re.search(r"<!\[CDATA\[(SELECT[\s\S]+?)\]\]>", content).group(1)
query = query.replace("$P{AUTORIZACION}", "%s")

conn = psycopg2.connect(DSN)
cur = conn.cursor()
cur.execute(f"SELECT COUNT(*) FROM ({query}) q", (AUTH,))
print("row_count", cur.fetchone()[0])
cur.execute(query, (AUTH,))
row = cur.fetchone()
print("numero_autorizacion", row[0] if row else None)
