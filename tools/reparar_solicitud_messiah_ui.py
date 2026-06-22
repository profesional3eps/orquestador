"""Repara solicitudes/autorizaciones ORQ para que Messiah Consulta Autorización muestre todos los campos."""

from __future__ import annotations

import sys

import psycopg2

DSN = "postgresql://postgres:Sup3r4dm1n7ami1i4rC0l@10.0.1.102:5432/base_sie_comfasucre"
SERVICIO = "Medicamentos"
TIPO_SERVICIO_MEDICAMENTOS = 6

SOLICITUDES = (2761552, 2761553, 2761554)
AUTORIZACIONES = (2463408, 2463409, 2463410)


def main() -> int:
    consecutivos = sys.argv[1:] if len(sys.argv) > 1 else [str(s) for s in SOLICITUDES]
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()

    for sol_id in consecutivos:
        cur.execute(
            """
            UPDATE administrativo.ss_solicitud
            SET sw_terminada_hospitalaria = 1,
                servicio = COALESCE(NULLIF(TRIM(servicio), ''), %s),
                tipo_servicio_solicitado = %s
            WHERE consecutivo_solicitud = %s
            RETURNING consecutivo_solicitud, numero_solicitud
            """,
            (SERVICIO, TIPO_SERVICIO_MEDICAMENTOS, int(sol_id)),
        )
        row = cur.fetchone()
        print("solicitud reparada:", row)

    for auth_id in AUTORIZACIONES:
        cur.execute(
            """
            SELECT a.consecutivo_autorizacion, a.consecutivo_ips, a.municipio_afiliado,
                   a.consecutivo_contrato
            FROM administrativo.ss_autorizacion a
            WHERE a.consecutivo_autorizacion = %s
            """,
            (auth_id,),
        )
        auth = cur.fetchone()
        if not auth:
            print("autorización no encontrada:", auth_id)
            continue
        _, ips, municipio, contrato_actual = auth
        if contrato_actual:
            print("autorización ya con contrato:", auth_id, contrato_actual)
            continue
        cur.execute(
            """
            SELECT c.consecutivo_contrato
            FROM administrativo.ct_ips_contrato c
            LEFT JOIN administrativo.ct_ips_contrato_cobertura cob
                ON cob.contrato_ips = c.consecutivo_contrato
            WHERE c.ips = %s
              AND COALESCE(c.sw_bloqueado, 0) = 0
              AND c.consecutivo_contrato_base IS NULL
              AND c.estado = 3
              AND c.tipo_red = 1
              AND (timezone('America/Bogota', now()))::date BETWEEN c.fecha_inicio AND c.fecha_terminacion
              AND c.consecutivo_tarifario_medicamento IS NOT NULL
              AND (
                %s = ''
                OR cob.municipio IS NULL
                OR TRIM(CAST(cob.municipio AS TEXT)) = TRIM(%s)
              )
            ORDER BY c.numero_contrato DESC
            LIMIT 1
            """,
            (ips, municipio or "", municipio or ""),
        )
        contrato = cur.fetchone()
        if not contrato:
            print("sin contrato para auth", auth_id)
            continue
        cur.execute(
            """
            UPDATE administrativo.ss_autorizacion
            SET consecutivo_contrato = %s,
                tipo_servicio_solicitado = %s
            WHERE consecutivo_autorizacion = %s
            RETURNING consecutivo_interno, consecutivo_contrato
            """,
            (contrato[0], TIPO_SERVICIO_MEDICAMENTOS, auth_id),
        )
        print("autorización reparada:", cur.fetchone())

    conn.commit()
    cur.close()
    conn.close()
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
