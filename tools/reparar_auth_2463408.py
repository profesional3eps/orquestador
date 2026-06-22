"""Repara autorización 1891553 / PK 2463408: concepto en medicamentos y vínculo contable."""
import psycopg2

CONN = "postgresql://postgres:Sup3r4dm1n7ami1i4rC0l@10.0.1.102:5432/base_sie_comfasucre"
AUTH = 2463408

with psycopg2.connect(CONN) as conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE administrativo.ss_autorizacion_medicamento am
            SET consecutivo_concepto = sub.consecutivo_concepto
            FROM (
                SELECT mn.consecutivo_medicamento, MIN(mn.consecutivo_concepto) AS consecutivo_concepto
                FROM administrativo.tb_medicamento_nota_tecnica mn
                GROUP BY mn.consecutivo_medicamento
            ) sub
            WHERE am.consecutivo_autorizacion = %s
              AND am.medicamento = sub.consecutivo_medicamento
              AND am.consecutivo_concepto IS NULL
            """,
            (AUTH,),
        )
        print("medicamentos actualizados:", cur.rowcount)

        cur.execute(
            """
            UPDATE administrativo.ss_autorizacion a
            SET consecutivo_saldo = e.consecutivo_saldo
            FROM administrativo.sc_saldo_encabezado e
            WHERE a.consecutivo_autorizacion = %s
              AND a.consecutivo_saldo IS NULL
              AND e.documento = %s
              AND e.tipo_documento = 1
              AND e.documento_nota LIKE 'NC-AT%%'
              AND e.estado = 1
            """,
            (AUTH, AUTH),
        )
        print("autorización vinculada a saldo:", cur.rowcount)
    conn.commit()

print("Listo.")
