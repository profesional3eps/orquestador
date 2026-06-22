"""Prueba PDF con ID de autorización fijo en el SQL."""
import os
import subprocess
from pathlib import Path

AUTH = 2463410
ROOT = Path("/app")
JRXML = ROOT / "app/reports/messiah/reAutorizacion.jrxml"
OUT = Path("/tmp/reAuth_hard.jrxml")
JASPER = ROOT / "tools/jasperstarter/bin/jasperstarter"
JDBC = ROOT / "tools/jasperstarter/jdbc"
REPORTS = ROOT / "app/reports/messiah"

text = JRXML.read_text(encoding="utf-8")
text = text.replace("= $P!{AUTORIZACION}", f"= {AUTH}")
OUT.write_text(text, encoding="utf-8")
print("hardcoded", f"= {AUTH}" in text)

subprocess.run([str(JASPER), "compile", str(OUT), "-o", "/tmp"], check=True)
jasper = OUT.with_suffix(".jasper")
params = [
    "-P", "RUTA=/app/app/reports/messiah/",
    "-P", "PRESTADO=1",
    "-P", "EMPRESA=COMFASUCRE",
    "-P", "MANEJO_INTEGRAL=x",
    "-P", "CODIGO_CUPS=x",
    "-P", "CANTIDAD=x",
    "-P", "DESCRIPCION=x",
    "-P", "ITEM=1",
    "-P", "SR_COPAGO=x",
    "-P", "APLICA=x",
    "-P", "NO_APLICA=x",
    "-P", "OBSERVACION=x",
    "-P", "ENTIDAD_RESPONSABLE=x",
    "-P", "COD_ENTIDAD_RESPONSABLE=x",
    "-P", "NOMBRE_USUARIO=x",
    "-P", "ES_EMPRESA=true",
    "-P", "NUMERO_SOLICITUD_USUARIO=439082",
    "-P", "NIT_EMPRESA=x",
    "-P", "LINEA_NACIONAL=x",
]
cmd = [
    str(JASPER), "pr", str(jasper), "-o", "/tmp/hardout", "-f", "pdf",
    "-r", str(REPORTS), "--jdbc-dir", str(JDBC),
    "-t", "postgres", "-H", "10.0.1.102", "--db-port", "5432",
    "-n", "base_sie_comfasucre", "-u", "postgres", "-p", "Sup3r4dm1n7ami1i4rC0l",
    *params,
]
proc = subprocess.run(cmd, capture_output=True, text=True)
print("rc", proc.returncode)
if proc.stderr:
    print(proc.stderr[-800:])
pdf = Path("/tmp/hardout.pdf")
print("pdf_bytes", pdf.stat().st_size if pdf.is_file() else 0)
