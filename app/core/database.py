from __future__ import annotations

import logging
import re
from collections.abc import Generator
from urllib.parse import unquote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

_PREFERRED_SQLSERVER_ODBC_DRIVERS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
)

settings = get_settings()


def _installed_sqlserver_odbc_drivers() -> list[str]:
    try:
        import pyodbc

        return list(pyodbc.drivers())
    except Exception:
        return []


def resolve_sqlserver_url(url: str) -> str:
    """Usa un driver ODBC instalado si el de SQLSERVER_URL no está en el equipo."""
    drivers = _installed_sqlserver_odbc_drivers()
    if not drivers:
        return url

    installed = set(drivers)
    match = re.search(r"([?&])driver=([^&]+)", url, re.IGNORECASE)
    if not match:
        return url

    prefix, raw_driver = match.group(1), match.group(2)
    requested = unquote_plus(raw_driver.replace("+", " "))
    if requested in installed:
        return url

    for candidate in _PREFERRED_SQLSERVER_ODBC_DRIVERS:
        if candidate in installed:
            encoded = candidate.replace(" ", "+")
            resolved = url[: match.start()] + f"{prefix}driver={encoded}" + url[match.end() :]
            logger.warning(
                "sqlserver_odbc_driver_fallback solicitado=%s usando=%s",
                requested,
                candidate,
            )
            return resolved

    raise RuntimeError(
        f"No hay controlador ODBC para SQL Server. Solicitado: {requested!r}. "
        f"Instale ODBC Driver 17/18 for SQL Server. Instalados: {sorted(installed)!r}"
    )


def _pool_kwargs(cfg: Settings, *, pool_size: int | None, max_overflow: int | None) -> dict:
    """Dimensiona el pool para soportar SIIFA_WORKERS sesiones concurrentes por motor."""
    default = max(int(cfg.siifa_workers) + 4, 10)
    return {
        "pool_size": pool_size if pool_size is not None else default,
        "max_overflow": max_overflow if max_overflow is not None else default,
        "pool_timeout": 60,
        "pool_recycle": int(cfg.db_pool_recycle_seconds),
    }


postgres_engine = create_engine(
    settings.postgres_url,
    pool_pre_ping=True,
    future=True,
    **_pool_kwargs(
        settings,
        pool_size=settings.postgres_pool_size,
        max_overflow=settings.postgres_pool_max_overflow,
    ),
)

sqlserver_engine = create_engine(
    resolve_sqlserver_url(settings.sqlserver_url),
    pool_pre_ping=True,
    future=True,
    **_pool_kwargs(
        settings,
        pool_size=settings.sqlserver_pool_size,
        max_overflow=settings.sqlserver_pool_max_overflow,
    ),
)

PostgresSessionLocal = sessionmaker(bind=postgres_engine, autoflush=False, autocommit=False, class_=Session)
SqlServerSessionLocal = sessionmaker(bind=sqlserver_engine, autoflush=False, autocommit=False, class_=Session)


def get_postgres_session() -> Generator[Session, None, None]:
    db = PostgresSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_sqlserver_session() -> Generator[Session, None, None]:
    db = SqlServerSessionLocal()
    try:
        yield db
    finally:
        db.close()
