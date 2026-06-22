"""Persistencia operacional SIIFA en SQL Server (auditoría, reintentos, trazas)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.siifa_lote import LoteCheckpoint
from app.models.siifa_radicacion import (
    EstadoProcesoFactura,
    FacturaSiifaItem,
    ResultadoTraza,
)


class SQLServerRepository:
    """Repositorio de tablas SIIFA_* en OrquestacionDB."""

    PROCESO_LOTE_RADICACION = "RADICACION"
    PROCESO_LOTE_CSV = "RADICACION_CSV"
    PROCESO_LOTE_SEGUIMIENTO_ERP = "RADICACION_SEGUIMIENTO_ERP"
    ESTADOS_CLASIFICADOS_FINALES = frozenset({
        str(EstadoProcesoFactura.RADICADA),
        str(EstadoProcesoFactura.NO_ENCONTRADA_ERP),
        str(EstadoProcesoFactura.NO_RADICADA_ERP),
        str(EstadoProcesoFactura.OMITIDA),
    })

    def __init__(self, session: Session) -> None:
        self._session = session

    def iniciar_ejecucion(
        self,
        *,
        tipo_ejecucion: str,
        workers: int,
        usuario: str | None = None,
    ) -> int:
        row = self._session.execute(
            text(
                """
                INSERT INTO dbo.SIIFA_IntegracionLog
                    (TipoEjecucion, FechaInicio, Estado, Workers, Usuario, Procesadas)
                OUTPUT INSERTED.IdEjecucion
                VALUES (:tipo, :inicio, 'EN_CURSO', :workers, :usuario, 0)
                """
            ),
            {
                "tipo": tipo_ejecucion,
                "inicio": datetime.now(timezone.utc).replace(tzinfo=None),
                "workers": workers,
                "usuario": usuario,
            },
        ).scalar_one()
        self._session.commit()
        return int(row)

    def finalizar_ejecucion(
        self,
        id_ejecucion: int,
        *,
        estado: str,
        metricas: dict[str, Any],
        total_registros_siifa: int | None = None,
        total_paginas: int | None = None,
        duracion_ms: int | None = None,
    ) -> None:
        self._session.execute(
            text(
                """
                UPDATE dbo.SIIFA_IntegracionLog
                SET FechaFin = :fin,
                    DuracionMs = :duracion,
                    Estado = :estado,
                    TotalRegistrosSIIFA = :total_reg,
                    TotalPaginas = :total_pag,
                    Procesadas = :procesadas,
                    Radicadas = :radicadas,
                    NoEncontradasERP = :no_enc,
                    NoRadicadasERP = :no_rad,
                    Errores = :errores,
                    Omitidas = :omitidas,
                    DetalleJson = :detalle
                WHERE IdEjecucion = :id
                """
            ),
            {
                "id": id_ejecucion,
                "fin": datetime.now(timezone.utc).replace(tzinfo=None),
                "duracion": duracion_ms,
                "estado": estado,
                "total_reg": total_registros_siifa,
                "total_pag": total_paginas,
                "procesadas": metricas.get("procesadas", 0),
                "radicadas": metricas.get("radicadas", 0),
                "no_enc": metricas.get("no_encontradas_erp", 0),
                "no_rad": metricas.get("no_radicadas_erp", 0),
                "errores": metricas.get("errores", 0),
                "omitidas": metricas.get("omitidas", 0),
                "detalle": json.dumps(metricas, ensure_ascii=False, default=str),
            },
        )
        self._session.commit()

    def upsert_factura_siifa(
        self,
        item: FacturaSiifaItem,
        *,
        id_ejecucion: int | None,
        pagina: int | None,
        estado: EstadoProcesoFactura,
        observacion: str | None = None,
    ) -> None:
        self._session.execute(
            text(
                """
                MERGE dbo.SIIFA_Factura AS tgt
                USING (SELECT :id AS IdFacturaSIIFA) AS src
                ON tgt.IdFacturaSIIFA = src.IdFacturaSIIFA
                WHEN MATCHED THEN
                    UPDATE SET
                        NumeroFactura = :numero,
                        NitEmisor = :nit,
                        EstadoProceso = :estado,
                        Observacion = :obs,
                        PaginaOrigen = :pagina,
                        IdEjecucion = :ejecucion,
                        FechaConsulta = GETDATE()
                WHEN NOT MATCHED THEN
                    INSERT (IdFacturaSIIFA, NumeroFactura, NitEmisor, EstadoProceso,
                            Observacion, PaginaOrigen, IdEjecucion)
                    VALUES (:id, :numero, :nit, :estado, :obs, :pagina, :ejecucion);
                """
            ),
            {
                "id": item.id_factura,
                "numero": item.numero_factura,
                "nit": item.nit_emisor,
                "estado": str(estado),
                "obs": observacion,
                "pagina": pagina,
                "ejecucion": id_ejecucion,
            },
        )

    def registrar_factura_erp(
        self,
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
        self._session.execute(
            text(
                """
                MERGE dbo.SIIFA_FacturaERP AS tgt
                USING (SELECT :id_siifa AS IdFacturaSIIFA) AS src
                ON tgt.IdFacturaSIIFA = src.IdFacturaSIIFA
                WHEN MATCHED THEN
                    UPDATE SET
                        ConsecutivoRipsAf = :crips_af,
                        ConsecutivoRips = :crips,
                        NumeroFacturaERP = :numero,
                        NitPrestadorERP = :nit,
                        EstadoERP = :estado,
                        RadicaRips = :radica,
                        FechaRadicaERP = :fecha_rad,
                        Resultado = :resultado,
                        Mensaje = :mensaje,
                        FechaRelacion = GETDATE()
                WHEN NOT MATCHED THEN
                    INSERT (IdFacturaSIIFA, ConsecutivoRipsAf, ConsecutivoRips,
                            NumeroFacturaERP, NitPrestadorERP, EstadoERP, RadicaRips,
                            FechaRadicaERP, Resultado, Mensaje)
                    VALUES (:id_siifa, :crips_af, :crips, :numero, :nit, :estado,
                            :radica, :fecha_rad, :resultado, :mensaje);
                """
            ),
            {
                "id_siifa": id_factura_siifa,
                "crips_af": consecutivo_rips_af,
                "crips": consecutivo_rips,
                "numero": numero_factura,
                "nit": nit_prestador,
                "estado": estado_erp,
                "radica": radica_rips,
                "fecha_rad": fecha_radica,
                "resultado": resultado,
                "mensaje": mensaje,
            },
        )

    def registrar_radicado(
        self,
        *,
        id_factura_siifa: int,
        radicado_numero: str,
        fecha_radicacion: datetime,
        estado: str,
        id_factura_radicado_siifa: int | None = None,
        http_code: int | None = None,
        respuesta_json: str | None = None,
        error_mensaje: str | None = None,
        sincronizado_erp: bool = False,
    ) -> int:
        row = self._session.execute(
            text(
                """
                INSERT INTO dbo.SIIFA_Radicado
                    (IdFacturaSIIFA, IdFacturaRadicadoSIIFA, RadicadoNumero,
                     FechaRadicacionSIIFA, Estado, HttpCode, RespuestaJson,
                     ErrorMensaje, SincronizadoERP, FechaSincronizacionERP)
                OUTPUT INSERTED.IdRadicado
                VALUES (:id_factura, :id_radicado, :radicado, :fecha, :estado,
                        :http, :resp, :err, :sync, CASE WHEN :sync = 1 THEN GETDATE() ELSE NULL END)
                """
            ),
            {
                "id_factura": id_factura_siifa,
                "id_radicado": id_factura_radicado_siifa,
                "radicado": radicado_numero,
                "fecha": fecha_radicacion,
                "estado": estado,
                "http": http_code,
                "resp": respuesta_json,
                "err": error_mensaje,
                "sync": 1 if sincronizado_erp else 0,
            },
        ).scalar_one()
        return int(row)

    def registrar_traza(
        self,
        *,
        id_ejecucion: int | None,
        id_factura_siifa: int | None,
        numero_factura: str | None,
        nit_emisor: str | None,
        paso: str,
        resultado: ResultadoTraza,
        mensaje: str | None = None,
        detalle: dict[str, Any] | None = None,
    ) -> None:
        self._session.execute(
            text(
                """
                INSERT INTO dbo.SIIFA_FacturaTraza
                    (IdEjecucion, IdFacturaSIIFA, NumeroFactura, NitEmisor,
                     Paso, Resultado, Mensaje, DetalleJson)
                VALUES (:ejec, :id_fact, :numero, :nit, :paso, :res, :msg, :det)
                """
            ),
            {
                "ejec": id_ejecucion,
                "id_fact": id_factura_siifa,
                "numero": numero_factura,
                "nit": nit_emisor,
                "paso": paso,
                "res": str(resultado),
                "msg": mensaje,
                "det": json.dumps(detalle, ensure_ascii=False, default=str) if detalle else None,
            },
        )

    def encolar_reintento(
        self,
        *,
        id_factura_siifa: int,
        id_radicado: int | None,
        motivo: str,
        payload: dict[str, Any] | None,
        max_intentos: int,
        delay_seconds: int,
    ) -> None:
        proximo = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=delay_seconds)
        self._session.execute(
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

    def listar_reintentos_pendientes(self, limite: int = 500) -> list[dict[str, Any]]:
        rows = self._session.execute(
            text(
                """
                SELECT TOP (:lim)
                    r.IdReintento, r.IdFacturaSIIFA, r.IdRadicado, r.Motivo,
                    r.Intentos, r.MaxIntentos, r.PayloadJson,
                    f.NumeroFactura, f.NitEmisor
                FROM dbo.SIIFA_Reintento r
                INNER JOIN dbo.SIIFA_Factura f ON f.IdFacturaSIIFA = r.IdFacturaSIIFA
                WHERE r.Estado = 'PENDIENTE'
                  AND r.Intentos < r.MaxIntentos
                  AND (r.ProximoIntento IS NULL OR r.ProximoIntento <= GETDATE())
                ORDER BY r.ProximoIntento, r.FechaCreacion
                """
            ),
            {"lim": limite},
        ).mappings().all()
        return [dict(r) for r in rows]

    def marcar_reintento(self, id_reintento: int, *, estado: str, error: str | None = None) -> None:
        self._session.execute(
            text(
                """
                UPDATE dbo.SIIFA_Reintento
                SET Estado = :estado,
                    Intentos = Intentos + 1,
                    FechaUltimoIntento = GETDATE(),
                    UltimoError = :err
                WHERE IdReintento = :id
                """
            ),
            {"id": id_reintento, "estado": estado, "err": error},
        )

    def obtener_checkpoint_lote(self, proceso: str = PROCESO_LOTE_RADICACION) -> LoteCheckpoint:
        row = self._session.execute(
            text(
                """
                SELECT UltimaPaginaProcesada, TotalPaginasSiifa, TotalRegistrosSiifa,
                       LoteCompletado, FechaActualizacion, IdEjecucionUltima
                FROM dbo.SIIFA_LoteCheckpoint
                WHERE Proceso = :proceso
                """
            ),
            {"proceso": proceso},
        ).mappings().first()
        if not row:
            return LoteCheckpoint()
        return LoteCheckpoint(
            ultima_pagina_procesada=int(row["UltimaPaginaProcesada"] or 0),
            total_paginas_siifa=int(row["TotalPaginasSiifa"]) if row["TotalPaginasSiifa"] is not None else None,
            total_registros_siifa=int(row["TotalRegistrosSiifa"]) if row["TotalRegistrosSiifa"] is not None else None,
            lote_completado=bool(row["LoteCompletado"]),
            fecha_actualizacion=row["FechaActualizacion"],
            id_ejecucion_ultima=int(row["IdEjecucionUltima"]) if row["IdEjecucionUltima"] is not None else None,
        )

    def guardar_checkpoint_lote(
        self,
        *,
        ultima_pagina: int,
        total_paginas: int | None,
        total_registros: int | None,
        lote_completado: bool,
        id_ejecucion: int | None,
        proceso: str = PROCESO_LOTE_RADICACION,
    ) -> None:
        self._session.execute(
            text(
                """
                MERGE dbo.SIIFA_LoteCheckpoint AS tgt
                USING (SELECT :proceso AS Proceso) AS src
                ON tgt.Proceso = src.Proceso
                WHEN MATCHED THEN
                    UPDATE SET
                        UltimaPaginaProcesada = :ultima,
                        TotalPaginasSiifa = :total_pag,
                        TotalRegistrosSiifa = :total_reg,
                        LoteCompletado = :completado,
                        FechaActualizacion = GETDATE(),
                        IdEjecucionUltima = :id_ejec
                WHEN NOT MATCHED THEN
                    INSERT (Proceso, UltimaPaginaProcesada, TotalPaginasSiifa,
                            TotalRegistrosSiifa, LoteCompletado, IdEjecucionUltima)
                    VALUES (:proceso, :ultima, :total_pag, :total_reg, :completado, :id_ejec);
                """
            ),
            {
                "proceso": proceso,
                "ultima": ultima_pagina,
                "total_pag": total_paginas,
                "total_reg": total_registros,
                "completado": 1 if lote_completado else 0,
                "id_ejec": id_ejecucion,
            },
        )

    def reiniciar_checkpoint_lote(
        self,
        *,
        id_ejecucion: int | None = None,
        proceso: str = PROCESO_LOTE_RADICACION,
    ) -> None:
        self.guardar_checkpoint_lote(
            ultima_pagina=0,
            total_paginas=None,
            total_registros=None,
            lote_completado=False,
            id_ejecucion=id_ejecucion,
            proceso=proceso,
        )

    def factura_ya_clasificada(self, id_factura_siifa: int) -> str | None:
        row = self._session.execute(
            text(
                """
                SELECT EstadoProceso
                FROM dbo.SIIFA_Factura
                WHERE IdFacturaSIIFA = :id
                """
            ),
            {"id": id_factura_siifa},
        ).mappings().first()
        if not row:
            return None
        estado = str(row["EstadoProceso"] or "").strip()
        if estado in self.ESTADOS_CLASIFICADOS_FINALES:
            return estado
        return None

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()
