"""Servicio de radicación SIIFA: consulta facturas sin radicar y sincroniza con ERP."""

from __future__ import annotations

import csv
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.core.database import PostgresSessionLocal, SqlServerSessionLocal
from app.models.siifa_radicacion import (
    EstadoProcesoFactura,
    FacturaSiifaItem,
    MetricasEjecucion,
    ProcesoFacturaResultado,
    RadicadoSiifaRequest,
    ResultadoTraza,
)
from app.repositories.siifa_postgres_repository import PostgreSQLRepository
from app.repositories.siifa_sqlserver_repository import SQLServerRepository
from app.services.siifa_client import SIIFAClient

logger = logging.getLogger(__name__)

_CSV_ID_COLS = ("id_factura_siifa", "idFactura", "id_factura")
_CSV_NUMERO_COLS = ("numero_factura", "numeroFactura")
_CSV_NIT_COLS = ("nit_emisor", "emisor_nitEmisor", "nitEmisor", "emisor_nit")


def _csv_valor(row: dict[str, str], keys: Iterable[str]) -> str:
    for key in keys:
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def leer_facturas_desde_csv(csv_path: Path) -> list[FacturaSiifaItem]:
    """Carga facturas SIIFA desde CSV exportado (sin seguimiento)."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"No existe el archivo CSV: {path}")

    items: list[FacturaSiifaItem] = []
    omitidas = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV sin encabezados: {path}")
        for row in reader:
            if not row:
                continue
            norm = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            id_raw = _csv_valor(norm, _CSV_ID_COLS)
            numero = _csv_valor(norm, _CSV_NUMERO_COLS)
            nit = _csv_valor(norm, _CSV_NIT_COLS)
            if not id_raw or not numero or not nit:
                omitidas += 1
                continue
            try:
                items.append(
                    FacturaSiifaItem(
                        id_factura=int(id_raw),
                        numero_factura=numero,
                        nit_emisor=nit,
                    )
                )
            except (TypeError, ValueError):
                omitidas += 1

    if not items:
        raise ValueError(f"CSV sin filas válidas: {path}")
    if omitidas:
        logger.warning("siifa_csv_filas_omitidas archivo=%s cantidad=%s", path, omitidas)
    return items


def fecha_erp_a_iso_utc(fecha: datetime) -> str:
    """Convierte timestamp ERP (naive local) a ISO UTC para SIIFA."""
    if fecha.tzinfo is None:
        utc = fecha.replace(tzinfo=timezone.utc)
    else:
        utc = fecha.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.0Z")


class RadicacionService:
    """Orquesta paginación SIIFA, búsqueda ERP y radicación concurrente."""

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
        self._client = SIIFAClient(settings)
        self._client_lock = threading.Lock()

    def ejecutar_sincronizacion(
        self,
        *,
        tipo_ejecucion: str = "MANUAL_API",
        usuario: str | None = None,
        reprocesar_fallidos: bool | None = None,
        reiniciar_lote: bool = False,
        max_paginas: int | None = None,
        modo_lote: bool | None = None,
        un_solo_lote: bool = False,
    ) -> dict[str, Any]:
        inicio = time.perf_counter()
        workers = max(1, int(self._settings.siifa_workers))
        usar_lote = (
            True
            if un_solo_lote
            else (modo_lote if modo_lote is not None else bool(self._settings.siifa_modo_lote))
        )
        if un_solo_lote and max_paginas is None:
            max_paginas = (
                self._settings.siifa_api_paginas_por_lote
                or self._settings.siifa_lote_paginas_por_ejecucion
            )
        reprocesar = (
            reprocesar_fallidos
            if reprocesar_fallidos is not None
            else (
                self._settings.siifa_api_reprocesar_fallidos
                if un_solo_lote
                else self._settings.siifa_reprocesar_fallidos
            )
        )
        paginas_por_lote = self._resolver_paginas_por_ejecucion(max_paginas, usar_lote)
        if un_solo_lote:
            paginas_por_lote = max(1, paginas_por_lote)

        sql_main = self._sql_factory()
        sql_repo = SQLServerRepository(sql_main)
        id_ejecucion = sql_repo.iniciar_ejecucion(
            tipo_ejecucion=tipo_ejecucion,
            workers=workers,
            usuario=usuario,
        )

        metricas = MetricasEjecucion()
        total_registros: int | None = None
        total_paginas: int | None = None
        paginas_procesadas = 0
        pagina_inicio = 1
        pagina_fin = 1
        proxima_pagina = 1
        lote_completado = False
        estado_final = "OK"

        try:
            if reprocesar:
                reprocesados = self._reprocesar_fallidos(id_ejecucion, sql_repo, metricas, workers)
                metricas.procesadas += reprocesados

            self._client.login()
            reg_por_pagina = int(self._settings.siifa_registros_por_pagina)

            if usar_lote:
                checkpoint = sql_repo.obtener_checkpoint_lote()
                if reiniciar_lote:
                    sql_repo.reiniciar_checkpoint_lote(id_ejecucion=id_ejecucion)
                    sql_repo.commit()
                    checkpoint = sql_repo.obtener_checkpoint_lote()
                    logger.info("siifa_lote_reiniciado id_ejecucion=%s", id_ejecucion)
                elif checkpoint.lote_completado:
                    logger.info(
                        "siifa_lote_ciclo_previo_completado reiniciando_desde_pagina_1 id_ejecucion=%s",
                        id_ejecucion,
                    )
                    sql_repo.reiniciar_checkpoint_lote(id_ejecucion=id_ejecucion)
                    sql_repo.commit()
                    checkpoint = sql_repo.obtener_checkpoint_lote()

                pagina_inicio = checkpoint.proxima_pagina

            primera = self._client.get_facturas_sin_radicar(
                pagina_actual=pagina_inicio,
                registros_por_pagina=reg_por_pagina,
            )
            total_registros = int(primera.get("totalRegistros") or 0)
            total_paginas = max(1, int(primera.get("totalPaginas") or 1))

            if usar_lote:
                pagina_fin = min(pagina_inicio + paginas_por_lote - 1, total_paginas)
            else:
                limite = total_paginas if paginas_por_lote <= 0 else min(total_paginas, paginas_por_lote)
                pagina_inicio = 1
                pagina_fin = limite

            if pagina_inicio > total_paginas:
                lote_completado = True
                proxima_pagina = 1
                sql_repo.guardar_checkpoint_lote(
                    ultima_pagina=total_paginas,
                    total_paginas=total_paginas,
                    total_registros=total_registros,
                    lote_completado=True,
                    id_ejecucion=id_ejecucion,
                )
                sql_repo.commit()
                logger.info(
                    "siifa_lote_sin_pendientes total_paginas=%s id_ejecucion=%s",
                    total_paginas,
                    id_ejecucion,
                )
            else:
                logger.info(
                    "siifa_radicacion_inicio id_ejecucion=%s modo_lote=%s pagina_inicio=%s pagina_fin=%s "
                    "total_registros=%s total_paginas=%s workers=%s",
                    id_ejecucion,
                    usar_lote,
                    pagina_inicio,
                    pagina_fin,
                    total_registros,
                    total_paginas,
                    workers,
                )

                for pagina in range(pagina_inicio, pagina_fin + 1):
                    payload = primera if pagina == pagina_inicio else self._client.get_facturas_sin_radicar(
                        pagina_actual=pagina,
                        registros_por_pagina=reg_por_pagina,
                    )
                    items = self._extraer_items(payload)
                    self._procesar_pagina(items, pagina, id_ejecucion, workers, metricas)
                    paginas_procesadas += 1

                    lote_completado = pagina >= total_paginas
                    proxima_pagina = 1 if lote_completado else pagina + 1
                    sql_repo.guardar_checkpoint_lote(
                        ultima_pagina=pagina,
                        total_paginas=total_paginas,
                        total_registros=total_registros,
                        lote_completado=lote_completado,
                        id_ejecucion=id_ejecucion,
                    )
                    sql_repo.commit()
                    logger.info(
                        "siifa_lote_pagina_ok pagina=%s/%s lote_completado=%s id_ejecucion=%s",
                        pagina,
                        total_paginas,
                        lote_completado,
                        id_ejecucion,
                    )

            if metricas.errores > 0 and metricas.radicadas > 0:
                estado_final = "PARCIAL"
            elif metricas.errores > 0 and metricas.radicadas == 0 and paginas_procesadas > 0:
                estado_final = "ERROR"

        except Exception as exc:
            estado_final = "ERROR"
            metricas.advertencias.append(f"Error fatal: {exc}")
            logger.exception("siifa_radicacion_fatal id_ejecucion=%s", id_ejecucion)
            raise
        finally:
            duracion_ms = int((time.perf_counter() - inicio) * 1000)
            try:
                detalle = metricas.to_dict()
                detalle.update(
                    {
                        "modo_lote": usar_lote,
                        "pagina_inicio": pagina_inicio,
                        "pagina_fin": pagina_fin,
                        "proxima_pagina": proxima_pagina,
                        "lote_completado": lote_completado,
                        "paginas_por_lote": paginas_por_lote,
                    }
                )
                sql_repo.finalizar_ejecucion(
                    id_ejecucion,
                    estado=estado_final,
                    metricas=detalle,
                    total_registros_siifa=total_registros,
                    total_paginas=total_paginas,
                    duracion_ms=duracion_ms,
                )
            except Exception:
                logger.exception("siifa_finalizar_ejecucion_fallo id_ejecucion=%s", id_ejecucion)
            finally:
                sql_main.close()

        return {
            "id_ejecucion": id_ejecucion,
            "estado": estado_final,
            "duracion_ms": duracion_ms,
            "modo_lote": usar_lote,
            "un_solo_lote": un_solo_lote,
            "pagina_inicio": pagina_inicio,
            "pagina_fin": pagina_fin,
            "proxima_pagina": proxima_pagina,
            "lote_completado": lote_completado,
            "requiere_siguiente_lote": usar_lote and not lote_completado,
            "paginas_procesadas": paginas_procesadas,
            "paginas_por_lote": paginas_por_lote,
            "total_paginas_reportadas_siifa": total_paginas or 0,
            "total_registros_reportados_siifa": total_registros or 0,
            "paginas_limite_aplicado": paginas_por_lote > 0,
            "workers": workers,
            **metricas.to_dict(),
        }

    def ejecutar_desde_csv(
        self,
        csv_path: Path | str,
        *,
        tipo_ejecucion: str = "CSV_BATCH",
        usuario: str | None = None,
        reprocesar_fallidos: bool | None = None,
        reiniciar_lote: bool = False,
        max_filas: int | None = None,
        un_solo_lote: bool = True,
    ) -> dict[str, Any]:
        """
        Procesa facturas desde CSV (sin consultar listado SIIFA).

        Por cada fila: busca en PostgreSQL (rips_af / rips_resumen estado=5),
        radica en SIIFA y actualiza rips_af (radicado_siifa, fecha_rad_siifa,
        idfactura_siifa). Auditoría y reintentos en SQL Server (misma lógica
        que scripts/siifa_radicacion_sync.py y export/siifa_radicacion_sin_seguimiento_api.py).
        """
        path = Path(csv_path)
        inicio = time.perf_counter()
        workers = max(1, int(self._settings.siifa_workers))
        proceso_csv = SQLServerRepository.PROCESO_LOTE_CSV
        reprocesar = (
            reprocesar_fallidos
            if reprocesar_fallidos is not None
            else self._settings.siifa_reprocesar_fallidos
        )
        filas_por_lote = self._resolver_filas_por_ejecucion_csv(max_filas, un_solo_lote)

        todas = leer_facturas_desde_csv(path)
        total_filas = len(todas)

        sql_main = self._sql_factory()
        sql_repo = SQLServerRepository(sql_main)
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
            if reprocesar:
                reprocesados = self._reprocesar_fallidos(id_ejecucion, sql_repo, metricas, workers)
                metricas.procesadas += reprocesados

            checkpoint = sql_repo.obtener_checkpoint_lote(proceso=proceso_csv)
            if reiniciar_lote:
                sql_repo.reiniciar_checkpoint_lote(id_ejecucion=id_ejecucion, proceso=proceso_csv)
                sql_repo.commit()
                checkpoint = sql_repo.obtener_checkpoint_lote(proceso=proceso_csv)
                logger.info("siifa_csv_lote_reiniciado id_ejecucion=%s archivo=%s", id_ejecucion, path)
            elif checkpoint.lote_completado:
                sql_repo.reiniciar_checkpoint_lote(id_ejecucion=id_ejecucion, proceso=proceso_csv)
                sql_repo.commit()
                checkpoint = sql_repo.obtener_checkpoint_lote(proceso=proceso_csv)
                logger.info(
                    "siifa_csv_ciclo_previo_completado reiniciando_desde_fila_1 id_ejecucion=%s",
                    id_ejecucion,
                )

            fila_inicio = checkpoint.proxima_pagina
            if fila_inicio > total_filas:
                lote_completado = True
                proxima_fila = 1
                sql_repo.guardar_checkpoint_lote(
                    ultima_pagina=total_filas,
                    total_paginas=total_filas,
                    total_registros=total_filas,
                    lote_completado=True,
                    id_ejecucion=id_ejecucion,
                    proceso=proceso_csv,
                )
                sql_repo.commit()
                logger.info(
                    "siifa_csv_sin_pendientes total_filas=%s id_ejecucion=%s archivo=%s",
                    total_filas,
                    id_ejecucion,
                    path,
                )
            else:
                fila_fin = (
                    total_filas
                    if filas_por_lote <= 0
                    else min(fila_inicio + filas_por_lote - 1, total_filas)
                )
                self._client.login()
                logger.info(
                    "siifa_csv_inicio id_ejecucion=%s archivo=%s fila_inicio=%s fila_fin=%s "
                    "total_filas=%s workers=%s",
                    id_ejecucion,
                    path,
                    fila_inicio,
                    fila_fin,
                    total_filas,
                    workers,
                )

                lote_items = todas[fila_inicio - 1 : fila_fin]
                self._procesar_pagina(lote_items, pagina=fila_inicio, id_ejecucion=id_ejecucion, workers=workers, metricas=metricas)
                filas_procesadas = len(lote_items)

                lote_completado = fila_fin >= total_filas
                proxima_fila = 1 if lote_completado else fila_fin + 1
                sql_repo.guardar_checkpoint_lote(
                    ultima_pagina=fila_fin,
                    total_paginas=total_filas,
                    total_registros=total_filas,
                    lote_completado=lote_completado,
                    id_ejecucion=id_ejecucion,
                    proceso=proceso_csv,
                )
                sql_repo.commit()
                logger.info(
                    "siifa_csv_lote_ok fila_fin=%s/%s lote_completado=%s id_ejecucion=%s",
                    fila_fin,
                    total_filas,
                    lote_completado,
                    id_ejecucion,
                )

            if metricas.errores > 0 and metricas.radicadas > 0:
                estado_final = "PARCIAL"
            elif metricas.errores > 0 and metricas.radicadas == 0 and filas_procesadas > 0:
                estado_final = "ERROR"

        except Exception as exc:
            estado_final = "ERROR"
            metricas.advertencias.append(f"Error fatal: {exc}")
            logger.exception("siifa_csv_fatal id_ejecucion=%s archivo=%s", id_ejecucion, path)
            raise
        finally:
            duracion_ms = int((time.perf_counter() - inicio) * 1000)
            try:
                detalle = metricas.to_dict()
                detalle.update(
                    {
                        "origen": "csv",
                        "archivo_csv": str(path),
                        "fila_inicio": fila_inicio,
                        "fila_fin": fila_fin,
                        "proxima_fila": proxima_fila,
                        "lote_completado": lote_completado,
                        "filas_por_lote": filas_por_lote,
                        "total_filas_csv": total_filas,
                    }
                )
                sql_repo.finalizar_ejecucion(
                    id_ejecucion,
                    estado=estado_final,
                    metricas=detalle,
                    total_registros_siifa=total_filas,
                    total_paginas=total_filas,
                    duracion_ms=duracion_ms,
                )
            except Exception:
                logger.exception("siifa_csv_finalizar_ejecucion_fallo id_ejecucion=%s", id_ejecucion)
            finally:
                sql_main.close()

        return {
            "id_ejecucion": id_ejecucion,
            "estado": estado_final,
            "duracion_ms": duracion_ms,
            "origen": "csv",
            "archivo_csv": str(path),
            "fila_inicio": fila_inicio,
            "fila_fin": fila_fin,
            "proxima_fila": proxima_fila,
            "lote_completado": lote_completado,
            "requiere_siguiente_lote": not lote_completado,
            "filas_procesadas": filas_procesadas,
            "filas_por_lote": filas_por_lote,
            "total_filas_csv": total_filas,
            "workers": workers,
            **metricas.to_dict(),
        }

    def _resolver_filas_por_ejecucion_csv(self, max_filas: int | None, un_solo_lote: bool) -> int:
        if max_filas is not None and max_filas > 0:
            return max_filas
        if un_solo_lote:
            pag = int(self._settings.siifa_lote_paginas_por_ejecucion)
            reg = int(self._settings.siifa_registros_por_pagina)
            return max(1, pag * reg)
        cfg_max = int(self._settings.siifa_max_paginas)
        if cfg_max > 0:
            reg = int(self._settings.siifa_registros_por_pagina)
            return max(1, cfg_max * reg)
        return 0

    def _resolver_paginas_por_ejecucion(self, max_paginas: int | None, usar_lote: bool) -> int:
        if max_paginas is not None and max_paginas > 0:
            return max_paginas
        cfg_max = int(self._settings.siifa_max_paginas)
        if cfg_max > 0:
            return cfg_max
        if usar_lote:
            return int(self._settings.siifa_lote_paginas_por_ejecucion)
        return 0

    def _extraer_items(self, payload: dict[str, Any]) -> list[FacturaSiifaItem]:
        resultado: list[FacturaSiifaItem] = []
        for raw in payload.get("resultado") or []:
            if not isinstance(raw, dict):
                continue
            try:
                resultado.append(FacturaSiifaItem.from_api(raw))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("siifa_item_invalido error=%s raw=%s", exc, raw)
        return resultado

    def _procesar_pagina(
        self,
        items: list[FacturaSiifaItem],
        pagina: int,
        id_ejecucion: int,
        workers: int,
        metricas: MetricasEjecucion,
    ) -> None:
        if not items:
            return

        lock = threading.Lock()

        def _worker(item: FacturaSiifaItem) -> ProcesoFacturaResultado:
            return self._procesar_factura(item, pagina, id_ejecucion)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_worker, item): item for item in items}
            for future in as_completed(futures):
                try:
                    res = future.result()
                except Exception as exc:
                    item = futures[future]
                    res = ProcesoFacturaResultado(
                        id_factura_siifa=item.id_factura,
                        numero_factura=item.numero_factura,
                        nit_emisor=item.nit_emisor,
                        estado=EstadoProcesoFactura.ERROR,
                        mensaje=str(exc),
                    )
                    logger.exception("siifa_worker_fallo id_factura=%s", item.id_factura)

                with lock:
                    metricas.procesadas += 1
                    if res.estado == EstadoProcesoFactura.RADICADA:
                        metricas.radicadas += 1
                    elif res.estado == EstadoProcesoFactura.NO_ENCONTRADA_ERP:
                        metricas.no_encontradas_erp += 1
                    elif res.estado == EstadoProcesoFactura.NO_RADICADA_ERP:
                        metricas.no_radicadas_erp += 1
                    elif res.estado == EstadoProcesoFactura.OMITIDA:
                        metricas.omitidas += 1
                    else:
                        metricas.errores += 1
                    if res.mensaje:
                        metricas.advertencias.append(
                            f"idFactura={res.id_factura_siifa}: {res.mensaje}"
                        )

    def _procesar_factura(
        self,
        item: FacturaSiifaItem,
        pagina: int,
        id_ejecucion: int,
    ) -> ProcesoFacturaResultado:
        if self._settings.siifa_reutilizar_clasificadas:
            sql_check = self._sql_factory()
            try:
                previo = SQLServerRepository(sql_check).factura_ya_clasificada(item.id_factura)
                if previo:
                    return ProcesoFacturaResultado(
                        id_factura_siifa=item.id_factura,
                        numero_factura=item.numero_factura,
                        nit_emisor=item.nit_emisor,
                        estado=EstadoProcesoFactura.OMITIDA,
                        mensaje=f"Ya clasificada ({previo})",
                    )
            finally:
                sql_check.close()

        pg = self._pg_factory()
        sql = self._sql_factory()
        pg_repo = PostgreSQLRepository(pg)
        sql_repo = SQLServerRepository(sql)

        try:
            if item.id_factura and self._ya_radicada_en_erp(pg_repo, item):
                sql_repo.upsert_factura_siifa(
                    item,
                    id_ejecucion=id_ejecucion,
                    pagina=pagina,
                    estado=EstadoProcesoFactura.OMITIDA,
                    observacion="Ya radicada en ERP (idfactura_siifa presente)",
                )
                sql_repo.registrar_traza(
                    id_ejecucion=id_ejecucion,
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    paso="VALIDACION_PREVIA",
                    resultado=ResultadoTraza.OMITIDA,
                    mensaje="Factura ya sincronizada previamente",
                )
                sql_repo.commit()
                return ProcesoFacturaResultado(
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    estado=EstadoProcesoFactura.OMITIDA,
                    mensaje="Ya radicada en ERP",
                )

            match = pg_repo.buscar_rips_af(item.numero_factura, item.nit_emisor)
            if not match:
                sql_repo.upsert_factura_siifa(
                    item,
                    id_ejecucion=id_ejecucion,
                    pagina=pagina,
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
                sql_repo.commit()
                return ProcesoFacturaResultado(
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    estado=EstadoProcesoFactura.NO_ENCONTRADA_ERP,
                    mensaje="NO ENCONTRADA en ERP",
                )

            resumen = pg_repo.buscar_rips_resumen(match.consecutivo_rips)
            if not resumen:
                msg = f"rips_resumen no encontrado para consecutivo_rips={match.consecutivo_rips}"
                self._registrar_error_sql(
                    sql_repo, item, id_ejecucion, pagina, match, msg, "BUSCAR_RIPS_RESUMEN"
                )
                sql_repo.commit()
                return ProcesoFacturaResultado(
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    estado=EstadoProcesoFactura.ERROR,
                    mensaje=msg,
                    consecutivo_rips_af=match.consecutivo_rips_af,
                )

            if resumen.estado != PostgreSQLRepository.ESTADO_RADICADO_ERP:
                obs = "Factura encontrada pero no radicada en ERP"
                sql_repo.upsert_factura_siifa(
                    item,
                    id_ejecucion=id_ejecucion,
                    pagina=pagina,
                    estado=EstadoProcesoFactura.NO_RADICADA_ERP,
                    observacion=obs,
                )
                sql_repo.registrar_factura_erp(
                    id_factura_siifa=item.id_factura,
                    consecutivo_rips_af=match.consecutivo_rips_af,
                    consecutivo_rips=match.consecutivo_rips,
                    numero_factura=item.numero_factura,
                    nit_prestador=item.nit_emisor,
                    estado_erp=resumen.estado,
                    radica_rips=resumen.radica_rips,
                    fecha_radica=resumen.fecha_radica,
                    resultado="NO_RADICADA_ERP",
                    mensaje=obs,
                )
                sql_repo.registrar_traza(
                    id_ejecucion=id_ejecucion,
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    paso="VALIDAR_ESTADO_ERP",
                    resultado=ResultadoTraza.NO_RADICADA_ERP,
                    mensaje=obs,
                    detalle={"estado": resumen.estado},
                )
                sql_repo.commit()
                return ProcesoFacturaResultado(
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    estado=EstadoProcesoFactura.NO_RADICADA_ERP,
                    mensaje=obs,
                    consecutivo_rips_af=match.consecutivo_rips_af,
                )

            if not resumen.radica_rips:
                msg = "radica_rips vacío en rips_resumen con estado=5"
                self._registrar_error_sql(
                    sql_repo, item, id_ejecucion, pagina, match, msg, "VALIDAR_RADICA_RIPS"
                )
                sql_repo.commit()
                return ProcesoFacturaResultado(
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    estado=EstadoProcesoFactura.ERROR,
                    mensaje=msg,
                    consecutivo_rips_af=match.consecutivo_rips_af,
                )

            if resumen.fecha_radica is None:
                msg = (
                    f"fecha_radica vacía o inválida en rips_resumen "
                    f"(consecutivo_rips={match.consecutivo_rips})"
                )
                self._registrar_error_sql(
                    sql_repo, item, id_ejecucion, pagina, match, msg, "VALIDAR_FECHA_RADICA"
                )
                sql_repo.commit()
                return ProcesoFacturaResultado(
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    estado=EstadoProcesoFactura.ERROR,
                    mensaje=msg,
                    consecutivo_rips_af=match.consecutivo_rips_af,
                )

            fecha_iso = fecha_erp_a_iso_utc(resumen.fecha_radica)
            req = RadicadoSiifaRequest(
                id_factura=item.id_factura,
                radicado=resumen.radica_rips,
                fecha_radicado=fecha_iso,
            )

            try:
                with self._client_lock:
                    resp = self._client.radicar_factura(req)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
                sql_repo.upsert_factura_siifa(
                    item,
                    id_ejecucion=id_ejecucion,
                    pagina=pagina,
                    estado=EstadoProcesoFactura.ERROR,
                    observacion=str(exc),
                )
                id_rad = sql_repo.registrar_radicado(
                    id_factura_siifa=item.id_factura,
                    radicado_numero=resumen.radica_rips,
                    fecha_radicacion=resumen.fecha_radica,
                    estado="ERROR",
                    error_mensaje=str(exc),
                )
                sql_repo.encolar_reintento(
                    id_factura_siifa=item.id_factura,
                    id_radicado=id_rad,
                    motivo="RADICAR_SIIFA",
                    payload={"request": req.__dict__},
                    max_intentos=int(self._settings.siifa_retry_max_attempts),
                    delay_seconds=int(self._settings.siifa_retry_base_delay_seconds) * 2,
                )
                sql_repo.registrar_traza(
                    id_ejecucion=id_ejecucion,
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    paso="RADICAR_SIIFA",
                    resultado=ResultadoTraza.ERROR,
                    mensaje=str(exc),
                )
                sql_repo.commit()
                pg_repo.rollback()
                return ProcesoFacturaResultado(
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    estado=EstadoProcesoFactura.ERROR,
                    mensaje=str(exc),
                    consecutivo_rips_af=match.consecutivo_rips_af,
                )

            try:
                pg_repo.actualizar_radicado_siifa(
                    consecutivo_rips_af=match.consecutivo_rips_af,
                    id_factura_siifa=item.id_factura,
                    id_factura_radicado_siifa=resp.id_factura_radicado,
                )
                pg_repo.commit()
            except Exception as exc:
                pg_repo.rollback()
                sql_repo.upsert_factura_siifa(
                    item,
                    id_ejecucion=id_ejecucion,
                    pagina=pagina,
                    estado=EstadoProcesoFactura.ERROR,
                    observacion=f"SIIFA OK pero falló UPDATE ERP: {exc}",
                )
                sql_repo.registrar_radicado(
                    id_factura_siifa=item.id_factura,
                    radicado_numero=resumen.radica_rips,
                    fecha_radicacion=resumen.fecha_radica,
                    estado="ERROR_ERP",
                    id_factura_radicado_siifa=resp.id_factura_radicado,
                    respuesta_json=json.dumps(resp.__dict__, default=str),
                    error_mensaje=str(exc),
                )
                sql_repo.encolar_reintento(
                    id_factura_siifa=item.id_factura,
                    id_radicado=None,
                    motivo="UPDATE_ERP",
                    payload={
                        "consecutivo_rips_af": match.consecutivo_rips_af,
                        "id_factura_siifa": item.id_factura,
                        "id_factura_radicado_siifa": resp.id_factura_radicado,
                    },
                    max_intentos=int(self._settings.siifa_retry_max_attempts),
                    delay_seconds=int(self._settings.siifa_retry_base_delay_seconds) * 2,
                )
                sql_repo.registrar_traza(
                    id_ejecucion=id_ejecucion,
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    paso="UPDATE_ERP",
                    resultado=ResultadoTraza.ERROR,
                    mensaje=str(exc),
                )
                sql_repo.commit()
                return ProcesoFacturaResultado(
                    id_factura_siifa=item.id_factura,
                    numero_factura=item.numero_factura,
                    nit_emisor=item.nit_emisor,
                    estado=EstadoProcesoFactura.ERROR,
                    mensaje=f"SIIFA OK, falló ERP: {exc}",
                    consecutivo_rips_af=match.consecutivo_rips_af,
                )

            sql_repo.upsert_factura_siifa(
                item,
                id_ejecucion=id_ejecucion,
                pagina=pagina,
                estado=EstadoProcesoFactura.RADICADA,
                observacion="Radicada exitosamente",
            )
            sql_repo.registrar_factura_erp(
                id_factura_siifa=item.id_factura,
                consecutivo_rips_af=match.consecutivo_rips_af,
                consecutivo_rips=match.consecutivo_rips,
                numero_factura=item.numero_factura,
                nit_prestador=item.nit_emisor,
                estado_erp=resumen.estado,
                radica_rips=resumen.radica_rips,
                fecha_radica=resumen.fecha_radica,
                resultado="RADICADA",
                mensaje="OK",
            )
            sql_repo.registrar_radicado(
                id_factura_siifa=item.id_factura,
                radicado_numero=resumen.radica_rips,
                fecha_radicacion=resumen.fecha_radica,
                estado="EXITOSO",
                id_factura_radicado_siifa=resp.id_factura_radicado,
                respuesta_json=json.dumps(resp.__dict__, default=str),
                sincronizado_erp=True,
            )
            sql_repo.registrar_traza(
                id_ejecucion=id_ejecucion,
                id_factura_siifa=item.id_factura,
                numero_factura=item.numero_factura,
                nit_emisor=item.nit_emisor,
                paso="COMPLETADO",
                resultado=ResultadoTraza.OK,
                mensaje="Radicación y actualización ERP exitosas",
                detalle={
                    "idFacturaRadicado": resp.id_factura_radicado,
                    "radicado": resumen.radica_rips,
                },
            )
            sql_repo.commit()

            return ProcesoFacturaResultado(
                id_factura_siifa=item.id_factura,
                numero_factura=item.numero_factura,
                nit_emisor=item.nit_emisor,
                estado=EstadoProcesoFactura.RADICADA,
                id_factura_radicado_siifa=resp.id_factura_radicado,
                consecutivo_rips_af=match.consecutivo_rips_af,
            )

        except Exception:
            pg_repo.rollback()
            sql_repo.rollback()
            raise
        finally:
            pg.close()
            sql.close()

    def _ya_radicada_en_erp(self, pg_repo: PostgreSQLRepository, item: FacturaSiifaItem) -> bool:
        match = pg_repo.buscar_rips_af(item.numero_factura, item.nit_emisor)
        if not match:
            return False
        if match.idfactura_siifa and str(match.idfactura_siifa).strip() == str(item.id_factura):
            return True
        if match.radicado_siifa and int(match.radicado_siifa) > 0:
            return True
        return False

    def _registrar_error_sql(
        self,
        sql_repo: SQLServerRepository,
        item: FacturaSiifaItem,
        id_ejecucion: int,
        pagina: int,
        match: Any,
        mensaje: str,
        paso: str,
    ) -> None:
        sql_repo.upsert_factura_siifa(
            item,
            id_ejecucion=id_ejecucion,
            pagina=pagina,
            estado=EstadoProcesoFactura.ERROR,
            observacion=mensaje,
        )
        sql_repo.registrar_factura_erp(
            id_factura_siifa=item.id_factura,
            consecutivo_rips_af=match.consecutivo_rips_af,
            consecutivo_rips=match.consecutivo_rips,
            numero_factura=item.numero_factura,
            nit_prestador=item.nit_emisor,
            estado_erp=None,
            radica_rips=None,
            fecha_radica=None,
            resultado="ERROR",
            mensaje=mensaje,
        )
        sql_repo.registrar_traza(
            id_ejecucion=id_ejecucion,
            id_factura_siifa=item.id_factura,
            numero_factura=item.numero_factura,
            nit_emisor=item.nit_emisor,
            paso=paso,
            resultado=ResultadoTraza.ERROR,
            mensaje=mensaje,
        )

    def _reprocesar_fallidos(
        self,
        id_ejecucion: int,
        sql_repo: SQLServerRepository,
        metricas: MetricasEjecucion,
        workers: int,
    ) -> int:
        pendientes = sql_repo.listar_reintentos_pendientes(
            limite=int(self._settings.siifa_reintento_lote_max)
        )
        if not pendientes:
            return 0

        logger.info("siifa_reproceso_inicio cantidad=%s", len(pendientes))
        items: list[tuple[FacturaSiifaItem, int]] = []
        for row in pendientes:
            items.append(
                (
                    FacturaSiifaItem(
                        id_factura=int(row["IdFacturaSIIFA"]),
                        numero_factura=str(row["NumeroFactura"] or ""),
                        nit_emisor=str(row["NitEmisor"] or ""),
                    ),
                    int(row["IdReintento"]),
                )
            )

        procesados = 0
        lock = threading.Lock()

        def _reintento_worker(pair: tuple[FacturaSiifaItem, int]) -> None:
            item, id_reintento = pair
            try:
                res = self._procesar_factura(item, pagina=0, id_ejecucion=id_ejecucion)
                sql = self._sql_factory()
                repo = SQLServerRepository(sql)
                try:
                    if res.estado == EstadoProcesoFactura.RADICADA:
                        repo.marcar_reintento(id_reintento, estado="COMPLETADO")
                    elif res.estado in (
                        EstadoProcesoFactura.NO_ENCONTRADA_ERP,
                        EstadoProcesoFactura.NO_RADICADA_ERP,
                        EstadoProcesoFactura.OMITIDA,
                    ):
                        repo.marcar_reintento(id_reintento, estado="CANCELADO", error=res.mensaje)
                    else:
                        repo.marcar_reintento(id_reintento, estado="PENDIENTE", error=res.mensaje)
                    repo.commit()
                finally:
                    sql.close()
                with lock:
                    metricas.procesadas += 1
                    if res.estado == EstadoProcesoFactura.RADICADA:
                        metricas.radicadas += 1
                    elif res.estado == EstadoProcesoFactura.ERROR:
                        metricas.errores += 1
            except Exception as exc:
                sql = self._sql_factory()
                repo = SQLServerRepository(sql)
                try:
                    repo.marcar_reintento(id_reintento, estado="PENDIENTE", error=str(exc))
                    repo.commit()
                finally:
                    sql.close()
                with lock:
                    metricas.errores += 1

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_reintento_worker, items))

        procesados = len(items)
        logger.info("siifa_reproceso_fin procesados=%s", procesados)
        return procesados
