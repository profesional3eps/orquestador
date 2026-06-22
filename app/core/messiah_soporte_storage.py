"""Almacenamiento de soportes en el repositorio de archivos de Messiah (servidor remoto o montaje)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Literal

from app.config.settings import Settings

logger = logging.getLogger(__name__)

# Util.getRutaDescarga(archivo) => descargaRuta + "/sie_descargas/" + archivo
SIEB_DESCARGAS_SUBDIR = "sie_descargas"
CARPETA_SOPORTE_IPS_AUTORIZACION = "soporte_ips_solicitud_autorizacion"
CARPETA_SOPORTE_SOLICITUD = "soporte_solicitud"
CARPETA_AUTORIZACION_CONFIRMADA = "autorizacion_confirmada"

MessiahSoporteTransport = Literal["auto", "local", "sftp"]


def _nombres_archivo(consecutivo_ips_ss: int, consecutivo_ss_solicitud: int) -> tuple[str, str]:
    nombre_ips = f"{int(consecutivo_ips_ss)}.pdf"
    nombre_ss = f"{int(consecutivo_ss_solicitud)}-orden_medica.pdf"
    return nombre_ips, nombre_ss


def _resolver_transporte(settings: Settings) -> MessiahSoporteTransport:
    modo = (settings.messiah_soporte_transport or "auto").strip().lower()
    if modo in ("local", "sftp"):
        return modo  # type: ignore[return-value]
    if settings.messiah_sftp_host and settings.messiah_sftp_host.strip():
        return "sftp"
    return "local"


def _local_root(settings: Settings) -> Path | None:
    """
    Raíz descargaRuta de Messiah (sin sie_descargas).
    Puede ser ruta local, UNC Windows (\\\\servidor\\share) o montaje NFS/SMB.
    """
    base = settings.messiah_descargas_ruta
    if base is None:
        return None
    path = Path(base)
    if not path.exists():
        logger.warning(
            "MESSIAH_DESCARGAS_RUTA no accesible: %s. "
            "Use unidad de red al servidor Messiah o MESSIAH_SOPORTE_TRANSPORT=sftp.",
            path,
        )
        return None
    return path


def _escribir_local(
    root: Path,
    *,
    nombre_ips: str,
    nombre_ss: str,
    pdf_bytes: bytes,
) -> bool:
    dir_ips = root / SIEB_DESCARGAS_SUBDIR / CARPETA_SOPORTE_IPS_AUTORIZACION
    dir_ss = root / SIEB_DESCARGAS_SUBDIR / CARPETA_SOPORTE_SOLICITUD
    dir_ips.mkdir(parents=True, exist_ok=True)
    dir_ss.mkdir(parents=True, exist_ok=True)

    path_ips = dir_ips / nombre_ips
    path_ss = dir_ss / nombre_ss
    path_ips.write_bytes(pdf_bytes)
    path_ss.write_bytes(pdf_bytes)
    logger.info("Soporte orden médica (local): %s y %s", path_ips, path_ss)
    return True


def _sftp_mkdirs(sftp: object, remote_dir: str) -> None:
    """Crea directorios remotos recursivamente (POSIX)."""
    normalized = remote_dir.replace("\\", "/")
    if not normalized.startswith("/"):
        return
    parts = [p for p in normalized.split("/") if p]
    path_acc = ""
    for part in parts:
        path_acc = f"{path_acc}/{part}"
        try:
            sftp.stat(path_acc)  # type: ignore[attr-defined]
        except OSError:
            sftp.mkdir(path_acc)  # type: ignore[attr-defined]


def _escribir_sftp(
    settings: Settings,
    *,
    nombre_ips: str,
    nombre_ss: str,
    pdf_bytes: bytes,
) -> bool:
    try:
        import paramiko
    except ImportError as exc:
        logger.error("paramiko no instalado; requerido para MESSIAH_SOPORTE_TRANSPORT=sftp: %s", exc)
        return False

    host = (settings.messiah_sftp_host or "").strip()
    user = (settings.messiah_sftp_user or "").strip()
    if not host or not user:
        logger.warning("SFTP incompleto: defina MESSIAH_SFTP_HOST y MESSIAH_SFTP_USER.")
        return False

    remote_root = (settings.messiah_sftp_remote_root or "").strip().replace("\\", "/")
    if not remote_root:
        logger.warning("Defina MESSIAH_SFTP_REMOTE_ROOT (descargaRuta del servidor Messiah).")
        return False

    port = int(settings.messiah_sftp_port)
    remote_ips_dir = f"{remote_root.rstrip('/')}/{SIEB_DESCARGAS_SUBDIR}/{CARPETA_SOPORTE_IPS_AUTORIZACION}"
    remote_ss_dir = f"{remote_root.rstrip('/')}/{SIEB_DESCARGAS_SUBDIR}/{CARPETA_SOPORTE_SOLICITUD}"
    remote_ips_file = f"{remote_ips_dir}/{nombre_ips}"
    remote_ss_file = f"{remote_ss_dir}/{nombre_ss}"

    transport = paramiko.Transport((host, port))
    try:
        if settings.messiah_sftp_private_key_path:
            key_path = Path(settings.messiah_sftp_private_key_path)
            pkey = paramiko.RSAKey.from_private_key_file(str(key_path))
            transport.connect(username=user, pkey=pkey)
        else:
            password = settings.messiah_sftp_password or ""
            transport.connect(username=user, password=password)

        sftp = paramiko.SFTPClient.from_transport(transport)
        assert sftp is not None
        _sftp_mkdirs(sftp, remote_ips_dir)
        _sftp_mkdirs(sftp, remote_ss_dir)

        with sftp.file(remote_ips_file, "wb") as remote_f:
            remote_f.write(pdf_bytes)
        with sftp.file(remote_ss_file, "wb") as remote_f:
            remote_f.write(pdf_bytes)

        logger.info(
            "Soporte orden médica (SFTP %s): %s y %s",
            host,
            remote_ips_file,
            remote_ss_file,
        )
        return True
    except Exception as exc:
        logger.error("Error subiendo soporte por SFTP a Messiah: %s", exc)
        return False
    finally:
        try:
            transport.close()
        except Exception:
            pass


def guardar_soporte_orden_medica_messiah(
    settings: Settings,
    *,
    consecutivo_ips_ss: int,
    consecutivo_ss_solicitud: int,
    pdf_bytes: bytes,
) -> tuple[str, str] | None:
    """
    Guarda el PDF en el mismo repositorio que Messiah (estructura sie_descargas/...).

    BD (vía caller): ct_ips_ss_solicitud.url_archivo = nombre_ips;
    ss_solicitud_soporte.url = nombre_ss.

    Returns:
        (nombre_archivo_ips, nombre_archivo_ss) o None si falló el almacenamiento.
    """
    nombre_ips, nombre_ss = _nombres_archivo(consecutivo_ips_ss, consecutivo_ss_solicitud)
    transporte = _resolver_transporte(settings)
    ok = False

    if transporte == "sftp":
        ok = _escribir_sftp(settings, nombre_ips=nombre_ips, nombre_ss=nombre_ss, pdf_bytes=pdf_bytes)
    else:
        root = _local_root(settings)
        if root is not None:
            ok = _escribir_local(root, nombre_ips=nombre_ips, nombre_ss=nombre_ss, pdf_bytes=pdf_bytes)

    if not ok:
        return None

    if settings.messiah_soporte_copia_local:
        try:
            backup = settings.ticket_supports_dir / "autorizacion_orden_medica_ips" / nombre_ips
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(pdf_bytes)
        except OSError as exc:
            logger.debug("Copia local de respaldo omitida: %s", exc)

    return nombre_ips, nombre_ss


def guardar_soporte_confirmacion_autorizacion_messiah(
    settings: Settings,
    *,
    consecutivo_autorizacion: int,
    pdf_bytes: bytes,
    nombre_original: str = "soporte_confirmacion.pdf",
) -> str | None:
    """
    Guarda soporte de confirmación en sie_descargas/autorizacion_confirmada/
    (Messiah FilePath.CARPETA_AUTORIZACION_CONFIRMADA).
    Retorna url_activacion para ss_autorizacion ({consecutivo}-{nombre}).
    """
    base_name = nombre_original.strip() or "soporte_confirmacion.pdf"
    if not base_name.lower().endswith(".pdf"):
        base_name = f"{base_name}.pdf"
    url_db = f"{int(consecutivo_autorizacion)}-{base_name}"
    transporte = _resolver_transporte(settings)
    ok = False

    if transporte == "sftp":
        ok = _escribir_sftp_confirmacion(settings, url_db=url_db, pdf_bytes=pdf_bytes)
    else:
        root = _local_root(settings)
        if root is not None:
            ok = _escribir_local_confirmacion(root, url_db=url_db, pdf_bytes=pdf_bytes)

    if not ok:
        return None

    if settings.messiah_soporte_copia_local:
        try:
            backup = settings.ticket_supports_dir / "autorizacion_confirmada_ips" / url_db
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(pdf_bytes)
        except OSError as exc:
            logger.debug("Copia local confirmación omitida: %s", exc)

    return url_db


def _escribir_local_confirmacion(root: Path, *, url_db: str, pdf_bytes: bytes) -> bool:
    dir_confirm = root / SIEB_DESCARGAS_SUBDIR / CARPETA_AUTORIZACION_CONFIRMADA
    dir_confirm.mkdir(parents=True, exist_ok=True)
    path = dir_confirm / url_db
    path.write_bytes(pdf_bytes)
    logger.info("Soporte confirmación autorización (local): %s", path)
    return True


def _escribir_sftp_confirmacion(settings: Settings, *, url_db: str, pdf_bytes: bytes) -> bool:
    try:
        import paramiko
    except ImportError as exc:
        logger.error("paramiko no instalado para SFTP confirmación: %s", exc)
        return False

    host = (settings.messiah_sftp_host or "").strip()
    user = (settings.messiah_sftp_user or "").strip()
    if not host or not user:
        return False

    remote_root = (settings.messiah_sftp_remote_root or "").strip().replace("\\", "/")
    if not remote_root:
        return False

    remote_dir = f"{remote_root.rstrip('/')}/{SIEB_DESCARGAS_SUBDIR}/{CARPETA_AUTORIZACION_CONFIRMADA}"
    remote_file = f"{remote_dir}/{url_db}"
    port = int(settings.messiah_sftp_port)
    transport = paramiko.Transport((host, port))
    try:
        if settings.messiah_sftp_private_key_path:
            pkey = paramiko.RSAKey.from_private_key_file(str(Path(settings.messiah_sftp_private_key_path)))
            transport.connect(username=user, pkey=pkey)
        else:
            transport.connect(username=user, password=settings.messiah_sftp_password or "")
        sftp = paramiko.SFTPClient.from_transport(transport)
        assert sftp is not None
        _sftp_mkdirs(sftp, remote_dir)
        with sftp.file(remote_file, "wb") as remote_f:
            remote_f.write(pdf_bytes)
        logger.info("Soporte confirmación autorización (SFTP %s): %s", host, remote_file)
        return True
    except Exception as exc:
        logger.error("Error subiendo soporte confirmación por SFTP: %s", exc)
        return False
    finally:
        try:
            transport.close()
        except Exception:
            pass


def mensaje_soporte_no_disponible(settings: Settings) -> str:
    transporte = _resolver_transporte(settings)
    if transporte == "sftp":
        return (
            "No se pudo subir el soporte al servidor Messiah por SFTP. "
            "Revise MESSIAH_SFTP_HOST, MESSIAH_SFTP_REMOTE_ROOT (descargaRuta) y credenciales."
        )
    return (
        "No se pudo guardar el soporte en el repositorio de Messiah. "
        "Configure MESSIAH_DESCARGAS_RUTA apuntando a descargaRuta del servidor Messiah "
        "(montaje SMB/UNC) o use MESSIAH_SOPORTE_TRANSPORT=sftp con acceso al mismo servidor."
    )
