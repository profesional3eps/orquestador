"""Backfill ERP (PostgreSQL) desde CSV de facturas SIIFA con seguimiento."""

from __future__ import annotations

import csv
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.core.database import PostgresSessionLocal, SqlServerSessionLocal
from app.models.siifa_radicacion import (
    EstadoProcesoFactura,
    FacturaSiifaItem,
    MetricasEjecucion,
    ResultadoTraza,
)
from app.repositories.siifa_postgres_repository import PostgreSQLRepository
from app.repositories.siifa_sqlserver_repository import SQLServerRepository

logger = logging.getLogger(__name__)

_CSV_ID_COLS = ("id_factura_siifa", "idFactura", "id_factura")
_CSV_RADICADO_COLS = ("radicado_siifa", "radicado", "numero_radicado")
_CSV_NUMERO_COLS = ("numero_factura", "numeroFactura")
_CSV_NIT_COLS = ("nit_emisor", "emisor_nitEmisor", "nitEmisor", "emisor_nit")
_CSV_ID_RADICADO_COLS = ("id_factura_radicado_siifa", "idFacturaRadicado")


@dataclass(frozen=True)
class FacturaSeguimientoCsv:
    id_factura_siifa: int
    radicado_siifa: int
    numero_factura: str
    nit_emisor: str
    id_factura_radicado_siifa: int | None = None


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


def leer_facturas_seguimiento_csv(csv_path: Path) -> list[FacturaSeguimientoCsv]:
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"No existe el archivo CSV: {path}")

    delim = _detectar_delimitador(path)
    items: list[FacturaSeguimientoCsv] = []
    omitidas = 0

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delim)
        if not reader.fieldnames:
            raise ValueError(f"CSV sin encabezados: {path}")
        for row in reader:
            if not row:
                continue
            norm = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            id_raw = _csv_valor(norm, _CSV_ID_COLS)
            rad_raw = _csv_valor(norm, _CSV_RADICADO_COLS)
            numero = _csv_valor(norm, _CSV_NUMERO_COLS)
            nit = _csv_valor(norm, _CSV_NIT_COLS)
            id_rad_raw = _csv_valor(norm, _CSV_ID_RADICADO_COLS)

            if not id_raw or not rad_raw or not numero or not nit:
                omitidas += 1
                continue
            try:
                id_rad = int(id_rad_raw) if id_rad_raw else None
                items.append(
                    FacturaSeguimientoCsv(
                        id_factura_siifa=int(id_raw),
                        radicado_siifa=int(float(rad_raw)),
                        numero_factura=numero,
                        nit_emisor=nit,
                        id_factura_radicado_siifa=id_rad,
                    )
                )
            except (TypeError, ValueError):
                omitidas += 1

    if not items:
        raise ValueError(f"CSV sin filas válidas: {path}")
    if omitidas:
        logger.warning("siifa_seguimiento_csv_filas_omitidas archivo=%s cantidad=%s", path, omitidas)
    return items


def _parse_fecha(fecha: str | date | datetime) -> datetime:
    if isinstance(fecha, datetime):
        return fecha.replace(tzinfo=None) if fecha.tzinfo else fecha
    if isinstance(fecha, date):
        return datetime.combine(fecha, datetime.min.time())
    raw = str(fecha).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:10], fmt)
        except ValueError:
            continue
    raise ValueError(f"Fecha inválida: {fecha!r}. Use YYYY-MM-DD.")


class SeguimientoErpService:
    """Sincroniza rips_af desde CSV SIIFA con seguimiento y traza SQL Server."""

    def __init__(
        self,
        settings: Settings,
        *,
        pg_session_factory: sessionmaker[Session] = PostgresSessionLocal,
        sql_session_factory: sessionmaker[Session] = SqlServerSessionLocal,
    ) -> None:
        self._settings = settings
        self._pg_factory = pg_session_factory
        self._sql_factory = sql_session_factory

    def ejecutar_desde_csv(
        self,
        csv_path: Path | str,
        *,
        fecha_rad_siifa: str | date | datetime = "2026-04-10",
        tipo_ejecucion: str = "CSV_SEGUIMIENTO_ERP",
        usuario: str | None = None,
        reiniciar_lote: bool = False,
        max_filas: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        path = Path(csv_path)
        fecha_erp = _parse_fecha(fecha_rad_siifa)
        inicio = time.perf_counter()
        workers = max(1, int(self._settings.siifa_workers))
        proceso = SQLServerRepository.PROCESO_LOTE_SEGUIMIENTO_ERP
        filas_por_lote = max_filas if max_filas and max_filas > 0 else 5000

        todas = leer_facturas_seguimiento_csv(path)
        total_filas = len(todas)

        sql_main = self._sql_factory()
        sql_repo = SQLServerRepository(sql_main)
        id_ejecucion: int | None = None
        if not dry_run:
            id_ejecucion = sql_repo.iniciar_ejecucion(
                tipo_ejecucion=tipo_ejecucion,
                workers=workers,
                usuario=usuario,
            )

        metricas = MetricasEjecucion()
        fila_inicio = 1
        fila_fin = 0
        proxima_fila = 1
        lote_completado = False
        filas_procesadas = 0
        estado_final = "OK"

        try:
            checkpoint = sql_repo.obtener_checkpoint_lote(proceso=proceso)
            if reiniciar_lote and not dry_run:
                sql_repo.reiniciar_checkpoint_lote(id_ejecucion=id_ejecucion, proceso=proceso)
                sql_repo.commit()
                checkpoint = sql_repo.obtener_checkpoint_lote(proceso=proceso)
            elif checkpoint.lote_completado and not dry_run:
                sql_repo.reiniciar_checkpoint_lote(id_ejecucion=id_ejecucion, proceso=proceso)
                sql_repo.commit()
                checkpoint = sql_repo.obtener_checkpoint_lote(proceso=proceso)

            fila_inicio = 1 if dry_run else checkpoint.proxima_pagina
            if fila_inicio > total_filas:
                lote_completado = True
                proxima_fila = 1
            else:
                fila_fin = min(fila_inicio + filas_por_lote - 1, total_filas)
                lote = todas[fila_inicio - 1 : fila_fin]
                logger.info(
                    "siifa_seguimiento_erp_inicio id_ejecucion=%s archivo=%s fila_inicio=%s "
                    "fila_fin=%s total=%s fecha_rad_siifa=%s dry_run=%s",
                    id_ejecucion,
                    path,
                    fila_inicio,
                    fila_fin,
                    total_filas,
                    fecha_erp.date(),
                    dry_run,
                )
                self._procesar_lote(lote, fila_inicio, id_ejecucion, workers, metricas, fecha_erp, dry_run)
                filas_procesadas = len(lote)
                lote_completado = fila_fin >= total_filas
                proxima_fila = 1 if lote_completado else fila_fin + 1

                if not dry_run:
                    sql_repo.guardar_checkpoint_lote(
                        ultima_pagina=fila_fin,
                        total_paginas=total_filas,
                        total_registros=total_filas,
                        lote_completado=lote_completado,
                        id_ejecucion=id_ejecucion,
                        proceso=proceso,
                    )
                    sql_repo.commit()

            if metricas.errores > 0 and metricas.radicadas > 0:
                estado_final = "PARCIAL"
            elif metricas.errores > 0 and metricas.radicadas == 0 and filas_procesadas > 0:
                estado_final = "ERROR"
            if dry_run:
                estado_final = "OK"

        except Exception as exc:
            estado_final = "ERROR"
            metricas.advertencias.append(f"Error fatal: {exc}")
            logger.exception("siifa_seguimiento_erp_fatal id_ejecucion=%s", id_ejecucion)
            raise
        finally:
            duracion_ms = int((time.perf_counter() - inicio) * 1000)
            try:
                detalle = metricas.to_dict()
                detalle.update(
                    {
                        "origen": "csv_seguimiento",
                        "archivo_csv": str(path),
                        "fecha_rad_siifa": str(fecha_erp.date()),
                        "fila_inicio": fila_inicio,
                        "fila_fin": fila_fin,
                        "proxima_fila": proxima_fila,
                        "lote_completado": lote_completado,
                        "filas_por_lote": filas_por_lote,
                        "dry_run": dry_run,
                    }
                )
                if not dry_run:
                    sql_repo.finalizar_ejecucion(
                        id_ejecucion,
                        estado=estado_final,
                        metricas=detalle,
                        total_registros_siifa=total_filas,
                        total_paginas=total_filas,
                        duracion_ms=duracion_ms,
                    )
            except Exception:
                logger.exception("siifa_seguimiento_erp_finalizar_fallo id_ejecucion=%s", id_ejecucion)
            finally:
                sql_main.close()

        return {
            "id_ejecucion": id_ejecucion,
            "estado": estado_final,
            "duracion_ms": duracion_ms,
            "origen": "csv_seguimiento",
            "archivo_csv": str(path),
            "fecha_rad_siifa": str(fecha_erp.date()),
            "fila_inicio": fila_inicio,
            "fila_fin": fila_fin,
            "proxima_fila": proxima_fila,
            "lote_completado": lote_completado,
            "requiere_siguiente_lote": not lote_completado and not dry_run,
            "filas_procesadas": filas_procesadas,
            "filas_por_lote": filas_por_lote,
            "total_filas_csv": total_filas,
            "dry_run": dry_run,
            "workers": workers,
            **metricas.to_dict(),
        }

    def _procesar_lote(
        self,
        items: list[FacturaSeguimientoCsv],
        fila_base: int,
        id_ejecucion: int,
        workers: int,
        metricas: MetricasEjecucion,
        fecha_erp: datetime,
        dry_run: bool,
    ) -> None:
        lock = threading.Lock()

        def _worker(item: FacturaSeguimientoCsv, idx: int) -> None:
            res = self._procesar_fila(item, fila_base + idx, id_ejecucion, fecha_erp, dry_run)
            with lock:
                metricas.procesadas += 1
                if res == EstadoProcesoFactura.RADICADA:
                    metricas.radicadas += 1
                elif res == EstadoProcesoFactura.NO_ENCONTRADA_ERP:
                    metricas.no_encontradas_erp += 1
                elif res == EstadoProcesoFactura.OMITIDA:
                    metricas.omitidas += 1
                else:
                    metricas.errores += 1

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_worker, item, i) for i, item in enumerate(items)]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    with lock:
                        metricas.errores += 1
                        metricas.advertencias.append(str(exc))

    def _procesar_fila(
        self,
        row: FacturaSeguimientoCsv,
        fila: int,
        id_ejecucion: int,
        fecha_erp: datetime,
        dry_run: bool,
    ) -> EstadoProcesoFactura:
        item = FacturaSiifaItem(
            id_factura=row.id_factura_siifa,
            numero_factura=row.numero_factura,
            nit_emisor=row.nit_emisor,
        )

        pg = self._pg_factory()
        sql = self._sql_factory()
        pg_repo = PostgreSQLRepository(pg)
        sql_repo = SQLServerRepository(sql)

        try:
            match = pg_repo.buscar_rips_af(row.numero_factura, row.nit_emisor)
            if not match:
                if not dry_run:
                    self._registrar_no_encontrada(sql_repo, item, id_ejecucion, fila)
                    sql_repo.commit()
                return EstadoProcesoFactura.NO_ENCONTRADA_ERP

            if match.idfactura_siifa and str(match.idfactura_siifa).strip() == str(row.id_factura_siifa):
                if not dry_run:
                    sql_repo.upsert_factura_siifa(
                        item,
                        id_ejecucion=id_ejecucion,
                        pagina=fila,
                        estado=EstadoProcesoFactura.OMITIDA,
                        observacion="Ya sincronizada con mismo idfactura_siifa",
                    )
                    sql_repo.registrar_traza(
                        id_ejecucion=id_ejecucion,
                        id_factura_siifa=row.id_factura_siifa,
                        numero_factura=row.numero_factura,
                        nit_emisor=row.nit_emisor,
                        paso="VALIDACION_PREVIA",
                        resultado=ResultadoTraza.OMITIDA,
                        mensaje="ERP ya tenía idfactura_siifa",
                    )
                    sql_repo.commit()
                return EstadoProcesoFactura.OMITIDA

            resumen = pg_repo.buscar_rips_resumen(match.consecutivo_rips)
            radica_erp = resumen.radica_rips if resumen else str(row.radicado_siifa)
            fecha_radica_erp = resumen.fecha_radica if resumen and resumen.fecha_radica else fecha_erp
            estado_erp = resumen.estado if resumen else None

            if dry_run:
                pg_repo.rollback()
                return EstadoProcesoFactura.RADICADA

            radicado_guardado = pg_repo.actualizar_siifa_erp_desde_seguimiento(
                consecutivo_rips_af=match.consecutivo_rips_af,
                id_factura_siifa=row.id_factura_siifa,
                radicado_siifa=row.radicado_siifa,
                fecha_rad_siifa=fecha_erp,
            )
            pg_repo.commit()

            if radicado_guardado != row.radicado_siifa:
                logger.warning(
                    "radicado_siifa_ajustado_smallint id_factura=%s valor_csv=%s valor_erp=%s",
                    row.id_factura_siifa,
                    row.radicado_siifa,
                    radicado_guardado,
                )

            id_radicado_siifa = row.id_factura_radicado_siifa or row.radicado_siifa
            respuesta = {
                "idFactura": row.id_factura_siifa,
                "idFacturaRadicado": id_radicado_siifa,
                "numeroFactura": row.numero_factura,
                "nitEmisor": row.nit_emisor,
                "radicado": str(row.radicado_siifa),
                "fechaRadicado": str(fecha_erp.date()),
                "origen": "CSV_SEGUIMIENTO_ERP",
            }

            sql_repo.upsert_factura_siifa(
                item,
                id_ejecucion=id_ejecucion,
                pagina=fila,
                estado=EstadoProcesoFactura.RADICADA,
                observacion="Sincronizada desde CSV con seguimiento SIIFA",
            )
            sql_repo.registrar_factura_erp(
                id_factura_siifa=row.id_factura_siifa,
                consecutivo_rips_af=match.consecutivo_rips_af,
                consecutivo_rips=match.consecutivo_rips,
                numero_factura=row.numero_factura,
                nit_prestador=row.nit_emisor,
                estado_erp=estado_erp,
                radica_rips=radica_erp,
                fecha_radica=fecha_radica_erp,
                resultado="RADICADA",
                mensaje="OK backfill CSV seguimiento",
            )
            sql_repo.registrar_radicado(
                id_factura_siifa=row.id_factura_siifa,
                radicado_numero=str(row.radicado_siifa),
                fecha_radicacion=fecha_erp,
                estado="EXITOSO",
                id_factura_radicado_siifa=id_radicado_siifa,
                respuesta_json=json.dumps(respuesta, ensure_ascii=False, default=str),
                sincronizado_erp=True,
            )
            sql_repo.registrar_traza(
                id_ejecucion=id_ejecucion,
                id_factura_siifa=row.id_factura_siifa,
                numero_factura=row.numero_factura,
                nit_emisor=row.nit_emisor,
                paso="COMPLETADO",
                resultado=ResultadoTraza.OK,
                mensaje="Actualización ERP desde CSV seguimiento (equivalente API)",
                detalle={
                    "idFacturaRadicado": id_radicado_siifa,
                    "radicado": row.radicado_siifa,
                    "fecha_rad_siifa": str(fecha_erp.date()),
                    "radicado_siifa_erp": radicado_guardado,
                },
            )
            sql_repo.commit()
            return EstadoProcesoFactura.RADICADA

        except Exception:
            pg_repo.rollback()
            sql_repo.rollback()
            raise
        finally:
            pg.close()
            sql.close()

    @staticmethod
    def _registrar_no_encontrada(
        sql_repo: SQLServerRepository,
        item: FacturaSiifaItem,
        id_ejecucion: int,
        fila: int,
    ) -> None:
        sql_repo.upsert_factura_siifa(
            item,
            id_ejecucion=id_ejecucion,
            pagina=fila,
            estado=EstadoProcesoFactura.NO_ENCONTRADA_ERP,
            observacion="NO ENCONTRADA en ERP",
        )
        sql_repo.registrar_traza(
            id_ejecucion=id_ejecucion,
            id_factura_siifa=item.id_factura,
            numero_factura=item.numero_factura,
            nit_emisor=item.nit_emisor,
            paso="BUSCAR_RIPS_AF",
            resultado=ResultadoTraza.NO_ENCONTRADA,
            mensaje="Sin coincidencia en administrativo.rips_af",
        )
