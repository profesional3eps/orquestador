from datetime import datetime

from app.core.database import PostgresSessionLocal, SqlServerSessionLocal
from app.core.logging_setup import get_logger
from app.models.dto import ApiExecutionResponse
from app.repositories.postgres_repository import PostgresRepository
from app.repositories.sqlserver_repository import SqlServerRepository
from app.services.logging_service import LoggingService

logger = get_logger(__name__)

SERVICE_FACTURAS_DECIMALES = "facturas_decimales"
SERVICE_UPDATE_SALDO = "update_saldo_factura_factura_encabezado"
SERVICE_UPDATE_VALOR = "update_valor_por_aplicar_factura_detalle"
SERVICE_UPDATE_SALDO_Y_VALOR = "update_saldo_y_valor_factura"


class OrchestratorService:
    def run_service1(self, executed_by: str = "scheduler") -> ApiExecutionResponse:
        sql_session = SqlServerSessionLocal()
        pg_session = PostgresSessionLocal()
        log_service = LoggingService(SqlServerRepository(sql_session))
        try:
            pg_repo = PostgresRepository(pg_session)
            sql_repo = SqlServerRepository(sql_session)

            data = pg_repo.fetch_service1_candidates()
            started_at = datetime.now()
            inserted, duplicates = sql_repo.store_service1_results(rows=data, username=executed_by)
            errors = 0
            success = inserted

            logger.info("%s_finished records=%s", SERVICE_FACTURAS_DECIMALES, success)
            log_service.write_log(
                servicio=SERVICE_FACTURAS_DECIMALES,
                referencia=None,
                fecha_inicio=started_at,
                estado="SUCCESS",
                mensaje=(
                    f"Ejecucion completada. total={len(data)}, insertados={inserted}, "
                    f"duplicados={duplicates}, errores={errors}"
                ),
                intentos=1,
                usuario=executed_by,
            )
            return ApiExecutionResponse(
                service_name=SERVICE_FACTURAS_DECIMALES,
                total=len(data),
                success=success,
                errors=errors,
                detail=(
                    "Consulta ejecutada con observacion JSON para duplicados y "
                    f"registro de ejecucion. Duplicados detectados: {duplicates}"
                ),
            )
        finally:
            pg_session.close()
            sql_session.close()

    def run_service2_and_service3(self, executed_by: str = "scheduler") -> ApiExecutionResponse:
        """
        Flujo unificado: consulta PostgreSQL (NITs de ct_ips), luego por lotes:
        1) sc_factura_encabezado.saldo_factura
        2) sc_factura_detalle_valor.valor_por_aplicar
        con int(valor) de la consulta (suma contable sin decimales).
        """
        sql_session = SqlServerSessionLocal()
        pg_session = PostgresSessionLocal()
        sql_repo = SqlServerRepository(sql_session)
        pg_repo = PostgresRepository(pg_session)
        log_service = LoggingService(sql_repo)
        started_at = datetime.now()

        try:
            items, query_rows = pg_repo.fetch_saldo_valor_update_payload()
            total = len(items)

            if total == 0:
                log_service.write_log(
                    servicio=SERVICE_UPDATE_SALDO_Y_VALOR,
                    referencia=None,
                    fecha_inicio=started_at,
                    estado="SUCCESS",
                    mensaje="Sin candidatos para actualizar (consulta PostgreSQL / ct_ips).",
                    intentos=1,
                    usuario=executed_by,
                )
                return ApiExecutionResponse(
                    service_name=SERVICE_UPDATE_SALDO_Y_VALOR,
                    total=0,
                    success=0,
                    errors=0,
                    detail="Sin registros con delta distinto de cero para IPS en ct_ips.",
                )

            updated = pg_repo.apply_saldo_valor_updates_batched(items)
            pg_session.commit()
            remaining_rows = pg_repo.count_saldo_valor_candidates()

            logger.info(
                "%s_finished query_rows=%s unique=%s updated=%s remaining_rows=%s",
                SERVICE_UPDATE_SALDO_Y_VALOR,
                query_rows,
                total,
                updated,
                remaining_rows,
            )
            log_service.write_log(
                servicio=SERVICE_UPDATE_SALDO_Y_VALOR,
                referencia=None,
                fecha_inicio=started_at,
                estado="SUCCESS",
                mensaje=(
                    f"Flujo unificado PostgreSQL: filas_consulta={query_rows}, "
                    f"consecutivos_unicos={total}, actualizados={updated}, "
                    f"pendientes_tras_proceso={remaining_rows}."
                ),
                intentos=1,
                usuario=executed_by,
            )
            return ApiExecutionResponse(
                service_name=SERVICE_UPDATE_SALDO_Y_VALOR,
                total=total,
                success=updated,
                errors=0,
                detail=(
                    f"Consulta: {query_rows} fila(s), {total} consecutivo(s) único(s). "
                    f"Actualizados con int(valor): {updated}. "
                    f"Pendientes en consulta tras proceso: {remaining_rows}. "
                    "Cruce ct_ips.nit = sc_tercero.nro_identificacion."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            pg_session.rollback()
            logger.exception("%s_error", SERVICE_UPDATE_SALDO_Y_VALOR)
            log_service.write_log(
                servicio=SERVICE_UPDATE_SALDO_Y_VALOR,
                referencia=None,
                fecha_inicio=started_at,
                estado="ERROR",
                mensaje=f"Fallo en flujo unificado PostgreSQL: {exc}",
                intentos=1,
                usuario=executed_by,
            )
            return ApiExecutionResponse(
                service_name=SERVICE_UPDATE_SALDO_Y_VALOR,
                total=0,
                success=0,
                errors=1,
                detail=f"{SERVICE_UPDATE_SALDO_Y_VALOR} fallo: {exc}",
            )
        finally:
            sql_session.close()
            pg_session.close()
