"""Certificado de afiliación: plantilla DOCX, conversión a PDF con LibreOffice, salida Base64."""

from __future__ import annotations

import base64
import copy
import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
import unicodedata
from xml.sax.saxutils import escape

from app.config.settings import Settings

MES_ES = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}


def _xml_escape(value: str) -> str:
    return str(escape(value, entities={'"': "&quot;", "'": "&apos;"}))


def _str_cell(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _build_nombre_completo(row: dict[str, Any]) -> str:
    explicit = _str_cell(row.get("nombre_completo"))
    if explicit:
        return explicit
    parts = [
        _str_cell(row.get("primer_nombre")),
        _str_cell(row.get("segundo_nombre")),
        _str_cell(row.get("primer_apellido")),
        _str_cell(row.get("segundo_apellido")),
    ]
    return " ".join(p for p in parts if p).strip()


def _regimen_textos(row: dict[str, Any]) -> tuple[str, str]:
    reg = _str_cell(row.get("des_tipo_reg"))
    tipo_af = _str_cell(row.get("tipo_afiliado_texto"))
    if reg.lower() == "contributivo":
        return reg, f"Contributivo ({tipo_af or 'Afiliado'})"
    if reg.lower() == "subsidiado":
        return reg, f"Subsidiado ({tipo_af or 'Afiliado'})"
    return reg, tipo_af


def build_placeholder_map(
    row: dict[str, Any],
    *,
    tipo_documento_etiqueta: str,
    fecha_emision: date | None = None,
) -> dict[str, str]:
    hoy = fecha_emision or date.today()
    mes_es = MES_ES.get(hoy.month, str(hoy.month))
    reg_s, reg_detalle = _regimen_textos(row)
    nombre_completo = _build_nombre_completo(row)
    numero_identificacion = _str_cell(row.get("numero_identificacion"))
    tipo_afiliado = _str_cell(row.get("tipo_afiliado_texto"))
    estado_afiliado = _str_cell(row.get("nombre_estado_afiliado"))
    full_identification = f"{tipo_documento_etiqueta} - {numero_identificacion}".strip()
    fecha_afiliacion = _fmt_date_sql(
        row.get("fecha_afiliacion_entidad")
        or row.get("fecha_afilia")
        or row.get("fecha_afiliacion_inicial")
        or row.get("fecha_afiliacion_sgsss")
    )
    fecha_retiro = _fmt_date_sql(row.get("fecha_retiro"))
    texto_certifica = (
        f"Que el {tipo_afiliado or 'afiliado'} {nombre_completo}, identificado(a) con {full_identification}, "
        f"en el Plan de Beneficios del Sistema General de Seguridad Social en Salud -SGSSS- del Régimen {reg_s} "
        f"de nuestra entidad se encuentra en estado {estado_afiliado}."
    )
    base = {
        "TIPO_DOCUMENTO": _xml_escape(tipo_documento_etiqueta),
        "DOCUMENTO": _xml_escape(numero_identificacion),
        "PRIMER_NOMBRE": _xml_escape(_str_cell(row.get("primer_nombre"))),
        "SEGUNDO_NOMBRE": _xml_escape(_str_cell(row.get("segundo_nombre"))),
        "PRIMER_APELLIDO": _xml_escape(_str_cell(row.get("primer_apellido"))),
        "SEGUNDO_APELLIDO": _xml_escape(_str_cell(row.get("segundo_apellido"))),
        "ESTADO_AFILIADO": _xml_escape(estado_afiliado),
        "TIPO_REGIMEN": _xml_escape(reg_s),
        "TIPO_REGIMEN_DETALLE": _xml_escape(reg_detalle),
        "NOMBRE_COMPLETO": _xml_escape(nombre_completo),
        "TIPO_AFILIADO": _xml_escape(tipo_afiliado),
        "IDENTIFICACION_COMPLETA": _xml_escape(full_identification),
        "TEXTO_CERTIFICA": _xml_escape(texto_certifica),
        "FECHA_AFILIACION": _xml_escape(fecha_afiliacion),
        "FECHA_RETIRO": _xml_escape(fecha_retiro),
        "IPS_PRIMARIA": _xml_escape(_str_cell(row.get("ips_primaria"))),
        "IPS_ODONTOLOGICA": _xml_escape(_str_cell(row.get("ips_odontologica"))),
        "DIA_ACTUAL": _xml_escape(str(hoy.day)),
        "MES_ACTUAL": _xml_escape(str(mes_es)),
        "ANIO_ACTUAL": _xml_escape(str(hoy.year)),
    }
    # Soporta placeholders heredados y variantes vistas en plantillas Word.
    return {
        "{TIPO_DOCUMENTO}": base["TIPO_DOCUMENTO"],
        "{DOCUMENTO}": base["DOCUMENTO"],
        "{PRIMER_NOMBRE}": base["PRIMER_NOMBRE"],
        "{SEGUNDO_NOMBRE}": base["SEGUNDO_NOMBRE"],
        "{PRIMER_APELLIDO}": base["PRIMER_APELLIDO"],
        "{SEGUNDO_APELLIDO}": base["SEGUNDO_APELLIDO"],
        "{ESTADO_AFILIADO}": base["ESTADO_AFILIADO"],
        "{TIPO_REGIMEN}": base["TIPO_REGIMEN"],
        "{TIPO_REGIMEN_DETALLE}": base["TIPO_REGIMEN_DETALLE"],
        "{NOMBRE_COMPLETO}": base["NOMBRE_COMPLETO"],
        "{TIPO_AFILIADO}": base["TIPO_AFILIADO"],
        "{IDENTIFICACION_COMPLETA}": base["IDENTIFICACION_COMPLETA"],
        "{TEXTO_CERTIFICA}": base["TEXTO_CERTIFICA"],
        "{FECHA_AFILIACION}": base["FECHA_AFILIACION"],
        "{FECHA_RETIRO}": base["FECHA_RETIRO"],
        "{IPS_PRIMARIA}": base["IPS_PRIMARIA"],
        "{IPS_ODONTOLOGICA}": base["IPS_ODONTOLOGICA"],
        "{día_actual}": base["DIA_ACTUAL"],
        "{mes_actual}": base["MES_ACTUAL"],
        "{año_actual}": base["ANIO_ACTUAL"],
        "{DIA_ACTUAL}": base["DIA_ACTUAL"],
        "{MES_ACTUAL}": base["MES_ACTUAL"],
        "{ANIO_ACTUAL}": base["ANIO_ACTUAL"],
        "{dia_actual}": base["DIA_ACTUAL"],
        "{anio_actual}": base["ANIO_ACTUAL"],
        "{nombreCompleto}": base["NOMBRE_COMPLETO"],
        "{tipo_identificacion_descripcion}": base["TIPO_DOCUMENTO"],
        "{numero_identificacion}": base["DOCUMENTO"],
        "{tipo_regimen}": base["TIPO_REGIMEN"],
        "nombreCompleto": base["NOMBRE_COMPLETO"],
        "tipo_identificacion_descripcion": base["TIPO_DOCUMENTO"],
        "numero_identificacion": base["DOCUMENTO"],
        "tipo_regimen": base["TIPO_REGIMEN"],
        "tipo_afiliado": base["TIPO_AFILIADO"],
        "tipo_regimen_detalle": base["TIPO_REGIMEN_DETALLE"],
        "texto_certifica": base["TEXTO_CERTIFICA"],
        "nombre_estado_afiliado": base["ESTADO_AFILIADO"],
        "fecha_afiliacion": base["FECHA_AFILIACION"],
        "fecha_retiro": base["FECHA_RETIRO"],
        "ips_primaria": base["IPS_PRIMARIA"],
        "ips_odontologica": base["IPS_ODONTOLOGICA"],
    }


def fill_docx_placeholders(template_bytes: bytes, replacements: dict[str, str]) -> bytes:
    src = io.BytesIO(template_bytes)
    out = io.BytesIO()
    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                text_content = data.decode("utf-8")
                for k, v in replacements.items():
                    if k in text_content:
                        text_content = text_content.replace(k, v)
                # Variantes frecuentes: token con llaves separadas por espacios/saltos o sin llaves.
                for raw_token, val in (
                    ("nombreCompleto", replacements.get("nombreCompleto", "")),
                    ("tipo_identificacion_descripcion", replacements.get("tipo_identificacion_descripcion", "")),
                    ("numero_identificacion", replacements.get("numero_identificacion", "")),
                    ("tipo_regimen", replacements.get("tipo_regimen", "")),
                    ("tipo_afiliado", replacements.get("tipo_afiliado", "")),
                    ("dia_actual", replacements.get("{DIA_ACTUAL}", "")),
                    ("mes_actual", replacements.get("{MES_ACTUAL}", "")),
                    ("anio_actual", replacements.get("{ANIO_ACTUAL}", "")),
                ):
                    if not val:
                        continue
                    text_content = re.sub(rf"\{{\s*{raw_token}\s*\}}", val, text_content, flags=re.IGNORECASE)
                data = text_content.encode("utf-8")
            zout.writestr(item, data)
    return out.getvalue()


def _fmt_date_sql(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y/%m/%d")
    if isinstance(value, date):
        return value.strftime("%Y/%m/%d")
    txt = _str_cell(value)
    return txt[:10].replace("-", "/") if txt else ""


def _norm_text(value: str) -> str:
    s = unicodedata.normalize("NFD", value or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


def _get_tc_text(tc: ET.Element, ns: dict[str, str]) -> str:
    return " ".join((t.text or "") for t in tc.findall(".//w:t", ns)).strip()


def _set_tc_text(tc: ET.Element, value: str, ns: dict[str, str]) -> None:
    texts = tc.findall(".//w:t", ns)
    if not texts:
        p = tc.find("w:p", ns)
        if p is None:
            p = ET.SubElement(tc, f"{{{ns['w']}}}p")
        r = ET.SubElement(p, f"{{{ns['w']}}}r")
        t = ET.SubElement(r, f"{{{ns['w']}}}t")
        t.text = value
        return
    first = True
    for t in texts:
        t.text = value if first else ""
        first = False


def _adjust_info_table(docx_xml: str, row: dict[str, Any]) -> str:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    ET.register_namespace("w", ns["w"])
    root = ET.fromstring(docx_xml.encode("utf-8"))
    body = root.find("w:body", ns)
    if body is None:
        return docx_xml

    fecha_afiliacion = _fmt_date_sql(row.get("fecha_afiliacion_entidad") or row.get("fecha_afilia"))
    fecha_retiro = _fmt_date_sql(row.get("fecha_retiro"))

    for tbl in body.findall("w:tbl", ns):
        tbl_text = _norm_text(" ".join((t.text or "") for t in tbl.findall(".//w:t", ns)))
        if "informacion del cotizante" not in tbl_text:
            continue
        for tr in tbl.findall("w:tr", ns):
            cells = tr.findall("w:tc", ns)
            if not cells:
                continue
            c0 = _norm_text(_get_tc_text(cells[0], ns))
            if "nombre:" in c0 and len(cells) > 1:
                _set_tc_text(cells[1], _build_nombre_completo(row), ns)
            elif "tipo identificacion" in c0 and len(cells) > 1:
                _set_tc_text(cells[1], _str_cell(row.get("tipo_identificacion_descripcion")), ns)
                if len(cells) > 3:
                    _set_tc_text(cells[3], _str_cell(row.get("nombre_estado_afiliado")), ns)
            elif "numero identificacion" in c0 and len(cells) > 1:
                _set_tc_text(cells[1], _str_cell(row.get("numero_identificacion")), ns)
            elif "fecha afiliacion" in c0 and len(cells) > 1:
                _set_tc_text(cells[1], fecha_afiliacion, ns)
                if len(cells) > 3:
                    _set_tc_text(cells[3], fecha_retiro, ns)
            elif "ips primaria" in c0 and len(cells) > 1:
                _set_tc_text(cells[1], _str_cell(row.get("ips_primaria")), ns)
            elif "ips odontologica" in c0 and len(cells) > 1:
                _set_tc_text(cells[1], _str_cell(row.get("ips_odontologica")), ns)
        break
    return ET.tostring(root, encoding="unicode")


def _adjust_relacion_laboral_table(
    docx_xml: str,
    *,
    is_contributivo: bool,
    relaciones: list[dict[str, Any]],
    afiliado_numero_identificacion: str,
    afiliado_nombre_completo: str,
) -> str:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    ET.register_namespace("w", ns["w"])
    root = ET.fromstring(docx_xml.encode("utf-8"))
    body = root.find("w:body", ns)
    if body is None:
        return docx_xml

    target_tbl = None
    template_row_idx: int | None = None
    token_markers = (
        "{rl_numero_aportante}",
        "{rl_razon_social_aportante}",
        "{rl_estado_cotizante}",
        "{rl_fecha_inicial_cotizante}",
        "{rl_fecha_retiro_cotizante}",
        "{rl_nivel_ibc}",
    )
    tbl_after_relacion = None
    paragraphs_relacion: list[ET.Element] = []
    body_children = list(body)
    for i, node in enumerate(body_children):
        if node.tag.endswith("}p"):
            txt = _norm_text(" ".join((t.text or "") for t in node.findall(".//w:t", ns)))
            if "relacion laboral" in txt:
                paragraphs_relacion.append(node)
                for nxt in body_children[i + 1 :]:
                    if nxt.tag.endswith("}tbl"):
                        tbl_after_relacion = nxt
                        break
                break
    for tbl in body.findall("w:tbl", ns):
        rows = tbl.findall("w:tr", ns)
        for idx, tr in enumerate(rows):
            row_text = _norm_text(" ".join((t.text or "") for t in tr.findall(".//w:t", ns)))
            if any(tok in row_text for tok in token_markers):
                target_tbl = tbl
                template_row_idx = idx
                break
        if target_tbl is not None:
            break
        tbl_text = _norm_text(" ".join((t.text or "") for t in tbl.findall(".//w:t", ns)))
        if "documento" in tbl_text and "aportante" in tbl_text and "fecha inicio" in tbl_text:
            target_tbl = tbl
            break
    if target_tbl is None:
        target_tbl = tbl_after_relacion
    if target_tbl is None:
        return docx_xml

    if not is_contributivo:
        for p in paragraphs_relacion:
            if p in list(body):
                body.remove(p)
        if target_tbl in list(body):
            body.remove(target_tbl)
        return ET.tostring(root, encoding="unicode")

    rows = target_tbl.findall("w:tr", ns)
    if not rows:
        return ET.tostring(root, encoding="unicode")

    header = rows[0]
    header_cells = header.findall("w:tc", ns)
    expected_headers = [
        "Documento Aportante",
        "Razón Social Aportante",
        "",
        "Fecha Inicio",
        "Fecha Retiro",
        "Nivel IBC",
    ]
    for i, tc in enumerate(header_cells):
        _set_tc_text(tc, expected_headers[i] if i < len(expected_headers) else "", ns)

    if template_row_idx is not None and template_row_idx < len(rows):
        template = rows[template_row_idx]
        for idx, r in enumerate(rows):
            if idx >= template_row_idx:
                target_tbl.remove(r)
    else:
        template = rows[1] if len(rows) > 1 else copy.deepcopy(header)
        for r in rows[1:]:
            target_tbl.remove(r)

    if not relaciones:
        relaciones = [
            {
                "numero_identificacion": "",
                "razon_social": "SIN RELACIÓN LABORAL REGISTRADA",
                "sw_activo": None,
                "fecha_ingreso": None,
                "fecha_grabado_retiro": None,
                "nivel_ibc": "",
            }
        ]

    for rel in relaciones:
        row = copy.deepcopy(template)
        tcs = row.findall("w:tc", ns)
        numero_aportante = _str_cell(rel.get("numero_identificacion"))
        razon_social_aportante = _str_cell(rel.get("razon_social"))
        # Regla de negocio: si aparece INDEPENDIENTE, el aportante es el mismo afiliado.
        if razon_social_aportante.strip().upper() == "INDEPENDIENTE":
            numero_aportante = afiliado_numero_identificacion or numero_aportante
            razon_social_aportante = afiliado_nombre_completo or razon_social_aportante
        values = [
            numero_aportante,
            razon_social_aportante,
            "Vigente" if rel.get("sw_activo") in (None, 1, "1", True) else "No Vigente",
            _fmt_date_sql(rel.get("fecha_ingreso")),
            _fmt_date_sql(rel.get("fecha_grabado_retiro")),
            _str_cell(rel.get("nivel_ibc")),
        ]
        for i, tc in enumerate(tcs):
            _set_tc_text(tc, values[i] if i < len(values) else "", ns)
        target_tbl.append(row)

    return ET.tostring(root, encoding="unicode")


def resolve_soffice_executable(settings: Settings) -> str | None:
    if settings.libreoffice_soffice_path:
        p = Path(settings.libreoffice_soffice_path)
        if p.is_file():
            return str(p)
    w = shutil.which("soffice")
    if w:
        return w
    for candidate in (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def convert_docx_to_pdf(docx_bytes: bytes, soffice: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        docx_path = tdir / "cert.docx"
        docx_path.write_bytes(docx_bytes)
        pdf_path = tdir / "cert.pdf"
        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nologo",
            "--nolockcheck",
            "--convert-to",
            "pdf",
            "--outdir",
            str(tdir),
            str(docx_path),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0 or not pdf_path.is_file():
            err = (proc.stderr or "") + (proc.stdout or "")
            raise RuntimeError(
                f"LibreOffice no pudo generar el PDF (código {proc.returncode}). Detalle: {err[:2000]}"
            )
        return pdf_path.read_bytes()


def generar_certificado_pdf_base64(
    settings: Settings,
    row: dict[str, Any],
    tipo_descripcion: str,
    relaciones_laborales: list[dict[str, Any]] | None = None,
) -> str:
    regimen = _str_cell(row.get("des_tipo_reg")).lower()
    if regimen == "subsidiado":
        preferred_name = settings.certificado_docx_filename_subsidiado
    elif regimen == "contributivo":
        preferred_name = settings.certificado_docx_filename_contributivo
    else:
        preferred_name = settings.certificado_docx_filename

    path = Path(settings.templates_dir) / preferred_name
    fallback_candidates = [
        Path.cwd() / preferred_name,
        Path(settings.templates_dir) / settings.certificado_docx_filename,
        Path.cwd() / settings.certificado_docx_filename,
        Path.cwd() / "CertificadoAfiliacion.docx",
    ]
    if not path.is_file():
        for cand in fallback_candidates:
            if cand.is_file():
                path = cand
                break
    if not path.is_file():
        raise FileNotFoundError(
            f"No existe la plantilla '{path}'. Configure y copie las plantillas por régimen en "
            f"TEMPLATES_DIR: '{settings.certificado_docx_filename_subsidiado}' (subsidiado) y "
            f"'{settings.certificado_docx_filename_contributivo}' (contributivo)."
        )
    template_bytes = path.read_bytes()
    repl = build_placeholder_map(row, tipo_documento_etiqueta=tipo_descripcion)
    soffice = resolve_soffice_executable(settings)
    if not soffice:
        raise RuntimeError(
            "No se encontró el ejecutable soffice de LibreOffice. "
            "Instale LibreOffice o defina la variable de entorno LIBREOFFICE_SOFFICE_PATH."
        )
    docx_filled = fill_docx_placeholders(template_bytes, repl)
    docx_zip = io.BytesIO(docx_filled)
    out_zip = io.BytesIO()
    is_contributivo = _str_cell(row.get("des_tipo_reg")).lower() == "contributivo"
    with zipfile.ZipFile(docx_zip, "r") as zin, zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                xml = data.decode("utf-8")
                xml = _adjust_info_table(xml, row)
                xml = _adjust_relacion_laboral_table(
                    xml,
                    is_contributivo=is_contributivo,
                    relaciones=relaciones_laborales or [],
                    afiliado_numero_identificacion=_str_cell(row.get("numero_identificacion")),
                    afiliado_nombre_completo=_build_nombre_completo(row),
                )
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    docx_filled = out_zip.getvalue()
    pdf = convert_docx_to_pdf(docx_filled, soffice)
    return base64.b64encode(pdf).decode("ascii")
