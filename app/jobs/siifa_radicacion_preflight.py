"""Verificación de conectividad antes de radicación SIIFA (CSV o API)."""
from __future__ import annotations

import socket
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text

from app.config.settings import get_settings
from app.core.database import PostgresSessionLocal, SqlServerSessionLocal
from app.services.siifa_client import SIIFAClient


def _host_de_url(url: str) -> str:
    parsed = urlparse(url.strip())
    return parsed.hostname or ""


def verificar_conectividad_radicacion() -> dict[str, Any]:
    """
    Comprueba acceso a PostgreSQL, SQL Server y SIIFA.
    Devuelve {"ok": bool, "checks": {servicio: {ok, mensaje}}}.
    """
    settings = get_settings()
    checks: dict[str, dict[str, Any]] = {}
    ok_global = True

    pg = PostgresSessionLocal()
    try:
        pg.execute(text("SELECT 1"))
        checks["postgresql"] = {
            "ok": True,
            "mensaje": f"Conexión OK ({_host_de_url(settings.postgres_url)}:5432)",
        }
    except Exception as exc:
        checks["postgresql"] = {
            "ok": False,
            "mensaje": (
                f"No se puede conectar a PostgreSQL ({_host_de_url(settings.postgres_url)}). "
                f"Requiere VPN/red interna. Detalle: {exc}"
            ),
        }
        ok_global = False
    finally:
        pg.close()

    sql = SqlServerSessionLocal()
    try:
        sql.execute(text("SELECT 1"))
        checks["sqlserver"] = {
            "ok": True,
            "mensaje": f"Conexión OK ({_host_de_url(settings.sqlserver_url)})",
        }
    except Exception as exc:
        checks["sqlserver"] = {
            "ok": False,
            "mensaje": f"No se puede conectar a SQL Server. Detalle: {exc}",
        }
        ok_global = False
    finally:
        sql.close()

    siifa_host = _host_de_url(settings.siifa_seguridad_base_url)
    try:
        if not siifa_host:
            raise ValueError("SIIFA_SEGURIDAD_BASE_URL sin host válido")
        socket.getaddrinfo(siifa_host, 443, type=socket.SOCK_STREAM)
        checks["siifa_dns"] = {"ok": True, "mensaje": f"DNS OK ({siifa_host})"}
    except Exception as exc:
        checks["siifa_dns"] = {
            "ok": False,
            "mensaje": (
                f"No se resuelve el host SIIFA ({siifa_host}). "
                f"Requiere internet/DNS saliente. Detalle: {exc}"
            ),
        }
        ok_global = False

    if checks.get("siifa_dns", {}).get("ok"):
        try:
            SIIFAClient(settings).login()
            checks["siifa_login"] = {"ok": True, "mensaje": "Login SIIFA OK"}
        except Exception as exc:
            checks["siifa_login"] = {
                "ok": False,
                "mensaje": f"Login SIIFA falló. Detalle: {exc}",
            }
            ok_global = False

    return {"ok": ok_global, "checks": checks}


def verificar_conectividad_erp() -> dict[str, Any]:
    """Comprueba PostgreSQL y SQL Server (sin SIIFA)."""
    full = verificar_conectividad_radicacion()
    checks = {
        k: v
        for k, v in full.get("checks", {}).items()
        if k in ("postgresql", "sqlserver")
    }
    ok = all(c.get("ok") for c in checks.values())
    return {"ok": ok, "checks": checks}
