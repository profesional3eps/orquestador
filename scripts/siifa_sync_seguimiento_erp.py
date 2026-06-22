#!/usr/bin/env python3
"""
Backfill ERP (PostgreSQL rips_af) desde CSV SIIFA con seguimiento + traza SQL Server.

Ejecutable desde consola, sin Docker ni archivo .env.
Configure las constantes _ENV_DEFAULTS abajo o exporte variables en PowerShell.

  python siifa_sync_seguimiento_erp.py --dry-run --filas 20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = PROJECT_ROOT / "export" / "siifa_con_seguimiento.csv"

# ── Configuración (edite aquí; no usa archivo .env) ──
_ENV_DEFAULTS: dict[str, str] = {
    "POSTGRES_URL": "postgresql+psycopg2://postgres:Sup3r4dm1n7ami1i4rC0l@10.0.1.240:5432/base_sie_comfasucre",
    "SQLSERVER_URL": (
        "mssql+pyodbc://app_orquestador:3P2F4m1l14rC0l0m8142026@150.136.57.32:1433/OrquestacionDB"
        "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
    ),
    "JWT_SECRET": "@EPSF4m1l14rdeC0l0mb142026@",
}
_ENV_KEYS = tuple(_ENV_DEFAULTS.keys())
_DEPS_REQUIRED = ("psycopg2", "pyodbc", "sqlalchemy", "pydantic_settings")


def _setup_path() -> None:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _apply_env_defaults() -> None:
    """Aplica _ENV_DEFAULTS si la variable no está ya en el entorno."""
    for key, value in _ENV_DEFAULTS.items():
        if value and not os.environ.get(key, "").strip():
            os.environ[key] = value


def _require_env_vars(names: Iterable[str]) -> None:
    missing = [n for n in names if not os.environ.get(n, "").strip()]
    if not missing:
        return
    raise SystemExit(
        f"Faltan variables de entorno: {', '.join(missing)}\n\n"
        "Edite _ENV_DEFAULTS en este script o defínalas en PowerShell:\n"
        '  $env:POSTGRES_URL = "postgresql+psycopg2://..."'
    )


def _require_dependencies(packages: Iterable[str]) -> None:
    missing = [p for p in packages if not _try_import(p)]
    if not missing:
        return
    raise SystemExit(
        f"Faltan dependencias: {', '.join(missing)}\n"
        f"Instale con: pip install -r \"{PROJECT_ROOT / 'requirements.txt'}\""
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
    for base in (Path.cwd(), PROJECT_ROOT):
        candidate = (base / raw).resolve()
        if candidate.is_file():
            return candidate
    return (PROJECT_ROOT / raw).resolve()


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Backfill ERP desde CSV SIIFA (consola, sin .env)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--fecha", default="2026-04-10", help="fecha_rad_siifa (YYYY-MM-DD)")
    parser.add_argument("--filas", type=int, default=None, help="Filas por lote (default 5000)")
    parser.add_argument("--reiniciar", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verificar", action="store_true")
    parser.add_argument("--sin-verificar", action="store_true")
    parser.add_argument("--usuario", default="cli_seguimiento_erp")


def _imprimir_preflight(resultado: dict) -> None:
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
    if not resultado.get("ok"):
        print("\n>>> Revise: VPN PostgreSQL y ODBC SQL Server.", file=sys.stderr)


def main() -> int:
    _setup_path()
    parser = _build_parser()
    _add_arguments(parser)
    args = parser.parse_args()

    _apply_env_defaults()
    _require_env_vars(_ENV_KEYS)
    _require_dependencies(_DEPS_REQUIRED)

    from app.core.logging_setup import configure_logging
    from app.jobs.siifa_seguimiento_erp_job import ejecutar_seguimiento_erp_desde_csv
    from app.jobs.siifa_radicacion_preflight import verificar_conectividad_erp

    csv_path = _resolve_csv(args.csv)
    if not csv_path.is_file():
        print(json.dumps({"estado": "ERROR", "error": f"CSV no encontrado: {csv_path}"}, ensure_ascii=False), file=sys.stderr)
        return 2

    configure_logging()
    try:
        if args.verificar or not args.sin_verificar:
            preflight = verificar_conectividad_erp()
            if args.verificar or not preflight.get("ok"):
                _imprimir_preflight(preflight)
                return 0 if args.verificar and preflight.get("ok") else 2

        resultado = ejecutar_seguimiento_erp_desde_csv(
            csv_path,
            fecha_rad_siifa=args.fecha,
            usuario=args.usuario,
            reiniciar_lote=args.reiniciar,
            max_filas=args.filas,
            dry_run=args.dry_run,
        )
        print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
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


if __name__ == "__main__":
    raise SystemExit(main())
