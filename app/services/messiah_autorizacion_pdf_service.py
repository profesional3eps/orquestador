"""Generación de PDF de autorización con reportes Jasper de Messiah (ssActivacion / reAutorizacion)."""

from __future__ import annotations

import base64
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config.settings import Settings

logger = logging.getLogger(__name__)

_APP_DIR = Path(__file__).resolve().parent.parent
_ROOT_DIR = _APP_DIR.parent
_DEFAULT_JASPER_DIR = _APP_DIR / "reports" / "messiah"
_DEFAULT_JASPERSTARTER_WIN = _ROOT_DIR / "tools" / "jasperstarter" / "bin" / "jasperstarter.exe"
_DEFAULT_JASPERSTARTER_UNIX = _ROOT_DIR / "tools" / "jasperstarter" / "bin" / "jasperstarter"
_DEFAULT_JDBC_DIR = _ROOT_DIR / "tools" / "jasperstarter" / "jdbc"
_MIN_PDF_BYTES = 2048

REPORT_ACTIVACION_PIN = "ssActivacion"
REPORT_AUTORIZACION_COMPLETA = "reAutorizacion"

# Etiquetas de reporte (ApplicationResources Messiah — re_authorization_* / lbl_aplica).
_LABELS_COMPLETO: dict[str, str] = {
    "MANEJO_INTEGRAL": "Manejo Integral Según Guía de:",
    "CODIGO_CUPS": "CÓDIGO",
    "CANTIDAD": "CANTIDAD",
    "DESCRIPCION": "DESCRIPCIÓN",
    "ITEM": "#",
    "OBSERVACION": "Observación",
    "SR_COPAGO": "SR - Copago",
    "APLICA": "APLICA",
    "NO_APLICA": "NO APLICA",
}

AUTORIZACION_META_SQL = """
SELECT
    a.consecutivo_autorizacion,
    a.consecutivo_interno,
    a.consecutivo_solicitud,
    a.consecutivo_ips,
    a.nit_prestador,
    a.usuario_grabado,
    a.tipo_proceso,
    a.fecha_activacion,
    a.fecha_real_prestacion_servicio,
    s.numero_solicitud
FROM administrativo.ss_autorizacion a
LEFT JOIN administrativo.ss_solicitud s ON s.consecutivo_solicitud = a.consecutivo_solicitud
WHERE a.consecutivo_autorizacion = :consecutivo_autorizacion
  AND a.fecha_anula IS NULL
LIMIT 1
"""

EMPRESA_SQL = """
SELECT
    razon_social,
    COALESCE(codigo_contributivo, '') AS codigo_contributivo,
    COALESCE(codigo_subsidiado, '') AS codigo_subsidiado,
    COALESCE(nit, '') AS nit,
    COALESCE(telefono, '') AS telefono
FROM administrativo.tb_empresa
ORDER BY consecutivo_empresa
LIMIT 1
"""

ES_EMPRESA_SQL = """
SELECT COUNT(*) AS total
FROM administrativo.af_afiliado
WHERE TRIM(CAST(numero_identificacion AS TEXT)) = TRIM(:nit)
"""


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def _path_matches_os(path: Path) -> bool:
    raw = str(path)
    if _is_windows():
        return "\\" in raw or (len(raw) > 1 and raw[1] == ":")
    return "\\" not in raw and not re.match(r"^[A-Za-z]:", raw)


def _java_available() -> bool:
    try:
        proc = subprocess.run(
            ["java", "-version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _stderr_relevant(stderr: str) -> str:
    """Filtra ruido WSL/Spring; conserva líneas con el error real."""
    relevant: list[str] = []
    for line in stderr.splitlines():
        text = line.strip()
        if not text:
            continue
        low = text.lower()
        if "wsl" in low and "error" in low:
            continue
        if "loadbeandefinitions" in low:
            continue
        if low.startswith("informaci") or low.startswith("advertencia:"):
            continue
        if re.match(r"^[a-z]{3}\s+\d{1,2},\s+\d{4}", text, re.I):
            continue
        relevant.append(text)
    return "; ".join(relevant[:6])[:500]


def _jasper_executable(settings: Settings) -> str | None:
    candidates: list[Path] = []
    if settings.jasperstarter_path:
        candidates.append(Path(settings.jasperstarter_path))
    if _is_windows():
        candidates.append(_DEFAULT_JASPERSTARTER_WIN)
    else:
        candidates.append(_DEFAULT_JASPERSTARTER_UNIX)
    for candidate in candidates:
        if not _path_matches_os(candidate):
            continue
        if candidate.is_file():
            return str(candidate)
    return shutil.which("jasperstarter") or shutil.which("jasperstarter.bat")


def _jasper_jdbc_dir(settings: Settings) -> Path | None:
    custom = settings.jasper_jdbc_dir
    if custom is not None:
        p = Path(custom)
        if p.is_dir():
            return p
    if _DEFAULT_JDBC_DIR.is_dir():
        return _DEFAULT_JDBC_DIR
    return None


def _report_file(jasper_dir: Path, stem: str) -> Path | None:
    compiled = jasper_dir / f"{stem}.jasper"
    if compiled.is_file():
        return compiled
    source = jasper_dir / f"{stem}.jrxml"
    if source.is_file():
        return source
    return None


def _jasper_ruta_param(jasper_dir: Path) -> str:
    return jasper_dir.resolve().as_posix().rstrip("/") + "/"


def _postgres_db_args(settings: Settings) -> list[str]:
    url = make_url(settings.postgres_url)
    return [
        "-t",
        "postgres",
        "-H",
        url.host or "localhost",
        "--db-port",
        str(url.port or 5432),
        "-n",
        url.database or "",
        "-u",
        url.username or "",
        "-p",
        url.password or "",
    ]


def _jasper_param_args(params: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            text_val = "true" if value else "false"
        else:
            text_val = str(value)
        args.extend(["-P", f"{key}={text_val}"])
    return args


_SUBREPORTS_BY_STEM: dict[str, tuple[str, ...]] = {
    REPORT_AUTORIZACION_COMPLETA: ("reAutorizacionServiciosAutorizados",),
    REPORT_ACTIVACION_PIN: (),
}

_JASPER_CACHE_ROOT = Path(os.environ.get("JASPER_CACHE_DIR", "/tmp/orq_jasper_cache"))
_jasper_compile_locks: dict[str, threading.Lock] = {}
_jasper_compile_locks_guard = threading.Lock()


def _jasper_compile_lock(cache_key: str) -> threading.Lock:
    with _jasper_compile_locks_guard:
        lock = _jasper_compile_locks.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _jasper_compile_locks[cache_key] = lock
        return lock


def _jasper_cache_stale(cache_dir: Path, jasper_dir: Path, stems: tuple[str, ...]) -> bool:
    for stem in stems:
        src = jasper_dir / f"{stem}.jrxml"
        compiled = cache_dir / f"{stem}.jasper"
        if not compiled.is_file():
            return True
        if src.is_file() and src.stat().st_mtime > compiled.stat().st_mtime:
            return True
    return False


def _inline_jrxml_auth_sql(text: str, bindings: dict[str, int]) -> str:
    """JasperStarter CLI no enlaza bien $P{}/$P!{} tipo Long; fijamos el ID en el SQL."""
    out = text
    for param, value in bindings.items():
        vid = int(value)
        for pat in (
            f"= $P!{{{param}}}",
            f"=$P!{{{param}}}",
            f"= $P{{{param}}}",
            f"=$P{{{param}}}",
        ):
            out = out.replace(pat, f"= {vid}")
    return out


def _prepare_ephemeral_jasper(
    *,
    jasper_dir: Path,
    jasper_cmd: str,
    work_dir: Path,
    report_stem: str,
    inline_auth: dict[str, int],
) -> tuple[Path, Path]:
    """Compila (o reutiliza caché) reporte con ID de autorización fijo en el SQL."""
    auth_id = int(next(iter(inline_auth.values())))
    cache_key = f"{report_stem}:{auth_id}"
    cache_dir = _JASPER_CACHE_ROOT / report_stem / str(auth_id)
    stems = (report_stem, *_SUBREPORTS_BY_STEM.get(report_stem, ()))
    jasper_file = cache_dir / f"{report_stem}.jasper"

    if not _jasper_cache_stale(cache_dir, jasper_dir, stems):
        return jasper_file, cache_dir

    with _jasper_compile_lock(cache_key):
        if not _jasper_cache_stale(cache_dir, jasper_dir, stems):
            return jasper_file, cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        for stem in stems:
            src = jasper_dir / f"{stem}.jrxml"
            if not src.is_file():
                raise FileNotFoundError(f"Falta reporte Jasper: {src}")
            text = _inline_jrxml_auth_sql(src.read_text(encoding="utf-8"), inline_auth)
            (cache_dir / src.name).write_text(text, encoding="utf-8")

        proc = subprocess.run(
            [jasper_cmd, "compile", str(cache_dir)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            msg = _stderr_relevant((proc.stderr or proc.stdout or "").strip())
            raise RuntimeError(msg or f"Jasper compile falló (code={proc.returncode})")

        if not jasper_file.is_file():
            raise FileNotFoundError(f"No se generó {jasper_file}")
    return jasper_file, cache_dir


def _ensure_jasper_binaries(jasper_dir: Path, jasper_cmd: str) -> bool:
    """Compila .jrxml a .jasper si aún no existen (requiere JasperStarter + Java)."""
    needed = ["ssActivacion.jasper", "reAutorizacion.jasper", "reAutorizacionServiciosAutorizados.jasper"]
    if all((jasper_dir / n).is_file() for n in needed):
        return True
    try:
        proc = subprocess.run(
            [jasper_cmd, "compile", str(jasper_dir)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                "Jasper compile falló (code=%s): %s",
                proc.returncode,
                (proc.stderr or proc.stdout or "")[:500],
            )
        return all((jasper_dir / n).is_file() for n in needed)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("No se pudo compilar reportes Jasper: %s", exc)
        return False


def _run_jasper_pdf(
    *,
    settings: Settings,
    report_stem: str,
    output_stem: str,
    params: dict[str, Any],
    inline_auth: dict[str, int] | None = None,
) -> tuple[bytes | None, str | None]:
    """
    Ejecuta JasperStarter con el reporte Messiah.
    Devuelve (pdf_bytes, aviso_error).
    """
    if not settings.messiah_pdf_enabled:
        return None, "MESSIAH_PDF_ENABLED=false."

    jasper_cmd = _jasper_executable(settings)
    if not jasper_cmd:
        hint = (
            "scripts/setup_jasperstarter.sh (Linux/Docker) o setup_jasperstarter.ps1 (Windows)."
        )
        if settings.jasperstarter_path and not _path_matches_os(Path(settings.jasperstarter_path)):
            return None, (
                f"JASPERSTARTER_PATH apunta a ruta de otro SO ({settings.jasperstarter_path}). "
                f"En Linux use tools/jasperstarter/bin/jasperstarter. {hint}"
            )
        return None, f"JasperStarter no encontrado. Configure JASPERSTARTER_PATH o ejecute {hint}"

    if not _java_available():
        return None, (
            "Java no está disponible en el servidor (requerido por JasperStarter). "
            "En Docker reconstruya la imagen (OpenJDK 8 para JasperStarter 3.6)."
        )

    jasper_dir = Path(settings.messiah_jasper_dir)
    if not _ensure_jasper_binaries(jasper_dir, jasper_cmd):
        logger.warning("Reportes .jasper no disponibles en %s", jasper_dir)

    with tempfile.TemporaryDirectory() as tmp:
        resource_dir = jasper_dir
        if inline_auth:
            try:
                report_path, resource_dir = _prepare_ephemeral_jasper(
                    jasper_dir=jasper_dir,
                    jasper_cmd=jasper_cmd,
                    work_dir=Path(tmp),
                    report_stem=report_stem,
                    inline_auth=inline_auth,
                )
            except (OSError, RuntimeError, FileNotFoundError) as exc:
                logger.warning("No se pudo preparar reporte Jasper efímero: %s", exc)
                return None, str(exc)
        else:
            report_path = _report_file(jasper_dir, report_stem)
            if report_path is None:
                return None, f"Reporte no encontrado en {jasper_dir}."

        jasper_params = {
            **params,
            "RUTA": _jasper_ruta_param(resource_dir),
        }

        jdbc_dir = _jasper_jdbc_dir(settings)
        jdbc_args: list[str] = []
        if jdbc_dir is not None:
            jdbc_args = ["--jdbc-dir", str(jdbc_dir)]

        out_prefix = str(Path(tmp) / output_stem)
        cmd = [
            jasper_cmd,
            "pr",
            str(report_path),
            "-o",
            out_prefix,
            "-f",
            "pdf",
            "-r",
            str(resource_dir),
            *jdbc_args,
            *_postgres_db_args(settings),
            *_jasper_param_args(jasper_params),
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=settings.messiah_pdf_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Error ejecutando JasperStarter: %s", exc)
            return None, f"Error ejecutando JasperStarter: {exc}"

        pdf_path = Path(f"{out_prefix}.pdf")
        stderr = (proc.stderr or proc.stdout or "").strip()

        if pdf_path.is_file():
            pdf_bytes = pdf_path.read_bytes()
            if pdf_bytes.startswith(b"%PDF") and len(pdf_bytes) >= _MIN_PDF_BYTES:
                return pdf_bytes, None
            if pdf_bytes.startswith(b"%PDF") and len(pdf_bytes) < _MIN_PDF_BYTES:
                detail = _stderr_relevant(stderr)
                return None, (
                    "El PDF generado está vacío o incompleto. "
                    + (detail or "Verifique datos de la autorización y conexión JDBC.")
                )

        if proc.returncode != 0:
            msg = _stderr_relevant(stderr) or f"JasperStarter terminó con código {proc.returncode}."
            logger.warning("Jasper PDF no generado (code=%s): %s", proc.returncode, stderr[:800])
            return None, msg

        return None, _stderr_relevant(stderr) or "JasperStarter no creó el archivo PDF."


def _fetch_empresa(pg: Session) -> dict[str, Any]:
    row = pg.execute(text(EMPRESA_SQL)).mappings().first()
    return dict(row) if row else {}


def _fetch_meta(pg: Session, consecutivo_autorizacion: int) -> dict[str, Any] | None:
    row = pg.execute(
        text(AUTORIZACION_META_SQL),
        {"consecutivo_autorizacion": int(consecutivo_autorizacion)},
    ).mappings().first()
    return dict(row) if row else None


def _es_empresa_prestador(pg: Session, nit_prestador: str | None) -> bool:
    if not nit_prestador or not str(nit_prestador).strip():
        return True
    total = pg.execute(
        text(ES_EMPRESA_SQL),
        {"nit": str(nit_prestador).strip()},
    ).scalar()
    try:
        return int(total or 0) == 0
    except (TypeError, ValueError):
        return True


def _codigo_entidad_responsable(empresa: dict[str, Any], settings: Settings) -> str:
    if settings.eps_codigo_entidad_responsable.strip():
        return settings.eps_codigo_entidad_responsable.strip()
    contrib = str(empresa.get("codigo_contributivo") or "").strip()
    subsi = str(empresa.get("codigo_subsidiado") or "").strip()
    if contrib and subsi:
        return f"{contrib}-{subsi}"
    return contrib or subsi or "ESSC33-CCF033"


def generar_pdf_codigo_activacion(
    pg: Session,
    settings: Settings,
    *,
    consecutivo_autorizacion: int,
    usuario_autoriza: str = "",
) -> tuple[bytes | None, str, str | None]:
    """PDF PIN / código de activación (Messiah reporteActivacion — ssActivacion.jasper)."""
    empresa = _fetch_empresa(pg)
    params = {
        "CONSECUTIVO": str(int(consecutivo_autorizacion)),
        "EMPRESA": str(empresa.get("razon_social") or settings.eps_nombre_entidad),
        "USUARIO_AUTORIZA": (usuario_autoriza or "")[:200],
    }
    pdf, aviso = _run_jasper_pdf(
        settings=settings,
        report_stem=REPORT_ACTIVACION_PIN,
        output_stem=f"codigo_activacion_{consecutivo_autorizacion}",
        params=params,
        inline_auth={"CONSECUTIVO": int(consecutivo_autorizacion)},
    )
    nombre = f"codigo_activacion_{consecutivo_autorizacion}.pdf"
    return pdf, nombre, aviso


def generar_pdf_autorizacion_completa(
    pg: Session,
    settings: Settings,
    *,
    consecutivo_autorizacion: int,
    usuario_sesion: str,
    prestado: bool = False,
) -> tuple[bytes | None, str, str | None]:
    """PDF autorización completa (Messiah reporteAutorizacionPrestado — reAutorizacion.jasper)."""
    meta = _fetch_meta(pg, consecutivo_autorizacion)
    if meta is None:
        return None, "", "Autorización no encontrada para generar PDF."

    empresa = _fetch_empresa(pg)
    nombre_empresa = str(empresa.get("razon_social") or settings.eps_nombre_entidad)
    usuario_grabado = str(meta.get("usuario_grabado") or usuario_sesion or "")
    numero_solicitud = str(meta.get("numero_solicitud") or "")

    params: dict[str, Any] = {
        **_LABELS_COMPLETO,
        "AUTORIZACION": str(int(consecutivo_autorizacion)),
        "ENTIDAD_RESPONSABLE": settings.eps_nombre_entidad or nombre_empresa,
        "COD_ENTIDAD_RESPONSABLE": _codigo_entidad_responsable(empresa, settings),
        "TELEFONO_USUARIO": "",
        "CELULAR_USUARIO": "",
        "NOMBRE_USUARIO": usuario_sesion[:200],
        "ES_EMPRESA": _es_empresa_prestador(pg, meta.get("nit_prestador")),
        "NUMERO_SOLICITUD_USUARIO": numero_solicitud,
        "NIT_EMPRESA": str(empresa.get("nit") or settings.eps_nit_entidad),
        "LINEA_NACIONAL": settings.eps_linea_nacional,
        "EMPRESA": nombre_empresa,
        "USUARIO_AUTORIZA": usuario_grabado[:200],
        "PRESTADO": "1" if prestado else "0",
    }

    pdf, aviso = _run_jasper_pdf(
        settings=settings,
        report_stem=REPORT_AUTORIZACION_COMPLETA,
        output_stem=f"reporte_autorizacion_{consecutivo_autorizacion}",
        params=params,
        inline_auth={"AUTORIZACION": int(consecutivo_autorizacion)},
    )
    sufijo = "_prestado" if prestado else ""
    nombre = f"reporte_autorizacion_{consecutivo_autorizacion}{sufijo}.pdf"
    return pdf, nombre, aviso


def adjuntar_pdf_respuesta(
    destino: dict[str, Any],
    pg: Session,
    settings: Settings,
    *,
    consecutivo_autorizacion: int | None,
    usuario: str,
    etapa: Literal["emitida", "activada", "confirmada"],
) -> None:
    """
    Añade autorizacion_pdf_base64, autorizacion_pdf_nombre, pdf_generado y pdf_aviso al dict.
    emitida → código activación; activada → autorización completa (PRESTADO=0);
    confirmada → autorización prestada (PRESTADO=1), igual a la vista Messiah.
    """
    destino["autorizacion_pdf_base64"] = None
    destino["autorizacion_pdf_nombre"] = None
    destino["pdf_generado"] = False
    destino["pdf_aviso"] = None

    if consecutivo_autorizacion is None:
        destino["pdf_aviso"] = "Sin consecutivo_autorizacion para generar PDF."
        return

    aviso: str | None = None
    try:
        if etapa == "emitida":
            pdf_bytes, nombre, aviso = generar_pdf_codigo_activacion(
                pg,
                settings,
                consecutivo_autorizacion=int(consecutivo_autorizacion),
                usuario_autoriza=usuario,
            )
        else:
            pdf_bytes, nombre, aviso = generar_pdf_autorizacion_completa(
                pg,
                settings,
                consecutivo_autorizacion=int(consecutivo_autorizacion),
                usuario_sesion=usuario,
                prestado=etapa == "confirmada",
            )
    except Exception as exc:  # pragma: no cover
        logger.warning("PDF autorización no disponible: %s", exc)
        destino["pdf_aviso"] = str(exc)
        return

    if aviso:
        destino["pdf_aviso"] = aviso

    if not pdf_bytes:
        return

    destino["autorizacion_pdf_base64"] = base64.b64encode(pdf_bytes).decode("ascii")
    destino["autorizacion_pdf_nombre"] = nombre
    destino["pdf_generado"] = True
    destino["pdf_aviso"] = None
