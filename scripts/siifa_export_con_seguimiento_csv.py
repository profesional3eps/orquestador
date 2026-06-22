#!/usr/bin/env python3
"""
Exporta desde SIIFA las facturas CON información de seguimiento (radicadas).

Solo consulta la API SIIFA (GET /api/Factura?TieneRadicado=true).
No consulta ERP / PostgreSQL / SQL Server.

Uso:
  python scripts/siifa_export_con_seguimiento_csv.py --nit-adquiriente 901543761
  python scripts/siifa_export_con_seguimiento_csv.py --env-file ../.env --nit-adquiriente 901543761
  python scripts/siifa_export_con_seguimiento_csv.py --max-paginas 5 --nit-adquiriente 901543761
  python scripts/siifa_export_con_seguimiento_csv.py -o export/con_seguimiento_siifa.csv --nit-adquiriente 901543761
  python scripts/siifa_export_con_seguimiento_csv.py -o export/con_seguimiento_siifa.csv --continuar --nit-adquiriente 901543761
  python scripts/siifa_export_con_seguimiento_csv.py --desde-pagina 65 --hasta-pagina 236 -o export/tramo2.csv --nit-adquiriente 901543761
  python scripts/siifa_export_con_seguimiento_csv.py --pausa-segundos 0.3 --nit-adquiriente 901543761

Requisitos: Python 3.11+ (stdlib). Credenciales SIIFA_* y SIIFA_NIT_ADQUIRIENTE en .env.
Sin nitAdquiriente SIIFA repite la misma página (duplicados). El export deduplica por idFactura.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
SCRIPT_VERSION = "standalone-5"

_lib_path = SCRIPTS_DIR / "siifa_export_facturas_csv.py"
_lib_name = "siifa_export_facturas_csv"
_spec = importlib.util.spec_from_file_location(_lib_name, _lib_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"No se pudo cargar {_lib_path}")
_lib = importlib.util.module_from_spec(_spec)
sys.modules[_lib_name] = _lib
_spec.loader.exec_module(_lib)

SiifaExportClient = _lib.SiifaExportClient
load_siifa_env = _lib.load_siifa_env
yield_siifa_facturas_paginado = _lib.yield_siifa_facturas_paginado
save_export_progress = _lib.save_export_progress
load_export_progress = _lib.load_export_progress
load_seen_ids_from_export_csv = _lib.load_seen_ids_from_export_csv
_factura_id = _lib._factura_id

COLUMNAS = [
    "pagina_siifa",
    "id_factura_siifa",
    "numero_factura",
    "nit_emisor",
    "razon_social_emisor",
    "nit_adquiriente",
    "razon_social_adquiriente",
    "valor_factura",
    "fecha_emision",
    "hora_emision",
    "fecha_vencimiento",
    "cufe",
    "tipo_factura",
    "indicador_tipo_operacion",
    "numero_elementos",
    "tiene_radicado_siifa",
    "id_factura_radicado_siifa",
    "numero_radicado",
    "fecha_radicado",
]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _party_nit(party: dict[str, Any]) -> str:
    for key in ("nitEmisor", "nitAdquiriente", "nit", "numeroIdentificacion"):
        val = party.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _party_name(party: dict[str, Any]) -> str:
    for key in ("razonSocial", "razonSocialEmisor", "razonSocialAdquiriente", "nombre"):
        val = party.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _first_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        val = item.get(key)
        if val is not None and val != "":
            return val
    return ""


def _bloque_radicado(raw: dict[str, Any]) -> dict[str, Any]:
    for key in ("facturaRadicado", "facturaRadicadoDto", "radicadoFactura", "ultimoRadicado"):
        block = raw.get(key)
        if isinstance(block, dict):
            return block
    return {}


def fila_con_seguimiento(raw: dict[str, Any], pagina: int) -> dict[str, Any]:
    emisor = _as_dict(raw.get("emisor"))
    adquiriente = _as_dict(raw.get("adquiriente"))
    rad = _bloque_radicado(raw)
    return {
        "pagina_siifa": pagina,
        "id_factura_siifa": _first_value(raw, "idFactura"),
        "numero_factura": _first_value(raw, "numeroFactura"),
        "nit_emisor": _party_nit(emisor) or _first_value(raw, "nitEmisor"),
        "razon_social_emisor": _party_name(emisor),
        "nit_adquiriente": _party_nit(adquiriente) or _first_value(raw, "nitAdquiriente"),
        "razon_social_adquiriente": _party_name(adquiriente),
        "valor_factura": _first_value(raw, "valorFactura", "totalValorBruto"),
        "fecha_emision": _first_value(raw, "fechaEmision"),
        "hora_emision": _first_value(raw, "horaEmision"),
        "fecha_vencimiento": _first_value(raw, "fechaVencimiento"),
        "cufe": _first_value(raw, "cufe"),
        "tipo_factura": _first_value(raw, "tipoFactura"),
        "indicador_tipo_operacion": _first_value(raw, "indicadorTipoOperacion"),
        "numero_elementos": _first_value(raw, "numeroElementos"),
        "tiene_radicado_siifa": _first_value(raw, "tieneRadicado", "TieneRadicado") or "true",
        "id_factura_radicado_siifa": _first_value(rad, "idFacturaRadicado") or _first_value(
            raw, "idFacturaRadicado"
        ),
        "numero_radicado": _first_value(rad, "radicado", "numeroRadicado") or _first_value(
            raw, "radicado", "numeroRadicado"
        ),
        "fecha_radicado": _first_value(rad, "fechaRadicado", "fechaRadicacion") or _first_value(
            raw, "fechaRadicado", "fechaRadicacion"
        ),
    }


def _flatten_record(data: dict[str, Any], parent_key: str = "", sep: str = "_") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        col = f"{parent_key}{sep}{key}" if parent_key else str(key)
        if isinstance(value, dict):
            out.update(_flatten_record(value, col, sep=sep))
        elif isinstance(value, list):
            out[col] = json.dumps(value, ensure_ascii=False)
        else:
            out[col] = value
    return out


def _write_csv_ordered(path: Path, rows: list[dict[str, Any]], base_columns: list[str]) -> None:
    extras: list[str] = []
    seen = set(base_columns)
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                extras.append(key)
    fieldnames = base_columns + extras
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: "" if row.get(k) is None else row.get(k) for k in fieldnames})


def _open_csv_writer(
    path: Path,
    base_columns: list[str],
    *,
    append: bool,
) -> tuple[Any, csv.DictWriter, list[str]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(base_columns)
    if append:
        if not path.is_file():
            raise FileNotFoundError(
                f"No existe {path} para continuar. Use el mismo -o de la ejecución anterior."
            )
        fh = path.open("a", encoding="utf-8-sig", newline="")
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        return fh, writer, fieldnames

    fh = path.open("w", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    return fh, writer, fieldnames


def _resolver_nit_adquiriente(cfg: Any, nit_adquiriente: str | None) -> str:
    nit = (nit_adquiriente or cfg.nit_adquiriente_default or "").strip()
    if not nit:
        raise ValueError(
            "Obligatorio --nit-adquiriente (ej. 901543761) o SIIFA_NIT_ADQUIRIENTE en .env. "
            "Sin NIT, SIIFA devuelve la misma página en cada numeroPagina (miles de duplicados)."
        )
    return nit


def export_con_seguimiento_csv(
    *,
    cfg: Any,
    output: Path,
    registros_por_pagina: int,
    max_paginas: int,
    nit_adquiriente: str | None,
    incluir_completo: bool,
    desde_pagina: int = 1,
    hasta_pagina: int = 0,
    pausa_segundos: float = 0.0,
    continuar: bool = False,
) -> dict[str, Any]:
    nit = _resolver_nit_adquiriente(cfg, nit_adquiriente)

    filas_previas = 0
    pagina_inicio = max(1, int(desde_pagina))
    append = False
    seen_inicial: set[str] | None = None

    if continuar:
        progress = load_export_progress(output)
        if progress:
            if progress.get("nit_adquiriente") and progress.get("nit_adquiriente") != nit:
                raise ValueError(
                    f"NIT distinto al checkpoint ({progress.get('nit_adquiriente')} vs {nit})."
                )
            pagina_inicio = int(progress.get("ultima_pagina_ok") or 0) + 1
            filas_previas = int(progress.get("filas_exportadas") or 0)
            append = pagina_inicio > 1
            print(
                f"Continuando desde página {pagina_inicio} "
                f"({filas_previas} filas ya exportadas en {output.name}).",
                file=sys.stderr,
            )
        elif pagina_inicio <= 1:
            raise FileNotFoundError(
                f"No hay checkpoint {output.name}.progress.json. "
                f"Use --desde-pagina N o indique el mismo -o de la corrida anterior."
            )
    elif pagina_inicio > 1:
        append = True

    if append:
        seen_inicial = load_seen_ids_from_export_csv(output)
        if seen_inicial:
            print(
                f"Reanudación: {len(seen_inicial)} idFactura ya presentes en {output.name} "
                "(no se volverán a escribir).",
                file=sys.stderr,
            )

    if incluir_completo:
        raise ValueError("--completo no está soportado con escritura incremental; omita --completo.")

    client = SiifaExportClient(cfg)
    client.login()

    fh, writer, fieldnames = _open_csv_writer(output, COLUMNAS, append=append)
    filas_exportadas = filas_previas
    filas_nuevas_esta_corrida = 0
    duplicados_omitidos_escritura = 0
    con_numero_radicado = 0
    seen_escritura = set(seen_inicial) if seen_inicial else set()
    meta: dict[str, Any] = {}

    try:
        for pagina, nuevas_rows, meta_parcial in yield_siifa_facturas_paginado(
            client,
            registros_por_pagina=registros_por_pagina,
            max_paginas=max_paginas,
            tiene_radicado=True,
            nit_adquiriente=nit,
            desde_pagina=pagina_inicio,
            hasta_pagina=hasta_pagina,
            pausa_segundos=pausa_segundos,
            seen_inicial=seen_inicial,
        ):
            if pagina is None:
                meta = meta_parcial
                break

            for raw in nuevas_rows:
                fid = _factura_id(raw)
                if not fid:
                    fid = str(raw.get("idFactura") or "").strip()
                if fid and fid in seen_escritura:
                    duplicados_omitidos_escritura += 1
                    continue
                if fid:
                    seen_escritura.add(fid)

                row = fila_con_seguimiento(raw, pagina)
                writer.writerow({k: "" if row.get(k) is None else row.get(k) for k in fieldnames})
                filas_exportadas += 1
                filas_nuevas_esta_corrida += 1
                if row.get("numero_radicado") or row.get("fecha_radicado"):
                    con_numero_radicado += 1

            fh.flush()
            save_export_progress(
                output,
                {
                    "archivo": str(output.resolve()),
                    "ultima_pagina_ok": pagina,
                    "filas_exportadas": filas_exportadas,
                    "filas_unicas": len(seen_escritura),
                    "nit_adquiriente": nit,
                    "filtro_siifa": "TieneRadicado=true",
                    "registros_por_pagina": registros_por_pagina,
                    "total_paginas_siifa": meta_parcial.get("total_paginas_siifa"),
                    "total_registros_siifa": meta_parcial.get("total_registros_siifa"),
                    "duplicados_omitidos_api": meta_parcial.get("duplicados_omitidos"),
                    "duplicados_omitidos_escritura": duplicados_omitidos_escritura,
                },
            )
    finally:
        fh.close()

    if meta.get("completado"):
        progress_path = output.with_name(output.name + ".progress.json")
        if progress_path.is_file():
            progress_path.unlink()

    ids_en_archivo = load_seen_ids_from_export_csv(output)
    filas_unicas_archivo = len(ids_en_archivo)
    advertencias: list[str] = []
    if filas_exportadas != filas_unicas_archivo:
        advertencias.append(
            f"El CSV tiene {filas_exportadas} filas pero {filas_unicas_archivo} idFactura únicos; "
            "regenere el archivo (no use un export antiguo con duplicados)."
        )
    if meta.get("paginacion_repetida"):
        advertencias.append(
            "SIIFA repitió páginas sin registros nuevos; verifique nitAdquiriente y numeroPagina."
        )

    return {
        "archivo": str(output.resolve()),
        "filas_exportadas": filas_exportadas,
        "filas_unicas": filas_unicas_archivo,
        "filas_nuevas_esta_corrida": filas_nuevas_esta_corrida,
        "duplicados_omitidos_escritura": duplicados_omitidos_escritura,
        "filas_con_numero_o_fecha_radicado": con_numero_radicado,
        "filtro_siifa": "TieneRadicado=true",
        "origen": "solo_siifa",
        "nit_adquiriente": nit,
        "registros_por_pagina": registros_por_pagina,
        "desde_pagina": pagina_inicio,
        "continuado": continuar or pagina_inicio > 1,
        "advertencias": advertencias,
        **meta,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exportar facturas SIIFA con seguimiento/radicación (solo API SIIFA)."
    )
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--registros-por-pagina", type=int, default=None)
    parser.add_argument("--max-paginas", type=int, default=0, help="0 = todas las páginas.")
    parser.add_argument(
        "--desde-pagina",
        type=int,
        default=1,
        help="Primera página SIIFA a descargar (default: 1).",
    )
    parser.add_argument(
        "--hasta-pagina",
        type=int,
        default=0,
        help="Última página SIIFA inclusive (0 = sin límite).",
    )
    parser.add_argument(
        "--continuar",
        action="store_true",
        help="Reanuda en -o usando el archivo .progress.json (requiere mismo -o).",
    )
    parser.add_argument(
        "--pausa-segundos",
        type=float,
        default=0.0,
        help="Pausa entre páginas (p. ej. 0.5 para aliviar SIIFA).",
    )
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument(
        "--nit-adquiriente",
        default=None,
        help="NIT adquiriente EPS (ej. 901543761). Obligatorio si no está en SIIFA_NIT_ADQUIRIENTE.",
    )
    parser.add_argument(
        "--completo",
        action="store_true",
        help="Agrega columnas extra con todo el JSON SIIFA aplanado.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    env_file = args.env_file.resolve() if args.env_file else (ROOT / ".env")
    if args.verbose:
        print(
            f"siifa_export_con_seguimiento_csv {SCRIPT_VERSION}\n"
            f"  script: {Path(__file__).resolve()}\n"
            f"  .env:   {env_file}\n"
            f"  filtro: TieneRadicado=false (con seguimiento en SIIFA)",
            file=sys.stderr,
        )

    try:
        cfg = load_siifa_env(env_file if args.env_file else None)
    except ValueError as exc:
        print(json.dumps({"estado": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    registros = args.registros_por_pagina or cfg.registros_por_pagina
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    default_output = ROOT / "export" / f"siifa_con_seguimiento_{stamp}.csv"
    output = args.output if args.output is not None else default_output
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()

    try:
        resumen = export_con_seguimiento_csv(
            cfg=cfg,
            output=output,
            registros_por_pagina=registros,
            max_paginas=max(0, int(args.max_paginas)),
            nit_adquiriente=args.nit_adquiriente,
            incluir_completo=args.completo,
            desde_pagina=max(1, int(args.desde_pagina)),
            hasta_pagina=max(0, int(args.hasta_pagina)),
            pausa_segundos=max(0.0, float(args.pausa_segundos)),
            continuar=bool(args.continuar),
        )
        print(json.dumps(resumen, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"estado": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
