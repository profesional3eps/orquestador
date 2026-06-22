"""Desvincula consecutivo_saldo erróneo (facturación) y crea nota NC-AT de autorización."""
from __future__ import annotations

import sys

import psycopg2
from psycopg2.extras import RealDictCursor

DSN = "postgresql://postgres:Sup3r4dm1n7ami1i4rC0l@10.0.1.102:5432/base_sie_comfasucre"
PREFIJO_NC_AT = "NC-AT"
TIPO_DOC_AUTORIZACION = 1


def main() -> int:
    auth_ids = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else []
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if not auth_ids:
        cur.execute(
            """
            SELECT a.consecutivo_autorizacion, a.consecutivo_interno, a.consecutivo_saldo,
                   e.tipo_documento, e.documento_nota, e.observacion
            FROM administrativo.ss_autorizacion a
            JOIN administrativo.sc_saldo_encabezado e
              ON e.consecutivo_saldo = a.consecutivo_saldo
            WHERE a.consecutivo_saldo IS NOT NULL
              AND (
                e.tipo_documento <> %s
                OR e.documento_nota NOT LIKE %s || '%%'
              )
            ORDER BY a.consecutivo_autorizacion DESC
            LIMIT 20
            """,
            (TIPO_DOC_AUTORIZACION, PREFIJO_NC_AT),
        )
        rows = cur.fetchall()
        print("autorizaciones con saldo incorrecto:", len(rows))
        for row in rows:
            print(row)
        auth_ids = [int(r["consecutivo_autorizacion"]) for r in rows]

    for auth_id in auth_ids:
        cur.execute(
            """
            UPDATE administrativo.ss_autorizacion
            SET consecutivo_saldo = NULL
            WHERE consecutivo_autorizacion = %s
            RETURNING consecutivo_interno, consecutivo_saldo
            """,
            (auth_id,),
        )
        print("desvinculado", auth_id, cur.fetchone())

    conn.commit()
    cur.close()
    conn.close()
    print("OK. Re-ejecute paso 2 o contabilice desde API para generar NC-AT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
