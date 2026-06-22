#!/usr/bin/env python3
"""
Exporta facturas desde SIIFA a CSV (solo lectura; no usa SQL Server ni PostgreSQL).

Script autónomo: solo librería estándar de Python (sin venv ni pip install).
Lee credenciales del .env en la raíz del proyecto (SIIFA_*).

Uso (desde cualquier carpeta):
  python scripts/siifa_export_facturas_csv.py
  python scripts/siifa_export_facturas_csv.py --output export/siifa_pendientes.csv
  python scripts/siifa_export_facturas_csv.py --todas --max-paginas 10
  python scripts/siifa_export_facturas_csv.py --radicadas
  python scripts/siifa_export_facturas_csv.py --nit-adquiriente 890102768

Filtros (mutuamente excluyentes; default = sin radicar):
  --sin-radicar     TieneRadicado=false (igual que la integración actual)
  --radicadas       TieneRadicado=true
  --todas           Sin filtro TieneRadicado
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
SCRIPT_VERSION = "standalone-6"

_RETRIABLE_HTTP = frozenset({429, 500, 502, 503, 504})


@dataclass
class SiifaEnv:
    seguridad_base_url: str
    factura_base_url: str
    username: str
    password: str
    registros_por_pagina: int = 500
    http_timeout_seconds: float = 90.0
    retry_max_attempts: int = 3
    retry_base_delay_seconds: float = 1.0
    login_bearer_token: str | None = None
    nit_adquiriente_default: str | None = None


def _load_dotenv(path: Path) -> dict[str, str]:
    """Carga variables KEY=VALUE del .env (sin dependencias externas)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        else:
            hash_pos = value.find(" #")
            if hash_pos >= 0:
                value = value[:hash_pos].rstrip()
        out[key] = value
    return out


def _env_int(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(env: dict[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_siifa_env(env_file: Path | None = None) -> SiifaEnv:
    dotenv_path = env_file or (ROOT / ".env")
    if not dotenv_path.is_file():
        raise ValueError(
            f"No se encontró .env en {dotenv_path}. "
            f"Ejecute desde la raíz del proyecto o use --env-file ruta\\.env"
        )
    env = _load_dotenv(dotenv_path)
    user = env.get("SIIFA_USERNAME", "").strip()
    pwd = env.get("SIIFA_PASSWORD", "").strip()
    if not user or not pwd:
        raise ValueError(
            f"Configure SIIFA_USERNAME y SIIFA_PASSWORD en {dotenv_path}"
        )
    return SiifaEnv(
        seguridad_base_url=env.get(
            "SIIFA_SEGURIDAD_BASE_URL",
            "https://siifa.sispro.gov.co/siifa-seguridad",
        ).strip(),
        factura_base_url=env.get(
            "SIIFA_FACTURA_BASE_URL",
            "https://siifa.sispro.gov.co/siifa-factura",
        ).strip(),
        username=user,
        password=pwd,
        registros_por_pagina=_env_int(env, "SIIFA_REGISTROS_POR_PAGINA", 500),
        http_timeout_seconds=_env_float(env, "SIIFA_HTTP_TIMEOUT_SECONDS", 90.0),
        retry_max_attempts=_env_int(env, "SIIFA_RETRY_MAX_ATTEMPTS", 3),
        retry_base_delay_seconds=_env_float(env, "SIIFA_RETRY_BASE_DELAY_SECONDS", 1.0),
        login_bearer_token=env.get("SIIFA_LOGIN_BEARER_TOKEN") or None,
        nit_adquiriente_default=(env.get("SIIFA_NIT_ADQUIRIENTE") or "").strip() or None,
    )


def _extract_token(payload: dict[str, Any]) -> str | None:
    for key in ("token", "accessToken", "access_token", "jwt", "bearerToken"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        return _extract_token(data)
    return None


class SiifaExportClient:
    """Cliente mínimo SIIFA (solo login + consulta facturas)."""

    def __init__(self, cfg: SiifaEnv) -> None:
        self._cfg = cfg
        self._token: str | None = None

    def login(self) -> str:
        url = f"{self._cfg.seguridad_base_url.rstrip('/')}/api/Auth/login"
        headers = {"Content-Type": "application/json", "Accept": "text/plain"}
        if self._cfg.login_bearer_token:
            headers["Authorization"] = f"Bearer {self._cfg.login_bearer_token.strip()}"
        data = self._request_json(
            "POST",
            url,
            body={"userName": self._cfg.username, "password": self._cfg.password},
            headers=headers,
            auth_required=False,
        )
        token = _extract_token(data)
        if not token:
            raise ValueError("Login SIIFA no devolvió token (token/accessToken).")
        self._token = token
        return token

    def get_facturas_page(
        self,
        *,
        pagina_actual: int,
        registros_por_pagina: int,
        tiene_radicado: bool | None,
        nit_adquiriente: str | None,
    ) -> dict[str, Any]:
        if not self._token:
            self.login()
        url = f"{self._cfg.factura_base_url.rstrip('/')}/api/Factura"
        # SIIFA usa numeroPagina (paginaActual en la respuesta es solo informativo).
        params: dict[str, Any] = {
            "numeroPagina": pagina_actual,
            "registrosPorPagina": registros_por_pagina,
        }
        if tiene_radicado is not None:
            params["tieneRadicado"] = "true" if tiene_radicado else "false"
        nit = (nit_adquiriente or "").strip()
        if nit:
            params["nitAdquiriente"] = nit
        query = urllib.parse.urlencode(params)
        full_url = f"{url}?{query}"
        headers = {
            "Accept": "text/plain",
            "Authorization": f"Bearer {self._token}",
        }
        return self._request_json("GET", full_url, headers=headers)

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth_required: bool = True,
    ) -> dict[str, Any]:
        cfg = self._cfg
        last_exc: Exception | None = None
        hdrs = dict(headers or {})

        for attempt in range(1, cfg.retry_max_attempts + 1):
            try:
                data_bytes = None
                if body is not None:
                    data_bytes = json.dumps(body).encode("utf-8")
                    hdrs.setdefault("Content-Type", "application/json")

                req = urllib.request.Request(url, data=data_bytes, headers=hdrs, method=method)
                with urllib.request.urlopen(req, timeout=cfg.http_timeout_seconds) as resp:
                    raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("Respuesta SIIFA no es un objeto JSON.")
                return parsed

            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 401 and auth_required and attempt < cfg.retry_max_attempts:
                    self.login()
                    hdrs["Authorization"] = f"Bearer {self._token}"
                    time.sleep(cfg.retry_base_delay_seconds * (2 ** (attempt - 1)))
                    continue
                max_attempts = (
                    max(cfg.retry_max_attempts, 8)
                    if exc.code in _RETRIABLE_HTTP
                    else cfg.retry_max_attempts
                )
                if exc.code in _RETRIABLE_HTTP and attempt < max_attempts:
                    delay = min(60.0, cfg.retry_base_delay_seconds * (2 ** (attempt - 1)))
                    print(
                        f"REINTENTO: HTTP {exc.code} intento {attempt}/{max_attempts}, "
                        f"espera {delay:.0f}s — {url[:120]}...",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc
            except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
                last_exc = exc
                if attempt < cfg.retry_max_attempts:
                    time.sleep(cfg.retry_base_delay_seconds * (2 ** (attempt - 1)))
                    continue
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError("Petición SIIFA falló sin detalle.")


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


def _extract_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("resultado")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _factura_id(raw: dict[str, Any]) -> str:
    for key in ("idFactura", "IdFactura", "id_factura_siifa"):
        val = raw.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    numero = str(raw.get("numeroFactura") or "").strip()
    emisor = raw.get("emisor") if isinstance(raw.get("emisor"), dict) else {}
    nit = ""
    if isinstance(emisor, dict):
        nit = str(emisor.get("nitEmisor") or emisor.get("nit") or "").strip()
    if numero and nit:
        return f"{numero}|{nit}"
    return ""


def _progress_path(output: Path) -> Path:
    return output.with_name(output.name + ".progress.json")


def save_export_progress(output: Path, data: dict[str, Any]) -> None:
    path = _progress_path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_export_progress(output: Path) -> dict[str, Any] | None:
    path = _progress_path(output)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def load_seen_ids_from_export_csv(path: Path) -> set[str]:
    """Ids ya presentes en un CSV exportado (para reanudar sin duplicar filas)."""
    if not path.is_file():
        return set()
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return seen
        for row in reader:
            if not row:
                continue
            fid = _factura_id(row)
            if not fid:
                for key in ("id_factura_siifa", "idFactura", "IdFactura"):
                    val = row.get(key)
                    if val is not None and str(val).strip():
                        fid = str(val).strip()
                        break
            if fid:
                seen.add(fid)
    return seen


def yield_siifa_facturas_paginado(
    client: SiifaExportClient,
    *,
    registros_por_pagina: int,
    max_paginas: int,
    tiene_radicado: bool | None,
    nit_adquiriente: str | None,
    desde_pagina: int = 1,
    hasta_pagina: int = 0,
    pausa_segundos: float = 0.0,
    seen_inicial: set[str] | None = None,
) -> Any:
    """
    Generador de páginas SIIFA con deduplicación por idFactura.

    Yields tuplas (pagina, filas_nuevas_raw, meta_parcial).
    """
    seen: set[str] = set(seen_inicial) if seen_inicial else set()
    pagina = max(1, int(desde_pagina))
    total_paginas = pagina
    total_registros: int | None = None
    duplicados_omitidos = 0
    paginas_leidas = 0
    paginacion_repetida = False
    filas_nuevas_total = 0

    while pagina <= total_paginas:
        if hasta_pagina > 0 and pagina > hasta_pagina:
            break
        if max_paginas > 0 and (pagina - max(1, int(desde_pagina)) + 1) > max_paginas:
            break

        payload = client.get_facturas_page(
            pagina_actual=pagina,
            registros_por_pagina=registros_por_pagina,
            tiene_radicado=tiene_radicado,
            nit_adquiriente=nit_adquiriente,
        )
        paginas_leidas = pagina
        total_paginas = max(total_paginas, max(1, int(payload.get("totalPaginas") or 1)))
        if total_registros is None:
            total_registros = int(payload.get("totalRegistros") or 0)

        page_rows = _extract_rows(payload)
        nuevas_rows: list[dict[str, Any]] = []
        nuevas = 0
        for raw in page_rows:
            fid = _factura_id(raw)
            if not fid:
                fid = f"__sin_id_{pagina}_{filas_nuevas_total + nuevas}"
            if fid in seen:
                duplicados_omitidos += 1
                continue
            seen.add(fid)
            nuevas_rows.append(raw)
            nuevas += 1

        filas_nuevas_total += nuevas
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
        print(
            f"Página {pagina}/{total_paginas} — recibidas {len(page_rows)}, "
            f"nuevas {nuevas}, únicas acumuladas {len(seen)}",
            file=sys.stderr,
        )
        yield pagina, nuevas_rows, meta

        if not page_rows:
            break
        if pagina > 1 and nuevas == 0:
            paginacion_repetida = True
            print(
                "AVISO: SIIFA devolvió solo duplicados en esta página; "
                "se detiene la paginación (revise --nit-adquiriente).",
                file=sys.stderr,
            )
            break
        if total_registros and len(seen) >= total_registros:
            break

        if pausa_segundos > 0:
            time.sleep(pausa_segundos)
        pagina += 1

    meta_final = {
        "paginas_leidas": paginas_leidas,
        "total_paginas_siifa": total_paginas,
        "total_registros_siifa": total_registros or 0,
        "filas_unicas": len(seen),
        "duplicados_omitidos": duplicados_omitidos,
        "paginacion_repetida": paginacion_repetida,
        "nit_adquiriente": nit_adquiriente,
        "desde_pagina": max(1, int(desde_pagina)),
        "completado": paginas_leidas >= total_paginas and not paginacion_repetida,
    }
    yield None, [], meta_final


def iter_siifa_facturas_paginado(
    client: SiifaExportClient,
    *,
    registros_por_pagina: int,
    max_paginas: int,
    tiene_radicado: bool | None,
    nit_adquiriente: str | None,
    desde_pagina: int = 1,
    hasta_pagina: int = 0,
    pausa_segundos: float = 0.0,
    seen_inicial: set[str] | None = None,
) -> tuple[list[tuple[int, dict[str, Any]]], dict[str, Any]]:
    """
    Descarga facturas SIIFA con deduplicación por idFactura.

    SIIFA repite la misma página si falta nitAdquiriente o si se usa paginaActual
    en lugar de numeroPagina; se detiene cuando una página no aporta registros nuevos.
    """
    acumulado: list[tuple[int, dict[str, Any]]] = []
    meta: dict[str, Any] = {}
    for pagina, nuevas_rows, meta_parcial in yield_siifa_facturas_paginado(
        client,
        registros_por_pagina=registros_por_pagina,
        max_paginas=max_paginas,
        tiene_radicado=tiene_radicado,
        nit_adquiriente=nit_adquiriente,
        desde_pagina=desde_pagina,
        hasta_pagina=hasta_pagina,
        pausa_segundos=pausa_segundos,
        seen_inicial=seen_inicial,
    ):
        if pagina is None:
            meta = meta_parcial
            break
        for raw in nuevas_rows:
            acumulado.append((pagina, raw))
    return acumulado, meta


def _write_csv(path: Path, flat_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in flat_rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in flat_rows:
            writer.writerow({k: "" if row.get(k) is None else row.get(k) for k in fieldnames})


def export_facturas_csv(
    *,
    cfg: SiifaEnv,
    output: Path,
    registros_por_pagina: int,
    max_paginas: int,
    tiene_radicado: bool | None,
    nit_adquiriente: str | None,
) -> dict[str, Any]:
    nit = (nit_adquiriente or cfg.nit_adquiriente_default or "").strip() or None
    if not nit:
        print(
            "AVISO: exporte con --nit-adquiriente o SIIFA_NIT_ADQUIRIENTE en .env "
            "(sin NIT, SIIFA suele repetir la misma página).",
            file=sys.stderr,
        )

    client = SiifaExportClient(cfg)
    client.login()

    paginado, meta = iter_siifa_facturas_paginado(
        client,
        registros_por_pagina=registros_por_pagina,
        max_paginas=max_paginas,
        tiene_radicado=tiene_radicado,
        nit_adquiriente=nit,
    )

    all_flat: list[dict[str, Any]] = []
    for pagina, raw in paginado:
        flat = _flatten_record(raw)
        flat["pagina_siifa"] = pagina
        all_flat.append(flat)

    _write_csv(output, all_flat)

    return {
        "archivo": str(output.resolve()),
        "filas_exportadas": len(all_flat),
        "registros_por_pagina": registros_por_pagina,
        "filtro_tiene_radicado": tiene_radicado,
        **meta,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exportar facturas SIIFA a CSV (lectura únicamente, sin afectar la integración)."
    )
    filtro = parser.add_mutually_exclusive_group()
    filtro.add_argument(
        "--sin-radicar",
        dest="filtro",
        action="store_const",
        const="sin_radicar",
        help="Solo facturas sin radicar (TieneRadicado=false). Default.",
    )
    filtro.add_argument(
        "--radicadas",
        dest="filtro",
        action="store_const",
        const="radicadas",
        help="Solo facturas radicadas (TieneRadicado=true).",
    )
    filtro.add_argument(
        "--todas",
        dest="filtro",
        action="store_const",
        const="todas",
        help="Sin filtro TieneRadicado (todas las facturas que devuelva la API).",
    )
    parser.set_defaults(filtro="sin_radicar")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Ruta del CSV (default: <raíz>/export/siifa_facturas_<timestamp>.csv).",
    )
    parser.add_argument(
        "--registros-por-pagina",
        type=int,
        default=None,
        help="Tamaño de página (default: SIIFA_REGISTROS_POR_PAGINA del .env).",
    )
    parser.add_argument(
        "--max-paginas",
        type=int,
        default=0,
        help="Límite de páginas a descargar (0 = todas).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=f"Ruta al .env (default: {ROOT / '.env'}).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Muestra versión del script y ruta del .env.",
    )
    parser.add_argument(
        "--nit-adquiriente",
        default=None,
        help="Filtrar por NIT adquiriente (opcional).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    env_file = args.env_file.resolve() if args.env_file else (ROOT / ".env")

    if args.verbose:
        print(
            f"siifa_export_facturas_csv {SCRIPT_VERSION}\n"
            f"  script: {Path(__file__).resolve()}\n"
            f"  .env:   {env_file}",
            file=sys.stderr,
        )

    if args.filtro == "todas":
        tiene_radicado: bool | None = None
    elif args.filtro == "radicadas":
        tiene_radicado = True
    else:
        tiene_radicado = False

    try:
        cfg = load_siifa_env(env_file if args.env_file else None)
    except ValueError as exc:
        print(json.dumps({"estado": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    registros = args.registros_por_pagina or cfg.registros_por_pagina
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = "sin_radicar" if tiene_radicado is False else "radicadas" if tiene_radicado else "todas"
    default_output = ROOT / "export" / f"siifa_facturas_{suffix}_{stamp}.csv"
    output = args.output if args.output is not None else default_output
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()

    try:
        resumen = export_facturas_csv(
            cfg=cfg,
            output=output,
            registros_por_pagina=registros,
            max_paginas=max(0, int(args.max_paginas)),
            tiene_radicado=tiene_radicado,
            nit_adquiriente=args.nit_adquiriente,
        )
        print(json.dumps(resumen, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"estado": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
