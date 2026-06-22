import psycopg2

c = psycopg2.connect(
    "postgresql://postgres:Sup3r4dm1n7ami1i4rC0l@10.0.1.102:5432/base_sie_comfasucre"
)
cur = c.cursor()
cur.execute(
    "SELECT usuario_grabado, afiliado, vigencia FROM administrativo.ss_autorizacion "
    "WHERE consecutivo_autorizacion=2463407"
)
print("auth", cur.fetchone())
cur.execute("SELECT 1 FROM administrativo.usuario WHERE usuario=%s", ("ghg_medic",))
print("usuario", cur.fetchone())
cur.execute("SELECT 1 FROM administrativo.af_afiliado_complemento WHERE afiliado=563623")
print("complemento", cur.fetchone())
cur.execute("SELECT discapacidad FROM administrativo.af_afiliado WHERE afiliado=563623")
d = cur.fetchone()
print("disc", d)
if d:
    cur.execute(
        "SELECT 1 FROM administrativo.tb_discapacidad WHERE discapacidad=%s",
        (d[0],),
    )
    print("tb_disc", cur.fetchone())
cur.execute(
    "SELECT vigencia FROM administrativo.ss_autorizacion WHERE consecutivo_autorizacion=2463407"
)
v = cur.fetchone()[0]
cur.execute(
    "SELECT 1 FROM administrativo.tb_preferencia WHERE consecutivo_preferencia=%s",
    (v,),
)
print("vigencia pref", cur.fetchone())
cur.execute("SELECT usuario FROM administrativo.usuario ORDER BY usuario LIMIT 10")
print("sample users", cur.fetchall())
c.close()
