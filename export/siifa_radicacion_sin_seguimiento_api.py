#!/usr/bin/env python3
"""
Radicación SIIFA desde API (sin seguimiento) — script 100% autocontenido.

NO importa app.* ni módulos del proyecto ORQUESTADORDB.

Flujo:
  1. Consulta SIIFA GET /api/Factura?TieneRadicado=false (paginado, sin duplicados)
  2. Por cada factura: busca administrativo.rips_af (numero_factura + nit emisor)
  3. Valida administrativo.rips_resumen.estado = 5
  4. POST SIIFA /api/FacturaRadicado con radica_rips y fecha_radica del ERP
  5. Actualiza rips_af (radicado_siifa, fecha_rad_siifa, idfactura_siifa)
  6. Auditoría SQL Server: SIIFA_Factura, SIIFA_FacturaERP, SIIFA_Radicado, SIIFA_FacturaTraza
  7. Checkpoint en SIIFA_LoteCheckpoint (proceso RADICACION_SIIFA_API)
  8. Si ya está registrado con el mismo estado en auditoría SQL Server, solo actualiza
     FechaConsulta/IdEjecucion (sin nueva fila en SIIFA_FacturaTraza, SIIFA_FacturaERP
     ni SIIFA_Radicado)
  9. Log opcional CSV con estado/observación por factura

Estados en SQL Server (dbo.SIIFA_Factura) — sin duplicar traza si no hay cambio:
  - RADICADA / OMITIDA: no se vuelven a procesar aunque SIIFA las liste sin seguimiento.
  - NO_ENCONTRADA_ERP / NO_RADICADA_ERP: se reevalúan en PostgreSQL en cada corrida;
    si aparece en ERP con estado=5 se radica y pasa a RADICADA.
  - MERGE por IdFacturaSIIFA evita filas duplicadas en SQL.

Dependencias pip: psycopg2-binary, sqlalchemy, pyodbc o pymssql.

Uso (desde export/):
  python3 -u siifa_radicacion_sin_seguimiento_api.py --verificar
  python3 -u siifa_radicacion_sin_seguimiento_api.py --nit-adquiriente 901543761 --max-paginas 2 --sin-verificar
  python3 -u siifa_radicacion_sin_seguimiento_api.py --nit-adquiriente 901543761 --hasta-completar --sin-verificar

Programación Ubuntu (~20k facturas SIIFA sin seguimiento, 500/página ≈ 40 páginas):
  # Ciclo completo reanudable (recomendado en cron cada 2–3 h)
  0 */2 * * * cd /ruta/export && python3 -u siifa_radicacion_sin_seguimiento_api.py \\
      --nit-adquiriente 901543761 --hasta-completar --sin-verificar >> /var/log/siifa_radicacion.log 2>&1

Tiempos orientativos (20k facturas, red estable, pymssql en Ubuntu):
  - 1ª corrida (mayoría sin clasificar en SQL): 45–90 min
  - Corridas siguientes (>85 % ya RADICADA/OMITIDA): 8–15 min ciclo completo
  - Solo consulta ERP (sin radicaciones nuevas): 5–10 min
  Cada radicación SIIFA exitosa suma ~1.5–3 s.
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
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

# ── Rutas (script y CSV en la misma carpeta export/) ───────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_VERSION = "standalone-api-4.2"
DEFAULT_LOG_CSV = SCRIPT_DIR / "siifa_radicacion_api_traza.csv"
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
    "PROCESO_CHECKPOINT": "RADICACION_SIIFA_API",
    "TIPO_EJECUCION": "SIIFA_API_BATCH",
    "SIIFA_NIT_ADQUIRIENTE": "901543761",
    "SIIFA_REGISTROS_POR_PAGINA": 500,
    "SIIFA_PAUSA_ENTRE_PAGINAS": 0.15,
    "SIIFA_MAX_PAGINAS_POR_LOTE": 20,
    "REUTILIZAR_CLASIFICADAS": True,
    # Optimización volumen (~20k facturas): commit y prefetch por página SIIFA
    "COMMIT_POR_PAGINA": True,
    "PG_PREFETCH_ERP": True,
    "PG_BATCH_CHUNK": 250,
    "SQL_BATCH_CHUNK": 400,
    "LOG_CADA_N_FILAS": 25,
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

_LOG_CSV_COLS = (
    "pagina_siifa",
    "id_factura_siifa",
    "numero_factura",
    "nit_emisor",
    "estado_proceso",
    "observacion",
    "fecha_proceso",
)

_RETRIABLE_HTTP = frozenset({429, 500, 502, 503, 504})
# Solo estos estados no se reevalúan (SIIFA puede seguir listándolas sin seguimiento).
_ESTADOS_SIN_REEVALUACION = frozenset({"RADICADA", "OMITIDA"})
# Estados que se vuelven a consultar en PostgreSQL en cada corrida.
_ESTADOS_REEVALUAR_ERP = frozenset({"NO_ENCONTRADA_ERP", "NO_RADICADA_ERP", "ERROR"})

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

# Una sola ida a PostgreSQL: rips_af + rips_resumen (evita 2 round-trips por factura)
SQL_BUSCAR_ERP_JOIN = """
SELECT af.consecutivo_rips_af, af.consecutivo_rips, af.numero_factura, af.numero_identificacion,
       af.radicado_siifa, af.idfactura_siifa,
       r.estado, r.radica_rips, r.fecha_radica
FROM administrativo.rips_af af
LEFT JOIN administrativo.rips_resumen r ON r.consecutivo_rips = af.consecutivo_rips
WHERE af.numero_factura = :numero_factura
  AND af.numero_identificacion = :nit_emisor
LIMIT 1
"""

SQL_BUSCAR_ERP_JOIN_TRIM = """
SELECT af.consecutivo_rips_af, af.consecutivo_rips, af.numero_factura, af.numero_identificacion,
       af.radicado_siifa, af.idfactura_siifa,
       r.estado, r.radica_rips, r.fecha_radica
FROM administrativo.rips_af af
LEFT JOIN administrativo.rips_resumen r ON r.consecutivo_rips = af.consecutivo_rips
WHERE TRIM(af.numero_factura) = :numero_factura
  AND TRIM(af.numero_identificacion) = :nit_emisor
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
logger = logging.getLogger("siifa_radicacion_api_standalone")


def _log_flush(msg: str, *args: Any) -> None:
    logger.info(msg, *args)
    sys.stderr.flush()


def _commit_sql(sql: Session, *, defer: bool) -> None:
    if not defer:
        sql.commit()


def _commit_pg(pg: Session, *, defer: bool) -> None:
    if not defer:
        pg.commit()


def _commit_ambas(sql: Session, pg: Session, *, defer: bool) -> None:
    if not defer:
        sql.commit()
        pg.commit()


def _rollback_pg(pg: Session, *, defer: bool) -> None:
    """Con commit diferido, no hacer rollback global (rompe savepoints de la página)."""
    if not defer:
        pg.rollback()


def _erp_clave(numero: str, nit: str) -> tuple[str, str]:
    return str(numero or "").strip(), str(nit or "").strip()


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


def _estado_desde_sql(estado: str | None) -> EstadoProceso | None:
    if not estado:
        return None
    try:
        return EstadoProceso(estado.strip())
    except ValueError:
        return None


def _traza_sql(resultado: ResultadoTraza) -> str:
    return resultado.value


@dataclass(frozen=True)
class ResultadoFila:
    estado: EstadoProceso
    observacion: str
    sin_cambio: bool = False
    reevaluada: bool = False


@dataclass(frozen=True)
class FacturaItem:
    id_factura_siifa: int
    numero_factura: str
    nit_emisor: str
    pagina_siifa: int = 0


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
class ErpMatch:
    """Coincidencia ERP: rips_af y opcionalmente rips_resumen en una consulta."""

    rips_af: RipsAfMatch
    resumen: RipsResumenMatch | None = None


@dataclass
class LoteCheckpoint:
    ultima_pagina: int = 0
    lote_completado: bool = False
    total_paginas_siifa: int | None = None

    @property
    def proxima_pagina(self) -> int:
        if self.lote_completado:
            return 1
        return self.ultima_pagina + 1


@dataclass
class Metricas:
    procesadas: int = 0
    radicadas: int = 0
    no_encontradas_erp: int = 0
    no_radicadas_erp: int = 0
    omitidas: int = 0
    errores: int = 0
    reevaluadas: int = 0
    sin_cambio_estado: int = 0
    advertencias: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "procesadas": self.procesadas,
            "radicadas": self.radicadas,
            "no_encontradas_erp": self.no_encontradas_erp,
            "no_radicadas_erp": self.no_radicadas_erp,
            "omitidas": self.omitidas,
            "errores": self.errores,
            "reevaluadas": self.reevaluadas,
            "sin_cambio_estado": self.sin_cambio_estado,
            "advertencias": self.advertencias[:200],
        }


# ── Utilidades ────────────────────────────────────────────────────────────────
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


def _resolver_nit_adquiriente(nit_override: str | None = None) -> str:
    nit = (nit_override or CONFIG.get("SIIFA_NIT_ADQUIRIENTE") or "").strip()
    if not nit:
        raise ValueError(
            "Obligatorio --nit-adquiriente o SIIFA_NIT_ADQUIRIENTE en CONFIG/entorno. "
            "Sin NIT, SIIFA repite la misma página (duplicados)."
        )
    return nit


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
        "SIIFA_NIT_ADQUIRIENTE",
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


# ── SIIFA: consulta facturas sin seguimiento ──────────────────────────────────
def _siifa_party_nit(party: dict[str, Any]) -> str:
    for key in ("nitEmisor", "nitAdquiriente", "nit", "numeroIdentificacion"):
        val = party.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _siifa_first_value(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = item.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _siifa_factura_id(raw: dict[str, Any]) -> str:
    for key in ("idFactura", "IdFactura"):
        val = raw.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    emisor = raw.get("emisor") if isinstance(raw.get("emisor"), dict) else {}
    nit = _siifa_party_nit(emisor) if isinstance(emisor, dict) else ""
    numero = str(raw.get("numeroFactura") or "").strip()
    if numero and nit:
        return f"{numero}|{nit}"
    return ""


def _parse_factura_siifa(raw: dict[str, Any], pagina: int) -> FacturaItem | None:
    id_raw = _siifa_first_value(raw, "idFactura", "IdFactura")
    numero = _siifa_first_value(raw, "numeroFactura")
    emisor = raw.get("emisor") if isinstance(raw.get("emisor"), dict) else {}
    nit = _siifa_party_nit(emisor) or _siifa_first_value(raw, "nitEmisor")
    if not id_raw or not numero or not nit:
        return None
    try:
        return FacturaItem(
            id_factura_siifa=int(id_raw),
            numero_factura=numero,
            nit_emisor=nit,
            pagina_siifa=pagina,
        )
    except (TypeError, ValueError):
        return None


def _extract_siifa_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("resultado")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


class TrazaCsvWriter:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._fh: Any = None
        self._writer: csv.DictWriter | None = None
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        nuevo = not path.is_file()
        self._fh = path.open("a", encoding="utf-8-sig", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=list(_LOG_CSV_COLS), extrasaction="ignore")
        if nuevo:
            self._writer.writeheader()

    def append(self, item: FacturaItem, resultado: ResultadoFila, *, dry_run: bool) -> None:
        if self._writer is None:
            return
        obs = resultado.observacion.strip()
        if dry_run and obs and not obs.upper().startswith("DRY-RUN"):
            obs = f"DRY-RUN: {obs}"
        self._writer.writerow(
            {
                "pagina_siifa": item.pagina_siifa,
                "id_factura_siifa": item.id_factura_siifa,
                "numero_factura": item.numero_factura,
                "nit_emisor": item.nit_emisor,
                "estado_proceso": _estado_sql(resultado.estado),
                "observacion": obs[:2000],
                "fecha_proceso": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


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

    def get_facturas_sin_seguimiento(
        self,
        *,
        pagina: int,
        registros_por_pagina: int,
        nit_adquiriente: str,
    ) -> dict[str, Any]:
        token = self.login()
        url = f"{str(CONFIG['SIIFA_FACTURA_BASE_URL']).rstrip('/')}/api/Factura"
        params = {
            "numeroPagina": pagina,
            "registrosPorPagina": registros_por_pagina,
            "tieneRadicado": "false",
            "nitAdquiriente": nit_adquiriente,
        }
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        return self._request_json(
            "GET",
            full_url,
            headers={"Authorization": f"Bearer {token}"},
        )

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


def yield_paginas_sin_seguimiento(
    client: SiifaClient,
    *,
    nit_adquiriente: str,
    registros_por_pagina: int,
    desde_pagina: int,
    max_paginas: int,
    pausa_segundos: float,
    seen_inicial: set[str] | None = None,
) -> Any:
    """Generador: (pagina, facturas_nuevas, meta_parcial). pagina=None al finalizar."""
    seen: set[str] = set(seen_inicial) if seen_inicial else set()
    pagina = max(1, int(desde_pagina))
    total_paginas = 1
    total_registros: int | None = None
    duplicados_omitidos = 0
    paginas_leidas = 0
    paginacion_repetida = False
    inicio_invalido = False

    while pagina <= total_paginas:
        if max_paginas > 0 and (pagina - max(1, int(desde_pagina)) + 1) > max_paginas:
            break

        payload = client.get_facturas_sin_seguimiento(
            pagina=pagina,
            registros_por_pagina=registros_por_pagina,
            nit_adquiriente=nit_adquiriente,
        )
        total_paginas = max(1, int(payload.get("totalPaginas") or 1))
        if pagina > total_paginas:
            inicio_invalido = True
            logger.warning(
                "Página solicitada %s supera totalPaginas SIIFA (%s); no hay más datos.",
                pagina,
                total_paginas,
            )
            break
        paginas_leidas = pagina
        if total_registros is None:
            total_registros = int(payload.get("totalRegistros") or 0)

        page_rows = _extract_siifa_rows(payload)
        nuevas: list[FacturaItem] = []
        nuevas_count = 0
        for raw in page_rows:
            fid = _siifa_factura_id(raw)
            if not fid:
                parsed = _parse_factura_siifa(raw, pagina)
                if parsed:
                    fid = str(parsed.id_factura_siifa)
            if fid and fid in seen:
                duplicados_omitidos += 1
                continue
            parsed = _parse_factura_siifa(raw, pagina)
            if not parsed:
                continue
            if fid:
                seen.add(fid)
            nuevas.append(parsed)
            nuevas_count += 1

        meta = {
            "paginas_leidas": paginas_leidas,
            "total_paginas_siifa": total_paginas,
            "total_registros_siifa": total_registros or 0,
            "filas_unicas": len(seen),
            "duplicados_omitidos": duplicados_omitidos,
            "paginacion_repetida": paginacion_repetida,
            "nit_adquiriente": nit_adquiriente,
            "desde_pagina": max(1, int(desde_pagina)),
        }
        _log_flush(
            "SIIFA página %s/%s — recibidas %s, nuevas %s, únicas acumuladas %s",
            pagina,
            total_paginas,
            len(page_rows),
            nuevas_count,
            len(seen),
        )
        yield pagina, nuevas, meta

        if not page_rows:
            break
        if pagina > 1 and nuevas_count == 0:
            paginacion_repetida = True
            logger.warning(
                "SIIFA devolvió solo duplicados en página %s; deteniendo paginación.",
                pagina,
            )
            break
        if total_registros and len(seen) >= total_registros:
            break
        if pausa_segundos > 0:
            time.sleep(pausa_segundos)
        pagina += 1

    meta_final: dict[str, Any] = {
        "paginas_leidas": paginas_leidas,
        "total_paginas_siifa": total_paginas,
        "total_registros_siifa": total_registros or 0,
        "filas_unicas": len(seen),
        "duplicados_omitidos": duplicados_omitidos,
        "paginacion_repetida": paginacion_repetida,
        "nit_adquiriente": nit_adquiriente,
        "desde_pagina": max(1, int(desde_pagina)),
        "completado": (
            not inicio_invalido
            and paginas_leidas >= total_paginas
            and not paginacion_repetida
            and paginas_leidas > 0
        ),
    }
    yield None, [], meta_final


# ── PostgreSQL ────────────────────────────────────────────────────────────────
def _row_a_erp_match(row: Any) -> ErpMatch:
    af = RipsAfMatch(
        consecutivo_rips_af=int(row["consecutivo_rips_af"]),
        consecutivo_rips=int(row["consecutivo_rips"]),
        numero_factura=str(row["numero_factura"] or ""),
        numero_identificacion=str(row["numero_identificacion"] or ""),
        radicado_siifa=int(row["radicado_siifa"]) if row["radicado_siifa"] is not None else None,
        idfactura_siifa=str(row["idfactura_siifa"]) if row["idfactura_siifa"] else None,
    )
    resumen: RipsResumenMatch | None = None
    if row.get("estado") is not None:
        fecha = _parse_fecha_radica_erp(row.get("fecha_radica"))
        resumen = RipsResumenMatch(
            consecutivo_rips=int(row["consecutivo_rips"]),
            estado=int(row["estado"]),
            radica_rips=str(row.get("radica_rips") or "").strip(),
            fecha_radica=fecha,
        )
    return ErpMatch(rips_af=af, resumen=resumen)


def pg_buscar_erp(session: Session, numero: str, nit: str) -> ErpMatch | None:
    """rips_af + rips_resumen en un solo SELECT."""
    numero, nit = _erp_clave(numero, nit)
    params = {"numero_factura": numero, "nit_emisor": nit}
    row = session.execute(text(SQL_BUSCAR_ERP_JOIN), params).mappings().first()
    if not row:
        row = session.execute(text(SQL_BUSCAR_ERP_JOIN_TRIM), params).mappings().first()
    if not row:
        return None
    return _row_a_erp_match(row)


def pg_prefetch_erp_pagina(
    session: Session,
    items: list[FacturaItem],
) -> dict[tuple[str, str], ErpMatch]:
    """Precarga coincidencias ERP de una página SIIFA (1–N queries según PG_BATCH_CHUNK)."""
    if not items:
        return {}
    chunk_size = max(1, int(CONFIG.get("PG_BATCH_CHUNK", 250)))
    cache: dict[tuple[str, str], ErpMatch] = {}
    for offset in range(0, len(items), chunk_size):
        chunk = items[offset : offset + chunk_size]
        params: dict[str, str] = {}
        tuples_sql: list[str] = []
        for idx, item in enumerate(chunk):
            params[f"n{idx}"] = item.numero_factura.strip()
            params[f"t{idx}"] = item.nit_emisor.strip()
            tuples_sql.append(f"(:n{idx}, :t{idx})")
        sql = f"""
        SELECT af.consecutivo_rips_af, af.consecutivo_rips, af.numero_factura, af.numero_identificacion,
               af.radicado_siifa, af.idfactura_siifa,
               r.estado, r.radica_rips, r.fecha_radica
        FROM administrativo.rips_af af
        LEFT JOIN administrativo.rips_resumen r ON r.consecutivo_rips = af.consecutivo_rips
        WHERE (af.numero_factura, af.numero_identificacion) IN ({", ".join(tuples_sql)})
        """
        for row in session.execute(text(sql), params).mappings().all():
            em = _row_a_erp_match(row)
            cache[_erp_clave(em.rips_af.numero_factura, em.rips_af.numero_identificacion)] = em
    return cache


def pg_buscar_rips_af(session: Session, numero: str, nit: str) -> RipsAfMatch | None:
    em = pg_buscar_erp(session, numero, nit)
    return em.rips_af if em else None


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


def sql_actualizar_fecha_proceso_factura(
    session: Session,
    *,
    id_factura: int,
    id_ejecucion: int | None,
    fila: int | None = None,
) -> None:
    """Solo toca fecha de proceso cuando el estado no cambió (evita duplicar auditoría)."""
    session.execute(
        text(
            """
            UPDATE dbo.SIIFA_Factura
            SET FechaConsulta = GETDATE(),
                IdEjecucion = :ejec,
                PaginaOrigen = COALESCE(:fila, PaginaOrigen)
            WHERE IdFacturaSIIFA = :id
            """
        ),
        {"id": id_factura, "ejec": id_ejecucion, "fila": fila},
    )


def sql_batch_actualizar_fecha_proceso_facturas(
    session: Session,
    ids_factura: list[int],
    *,
    id_ejecucion: int | None,
    fila: int | None = None,
) -> int:
    """Actualiza FechaConsulta en lote para facturas ya clasificadas (RADICADA/OMITIDA)."""
    if not ids_factura:
        return 0
    chunk_size = max(1, int(CONFIG.get("SQL_BATCH_CHUNK", 400)))
    total = 0
    for offset in range(0, len(ids_factura), chunk_size):
        chunk = ids_factura[offset : offset + chunk_size]
        ids_sql = ",".join(str(int(i)) for i in chunk)
        session.execute(
            text(
                f"""
                UPDATE dbo.SIIFA_Factura
                SET FechaConsulta = GETDATE(),
                    IdEjecucion = :ejec,
                    PaginaOrigen = COALESCE(:fila, PaginaOrigen)
                WHERE IdFacturaSIIFA IN ({ids_sql})
                """
            ),
            {"ejec": id_ejecucion, "fila": fila},
        )
        total += len(chunk)
    return total


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


def sql_estado_factura(session: Session, id_factura_siifa: int) -> str | None:
    row = session.execute(
        text("SELECT EstadoProceso FROM dbo.SIIFA_Factura WHERE IdFacturaSIIFA = :id"),
        {"id": id_factura_siifa},
    ).mappings().first()
    if not row:
        return None
    estado = str(row.get("EstadoProceso") or "").strip()
    return estado or None


def sql_cargar_estados_facturas(session: Session) -> dict[int, str]:
    """Mapa IdFacturaSIIFA → EstadoProceso para omitir/reevaluar sin consultas repetidas."""
    rows = session.execute(
        text("SELECT IdFacturaSIIFA, EstadoProceso FROM dbo.SIIFA_Factura"),
    ).mappings().all()
    out: dict[int, str] = {}
    for row in rows:
        try:
            out[int(row["IdFacturaSIIFA"])] = str(row.get("EstadoProceso") or "").strip()
        except (TypeError, ValueError):
            continue
    return out


def _debe_saltar_procesamiento(estado_previo: str | None) -> bool:
    return bool(estado_previo and estado_previo in _ESTADOS_SIN_REEVALUACION)


def _debe_reevaluar_erp(estado_previo: str | None) -> bool:
    return estado_previo is None or estado_previo in _ESTADOS_REEVALUAR_ERP


def _persistir_factura_sql(
    sql: Session,
    *,
    row: FacturaItem,
    fila: int,
    id_ejecucion: int | None,
    estado: str,
    observacion: str,
    estado_previo: str | None,
    paso: str,
    resultado_traza: str,
    mensaje_traza: str,
    detalle_traza: dict[str, Any] | None = None,
    registrar_traza: bool = True,
) -> bool:
    """
    Persiste en SIIFA_Factura. Si el estado no cambió, solo actualiza FechaConsulta.
    Traza / FacturaERP / Radicado solo cuando hay cambio de estado.
    Retorna True si hubo cambio de estado.
    """
    cambio = estado_previo != estado
    if cambio or estado_previo is None:
        sql_upsert_factura(
            sql,
            id_factura=row.id_factura_siifa,
            numero=row.numero_factura,
            nit=row.nit_emisor,
            id_ejecucion=id_ejecucion,
            fila=fila,
            estado=estado,
            observacion=observacion,
        )
    else:
        sql_actualizar_fecha_proceso_factura(
            sql,
            id_factura=row.id_factura_siifa,
            id_ejecucion=id_ejecucion,
            fila=fila,
        )
    if registrar_traza and cambio:
        sql_registrar_traza(
            sql,
            id_ejecucion=id_ejecucion,
            id_factura_siifa=row.id_factura_siifa,
            numero_factura=row.numero_factura,
            nit_emisor=row.nit_emisor,
            paso=paso,
            resultado=resultado_traza,
            mensaje=mensaje_traza,
            detalle=detalle_traza,
        )
    return cambio


def _tocar_fecha_si_clasificada(
    sql: Session,
    *,
    row: FacturaItem,
    fila: int,
    id_ejecucion: int | None,
    estado_previo: str,
) -> None:
    """Factura ya clasificada (RADICADA/OMITIDA): solo fecha de proceso, sin nueva traza."""
    sql_actualizar_fecha_proceso_factura(
        sql,
        id_factura=row.id_factura_siifa,
        id_ejecucion=id_ejecucion,
        fila=fila,
    )


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
            SELECT UltimaPaginaProcesada, LoteCompletado, TotalPaginasSiifa
            FROM dbo.SIIFA_LoteCheckpoint WHERE Proceso = :proceso
            """
        ),
        {"proceso": proceso},
    ).mappings().first()
    if not row:
        return LoteCheckpoint()
    total_pag = row.get("TotalPaginasSiifa")
    return LoteCheckpoint(
        ultima_pagina=int(row["UltimaPaginaProcesada"] or 0),
        lote_completado=bool(row["LoteCompletado"]),
        total_paginas_siifa=int(total_pag) if total_pag is not None else None,
    )


def _reiniciar_checkpoint_ciclo(
    session: Session,
    *,
    proceso: str,
    id_ejecucion: int | None,
    motivo: str,
) -> LoteCheckpoint:
    logger.info("Reiniciando checkpoint SIIFA (%s) → página 1.", motivo)
    sql_guardar_checkpoint(
        session,
        proceso=proceso,
        ultima_fila=0,
        total_filas=0,
        total_registros=0,
        lote_completado=False,
        id_ejecucion=id_ejecucion,
    )
    session.commit()
    return LoteCheckpoint()


def sql_guardar_checkpoint(
    session: Session,
    *,
    proceso: str,
    ultima_fila: int,
    total_filas: int,
    total_registros: int | None = None,
    lote_completado: bool,
    id_ejecucion: int | None,
) -> None:
    total_reg = total_registros if total_registros is not None else total_filas
    session.execute(
        text(
            """
            MERGE dbo.SIIFA_LoteCheckpoint AS tgt
            USING (SELECT :proceso AS Proceso) AS src
            ON tgt.Proceso = src.Proceso
            WHEN MATCHED THEN
                UPDATE SET UltimaPaginaProcesada = :ultima,
                           TotalPaginasSiifa = :total, TotalRegistrosSiifa = :total_reg,
                           LoteCompletado = :completado, FechaActualizacion = GETDATE(),
                           IdEjecucionUltima = :ejec
            WHEN NOT MATCHED THEN
                INSERT (Proceso, UltimaPaginaProcesada, TotalPaginasSiifa,
                        TotalRegistrosSiifa, LoteCompletado, IdEjecucionUltima)
                VALUES (:proceso, :ultima, :total, :total_reg, :completado, :ejec);
            """
        ),
        {
            "proceso": proceso,
            "ultima": ultima_fila,
            "total": total_filas,
            "total_reg": total_reg,
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
    row: FacturaItem,
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
    row: FacturaItem,
    fila: int,
    id_ejecucion: int | None,
    pg: Session,
    sql: Session,
    siifa: SiifaClient,
    *,
    dry_run: bool,
    estado_previo: str | None = None,
    erp_cache: dict[tuple[str, str], ErpMatch] | None = None,
    defer_commit: bool = False,
    log_detalle: bool = True,
) -> ResultadoFila:
    try:
        if estado_previo is None and not dry_run and id_ejecucion is not None:
            estado_previo = sql_estado_factura(sql, row.id_factura_siifa)

        reevaluada = bool(estado_previo and estado_previo in _ESTADOS_REEVALUAR_ERP)

        if (
            CONFIG.get("REUTILIZAR_CLASIFICADAS")
            and _debe_saltar_procesamiento(estado_previo)
            and not dry_run
        ):
            estado_skip = _estado_desde_sql(estado_previo) or EstadoProceso.OMITIDA
            return ResultadoFila(
                estado_skip,
                f"Sin reevaluación ({estado_previo})",
                sin_cambio=True,
            )

        if log_detalle:
            if reevaluada:
                _log_flush("  → Reevaluando ERP (estado previo SQL: %s)…", estado_previo)
            else:
                _log_flush("  → Buscando en ERP (rips_af + resumen)…")

        clave = _erp_clave(row.numero_factura, row.nit_emisor)
        erp: ErpMatch | None
        if erp_cache is not None:
            erp = erp_cache.get(clave)
        else:
            erp = pg_buscar_erp(pg, row.numero_factura, row.nit_emisor)

        if not erp:
            estado_nuevo = _estado_sql(EstadoProceso.NO_ENCONTRADA_ERP)
            obs = "NO ENCONTRADA en ERP (numero_factura + nit_emisor)"
            if estado_previo == estado_nuevo:
                obs = "NO ENCONTRADA en ERP (sin cambio)"
            if not dry_run and id_ejecucion is not None:
                _persistir_factura_sql(
                    sql,
                    row=row,
                    fila=fila,
                    id_ejecucion=id_ejecucion,
                    estado=estado_nuevo,
                    observacion=obs,
                    estado_previo=estado_previo,
                    paso="BUSCAR_RIPS_AF",
                    resultado_traza=_traza_sql(ResultadoTraza.NO_ENCONTRADA),
                    mensaje_traza=(
                        f"Sin coincidencia: numero_factura={row.numero_factura!r}, "
                        f"nit_emisor={row.nit_emisor!r}"
                    ),
                )
                _commit_sql(sql, defer=defer_commit)
            return ResultadoFila(
                EstadoProceso.NO_ENCONTRADA_ERP,
                obs,
                sin_cambio=(estado_previo == estado_nuevo),
                reevaluada=reevaluada or estado_previo == estado_nuevo,
            )

        match = erp.rips_af
        if match.idfactura_siifa and str(match.idfactura_siifa).strip():
            estado_nuevo = _estado_sql(EstadoProceso.OMITIDA)
            obs = "Ya tiene idfactura_siifa en rips_af"
            if not dry_run and id_ejecucion is not None:
                _persistir_factura_sql(
                    sql,
                    row=row,
                    fila=fila,
                    id_ejecucion=id_ejecucion,
                    estado=estado_nuevo,
                    observacion=obs,
                    estado_previo=estado_previo,
                    paso="VALIDACION_PREVIA",
                    resultado_traza=_traza_sql(ResultadoTraza.OMITIDA),
                    mensaje_traza="Factura ya sincronizada en ERP",
                )
                _commit_sql(sql, defer=defer_commit)
            return ResultadoFila(
                EstadoProceso.OMITIDA,
                obs,
                sin_cambio=(estado_previo == estado_nuevo),
                reevaluada=reevaluada,
            )

        if log_detalle:
            _log_flush("  → Validando rips_resumen estado=%s…", ESTADO_RADICADO_ERP)
        resumen = erp.resumen
        if not resumen:
            msg = f"rips_resumen no encontrado (consecutivo_rips={match.consecutivo_rips})"
            if not dry_run and id_ejecucion is not None:
                _registrar_error_sql(sql, row=row, id_ejecucion=id_ejecucion, fila=fila, match=match, mensaje=msg, paso="BUSCAR_RIPS_RESUMEN")
                _commit_sql(sql, defer=defer_commit)
            return ResultadoFila(EstadoProceso.ERROR, msg)

        if resumen.estado != ESTADO_RADICADO_ERP:
            obs = f"Factura en ERP pero estado={resumen.estado} (requiere {ESTADO_RADICADO_ERP})"
            estado_nuevo = _estado_sql(EstadoProceso.NO_RADICADA_ERP)
            if estado_previo == estado_nuevo:
                obs = f"{obs} (sin cambio)"
            if not dry_run and id_ejecucion is not None:
                cambio = _persistir_factura_sql(
                    sql,
                    row=row,
                    fila=fila,
                    id_ejecucion=id_ejecucion,
                    estado=estado_nuevo,
                    observacion=obs,
                    estado_previo=estado_previo,
                    paso="VALIDAR_ESTADO_ERP",
                    resultado_traza=_traza_sql(ResultadoTraza.NO_RADICADA_ERP),
                    mensaje_traza=obs,
                    detalle_traza={"estado": resumen.estado},
                )
                if cambio:
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
                _commit_sql(sql, defer=defer_commit)
            return ResultadoFila(
                EstadoProceso.NO_RADICADA_ERP,
                obs,
                sin_cambio=(estado_previo == estado_nuevo),
                reevaluada=reevaluada or estado_previo == estado_nuevo,
            )

        if not resumen.radica_rips:
            msg = "radica_rips vacío en rips_resumen con estado=5"
            if not dry_run and id_ejecucion is not None:
                _registrar_error_sql(sql, row=row, id_ejecucion=id_ejecucion, fila=fila, match=match, mensaje=msg, paso="VALIDAR_RADICA_RIPS")
                _commit_sql(sql, defer=defer_commit)
            return ResultadoFila(EstadoProceso.ERROR, msg)

        if resumen.fecha_radica is None:
            msg = f"fecha_radica vacía en rips_resumen (consecutivo_rips={match.consecutivo_rips})"
            if not dry_run and id_ejecucion is not None:
                _registrar_error_sql(sql, row=row, id_ejecucion=id_ejecucion, fila=fila, match=match, mensaje=msg, paso="VALIDAR_FECHA_RADICA")
                _commit_sql(sql, defer=defer_commit)
            return ResultadoFila(EstadoProceso.ERROR, msg)

        if dry_run:
            _rollback_pg(pg, defer=defer_commit)
            return ResultadoFila(
                EstadoProceso.RADICADA,
                (
                    f"Cumple condiciones para radicación "
                    f"(radica_rips={resumen.radica_rips}, fecha={resumen.fecha_radica})"
                ),
            )

        fecha_iso = _fecha_erp_a_iso_utc(resumen.fecha_radica)
        if log_detalle:
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
                _commit_sql(sql, defer=defer_commit)
            _rollback_pg(pg, defer=defer_commit)
            return ResultadoFila(EstadoProceso.ERROR, str(exc))

        id_radicado_siifa = int(
            resp_siifa.get("idFacturaRadicado") or resp_siifa.get("IdFacturaRadicado") or 0
        )
        if not id_radicado_siifa:
            msg = f"SIIFA no devolvió idFacturaRadicado: {resp_siifa}"
            if id_ejecucion is not None:
                _registrar_error_sql(sql, row=row, id_ejecucion=id_ejecucion, fila=fila, match=match, mensaje=msg, paso="RADICAR_SIIFA")
                _commit_sql(sql, defer=defer_commit)
            _rollback_pg(pg, defer=defer_commit)
            return ResultadoFila(EstadoProceso.ERROR, msg)

        fecha_sync = datetime.now(timezone.utc).replace(tzinfo=None)
        if log_detalle:
            _log_flush("  → Actualizando rips_af consecutivo=%s…", match.consecutivo_rips_af)
        try:
            radicado_guardado = pg_actualizar_rips_af(
                pg,
                consecutivo_rips_af=match.consecutivo_rips_af,
                id_factura_siifa=row.id_factura_siifa,
                radicado_siifa=id_radicado_siifa,
                fecha_rad_siifa=fecha_sync,
            )
            _commit_pg(pg, defer=defer_commit)
        except Exception as exc:
            _rollback_pg(pg, defer=defer_commit)
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
                _commit_sql(sql, defer=defer_commit)
            return ResultadoFila(
                EstadoProceso.ERROR,
                f"SIIFA OK pero falló UPDATE ERP: {exc}",
            )

        if log_detalle:
            _log_flush("  → Registrando auditoría SQL Server…")
        estado_nuevo = _estado_sql(EstadoProceso.RADICADA)
        obs = "Radicada exitosamente"
        if estado_previo == _estado_sql(EstadoProceso.NO_ENCONTRADA_ERP):
            obs = "Radicada exitosamente (antes NO_ENCONTRADA_ERP en SQL)"
        elif reevaluada:
            obs = f"Radicada exitosamente (reevaluación desde {estado_previo})"

        _persistir_factura_sql(
            sql,
            row=row,
            fila=fila,
            id_ejecucion=id_ejecucion,
            estado=estado_nuevo,
            observacion=obs,
            estado_previo=estado_previo,
            paso="COMPLETADO",
            resultado_traza=_traza_sql(ResultadoTraza.OK),
            mensaje_traza="Radicación SIIFA + actualización rips_af",
            detalle_traza={
                "idFacturaRadicado": id_radicado_siifa,
                "radicado_erp": resumen.radica_rips,
                "fecha_erp": str(resumen.fecha_radica),
                "radicado_siifa_erp": radicado_guardado,
                "estado_previo": estado_previo,
            },
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
        _commit_sql(sql, defer=defer_commit)
        return ResultadoFila(
            EstadoProceso.RADICADA,
            obs,
            sin_cambio=False,
            reevaluada=reevaluada,
        )

    except Exception:
        _rollback_pg(pg, defer=defer_commit)
        if not defer_commit:
            sql.rollback()
        raise


def _actualizar_metricas(metricas: Metricas, resultado: ResultadoFila) -> None:
    metricas.procesadas += 1
    if resultado.sin_cambio:
        metricas.sin_cambio_estado += 1
    if resultado.reevaluada:
        metricas.reevaluadas += 1
    estado = resultado.estado
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
def ejecutar_desde_siifa(
    *,
    nit_adquiriente: str,
    usuario: str,
    max_paginas: int | None,
    reiniciar: bool,
    dry_run: bool,
    log_csv: Path | None,
) -> dict[str, Any]:
    inicio = time.perf_counter()
    paginas_por_lote = (
        max_paginas
        if max_paginas and max_paginas > 0
        else int(CONFIG.get("SIIFA_MAX_PAGINAS_POR_LOTE", 10))
    )
    registros_por_pagina = int(CONFIG.get("SIIFA_REGISTROS_POR_PAGINA", 500))
    pausa = float(CONFIG.get("SIIFA_PAUSA_ENTRE_PAGINAS", 0.3))
    proceso = str(CONFIG["PROCESO_CHECKPOINT"])

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
    pagina_inicio = 1
    pagina_fin = 0
    proxima_pagina = 1
    lote_completado = False
    facturas_procesadas = 0
    estado_final = "OK"
    meta_siifa: dict[str, Any] = {}
    traza = TrazaCsvWriter(log_csv if not dry_run else None)

    try:
        checkpoint = sql_obtener_checkpoint(sql_main, proceso)
        if reiniciar and not dry_run:
            checkpoint = _reiniciar_checkpoint_ciclo(
                sql_main,
                proceso=proceso,
                id_ejecucion=id_ejecucion,
                motivo="--reiniciar",
            )
        elif not dry_run:
            if checkpoint.lote_completado:
                checkpoint = _reiniciar_checkpoint_ciclo(
                    sql_main,
                    proceso=proceso,
                    id_ejecucion=id_ejecucion,
                    motivo="ciclo SIIFA anterior completado",
                )
            elif (
                checkpoint.total_paginas_siifa
                and checkpoint.proxima_pagina > checkpoint.total_paginas_siifa
            ):
                checkpoint = _reiniciar_checkpoint_ciclo(
                    sql_main,
                    proceso=proceso,
                    id_ejecucion=id_ejecucion,
                    motivo=(
                        f"checkpoint página {checkpoint.proxima_pagina} "
                        f"> total SIIFA {checkpoint.total_paginas_siifa}"
                    ),
                )

        pagina_inicio = 1 if dry_run else checkpoint.proxima_pagina
        logger.info(
            "Checkpoint proceso=%s proxima_pagina_siifa=%s lote_completado=%s "
            "total_paginas_siifa=%s nit=%s",
            proceso,
            pagina_inicio,
            checkpoint.lote_completado,
            checkpoint.total_paginas_siifa,
            nit_adquiriente,
        )

        _log_flush(
            "Consultando SIIFA sin seguimiento (páginas %s+, máx %s por lote, dry_run=%s)…",
            pagina_inicio,
            paginas_por_lote,
            dry_run,
        )
        pg_sess = pg_factory()
        sql_sess = sql_factory()
        estados_sql: dict[int, str] = {}
        if not dry_run:
            estados_sql = sql_cargar_estados_facturas(sql_sess)
            logger.info(
                "Estados SQL cargados: %s facturas (%s RADICADA/OMITIDA sin reevaluar)",
                len(estados_sql),
                sum(1 for e in estados_sql.values() if e in _ESTADOS_SIN_REEVALUACION),
            )
        siifa = SiifaClient()
        siifa.login()
        defer_commit = bool(CONFIG.get("COMMIT_POR_PAGINA", True)) and not dry_run
        prefetch_erp = bool(CONFIG.get("PG_PREFETCH_ERP", True)) and not dry_run
        log_cada = max(1, int(CONFIG.get("LOG_CADA_N_FILAS", 25)))

        paginas_en_lote = 0
        total_registros_siifa = 0
        try:
            for pagina, facturas, meta_parcial in yield_paginas_sin_seguimiento(
                    siifa,
                    nit_adquiriente=nit_adquiriente,
                    registros_por_pagina=registros_por_pagina,
                    desde_pagina=pagina_inicio,
                    max_paginas=paginas_por_lote,
                    pausa_segundos=pausa,
                ):
                    if pagina is None:
                        meta_siifa = meta_parcial
                        break

                    pagina_fin = pagina
                    paginas_en_lote += 1
                    total_paginas_siifa = int(meta_parcial.get("total_paginas_siifa") or pagina)

                    ids_clasificadas: list[int] = []
                    items_a_procesar: list[FacturaItem] = []
                    estados_previos: dict[int, str | None] = {}
                    for item in facturas:
                        estado_previo = estados_sql.get(item.id_factura_siifa)
                        estados_previos[item.id_factura_siifa] = estado_previo
                        if _debe_saltar_procesamiento(estado_previo):
                            ids_clasificadas.append(item.id_factura_siifa)
                        else:
                            items_a_procesar.append(item)

                    ids_clasificadas_set = set(ids_clasificadas)
                    if ids_clasificadas_set:
                        for item in facturas:
                            if item.id_factura_siifa not in ids_clasificadas_set:
                                continue
                            ep = estados_sql.get(item.id_factura_siifa)
                            resultado = ResultadoFila(
                                _estado_desde_sql(ep) or EstadoProceso.OMITIDA,
                                f"Sin reevaluación ({ep})",
                                sin_cambio=True,
                            )
                            traza.append(item, resultado, dry_run=dry_run)
                            _actualizar_metricas(metricas, resultado)
                            facturas_procesadas += 1

                    erp_cache: dict[tuple[str, str], ErpMatch] = {}
                    if prefetch_erp and items_a_procesar:
                        t_pref = time.perf_counter()
                        erp_cache = pg_prefetch_erp_pagina(pg_sess, items_a_procesar)
                        logger.info(
                            "Pág %s prefetch ERP: %s facturas → %s coincidencias (%.2fs)",
                            pagina,
                            len(items_a_procesar),
                            len(erp_cache),
                            time.perf_counter() - t_pref,
                        )

                    for seq, item in enumerate(items_a_procesar, start=1):
                        estado_previo = estados_previos.get(item.id_factura_siifa)
                        t0 = time.perf_counter()
                        log_detalle = seq == 1 or seq == len(items_a_procesar) or seq % log_cada == 0
                        if log_detalle:
                            _log_flush(
                                "Pág %s [%s/%s] idFactura=%s factura=%s nit=%s",
                                pagina,
                                seq,
                                len(items_a_procesar),
                                item.id_factura_siifa,
                                item.numero_factura,
                                item.nit_emisor,
                            )
                        try:
                            if defer_commit:
                                # Un savepoint por fila; al salir del with se libera (evita apilar 500+ niveles).
                                with sql_sess.begin_nested(), pg_sess.begin_nested():
                                    resultado = procesar_fila(
                                        item,
                                        pagina,
                                        id_ejecucion,
                                        pg_sess,
                                        sql_sess,
                                        siifa,
                                        dry_run=dry_run,
                                        estado_previo=estado_previo,
                                        erp_cache=erp_cache if prefetch_erp else None,
                                        defer_commit=defer_commit,
                                        log_detalle=log_detalle,
                                    )
                            else:
                                resultado = procesar_fila(
                                    item,
                                    pagina,
                                    id_ejecucion,
                                    pg_sess,
                                    sql_sess,
                                    siifa,
                                    dry_run=dry_run,
                                    estado_previo=estado_previo,
                                    erp_cache=erp_cache if prefetch_erp else None,
                                    defer_commit=defer_commit,
                                    log_detalle=log_detalle,
                                )
                        except Exception as exc:
                            logger.exception("Error idFactura=%s", item.id_factura_siifa)
                            resultado = ResultadoFila(
                                EstadoProceso.ERROR,
                                f"Error inesperado: {exc}",
                            )
                            metricas.advertencias.append(f"idFactura={item.id_factura_siifa}: {exc}")

                        if not dry_run:
                            estados_sql[item.id_factura_siifa] = _estado_sql(resultado.estado)

                        traza.append(item, resultado, dry_run=dry_run)
                        _actualizar_metricas(metricas, resultado)
                        facturas_procesadas += 1
                        if log_detalle:
                            logger.info(
                                "  → %s (%.1fs) [total %s]",
                                _estado_sql(resultado.estado),
                                time.perf_counter() - t0,
                                metricas.procesadas,
                            )
                            sys.stderr.flush()

                    if not dry_run:
                        if ids_clasificadas:
                            tocadas = sql_batch_actualizar_fecha_proceso_facturas(
                                sql_sess,
                                ids_clasificadas,
                                id_ejecucion=id_ejecucion,
                                fila=pagina,
                            )
                            logger.info(
                                "Pág %s fechas actualizadas (clasificadas): %s",
                                pagina,
                                tocadas,
                            )
                        if defer_commit:
                            try:
                                sql_sess.commit()
                                pg_sess.commit()
                            except Exception:
                                sql_sess.rollback()
                                pg_sess.rollback()
                                raise

                    if not dry_run:
                        total_registros_siifa = int(
                            meta_parcial.get("total_registros_siifa") or total_registros_siifa
                        )
                        ciclo_cerrado = total_paginas_siifa > 0 and pagina >= total_paginas_siifa
                        sql_guardar_checkpoint(
                            sql_main,
                            proceso=proceso,
                            ultima_fila=0 if ciclo_cerrado else pagina,
                            total_filas=total_paginas_siifa,
                            total_registros=total_registros_siifa,
                            lote_completado=ciclo_cerrado,
                            id_ejecucion=id_ejecucion,
                        )
                        sql_main.commit()
                        if ciclo_cerrado:
                            logger.info(
                                "Ciclo SIIFA cerrado en página %s/%s.",
                                pagina,
                                total_paginas_siifa,
                            )

                    if meta_parcial.get("paginacion_repetida"):
                        break

            lote_completado = bool(meta_siifa.get("completado"))
            proxima_pagina = 1 if lote_completado else (pagina_fin + 1 if pagina_fin else pagina_inicio)

            if not dry_run and lote_completado and meta_siifa:
                sql_guardar_checkpoint(
                    sql_main,
                    proceso=proceso,
                    ultima_fila=0,
                    total_filas=int(meta_siifa.get("total_paginas_siifa") or pagina_fin),
                    total_registros=int(meta_siifa.get("total_registros_siifa") or 0),
                    lote_completado=True,
                    id_ejecucion=id_ejecucion,
                )
                sql_main.commit()
                logger.info(
                    "Ciclo SIIFA completo: %s páginas, %s registros. Siguiente corrida desde página 1.",
                    meta_siifa.get("total_paginas_siifa"),
                    meta_siifa.get("total_registros_siifa"),
                )
        finally:
            pg_sess.close()
            sql_sess.close()
            traza.close()

        if metricas.errores > 0 and metricas.radicadas > 0:
            estado_final = "PARCIAL"
        elif metricas.errores > 0 and metricas.radicadas == 0 and facturas_procesadas > 0:
            estado_final = "ERROR"
        if dry_run:
            estado_final = "OK"

    except Exception:
        estado_final = "ERROR"
        raise
    finally:
        traza.close()
        duracion_ms = int((time.perf_counter() - inicio) * 1000)
        detalle = metricas.to_dict()
        detalle.update(
            {
                "origen": "siifa_api",
                "nit_adquiriente": nit_adquiriente,
                "pagina_inicio": pagina_inicio,
                "pagina_fin": pagina_fin,
                "proxima_pagina": proxima_pagina,
                "lote_completado": lote_completado,
                "paginas_por_lote": paginas_por_lote,
                "registros_por_pagina": registros_por_pagina,
                "archivo_traza": str(log_csv) if log_csv else None,
                "dry_run": dry_run,
                "modo": "secuencial_api_v4",
                "commit_por_pagina": bool(CONFIG.get("COMMIT_POR_PAGINA", True)),
                "pg_prefetch_erp": bool(CONFIG.get("PG_PREFETCH_ERP", True)),
                **meta_siifa,
            }
        )
        try:
            if not dry_run and id_ejecucion is not None:
                sql_finalizar_ejecucion(
                    sql_main,
                    id_ejecucion,
                    estado=estado_final,
                    metricas=detalle,
                    total_filas=int(meta_siifa.get("total_registros_siifa") or facturas_procesadas),
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
        "nit_adquiriente": nit_adquiriente,
        "pagina_inicio": pagina_inicio,
        "pagina_fin": pagina_fin,
        "proxima_pagina": proxima_pagina,
        "lote_completado": lote_completado,
        "requiere_siguiente_lote": not lote_completado and not dry_run,
        "facturas_procesadas": facturas_procesadas,
        "paginas_por_lote": paginas_por_lote,
        "archivo_traza": str(log_csv) if log_csv else None,
        "dry_run": dry_run,
        "modo": "secuencial_api_v4",
        "commit_por_pagina": bool(CONFIG.get("COMMIT_POR_PAGINA", True)),
        "pg_prefetch_erp": bool(CONFIG.get("PG_PREFETCH_ERP", True)),
        **metricas.to_dict(),
        **meta_siifa,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Consulta SIIFA facturas sin seguimiento, valida ERP (estado=5) "
            "y radica en SIIFA (standalone, sin proyecto)."
        ),
    )
    parser.add_argument(
        "--nit-adquiriente",
        default=None,
        help="NIT adquiriente EPS (ej. 901543761). Obligatorio si no está en SIIFA_NIT_ADQUIRIENTE.",
    )
    parser.add_argument(
        "--max-paginas",
        type=int,
        default=None,
        help="Páginas SIIFA por ejecución (default: SIIFA_MAX_PAGINAS_POR_LOTE=10). 0 = una sola página.",
    )
    parser.add_argument(
        "--registros-por-pagina",
        type=int,
        default=None,
        help="Tamaño de página SIIFA (default: 500).",
    )
    parser.add_argument(
        "--pausa-segundos",
        type=float,
        default=None,
        help="Pausa entre páginas SIIFA (default: 0.3).",
    )
    parser.add_argument(
        "--log-csv",
        type=Path,
        default=DEFAULT_LOG_CSV,
        help="CSV append con traza por factura (estado/observación). Use --sin-log-csv para omitir.",
    )
    parser.add_argument("--sin-log-csv", action="store_true")
    parser.add_argument("--reiniciar", action="store_true", help="Reinicia checkpoint RADICACION_SIIFA_API")
    parser.add_argument("--dry-run", action="store_true", help="Simula sin escribir BD ni radicar")
    parser.add_argument("--verificar", action="store_true", help="Solo prueba conexiones")
    parser.add_argument("--sin-verificar", action="store_true")
    parser.add_argument("--usuario", default="cli_standalone_api")
    parser.add_argument(
        "--hasta-completar",
        action="store_true",
        help="Repite lotes hasta procesar todas las páginas SIIFA (reanuda checkpoint)",
    )
    parser.add_argument(
        "--listar-conectores",
        action="store_true",
        help="Muestra drivers ODBC y disponibilidad pymssql",
    )
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()

    if args.version:
        print(f"{Path(__file__).name} {SCRIPT_VERSION}")
        return 0

    _require_deps()
    _apply_env_config()

    if args.registros_por_pagina:
        CONFIG["SIIFA_REGISTROS_POR_PAGINA"] = args.registros_por_pagina
    if args.pausa_segundos is not None:
        CONFIG["SIIFA_PAUSA_ENTRE_PAGINAS"] = max(0.0, args.pausa_segundos)

    if args.listar_conectores:
        print(json.dumps(listar_conectores_sqlserver(), ensure_ascii=False, indent=2, default=str))
        return 0

    if not str(CONFIG.get("POSTGRES_URL", "")).strip():
        print("Configure POSTGRES_URL en CONFIG o variable de entorno.", file=sys.stderr)
        return 2
    if not str(CONFIG.get("SQLSERVER_URL", "")).strip():
        print("Configure SQLSERVER_URL en CONFIG o variable de entorno.", file=sys.stderr)
        return 2

    try:
        nit = _resolver_nit_adquiriente(args.nit_adquiriente)
    except ValueError as exc:
        print(json.dumps({"estado": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    log_csv: Path | None = None
    if not args.sin_log_csv and args.log_csv is not None:
        log_csv = args.log_csv if args.log_csv.is_absolute() else (SCRIPT_DIR / args.log_csv).resolve()

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
            preflight["nit_adquiriente"] = nit
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
                _log_flush("=== Lote API %s (continúa desde checkpoint) ===", lote_num)

            resultado = ejecutar_desde_siifa(
                nit_adquiriente=nit,
                usuario=args.usuario,
                max_paginas=args.max_paginas,
                reiniciar=args.reiniciar if lote_num == 1 else False,
                dry_run=args.dry_run,
                log_csv=log_csv,
            )
            resultado_final = resultado
            print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
            sys.stdout.flush()

            if not args.hasta_completar or not resultado.get("requiere_siguiente_lote"):
                break

        resultado = resultado_final or {}
        if resultado.get("requiere_siguiente_lote"):
            print(
                f"\n>>> Continuar: python {Path(__file__).name} "
                f"--nit-adquiriente {nit} --hasta-completar --sin-verificar "
                f"(próxima página SIIFA: {resultado.get('proxima_pagina')})",
                file=sys.stderr,
            )
        return 0 if resultado.get("estado") in ("OK", "PARCIAL") else 1
    except Exception as exc:
        print(json.dumps({"estado": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        pg_engine.dispose()
        try:
            sql_engine.dispose()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
