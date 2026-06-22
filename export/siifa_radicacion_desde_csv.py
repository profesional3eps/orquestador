#!/usr/bin/env python3
"""
Radicación SIIFA desde CSV — script 100% autocontenido (misma idea que
siifa_sync_seguimiento_standalone.py).

NO importa app.* ni ningún módulo del proyecto ORQUESTADORDB.
Única dependencia externa de red: endpoints SIIFA (login + FacturaRadicado).

Lógica equivalente a siifa_radicacion_desde_csv.py + RadicacionService.ejecutar_desde_csv:
  1. Lee CSV siifa_facturas_sin_radicar.csv (idFactura, numeroFactura, nit emisor)
  2. Busca administrativo.rips_af (numero_factura + numero_identificacion)
  3. Valida administrativo.rips_resumen.estado = 5
  4. POST SIIFA con radica_rips y fecha_radica del ERP (rips_resumen)
  5. Actualiza rips_af (radicado_siifa, fecha_rad_siifa, idfactura_siifa)
  6. Auditoría SQL Server: SIIFA_Factura, SIIFA_FacturaERP, SIIFA_Radicado, SIIFA_FacturaTraza
  7. Checkpoint en SIIFA_LoteCheckpoint (proceso RADICACION_CSV)
  8. Escribe estadoProceso, observacion y fechaProceso en el mismo CSV por fila

Dependencias pip: psycopg2-binary, sqlalchemy, pyodbc o pymssql.

Uso (desde export/, junto al CSV):
  python3 -u siifa_radicacion_desde_csv.py --verificar
  python3 -u siifa_radicacion_desde_csv.py --filas 100 --sin-verificar
  python3 -u siifa_radicacion_desde_csv.py --hasta-completar --sin-verificar
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum

try:
    from enum import StrEnum
except ImportError:

    class StrEnum(str, Enum):
        """Compatibilidad Python 3.10 (StrEnum nativo desde 3.11)."""

        def __str__(self) -> str:
            return str(self.value)
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

# ── Rutas (script y CSV en la misma carpeta export/) ───────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_VERSION = "standalone-radicacion-6"
DEFAULT_CSV = SCRIPT_DIR / "siifa_facturas_sin_radicar.csv"
ESTADO_RADICADO_ERP = 5

# ── Configuración (edite aquí; no usa .env ni servicios del proyecto) ─────────
CONFIG: dict[str, Any] = {
    "POSTGRES_URL": (
        "postgresql+psycopg2://postgres:Sup3r4dm1n7ami1i4rC0l@10.0.1.240:5432/base_sie_comfasucre"
    ),
    "SQLSERVER_URL": (
        "mssql+pyodbc://app_orquestador:3P2F4m1l14rC0l0m8142026@150.136.57.32:1433/OrquestacionDB"
        "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
    ),
    # URL alternativa sin ODBC (Linux/Ubuntu). Si está vacía, se deriva de SQLSERVER_URL.
    "SQLSERVER_URL_PYMSSQL": "",
    # auto = ODBC primero, luego pymssql | odbc | pymssql
    "SQLSERVER_BACKEND": "auto",
    "FILAS_POR_LOTE": 5000,
    "PROCESO_CHECKPOINT": "RADICACION_CSV",
    "TIPO_EJECUCION": "CSV_BATCH",
    "REUTILIZAR_CLASIFICADAS": True,
    "SIIFA_SEGURIDAD_BASE_URL": "https://siifa.sispro.gov.co/siifa-seguridad",
    "SIIFA_FACTURA_BASE_URL": "https://siifa.sispro.gov.co/siifa-factura",
    "SIIFA_USERNAME": "CC52154192",
    "SIIFA_PASSWORD": "Mariana/*",
    "SIIFA_HTTP_TIMEOUT_SECONDS": 90,
    "SIIFA_RETRY_MAX_ATTEMPTS": 8,
    "SIIFA_RETRY_BASE_DELAY_SECONDS": 2.0,
    # Vacío = autodetectar (18 → 17 → 13). En Linux: msodbcsql17 o msodbcsql18
    "SQLSERVER_ODBC_DRIVER": "",
    "PG_CONNECT_TIMEOUT": 15,
    "PG_STATEMENT_TIMEOUT_MS": 120000,
}

# Conexiones BD (proceso secuencial: una fila a la vez)
_DB_POOL_SIZE = 2

# Backend activo tras conectar (odbc | pymssql)
_SQLSERVER_BACKEND_ACTIVO: str = ""

_ODBC_DRIVERS_CANDIDATOS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "FreeTDS",
)

_DEPS_BASE = ("psycopg2", "sqlalchemy")

# ── CSV columnas ──────────────────────────────────────────────────────────────
_CSV_ID = ("id_factura_siifa", "idFactura", "id_factura")
_CSV_NUM = ("numero_factura", "numeroFactura")
_CSV_NIT = ("nit_emisor", "emisor_nitEmisor", "nitEmisor", "emisor_nit")
_CSV_COL_ESTADO = "estadoProceso"
_CSV_COL_OBS = "observacion"
_CSV_COL_FECHA = "fechaProceso"
_CSV_OBS_MAX = 2000

_RETRIABLE_HTTP = frozenset({429, 500, 502, 503, 504})
_ESTADOS_CLASIFICADOS_FINALES = frozenset({
    "RADICADA",
    "NO_ENCONTRADA_ERP",
    "NO_RADICADA_ERP",
    "OMITIDA",
})

# ── PostgreSQL ────────────────────────────────────────────────────────────────
# Búsqueda index-friendly (sin TRIM en columnas; params ya vienen recortados del CSV)
SQL_BUSCAR_RIPS_AF = """
SELECT consecutivo_rips_af, consecutivo_rips, numero_factura, numero_identificacion,
       radicado_siifa, idfactura_siifa
FROM administrativo.rips_af
WHERE numero_factura = :numero_factura
  AND numero_identificacion = :nit_emisor
LIMIT 1
"""

# Fallback si en ERP hay espacios en los campos
SQL_BUSCAR_RIPS_AF_TRIM = """
SELECT consecutivo_rips_af, consecutivo_rips, numero_factura, numero_identificacion,
       radicado_siifa, idfactura_siifa
FROM administrativo.rips_af
WHERE TRIM(numero_factura) = :numero_factura
  AND TRIM(numero_identificacion) = :nit_emisor
LIMIT 1
"""

SQL_BUSCAR_RIPS_RESUMEN = """
SELECT consecutivo_rips, estado, radica_rips, fecha_radica
FROM administrativo.rips_resumen
WHERE consecutivo_rips = :consecutivo_rips
LIMIT 1
"""

SQL_UPDATE_RIPS_AF = """
UPDATE administrativo.rips_af
SET radicado_siifa = :radicado_siifa,
    fecha_rad_siifa = :fecha_rad_siifa,
    idfactura_siifa = :idfactura_siifa
WHERE consecutivo_rips_af = :consecutivo_rips_af
"""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
    force=True,
)
logger = logging.getLogger("siifa_radicacion_standalone")


def _log_flush(msg: str, *args: Any) -> None:
    logger.info(msg, *args)
    sys.stderr.flush()


# ── Modelos ───────────────────────────────────────────────────────────────────
class EstadoProceso(StrEnum):
    RADICADA = "RADICADA"
    NO_ENCONTRADA_ERP = "NO_ENCONTRADA_ERP"
    NO_RADICADA_ERP = "NO_RADICADA_ERP"
    OMITIDA = "OMITIDA"
    ERROR = "ERROR"


class ResultadoTraza(StrEnum):
    OK = "OK"
    ERROR = "ERROR"
    NO_ENCONTRADA = "NO_ENCONTRADA"
    NO_RADICADA_ERP = "NO_RADICADA_ERP"
    OMITIDA = "OMITIDA"


def _estado_sql(estado: EstadoProceso) -> str:
    """Valor corto para VARCHAR(30) en SQL Server (evita 'EstadoProceso.X' en Py 3.10)."""
    return estado.value


def _traza_sql(resultado: ResultadoTraza) -> str:
    return resultado.value


@dataclass(frozen=True)
class ResultadoFila:
    estado: EstadoProceso
    observacion: str


@dataclass(frozen=True)
class FacturaCsv:
    id_factura_siifa: int
    numero_factura: str
    nit_emisor: str


@dataclass
class CsvSinRadicar:
    items: list[FacturaCsv]
    filas: list[dict[str, str]]
    fieldnames: list[str]
    delimiter: str
    # Índice en filas[] por cada item (misma posición que items); evita colisión por id duplicado.
    item_a_fila_idx: list[int]


@dataclass
class RipsResumenMatch:
    consecutivo_rips: int
    estado: int
    radica_rips: str
    fecha_radica: datetime | None = None


@dataclass
class RipsAfMatch:
    consecutivo_rips_af: int
    consecutivo_rips: int
    numero_factura: str
    numero_identificacion: str
    radicado_siifa: int | None = None
    idfactura_siifa: str | None = None


@dataclass
class LoteCheckpoint:
    ultima_pagina: int = 0
    lote_completado: bool = False

    @property
    def proxima_fila(self) -> int:
        return 1 if self.lote_completado else self.ultima_pagina + 1


@dataclass
class Metricas:
    procesadas: int = 0
    radicadas: int = 0
    no_encontradas_erp: int = 0
    no_radicadas_erp: int = 0
    omitidas: int = 0
    errores: int = 0
    advertencias: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "procesadas": self.procesadas,
            "radicadas": self.radicadas,
            "no_encontradas_erp": self.no_encontradas_erp,
            "no_radicadas_erp": self.no_radicadas_erp,
            "omitidas": self.omitidas,
            "errores": self.errores,
            "advertencias": self.advertencias[:200],
        }


# ── Utilidades ────────────────────────────────────────────────────────────────
def _csv_text(val: Any) -> str:
    """Convierte celda CSV a str; DictReader pone listas en clave None si hay columnas de más."""
    if val is None:
        return ""
    if isinstance(val, list):
        partes = [_csv_text(x) for x in val]
        return ", ".join(p for p in partes if p)
    if isinstance(val, str):
        return val.strip()
    return str(val).strip()


def _reparar_fila_csv(raw: list[str], fieldnames: list[str]) -> list[str]:
    """Recombina columnas extra por comas sin escapar (p. ej. razón social con coma)."""
    expected = len(fieldnames)
    if len(raw) == expected:
        return raw
    if len(raw) < expected:
        return raw + [""] * (expected - len(raw))

    row = list(raw)
    indices_razon = [
        fieldnames.index(name)
        for name in ("razon_social_emisor", "razon_social_adquiriente")
        if name in fieldnames
    ]
    while len(row) > expected and indices_razon:
        idx = indices_razon[0]
        if idx + 1 >= len(row):
            break
        row[idx : idx + 2] = [",".join(row[idx : idx + 2])]
        if len(row) <= expected:
            break
    if len(row) > expected:
        row = row[:expected]
    elif len(row) < expected:
        row = row + [""] * (expected - len(row))
    return row


def _csv_valor(row: dict[str, str], keys: Iterable[str]) -> str:
    for key in keys:
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _detectar_delimitador(path: Path) -> str:
    muestra = path.read_text(encoding="utf-8-sig")[:4096]
    primera = muestra.splitlines()[0] if muestra else ""
    return ";" if primera.count(";") > primera.count(",") else ","


def _parse_fecha(val: str | date | datetime) -> datetime:
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time())
    raw = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:10], fmt)
        except ValueError:
            continue
    raise ValueError(f"Fecha inválida: {val!r}. Use YYYY-MM-DD.")


def _valor_radicado_smallint(valor: int) -> int:
    """rips_af.radicado_siifa es SMALLINT: bandera 1 si excede rango."""
    return valor if valor <= 32767 else 1


def _require_deps() -> None:
    missing = [p for p in _DEPS_BASE if not _try_import(p)]
    if missing:
        raise SystemExit(
            f"Faltan dependencias: {', '.join(missing)}\n"
            "Instale: pip install psycopg2-binary sqlalchemy"
        )
    if not _try_import("pyodbc") and not _try_import("pymssql"):
        raise SystemExit(
            "Falta conector SQL Server. Instale UNA opción:\n"
            "  A) pip install pyodbc  +  msodbcsql17 (driver ODBC Microsoft)\n"
            "  B) pip install pymssql  +  apt install freetds-dev   (recomendado en Ubuntu)"
        )


def _try_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _resolve_csv(path: Path) -> Path:
    raw = path.expanduser()
    if raw.is_absolute() and raw.is_file():
        return raw.resolve()
    # Prioridad: directorio del script (export/) → cwd → nombre relativo en export/
    for base in (SCRIPT_DIR, Path.cwd()):
        candidate = (base / raw).resolve()
        if candidate.is_file():
            return candidate
    return (SCRIPT_DIR / raw).resolve()


def _apply_env_config() -> None:
    for key in (
        "POSTGRES_URL",
        "SQLSERVER_URL",
        "SQLSERVER_URL_PYMSSQL",
        "SQLSERVER_ODBC_DRIVER",
        "SQLSERVER_BACKEND",
        "SIIFA_SEGURIDAD_BASE_URL",
        "SIIFA_FACTURA_BASE_URL",
        "SIIFA_USERNAME",
        "SIIFA_PASSWORD",
    ):
        env_val = os.environ.get(key, "").strip()
        if env_val:
            CONFIG[key] = env_val


def _pyodbc_drivers_instalados() -> list[str]:
    try:
        import pyodbc

        return list(pyodbc.drivers())
    except Exception:
        return []


def _es_error_driver_odbc(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "can't open lib",
            "file not found",
            "im002",
            "driver manager",
            "data source name not found",
            "driver's sqlallochandle",
        )
    )


def _sqlserver_url_con_driver(url: str, driver_name: str) -> str:
    """Sustituye o agrega el parámetro driver= en la URL SQLAlchemy."""
    encoded = driver_name.replace(" ", "+")
    if re.search(r"driver=", url, flags=re.IGNORECASE):
        return re.sub(r"driver=[^&]+", f"driver={encoded}", url, flags=re.IGNORECASE)
    sep = "&" if "?" in url else "?"
    extra = "&TrustServerCertificate=yes" if "TrustServerCertificate" not in url else ""
    return f"{url}{sep}driver={encoded}{extra}"


def _drivers_odbc_a_probar(url: str) -> list[str]:
    manual = str(CONFIG.get("SQLSERVER_ODBC_DRIVER") or "").strip()
    if manual:
        return [manual]

    instalados = _pyodbc_drivers_instalados()
    if instalados:
        preferidos = [d for d in _ODBC_DRIVERS_CANDIDATOS if d in instalados]
        restantes = [d for d in instalados if "sql server" in d.lower() or d == "FreeTDS"]
        vistos: set[str] = set()
        ordenados: list[str] = []
        for d in preferidos + restantes:
            if d not in vistos:
                vistos.add(d)
                ordenados.append(d)
        if ordenados:
            return ordenados

    # Sin pyodbc.drivers(): probar candidatos en orden (Linux suele tener 17)
    return list(_ODBC_DRIVERS_CANDIDATOS)


def _to_pymssql_url(url: str) -> str | None:
    """Convierte mssql+pyodbc://... en mssql+pymssql://... (sin parámetros ODBC)."""
    explicit = str(CONFIG.get("SQLSERVER_URL_PYMSSQL") or "").strip()
    if explicit:
        return explicit
    raw = url.strip()
    if raw.startswith("mssql+pymssql://"):
        return raw.split("?", 1)[0]
    if raw.startswith("mssql+pyodbc://"):
        return raw.replace("mssql+pyodbc://", "mssql+pymssql://", 1).split("?", 1)[0]
    if raw.startswith("mssql://"):
        return raw.replace("mssql://", "mssql+pymssql://", 1).split("?", 1)[0]
    return None


def _hint_instalacion_sqlserver() -> str:
    instalados = _pyodbc_drivers_instalados()
    pymssql_ok = _try_import("pymssql")
    return (
        "Opción A (ODBC): sudo ACCEPT_EULA=Y apt-get install -y msodbcsql17 unixodbc-dev && "
        "pip install pyodbc\n"
        "Opción B (sin ODBC, recomendada Ubuntu): sudo apt-get install -y freetds-dev && "
        "pip install pymssql && export SQLSERVER_BACKEND=pymssql\n"
        f"Estado: ODBC drivers={instalados or 'ninguno'}, pymssql={'OK' if pymssql_ok else 'no instalado'}"
    )


def _crear_engine_pymssql(url: str, pool_size: int) -> Engine:
    global _SQLSERVER_BACKEND_ACTIVO
    if not _try_import("pymssql"):
        raise ImportError("pymssql no instalado. Ejecute: pip install pymssql")
    # NullPool: una conexión por operación (evita bloqueos con hilos + FreeTDS)
    engine = create_engine(
        url,
        poolclass=NullPool,
        pool_pre_ping=True,
        connect_args={
            "timeout": 60,
            "login_timeout": 60,
            "tds_version": "7.4",
        },
    )
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    _SQLSERVER_BACKEND_ACTIVO = "pymssql"
    logger.info("SQL Server conectado vía pymssql (FreeTDS, sin driver ODBC)")
    return engine


def _crear_engine_odbc(url: str, pool_size: int) -> Engine:
    """Conecta con pyodbc probando drivers ODBC 18, 17, 13…"""
    if not _try_import("pyodbc"):
        raise ImportError("pyodbc no instalado")

    errores: list[str] = []
    drivers = _drivers_odbc_a_probar(url)

    for driver in drivers:
        test_url = _sqlserver_url_con_driver(url, driver)
        try:
            engine = create_engine(
                test_url,
                pool_pre_ping=True,
                pool_size=pool_size,
                max_overflow=pool_size,
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            global _SQLSERVER_BACKEND_ACTIVO
            _SQLSERVER_BACKEND_ACTIVO = "odbc"
            logger.info("SQL Server conectado con driver ODBC: %s", driver)
            return engine
        except Exception as exc:
            errores.append(f"{driver}: {exc}")
            if not _es_error_driver_odbc(exc):
                raise

    raise RuntimeError("; ".join(errores))


def _crear_engine_sqlserver(url: str, pool_size: int) -> Engine:
    """Conecta a SQL Server: ODBC (pyodbc) y/o pymssql según SQLSERVER_BACKEND."""
    backend = str(CONFIG.get("SQLSERVER_BACKEND", "auto")).lower().strip()
    odbc_error: str | None = None
    pymssql_error: str | None = None

    if backend in ("odbc", "auto"):
        try:
            return _crear_engine_odbc(url, pool_size)
        except Exception as exc:
            if backend == "odbc":
                raise RuntimeError(f"{exc} | {_hint_instalacion_sqlserver()}") from exc
            odbc_error = str(exc)

    if backend in ("pymssql", "auto"):
        pymssql_url = _to_pymssql_url(url)
        if not pymssql_url:
            pymssql_error = "No se pudo derivar URL pymssql desde SQLSERVER_URL"
        else:
            try:
                return _crear_engine_pymssql(pymssql_url, pool_size)
            except Exception as exc:
                if backend == "pymssql":
                    raise RuntimeError(f"{exc} | {_hint_instalacion_sqlserver()}") from exc
                pymssql_error = str(exc)

    partes = [p for p in (odbc_error, pymssql_error) if p]
    raise RuntimeError(" | ".join(partes) + " | " + _hint_instalacion_sqlserver())


def _crear_engine_postgres() -> Engine:
    timeout = int(CONFIG.get("PG_CONNECT_TIMEOUT", 15))
    stmt_ms = int(CONFIG.get("PG_STATEMENT_TIMEOUT_MS", 120000))
    return create_engine(
        str(CONFIG["POSTGRES_URL"]),
        pool_pre_ping=True,
        pool_size=_DB_POOL_SIZE,
        max_overflow=_DB_POOL_SIZE,
        connect_args={
            "connect_timeout": timeout,
            "options": f"-c statement_timeout={stmt_ms}",
        },
    )


def listar_conectores_sqlserver() -> dict[str, Any]:
    return {
        "pyodbc_instalado": _try_import("pyodbc"),
        "pymssql_instalado": _try_import("pymssql"),
        "odbc_drivers": _pyodbc_drivers_instalados(),
        "backend_config": CONFIG.get("SQLSERVER_BACKEND", "auto"),
        "backend_activo": _SQLSERVER_BACKEND_ACTIVO or None,
        "url_pymssql": _to_pymssql_url(str(CONFIG.get("SQLSERVER_URL", ""))),
    }


# ── Lectura / escritura CSV ───────────────────────────────────────────────────
def _fieldnames_csv_sin_radicar(originales: list[str] | None) -> list[str]:
    base = [_csv_text(c) for c in (originales or []) if _csv_text(c)]
    for col in (_CSV_COL_ESTADO, _CSV_COL_OBS, _CSV_COL_FECHA):
        if col not in base:
            base.append(col)
    return base


def cargar_csv_sin_radicar(path: Path) -> CsvSinRadicar:
    if not path.is_file():
        raise FileNotFoundError(f"No existe el CSV: {path}")

    delim = _detectar_delimitador(path)
    items: list[FacturaCsv] = []
    filas: list[dict[str, str]] = []
    item_a_fila_idx: list[int] = []
    omitidas = 0
    fieldnames: list[str] = []
    raw_fieldnames: list[str] = []
    ids_vistos: set[int] = set()
    filas_duplicadas_id = 0
    filas_reparadas = 0

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter=delim)
        try:
            raw_fieldnames = [_csv_text(c) for c in next(reader)]
        except StopIteration:
            raise ValueError(f"CSV vacío: {path}") from None
        if not raw_fieldnames:
            raise ValueError(f"CSV sin encabezados: {path}")
        fieldnames = _fieldnames_csv_sin_radicar(raw_fieldnames)
        expected_raw = len(raw_fieldnames)

        for raw_row in reader:
            if not raw_row or not any(_csv_text(c) for c in raw_row):
                continue
            if len(raw_row) != expected_raw:
                filas_reparadas += 1
                raw_row = _reparar_fila_csv([_csv_text(c) for c in raw_row], raw_fieldnames)
            norm: dict[str, str] = {col: "" for col in fieldnames}
            for i, col in enumerate(raw_fieldnames):
                norm[col] = _csv_text(raw_row[i]) if i < len(raw_row) else ""
            filas.append(norm)
            idx = len(filas) - 1
            id_raw = _csv_valor(norm, _CSV_ID)
            numero = _csv_valor(norm, _CSV_NUM)
            nit = _csv_valor(norm, _CSV_NIT)
            if not id_raw or not numero or not nit:
                omitidas += 1
                continue
            try:
                id_factura = int(id_raw)
                if id_factura in ids_vistos:
                    filas_duplicadas_id += 1
                else:
                    ids_vistos.add(id_factura)
                items.append(
                    FacturaCsv(
                        id_factura_siifa=id_factura,
                        numero_factura=numero,
                        nit_emisor=nit,
                    )
                )
                item_a_fila_idx.append(idx)
            except (TypeError, ValueError):
                omitidas += 1

    if not items:
        raise ValueError(f"CSV sin filas válidas: {path}")
    if omitidas:
        logger.warning("Filas omitidas (datos incompletos): %s", omitidas)
    if filas_reparadas:
        logger.warning(
            "CSV con %s fila(s) reparada(s) (comas sin escapar en razón social u otras columnas)",
            filas_reparadas,
        )
    if filas_duplicadas_id:
        logger.warning(
            "CSV con idFactura repetidos: %s filas duplicadas (%s ids únicos de %s filas). "
            "La observación se escribe en la fila física procesada, no en la última aparición del id.",
            filas_duplicadas_id,
            len(ids_vistos),
            len(items),
        )
    return CsvSinRadicar(
        items=items,
        filas=filas,
        fieldnames=fieldnames,
        delimiter=delim,
        item_a_fila_idx=item_a_fila_idx,
    )


def leer_csv_sin_radicar(path: Path) -> list[FacturaCsv]:
    return cargar_csv_sin_radicar(path).items


def _aplicar_observacion_csv(
    csv_data: CsvSinRadicar,
    *,
    fila_idx: int,
    resultado: ResultadoFila,
    dry_run: bool,
) -> None:
    if fila_idx < 0 or fila_idx >= len(csv_data.filas):
        logger.warning("Índice CSV fuera de rango: %s (filas=%s)", fila_idx, len(csv_data.filas))
        return
    obs = resultado.observacion.strip()
    if dry_run and obs and not obs.upper().startswith("DRY-RUN"):
        obs = f"DRY-RUN: {obs}"
    fila = csv_data.filas[fila_idx]
    fila[_CSV_COL_ESTADO] = _estado_sql(resultado.estado)
    fila[_CSV_COL_OBS] = obs[:_CSV_OBS_MAX]
    fila[_CSV_COL_FECHA] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _contar_filas_con_observacion(csv_data: CsvSinRadicar) -> int:
    return sum(1 for fila in csv_data.filas if (fila.get(_CSV_COL_OBS) or "").strip())


def guardar_csv_sin_radicar(path: Path, csv_data: CsvSinRadicar) -> int:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=csv_data.fieldnames,
            delimiter=csv_data.delimiter,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for fila in csv_data.filas:
            writer.writerow({col: fila.get(col, "") for col in csv_data.fieldnames})
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
    return _contar_filas_con_observacion(csv_data)


def _parse_fecha_radica_erp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass
    for fmt, size in (("%Y-%m-%d %H:%M:%S.%f", 26), ("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(raw[:size], fmt)
        except ValueError:
            continue
    return None


def _fecha_erp_a_iso_utc(fecha: datetime) -> str:
    if fecha.tzinfo is None:
        utc = fecha.replace(tzinfo=timezone.utc)
    else:
        utc = fecha.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.0Z")


def pg_buscar_rips_resumen(session: Session, consecutivo_rips: int) -> RipsResumenMatch | None:
    row = session.execute(
        text(SQL_BUSCAR_RIPS_RESUMEN),
        {"consecutivo_rips": consecutivo_rips},
    ).mappings().first()
    if not row:
        return None
    fecha = _parse_fecha_radica_erp(row.get("fecha_radica"))
    return RipsResumenMatch(
        consecutivo_rips=int(row["consecutivo_rips"]),
        estado=int(row["estado"]),
        radica_rips=str(row.get("radica_rips") or "").strip(),
        fecha_radica=fecha,
    )


# ── Cliente SIIFA (stdlib) ───────────────────────────────────────────────────
class SiifaClient:
    def __init__(self) -> None:
        self._token: str | None = None

    def login(self, *, force: bool = False) -> str:
        if not force and self._token:
            return self._token
        user = str(CONFIG.get("SIIFA_USERNAME") or "").strip()
        pwd = str(CONFIG.get("SIIFA_PASSWORD") or "").strip()
        if not user or not pwd:
            raise ValueError("Configure SIIFA_USERNAME y SIIFA_PASSWORD en CONFIG o entorno.")
        url = f"{str(CONFIG['SIIFA_SEGURIDAD_BASE_URL']).rstrip('/')}/api/Auth/login"
        payload = self._request_json(
            "POST",
            url,
            body={"userName": user, "password": pwd},
            auth_required=False,
        )
        token = _extract_siifa_token(payload)
        if not token:
            raise ValueError("Login SIIFA no devolvió token.")
        self._token = token
        return token

    def radicar_factura(self, *, id_factura: int, radicado: str, fecha_radicado: str) -> dict[str, Any]:
        token = self.login()
        url = f"{str(CONFIG['SIIFA_FACTURA_BASE_URL']).rstrip('/')}/api/FacturaRadicado"
        body = {
            "idFactura": id_factura,
            "radicado": radicado,
            "fechaRadicado": fecha_radicado,
        }
        return self._request_json(
            "POST",
            url,
            body=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth_required: bool = True,
    ) -> dict[str, Any]:
        max_attempts = max(1, int(CONFIG.get("SIIFA_RETRY_MAX_ATTEMPTS", 8)))
        base_delay = float(CONFIG.get("SIIFA_RETRY_BASE_DELAY_SECONDS", 2.0))
        timeout = float(CONFIG.get("SIIFA_HTTP_TIMEOUT_SECONDS", 90))
        hdrs = {"Accept": "text/plain", "Content-Type": "application/json", **(headers or {})}
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                data_bytes = json.dumps(body).encode("utf-8") if body is not None else None
                req = urllib.request.Request(url, data=data_bytes, headers=hdrs, method=method)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("Respuesta SIIFA no es JSON objeto.")
                return parsed
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 401 and auth_required and attempt < max_attempts:
                    self.login(force=True)
                    hdrs["Authorization"] = f"Bearer {self._token}"
                    time.sleep(base_delay * (2 ** (attempt - 1)))
                    continue
                attempts_for_error = max(max_attempts, 8) if exc.code in _RETRIABLE_HTTP else max_attempts
                if exc.code in _RETRIABLE_HTTP and attempt < attempts_for_error:
                    delay = min(60.0, base_delay * (2 ** (attempt - 1)))
                    logger.warning("SIIFA HTTP %s reintento %s/%s espera %.0fs", exc.code, attempt, attempts_for_error, delay)
                    time.sleep(delay)
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc
            except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
                last_exc = exc
                if attempt < max_attempts:
                    time.sleep(base_delay * (2 ** (attempt - 1)))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("Petición SIIFA falló.")


def _extract_siifa_token(payload: dict[str, Any]) -> str | None:
    for key in ("token", "accessToken", "access_token", "jwt", "bearerToken"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        return _extract_siifa_token(data)
    return None


# ── PostgreSQL ────────────────────────────────────────────────────────────────
def pg_buscar_rips_af(session: Session, numero: str, nit: str) -> RipsAfMatch | None:
    numero = str(numero or "").strip()
    nit = str(nit or "").strip()
    params = {"numero_factura": numero, "nit_emisor": nit}
    row = session.execute(text(SQL_BUSCAR_RIPS_AF), params).mappings().first()
    if not row:
        row = session.execute(text(SQL_BUSCAR_RIPS_AF_TRIM), params).mappings().first()
    if not row:
        return None
    return RipsAfMatch(
        consecutivo_rips_af=int(row["consecutivo_rips_af"]),
        consecutivo_rips=int(row["consecutivo_rips"]),
        numero_factura=str(row["numero_factura"] or ""),
        numero_identificacion=str(row["numero_identificacion"] or ""),
        radicado_siifa=int(row["radicado_siifa"]) if row["radicado_siifa"] is not None else None,
        idfactura_siifa=str(row["idfactura_siifa"]) if row["idfactura_siifa"] else None,
    )


def pg_actualizar_rips_af(
    session: Session,
    *,
    consecutivo_rips_af: int,
    id_factura_siifa: int,
    radicado_siifa: int,
    fecha_rad_siifa: datetime,
) -> int:
    valor = _valor_radicado_smallint(radicado_siifa)
    session.execute(
        text(SQL_UPDATE_RIPS_AF),
        {
            "consecutivo_rips_af": consecutivo_rips_af,
            "radicado_siifa": valor,
            "fecha_rad_siifa": fecha_rad_siifa.replace(tzinfo=None)
            if fecha_rad_siifa.tzinfo
            else fecha_rad_siifa,
            "idfactura_siifa": str(id_factura_siifa),
        },
    )
    return valor


# ── SQL Server (auditoría SIIFA_*) ────────────────────────────────────────────
def sql_iniciar_ejecucion(session: Session, *, tipo: str, workers: int, usuario: str | None) -> int:
    row = session.execute(
        text(
            """
            INSERT INTO dbo.SIIFA_IntegracionLog
                (TipoEjecucion, FechaInicio, Estado, Workers, Usuario, Procesadas)
            OUTPUT INSERTED.IdEjecucion
            VALUES (:tipo, :inicio, 'EN_CURSO', :workers, :usuario, 0)
            """
        ),
        {
            "tipo": tipo,
            "inicio": datetime.now(timezone.utc).replace(tzinfo=None),
            "workers": workers,
            "usuario": usuario,
        },
    ).scalar_one()
    session.commit()
    return int(row)


def sql_finalizar_ejecucion(
    session: Session,
    id_ejecucion: int,
    *,
    estado: str,
    metricas: dict[str, Any],
    total_filas: int,
    duracion_ms: int,
) -> None:
    session.execute(
        text(
            """
            UPDATE dbo.SIIFA_IntegracionLog
            SET FechaFin = :fin, DuracionMs = :duracion, Estado = :estado,
                TotalRegistrosSIIFA = :total, TotalPaginas = :total,
                Procesadas = :proc, Radicadas = :rad, NoEncontradasERP = :no_enc,
                NoRadicadasERP = :no_rad, Errores = :err, Omitidas = :omit,
                DetalleJson = :detalle
            WHERE IdEjecucion = :id
            """
        ),
        {
            "id": id_ejecucion,
            "fin": datetime.now(timezone.utc).replace(tzinfo=None),
            "duracion": duracion_ms,
            "estado": estado,
            "total": total_filas,
            "proc": metricas.get("procesadas", 0),
            "rad": metricas.get("radicadas", 0),
            "no_enc": metricas.get("no_encontradas_erp", 0),
            "no_rad": metricas.get("no_radicadas_erp", 0),
            "err": metricas.get("errores", 0),
            "omit": metricas.get("omitidas", 0),
            "detalle": json.dumps(metricas, ensure_ascii=False, default=str),
        },
    )
    session.commit()


def sql_upsert_factura(
    session: Session,
    *,
    id_factura: int,
    numero: str,
    nit: str,
    id_ejecucion: int | None,
    fila: int,
    estado: str,
    observacion: str | None,
) -> None:
    session.execute(
        text(
            """
            MERGE dbo.SIIFA_Factura AS tgt
            USING (SELECT :id AS IdFacturaSIIFA) AS src
            ON tgt.IdFacturaSIIFA = src.IdFacturaSIIFA
            WHEN MATCHED THEN
                UPDATE SET NumeroFactura = :numero, NitEmisor = :nit,
                           EstadoProceso = :estado, Observacion = :obs,
                           PaginaOrigen = :fila, IdEjecucion = :ejec, FechaConsulta = GETDATE()
            WHEN NOT MATCHED THEN
                INSERT (IdFacturaSIIFA, NumeroFactura, NitEmisor, EstadoProceso,
                        Observacion, PaginaOrigen, IdEjecucion)
                VALUES (:id, :numero, :nit, :estado, :obs, :fila, :ejec);
            """
        ),
        {
            "id": id_factura,
            "numero": numero,
            "nit": nit,
            "estado": estado,
            "obs": observacion,
            "fila": fila,
            "ejec": id_ejecucion,
        },
    )


def sql_registrar_factura_erp(
    session: Session,
    *,
    id_factura_siifa: int,
    consecutivo_rips_af: int | None,
    consecutivo_rips: int | None,
    numero_factura: str,
    nit_prestador: str,
    estado_erp: int | None,
    radica_rips: str | None,
    fecha_radica: datetime | None,
    resultado: str,
    mensaje: str | None,
) -> None:
    session.execute(
        text(
            """
            MERGE dbo.SIIFA_FacturaERP AS tgt
            USING (SELECT :id AS IdFacturaSIIFA) AS src
            ON tgt.IdFacturaSIIFA = src.IdFacturaSIIFA
            WHEN MATCHED THEN
                UPDATE SET ConsecutivoRipsAf = :crips_af, ConsecutivoRips = :crips,
                           NumeroFacturaERP = :numero, NitPrestadorERP = :nit,
                           EstadoERP = :estado, RadicaRips = :radica,
                           FechaRadicaERP = :fecha, Resultado = :resultado,
                           Mensaje = :mensaje, FechaRelacion = GETDATE()
            WHEN NOT MATCHED THEN
                INSERT (IdFacturaSIIFA, ConsecutivoRipsAf, ConsecutivoRips,
                        NumeroFacturaERP, NitPrestadorERP, EstadoERP, RadicaRips,
                        FechaRadicaERP, Resultado, Mensaje)
                VALUES (:id, :crips_af, :crips, :numero, :nit, :estado,
                        :radica, :fecha, :resultado, :mensaje);
            """
        ),
        {
            "id": id_factura_siifa,
            "crips_af": consecutivo_rips_af,
            "crips": consecutivo_rips,
            "numero": numero_factura,
            "nit": nit_prestador,
            "estado": estado_erp,
            "radica": radica_rips,
            "fecha": fecha_radica,
            "resultado": resultado,
            "mensaje": mensaje,
        },
    )


def sql_registrar_radicado(
    session: Session,
    *,
    id_factura_siifa: int,
    radicado_numero: str,
    fecha_radicacion: datetime,
    estado: str,
    id_factura_radicado_siifa: int | None,
    respuesta_json: str | None,
    sincronizado_erp: bool,
    error_mensaje: str | None = None,
    http_code: int | None = None,
) -> int | None:
    row = session.execute(
        text(
            """
            INSERT INTO dbo.SIIFA_Radicado
                (IdFacturaSIIFA, IdFacturaRadicadoSIIFA, RadicadoNumero,
                 FechaRadicacionSIIFA, Estado, HttpCode, RespuestaJson,
                 ErrorMensaje, SincronizadoERP, FechaSincronizacionERP)
            OUTPUT INSERTED.IdRadicado
            VALUES (:id_fact, :id_rad, :radicado, :fecha, :estado, :http, :resp,
                    :err, :sync, CASE WHEN :sync = 1 THEN GETDATE() ELSE NULL END)
            """
        ),
        {
            "id_fact": id_factura_siifa,
            "id_rad": id_factura_radicado_siifa,
            "radicado": radicado_numero,
            "fecha": fecha_radicacion,
            "estado": estado,
            "http": http_code,
            "resp": respuesta_json,
            "err": error_mensaje,
            "sync": 1 if sincronizado_erp else 0,
        },
    ).scalar_one_or_none()
    return int(row) if row is not None else None


def sql_encolar_reintento(
    session: Session,
    *,
    id_factura_siifa: int,
    id_radicado: int | None,
    motivo: str,
    payload: dict[str, Any] | None,
    max_intentos: int,
    delay_seconds: int,
) -> None:
    proximo = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=delay_seconds)
    session.execute(
        text(
            """
            INSERT INTO dbo.SIIFA_Reintento
                (IdFacturaSIIFA, IdRadicado, Motivo, Estado, MaxIntentos,
                 ProximoIntento, PayloadJson)
            VALUES (:id_fact, :id_rad, :motivo, 'PENDIENTE', :max_int,
                    :proximo, :payload)
            """
        ),
        {
            "id_fact": id_factura_siifa,
            "id_rad": id_radicado,
            "motivo": motivo,
            "max_int": max_intentos,
            "proximo": proximo,
            "payload": json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
        },
    )


def sql_factura_ya_clasificada(session: Session, id_factura_siifa: int) -> str | None:
    row = session.execute(
        text("SELECT EstadoProceso FROM dbo.SIIFA_Factura WHERE IdFacturaSIIFA = :id"),
        {"id": id_factura_siifa},
    ).mappings().first()
    if not row:
        return None
    estado = str(row.get("EstadoProceso") or "").strip()
    if estado in _ESTADOS_CLASIFICADOS_FINALES:
        return estado
    return None


def sql_registrar_traza(
    session: Session,
    *,
    id_ejecucion: int | None,
    id_factura_siifa: int,
    numero_factura: str,
    nit_emisor: str,
    paso: str,
    resultado: str,
    mensaje: str | None,
    detalle: dict[str, Any] | None = None,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO dbo.SIIFA_FacturaTraza
                (IdEjecucion, IdFacturaSIIFA, NumeroFactura, NitEmisor,
                 Paso, Resultado, Mensaje, DetalleJson)
            VALUES (:ejec, :id, :numero, :nit, :paso, :res, :msg, :det)
            """
        ),
        {
            "ejec": id_ejecucion,
            "id": id_factura_siifa,
            "numero": numero_factura,
            "nit": nit_emisor,
            "paso": paso,
            "res": resultado,
            "msg": mensaje,
            "det": json.dumps(detalle, ensure_ascii=False, default=str) if detalle else None,
        },
    )


def sql_obtener_checkpoint(session: Session, proceso: str) -> LoteCheckpoint:
    row = session.execute(
        text(
            """
            SELECT UltimaPaginaProcesada, LoteCompletado
            FROM dbo.SIIFA_LoteCheckpoint WHERE Proceso = :proceso
            """
        ),
        {"proceso": proceso},
    ).mappings().first()
    if not row:
        return LoteCheckpoint()
    return LoteCheckpoint(
        ultima_pagina=int(row["UltimaPaginaProcesada"] or 0),
        lote_completado=bool(row["LoteCompletado"]),
    )


def sql_guardar_checkpoint(
    session: Session,
    *,
    proceso: str,
    ultima_fila: int,
    total_filas: int,
    lote_completado: bool,
    id_ejecucion: int | None,
) -> None:
    session.execute(
        text(
            """
            MERGE dbo.SIIFA_LoteCheckpoint AS tgt
            USING (SELECT :proceso AS Proceso) AS src
            ON tgt.Proceso = src.Proceso
            WHEN MATCHED THEN
                UPDATE SET UltimaPaginaProcesada = :ultima,
                           TotalPaginasSiifa = :total, TotalRegistrosSiifa = :total,
                           LoteCompletado = :completado, FechaActualizacion = GETDATE(),
                           IdEjecucionUltima = :ejec
            WHEN NOT MATCHED THEN
                INSERT (Proceso, UltimaPaginaProcesada, TotalPaginasSiifa,
                        TotalRegistrosSiifa, LoteCompletado, IdEjecucionUltima)
                VALUES (:proceso, :ultima, :total, :total, :completado, :ejec);
            """
        ),
        {
            "proceso": proceso,
            "ultima": ultima_fila,
            "total": total_filas,
            "completado": 1 if lote_completado else 0,
            "ejec": id_ejecucion,
        },
    )


# ── Preflight ─────────────────────────────────────────────────────────────────
def verificar_conectividad(pg_engine: Engine, sql_engine: Engine) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    ok = True

    try:
        with pg_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgresql"] = {"ok": True, "mensaje": "Conexión OK"}
    except Exception as exc:
        checks["postgresql"] = {"ok": False, "mensaje": f"PostgreSQL: {exc}"}
        ok = False

    try:
        with sql_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["sqlserver"] = {"ok": True, "mensaje": "Conexión OK"}
    except Exception as exc:
        checks["sqlserver"] = {"ok": False, "mensaje": f"SQL Server: {exc}"}
        ok = False

    try:
        client = SiifaClient()
        client.login()
        checks["siifa"] = {"ok": True, "mensaje": "Login OK"}
    except Exception as exc:
        checks["siifa"] = {"ok": False, "mensaje": f"SIIFA: {exc}"}
        ok = False

    return {"ok": ok, "checks": checks}


def _registrar_error_sql(
    sql: Session,
    *,
    row: FacturaCsv,
    id_ejecucion: int | None,
    fila: int,
    match: RipsAfMatch | None,
    mensaje: str,
    paso: str,
) -> None:
    sql_upsert_factura(
        sql,
        id_factura=row.id_factura_siifa,
        numero=row.numero_factura,
        nit=row.nit_emisor,
        id_ejecucion=id_ejecucion,
        fila=fila,
        estado=_estado_sql(EstadoProceso.ERROR),
        observacion=mensaje,
    )
    if match:
        sql_registrar_factura_erp(
            sql,
            id_factura_siifa=row.id_factura_siifa,
            consecutivo_rips_af=match.consecutivo_rips_af,
            consecutivo_rips=match.consecutivo_rips,
            numero_factura=row.numero_factura,
            nit_prestador=row.nit_emisor,
            estado_erp=None,
            radica_rips=None,
            fecha_radica=None,
            resultado="ERROR",
            mensaje=mensaje,
        )
    sql_registrar_traza(
        sql,
        id_ejecucion=id_ejecucion,
        id_factura_siifa=row.id_factura_siifa,
        numero_factura=row.numero_factura,
        nit_emisor=row.nit_emisor,
        paso=paso,
        resultado=_traza_sql(ResultadoTraza.ERROR),
        mensaje=mensaje,
    )


# ── Procesamiento por fila ────────────────────────────────────────────────────
def procesar_fila(
    row: FacturaCsv,
    fila: int,
    id_ejecucion: int | None,
    pg: Session,
    sql: Session,
    siifa: SiifaClient,
    *,
    dry_run: bool,
) -> ResultadoFila:
    try:
        if CONFIG.get("REUTILIZAR_CLASIFICADAS") and id_ejecucion is not None and not dry_run:
            previo = sql_factura_ya_clasificada(sql, row.id_factura_siifa)
            if previo:
                sql_upsert_factura(
                    sql,
                    id_factura=row.id_factura_siifa,
                    numero=row.numero_factura,
                    nit=row.nit_emisor,
                    id_ejecucion=id_ejecucion,
                    fila=fila,
                    estado=_estado_sql(EstadoProceso.OMITIDA),
                    observacion=f"Ya clasificada ({previo})",
                )
                sql_registrar_traza(
                    sql,
                    id_ejecucion=id_ejecucion,
                    id_factura_siifa=row.id_factura_siifa,
                    numero_factura=row.numero_factura,
                    nit_emisor=row.nit_emisor,
                    paso="VALIDACION_PREVIA",
                    resultado=_traza_sql(ResultadoTraza.OMITIDA),
                    mensaje=f"Ya clasificada en SIIFA_Factura ({previo})",
                )
                sql.commit()
                return ResultadoFila(
                    EstadoProceso.OMITIDA,
                    f"Ya clasificada ({previo})",
                )

        _log_flush("  → Buscando en rips_af…")
        match = pg_buscar_rips_af(pg, row.numero_factura, row.nit_emisor)
        if not match:
            if not dry_run and id_ejecucion is not None:
                sql_upsert_factura(
                    sql,
                    id_factura=row.id_factura_siifa,
                    numero=row.numero_factura,
                    nit=row.nit_emisor,
                    id_ejecucion=id_ejecucion,
                    fila=fila,
                    estado=_estado_sql(EstadoProceso.NO_ENCONTRADA_ERP),
                    observacion="NO ENCONTRADA en ERP (numero_factura + nit_emisor)",
                )
                sql_registrar_traza(
                    sql,
                    id_ejecucion=id_ejecucion,
                    id_factura_siifa=row.id_factura_siifa,
                    numero_factura=row.numero_factura,
                    nit_emisor=row.nit_emisor,
                    paso="BUSCAR_RIPS_AF",
                    resultado=_traza_sql(ResultadoTraza.NO_ENCONTRADA),
                    mensaje=(
                        f"Sin coincidencia: numero_factura={row.numero_factura!r}, "
                        f"nit_emisor={row.nit_emisor!r}"
                    ),
                )
                sql.commit()
            return ResultadoFila(
                EstadoProceso.NO_ENCONTRADA_ERP,
                "NO ENCONTRADA en ERP (numero_factura + nit_emisor)",
            )

        if match.idfactura_siifa and str(match.idfactura_siifa).strip():
            if not dry_run and id_ejecucion is not None:
                sql_upsert_factura(
                    sql,
                    id_factura=row.id_factura_siifa,
                    numero=row.numero_factura,
                    nit=row.nit_emisor,
                    id_ejecucion=id_ejecucion,
                    fila=fila,
                    estado=_estado_sql(EstadoProceso.OMITIDA),
                    observacion="Ya tiene idfactura_siifa en rips_af",
                )
                sql_registrar_traza(
                    sql,
                    id_ejecucion=id_ejecucion,
                    id_factura_siifa=row.id_factura_siifa,
                    numero_factura=row.numero_factura,
                    nit_emisor=row.nit_emisor,
                    paso="VALIDACION_PREVIA",
                    resultado=_traza_sql(ResultadoTraza.OMITIDA),
                    mensaje="Factura ya sincronizada en ERP",
                )
                sql.commit()
            return ResultadoFila(
                EstadoProceso.OMITIDA,
                "Ya tiene idfactura_siifa en rips_af",
            )

        _log_flush("  → Validando rips_resumen estado=%s…", ESTADO_RADICADO_ERP)
        resumen = pg_buscar_rips_resumen(pg, match.consecutivo_rips)
        if not resumen:
            msg = f"rips_resumen no encontrado (consecutivo_rips={match.consecutivo_rips})"
            if not dry_run and id_ejecucion is not None:
                _registrar_error_sql(sql, row=row, id_ejecucion=id_ejecucion, fila=fila, match=match, mensaje=msg, paso="BUSCAR_RIPS_RESUMEN")
                sql.commit()
            return ResultadoFila(EstadoProceso.ERROR, msg)

        if resumen.estado != ESTADO_RADICADO_ERP:
            obs = f"Factura en ERP pero estado={resumen.estado} (requiere {ESTADO_RADICADO_ERP})"
            if not dry_run and id_ejecucion is not None:
                sql_upsert_factura(
                    sql,
                    id_factura=row.id_factura_siifa,
                    numero=row.numero_factura,
                    nit=row.nit_emisor,
                    id_ejecucion=id_ejecucion,
                    fila=fila,
                    estado=_estado_sql(EstadoProceso.NO_RADICADA_ERP),
                    observacion=obs,
                )
                sql_registrar_factura_erp(
                    sql,
                    id_factura_siifa=row.id_factura_siifa,
                    consecutivo_rips_af=match.consecutivo_rips_af,
                    consecutivo_rips=match.consecutivo_rips,
                    numero_factura=row.numero_factura,
                    nit_prestador=row.nit_emisor,
                    estado_erp=resumen.estado,
                    radica_rips=resumen.radica_rips or None,
                    fecha_radica=resumen.fecha_radica,
                    resultado="NO_RADICADA_ERP",
                    mensaje=obs,
                )
                sql_registrar_traza(
                    sql,
                    id_ejecucion=id_ejecucion,
                    id_factura_siifa=row.id_factura_siifa,
                    numero_factura=row.numero_factura,
                    nit_emisor=row.nit_emisor,
                    paso="VALIDAR_ESTADO_ERP",
                    resultado=_traza_sql(ResultadoTraza.NO_RADICADA_ERP),
                    mensaje=obs,
                    detalle={"estado": resumen.estado},
                )
                sql.commit()
            return ResultadoFila(EstadoProceso.NO_RADICADA_ERP, obs)

        if not resumen.radica_rips:
            msg = "radica_rips vacío en rips_resumen con estado=5"
            if not dry_run and id_ejecucion is not None:
                _registrar_error_sql(sql, row=row, id_ejecucion=id_ejecucion, fila=fila, match=match, mensaje=msg, paso="VALIDAR_RADICA_RIPS")
                sql.commit()
            return ResultadoFila(EstadoProceso.ERROR, msg)

        if resumen.fecha_radica is None:
            msg = f"fecha_radica vacía en rips_resumen (consecutivo_rips={match.consecutivo_rips})"
            if not dry_run and id_ejecucion is not None:
                _registrar_error_sql(sql, row=row, id_ejecucion=id_ejecucion, fila=fila, match=match, mensaje=msg, paso="VALIDAR_FECHA_RADICA")
                sql.commit()
            return ResultadoFila(EstadoProceso.ERROR, msg)

        if dry_run:
            pg.rollback()
            return ResultadoFila(
                EstadoProceso.RADICADA,
                (
                    f"Cumple condiciones para radicación "
                    f"(radica_rips={resumen.radica_rips}, fecha={resumen.fecha_radica})"
                ),
            )

        fecha_iso = _fecha_erp_a_iso_utc(resumen.fecha_radica)
        _log_flush("  → Radicando en SIIFA radicado=%s fecha=%s…", resumen.radica_rips, fecha_iso)
        try:
            resp_siifa = siifa.radicar_factura(
                id_factura=row.id_factura_siifa,
                radicado=resumen.radica_rips,
                fecha_radicado=fecha_iso,
            )
        except Exception as exc:
            if id_ejecucion is not None:
                sql_upsert_factura(
                    sql,
                    id_factura=row.id_factura_siifa,
                    numero=row.numero_factura,
                    nit=row.nit_emisor,
                    id_ejecucion=id_ejecucion,
                    fila=fila,
                    estado=_estado_sql(EstadoProceso.ERROR),
                    observacion=str(exc),
                )
                id_rad = sql_registrar_radicado(
                    sql,
                    id_factura_siifa=row.id_factura_siifa,
                    radicado_numero=resumen.radica_rips,
                    fecha_radicacion=resumen.fecha_radica,
                    estado="ERROR",
                    id_factura_radicado_siifa=None,
                    respuesta_json=None,
                    sincronizado_erp=False,
                    error_mensaje=str(exc),
                )
                sql_encolar_reintento(
                    sql,
                    id_factura_siifa=row.id_factura_siifa,
                    id_radicado=id_rad,
                    motivo="RADICAR_SIIFA",
                    payload={
                        "idFactura": row.id_factura_siifa,
                        "radicado": resumen.radica_rips,
                        "fechaRadicado": fecha_iso,
                    },
                    max_intentos=int(CONFIG.get("SIIFA_RETRY_MAX_ATTEMPTS", 8)),
                    delay_seconds=int(float(CONFIG.get("SIIFA_RETRY_BASE_DELAY_SECONDS", 2)) * 2),
                )
                sql_registrar_traza(
                    sql,
                    id_ejecucion=id_ejecucion,
                    id_factura_siifa=row.id_factura_siifa,
                    numero_factura=row.numero_factura,
                    nit_emisor=row.nit_emisor,
                    paso="RADICAR_SIIFA",
                    resultado=_traza_sql(ResultadoTraza.ERROR),
                    mensaje=str(exc),
                )
                sql.commit()
            pg.rollback()
            return ResultadoFila(EstadoProceso.ERROR, str(exc))

        id_radicado_siifa = int(
            resp_siifa.get("idFacturaRadicado") or resp_siifa.get("IdFacturaRadicado") or 0
        )
        if not id_radicado_siifa:
            msg = f"SIIFA no devolvió idFacturaRadicado: {resp_siifa}"
            if id_ejecucion is not None:
                _registrar_error_sql(sql, row=row, id_ejecucion=id_ejecucion, fila=fila, match=match, mensaje=msg, paso="RADICAR_SIIFA")
                sql.commit()
            pg.rollback()
            return ResultadoFila(EstadoProceso.ERROR, msg)

        fecha_sync = datetime.now(timezone.utc).replace(tzinfo=None)
        _log_flush("  → Actualizando rips_af consecutivo=%s…", match.consecutivo_rips_af)
        try:
            radicado_guardado = pg_actualizar_rips_af(
                pg,
                consecutivo_rips_af=match.consecutivo_rips_af,
                id_factura_siifa=row.id_factura_siifa,
                radicado_siifa=id_radicado_siifa,
                fecha_rad_siifa=fecha_sync,
            )
            pg.commit()
        except Exception as exc:
            pg.rollback()
            if id_ejecucion is not None:
                sql_upsert_factura(
                    sql,
                    id_factura=row.id_factura_siifa,
                    numero=row.numero_factura,
                    nit=row.nit_emisor,
                    id_ejecucion=id_ejecucion,
                    fila=fila,
                    estado=_estado_sql(EstadoProceso.ERROR),
                    observacion=f"SIIFA OK pero falló UPDATE ERP: {exc}",
                )
                sql_registrar_radicado(
                    sql,
                    id_factura_siifa=row.id_factura_siifa,
                    radicado_numero=resumen.radica_rips,
                    fecha_radicacion=resumen.fecha_radica,
                    estado="ERROR_ERP",
                    id_factura_radicado_siifa=id_radicado_siifa,
                    respuesta_json=json.dumps(resp_siifa, ensure_ascii=False, default=str),
                    sincronizado_erp=False,
                    error_mensaje=str(exc),
                )
                sql_encolar_reintento(
                    sql,
                    id_factura_siifa=row.id_factura_siifa,
                    id_radicado=None,
                    motivo="UPDATE_ERP",
                    payload={
                        "consecutivo_rips_af": match.consecutivo_rips_af,
                        "id_factura_siifa": row.id_factura_siifa,
                        "id_factura_radicado_siifa": id_radicado_siifa,
                    },
                    max_intentos=int(CONFIG.get("SIIFA_RETRY_MAX_ATTEMPTS", 8)),
                    delay_seconds=int(float(CONFIG.get("SIIFA_RETRY_BASE_DELAY_SECONDS", 2)) * 2),
                )
                sql_registrar_traza(
                    sql,
                    id_ejecucion=id_ejecucion,
                    id_factura_siifa=row.id_factura_siifa,
                    numero_factura=row.numero_factura,
                    nit_emisor=row.nit_emisor,
                    paso="UPDATE_ERP",
                    resultado=_traza_sql(ResultadoTraza.ERROR),
                    mensaje=str(exc),
                )
                sql.commit()
            return ResultadoFila(
                EstadoProceso.ERROR,
                f"SIIFA OK pero falló UPDATE ERP: {exc}",
            )

        _log_flush("  → Registrando auditoría SQL Server…")
        sql_upsert_factura(
            sql,
            id_factura=row.id_factura_siifa,
            numero=row.numero_factura,
            nit=row.nit_emisor,
            id_ejecucion=id_ejecucion,
            fila=fila,
            estado=_estado_sql(EstadoProceso.RADICADA),
            observacion="Radicada exitosamente",
        )
        sql_registrar_factura_erp(
            sql,
            id_factura_siifa=row.id_factura_siifa,
            consecutivo_rips_af=match.consecutivo_rips_af,
            consecutivo_rips=match.consecutivo_rips,
            numero_factura=row.numero_factura,
            nit_prestador=row.nit_emisor,
            estado_erp=resumen.estado,
            radica_rips=resumen.radica_rips,
            fecha_radica=resumen.fecha_radica,
            resultado="RADICADA",
            mensaje="OK radicación SIIFA",
        )
        sql_registrar_radicado(
            sql,
            id_factura_siifa=row.id_factura_siifa,
            radicado_numero=resumen.radica_rips,
            fecha_radicacion=resumen.fecha_radica,
            estado="EXITOSO",
            id_factura_radicado_siifa=id_radicado_siifa,
            respuesta_json=json.dumps(resp_siifa, ensure_ascii=False, default=str),
            sincronizado_erp=True,
        )
        sql_registrar_traza(
            sql,
            id_ejecucion=id_ejecucion,
            id_factura_siifa=row.id_factura_siifa,
            numero_factura=row.numero_factura,
            nit_emisor=row.nit_emisor,
            paso="COMPLETADO",
            resultado=_traza_sql(ResultadoTraza.OK),
            mensaje="Radicación SIIFA + actualización rips_af",
            detalle={
                "idFacturaRadicado": id_radicado_siifa,
                "radicado_erp": resumen.radica_rips,
                "fecha_erp": str(resumen.fecha_radica),
                "radicado_siifa_erp": radicado_guardado,
            },
        )
        sql.commit()
        return ResultadoFila(EstadoProceso.RADICADA, "Radicada exitosamente")

    except Exception:
        pg.rollback()
        sql.rollback()
        raise


def _actualizar_metricas(metricas: Metricas, estado: EstadoProceso) -> None:
    metricas.procesadas += 1
    if estado == EstadoProceso.RADICADA:
        metricas.radicadas += 1
    elif estado == EstadoProceso.NO_ENCONTRADA_ERP:
        metricas.no_encontradas_erp += 1
    elif estado == EstadoProceso.OMITIDA:
        metricas.omitidas += 1
    elif estado == EstadoProceso.NO_RADICADA_ERP:
        metricas.no_radicadas_erp += 1
    else:
        metricas.errores += 1


# ── Orquestación ──────────────────────────────────────────────────────────────
def ejecutar_lote(
    csv_path: Path,
    *,
    usuario: str,
    max_filas: int | None,
    reiniciar: bool,
    dry_run: bool,
) -> dict[str, Any]:
    inicio = time.perf_counter()
    filas_por_lote = max_filas if max_filas and max_filas > 0 else int(CONFIG["FILAS_POR_LOTE"])
    proceso = str(CONFIG["PROCESO_CHECKPOINT"])

    csv_data = cargar_csv_sin_radicar(csv_path)
    todas = csv_data.items
    total_filas = len(todas)
    csv_modificado = False
    logger.info("CSV cargado: %s filas válidas (modo secuencial, sin hilos)", total_filas)

    pg_engine = _crear_engine_postgres()
    sql_engine = _crear_engine_sqlserver(str(CONFIG["SQLSERVER_URL"]), _DB_POOL_SIZE)
    pg_factory = sessionmaker(bind=pg_engine)
    sql_factory = sessionmaker(bind=sql_engine)

    sql_main = sql_factory()
    id_ejecucion: int | None = None
    if not dry_run:
        id_ejecucion = sql_iniciar_ejecucion(
            sql_main,
            tipo=str(CONFIG["TIPO_EJECUCION"]),
            workers=1,
            usuario=usuario,
        )
        logger.info("Ejecución SQL Server iniciada IdEjecucion=%s", id_ejecucion)

    metricas = Metricas()
    fila_inicio = 1
    fila_fin = 0
    proxima_fila = 1
    lote_completado = False
    filas_procesadas = 0
    estado_final = "OK"

    try:
        checkpoint = sql_obtener_checkpoint(sql_main, proceso)
        if reiniciar and not dry_run:
            sql_guardar_checkpoint(
                sql_main,
                proceso=proceso,
                ultima_fila=0,
                total_filas=total_filas,
                lote_completado=False,
                id_ejecucion=id_ejecucion,
            )
            sql_main.commit()
            checkpoint = LoteCheckpoint()
        elif checkpoint.lote_completado and not dry_run:
            sql_guardar_checkpoint(
                sql_main,
                proceso=proceso,
                ultima_fila=0,
                total_filas=total_filas,
                lote_completado=False,
                id_ejecucion=id_ejecucion,
            )
            sql_main.commit()
            checkpoint = LoteCheckpoint()

        fila_inicio = 1 if dry_run else checkpoint.proxima_fila
        logger.info(
            "Checkpoint proceso=%s proxima_fila=%s total_csv=%s lote_completado=%s",
            proceso,
            fila_inicio,
            total_filas,
            checkpoint.lote_completado,
        )

        if fila_inicio > total_filas:
            lote_completado = True
            proxima_fila = 1
            if not dry_run:
                sql_guardar_checkpoint(
                    sql_main,
                    proceso=proceso,
                    ultima_fila=total_filas,
                    total_filas=total_filas,
                    lote_completado=True,
                    id_ejecucion=id_ejecucion,
                )
                sql_main.commit()
        else:
            fila_fin = min(fila_inicio + filas_por_lote - 1, total_filas)
            lote = todas[fila_inicio - 1 : fila_fin]

            logger.info(
                "Procesando filas %s-%s de %s (secuencial dry_run=%s)",
                fila_inicio,
                fila_fin,
                total_filas,
                dry_run,
            )
            sys.stderr.flush()

            _log_flush("Abriendo sesiones PostgreSQL + SQL Server + login SIIFA…")
            pg_sess = pg_factory()
            sql_sess = sql_factory()
            siifa = SiifaClient()
            if not dry_run:
                siifa.login()
            try:
                for idx, item in enumerate(lote):
                    fila_num = fila_inicio + idx
                    t0 = time.perf_counter()
                    _log_flush(
                        "Fila %s idFactura=%s factura=%s nit=%s",
                        fila_num,
                        item.id_factura_siifa,
                        item.numero_factura,
                        item.nit_emisor,
                    )
                    try:
                        resultado = procesar_fila(
                            item,
                            fila_num,
                            id_ejecucion,
                            pg_sess,
                            sql_sess,
                            siifa,
                            dry_run=dry_run,
                        )
                    except Exception as exc:
                        logger.exception("Error idFactura=%s", item.id_factura_siifa)
                        resultado = ResultadoFila(
                            EstadoProceso.ERROR,
                            f"Error inesperado: {exc}",
                        )
                        metricas.advertencias.append(f"idFactura={item.id_factura_siifa}: {exc}")

                    _aplicar_observacion_csv(
                        csv_data,
                        fila_idx=csv_data.item_a_fila_idx[fila_num - 1],
                        resultado=resultado,
                        dry_run=dry_run,
                    )
                    csv_modificado = True

                    _actualizar_metricas(metricas, resultado.estado)
                    logger.info(
                        "Fila %s → %s (%.1fs) [%s/%s]",
                        fila_num,
                        _estado_sql(resultado.estado),
                        time.perf_counter() - t0,
                        metricas.procesadas,
                        len(lote),
                    )
                    sys.stderr.flush()
            finally:
                pg_sess.close()
                sql_sess.close()

            filas_procesadas = len(lote)
            lote_completado = fila_fin >= total_filas
            proxima_fila = 1 if lote_completado else fila_fin + 1

            if not dry_run:
                sql_guardar_checkpoint(
                    sql_main,
                    proceso=proceso,
                    ultima_fila=fila_fin,
                    total_filas=total_filas,
                    lote_completado=lote_completado,
                    id_ejecucion=id_ejecucion,
                )
                sql_main.commit()

            if csv_modificado:
                con_obs = guardar_csv_sin_radicar(csv_path, csv_data)
                logger.info(
                    "CSV actualizado: %s (%s filas con observación, %s filas totales)",
                    csv_path,
                    con_obs,
                    len(csv_data.filas),
                )
                csv_modificado = False

        if metricas.errores > 0 and metricas.radicadas > 0:
            estado_final = "PARCIAL"
        elif metricas.errores > 0 and metricas.radicadas == 0 and filas_procesadas > 0:
            estado_final = "ERROR"
        if dry_run:
            estado_final = "OK"

    except Exception:
        estado_final = "ERROR"
        raise
    finally:
        if csv_modificado:
            try:
                con_obs = guardar_csv_sin_radicar(csv_path, csv_data)
                logger.info(
                    "CSV actualizado (cierre): %s (%s filas con observación)",
                    csv_path,
                    con_obs,
                )
            except Exception:
                logger.exception("No se pudo guardar observaciones en CSV: %s", csv_path)
        duracion_ms = int((time.perf_counter() - inicio) * 1000)
        detalle = metricas.to_dict()
        detalle.update(
            {
                "origen": "csv",
                "archivo_csv": str(csv_path),
                "fila_inicio": fila_inicio,
                "fila_fin": fila_fin,
                "proxima_fila": proxima_fila,
                "lote_completado": lote_completado,
                "filas_por_lote": filas_por_lote,
                "dry_run": dry_run,
                "modo": "secuencial",
            }
        )
        try:
            if not dry_run and id_ejecucion is not None:
                sql_finalizar_ejecucion(
                    sql_main,
                    id_ejecucion,
                    estado=estado_final,
                    metricas=detalle,
                    total_filas=total_filas,
                    duracion_ms=duracion_ms,
                )
        finally:
            sql_main.close()
            pg_engine.dispose()
            sql_engine.dispose()

    return {
        "id_ejecucion": id_ejecucion,
        "estado": estado_final,
        "duracion_ms": duracion_ms,
        "archivo_csv": str(csv_path),
        "fila_inicio": fila_inicio,
        "fila_fin": fila_fin,
        "proxima_fila": proxima_fila,
        "lote_completado": lote_completado,
        "requiere_siguiente_lote": not lote_completado and not dry_run,
        "filas_procesadas": filas_procesadas,
        "filas_por_lote": filas_por_lote,
        "total_filas_csv": total_filas,
        "dry_run": dry_run,
        "modo": "secuencial",
        **metricas.to_dict(),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Radica en SIIFA desde siifa_facturas_sin_radicar.csv (standalone, sin proyecto)",
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--filas", type=int, default=None)
    parser.add_argument("--reiniciar", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Simula sin escribir BD")
    parser.add_argument("--verificar", action="store_true", help="Solo prueba conexiones")
    parser.add_argument("--sin-verificar", action="store_true")
    parser.add_argument("--usuario", default="cli_standalone")
    parser.add_argument(
        "--hasta-completar",
        action="store_true",
        help="Repite lotes hasta procesar todo el CSV (reanuda checkpoint)",
    )
    parser.add_argument(
        "--listar-conectores",
        action="store_true",
        help="Muestra drivers ODBC y disponibilidad pymssql",
    )
    parser.add_argument("--version", action="store_true", help="Muestra versión del script standalone.")
    args = parser.parse_args()

    if args.version:
        print(f"{Path(__file__).name} {SCRIPT_VERSION}")
        return 0

    _require_deps()
    _apply_env_config()

    if args.listar_conectores:
        print(json.dumps(listar_conectores_sqlserver(), ensure_ascii=False, indent=2, default=str))
        return 0

    if not str(CONFIG.get("POSTGRES_URL", "")).strip():
        print("Configure POSTGRES_URL en CONFIG o variable de entorno.", file=sys.stderr)
        return 2
    if not str(CONFIG.get("SQLSERVER_URL", "")).strip():
        print("Configure SQLSERVER_URL en CONFIG o variable de entorno.", file=sys.stderr)
        return 2

    csv_path = _resolve_csv(args.csv)
    if not args.verificar and not csv_path.is_file():
        print(json.dumps({"estado": "ERROR", "error": f"CSV no encontrado: {csv_path}"}), file=sys.stderr)
        return 2

    pg_engine = _crear_engine_postgres()
    try:
        sql_engine = _crear_engine_sqlserver(str(CONFIG["SQLSERVER_URL"]), _DB_POOL_SIZE)
    except Exception as exc:
        print(json.dumps({"estado": "ERROR", "error": f"SQL Server: {exc}"}), file=sys.stderr)
        return 2

    try:
        if args.verificar or not args.sin_verificar:
            preflight = verificar_conectividad(pg_engine, sql_engine)
            preflight["script_version"] = SCRIPT_VERSION
            if args.verificar:
                print(json.dumps(preflight, ensure_ascii=False, indent=2, default=str))
            elif not preflight.get("ok"):
                print(json.dumps(preflight, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
            else:
                logger.info("Preflight OK: PostgreSQL + SQL Server + SIIFA")
            if args.verificar or not preflight.get("ok"):
                return 0 if args.verificar and preflight.get("ok") else 2

        resultado_final: dict[str, Any] | None = None
        lote_num = 0
        while True:
            lote_num += 1
            if args.hasta_completar and lote_num > 1:
                _log_flush("=== Lote %s (continúa desde checkpoint) ===", lote_num)

            resultado = ejecutar_lote(
                csv_path,
                usuario=args.usuario,
                max_filas=args.filas,
                reiniciar=args.reiniciar if lote_num == 1 else False,
                dry_run=args.dry_run,
            )
            resultado_final = resultado
            print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
            sys.stdout.flush()

            if not args.hasta_completar or not resultado.get("requiere_siguiente_lote"):
                break
            if args.reiniciar and lote_num == 1:
                pass  # solo aplica al primer lote

        resultado = resultado_final or {}
        if resultado.get("requiere_siguiente_lote"):
            print(
                f"\n>>> Continuar: python {Path(__file__).name} --csv \"{csv_path}\" "
                f"(próxima fila: {resultado.get('proxima_fila')})",
                file=sys.stderr,
            )
        return 0 if resultado.get("estado") in ("OK", "PARCIAL") else 1
    except Exception as exc:
        print(json.dumps({"estado": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        pg_engine.dispose()
        sql_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
