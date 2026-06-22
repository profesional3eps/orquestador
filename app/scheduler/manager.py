import json
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.exc import DBAPIError, OperationalError

from app.config.settings import get_settings
from app.core.database import SqlServerSessionLocal, sqlserver_engine
from app.core.logging_setup import get_logger
from app.repositories.sqlserver_repository import SqlServerRepository
from app.services.orchestrator_service import (
    SERVICE_FACTURAS_DECIMALES,
    SERVICE_UPDATE_SALDO,
    SERVICE_UPDATE_SALDO_Y_VALOR,
    SERVICE_UPDATE_VALOR,
    OrchestratorService,
)
logger = get_logger(__name__)


class SchedulerManager:
    def __init__(self) -> None:
        settings = get_settings()
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.reload_interval = settings.job_reload_interval_seconds
        self.orchestrator_service = OrchestratorService()

    def start(self) -> None:
        self.scheduler.start()
        try:
            self.reload_jobs()
        except (DBAPIError, OperationalError) as exc:
            logger.error(
                "scheduler_initial_reload_failed: no se pudo conectar a SQL Server "
                "(revise SQLSERVER_URL, usuario y contraseña URL-encoded). error=%s",
                exc,
            )
        self.scheduler.add_job(
            self.reload_jobs,
            trigger="interval",
            seconds=self.reload_interval,
            id="reload_dynamic_jobs",
            replace_existing=True,
        )
        logger.info("scheduler_started interval=%s", self.reload_interval)

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def reload_jobs(self) -> None:
        max_attempts = 3
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            session = SqlServerSessionLocal()
            try:
                repo = SqlServerRepository(session)
                jobs = repo.fetch_active_scheduler_jobs()
                existing_ids = {int(job["id"]) for job in jobs}

                for scheduled in self.scheduler.get_jobs():
                    if scheduled.id.startswith("service_job_"):
                        job_id = int(scheduled.id.split("_")[-1])
                        if job_id not in existing_ids:
                            self.scheduler.remove_job(scheduled.id)

                for job in jobs:
                    job_id = int(job["id"])
                    service_name = str(job.get("nombre_servicio") or "").strip()
                    cron_expression = str(job.get("cron_expression") or "").strip()
                    raw_params = job.get("parametros")

                    trigger = self._parse_cron(cron_expression)
                    if trigger is None:
                        logger.error("invalid_cron_expression job_id=%s cron=%s", job_id, cron_expression)
                        continue

                    self.scheduler.add_job(
                        self._run_job,
                        trigger=trigger,
                        id=f"service_job_{job_id}",
                        replace_existing=True,
                        kwargs={
                            "job_id": job_id,
                            "service_name": service_name,
                            "raw_params": raw_params,
                        },
                    )
                return
            except (DBAPIError, OperationalError) as exc:
                last_exc = exc
                session.rollback()
                if attempt < max_attempts:
                    logger.warning(
                        "scheduler_reload_retry attempt=%s/%s error=%s",
                        attempt,
                        max_attempts,
                        exc,
                    )
                    sqlserver_engine.dispose()
                    time.sleep(attempt * 2)
                continue
            finally:
                session.close()

        logger.error(
            "scheduler_reload_failed: error de conexion SQL Server tras %s intentos. error=%s",
            max_attempts,
            last_exc,
        )

    def _run_job(self, job_id: int, service_name: str, raw_params: str | None) -> None:
        params: dict = {}
        if raw_params:
            try:
                parsed = json.loads(raw_params)
                if isinstance(parsed, dict):
                    params = parsed
                else:
                    logger.warning("invalid_json_params_not_object job_id=%s", job_id)
            except json.JSONDecodeError:
                logger.warning("invalid_json_params job_id=%s", job_id)

        logger.info("scheduler_execute service=%s params=%s", service_name, params)

        try:
            if service_name in {SERVICE_FACTURAS_DECIMALES, "service1"}:
                self.orchestrator_service.run_service1()
            elif service_name in {
                SERVICE_UPDATE_SALDO,
                SERVICE_UPDATE_VALOR,
                SERVICE_UPDATE_SALDO_Y_VALOR,
                "service2",
                "service3",
            }:
                self.orchestrator_service.run_service2_and_service3()
            elif service_name == "siifa_radicacion_lote":
                logger.warning(
                    "siifa_scheduler_ignorado job_id=%s: desactive activo=0 en orq.scheduler_jobs "
                    "y use script externo (sql/siifa_radicacion_programacion_externa.sql)",
                    job_id,
                )
                return
            else:
                logger.error("unknown_service job_id=%s service=%s", job_id, service_name)
        except Exception:
            logger.exception("scheduler_job_failed job_id=%s service=%s", job_id, service_name)
            raise

        self._update_job_times(job_id)

    def _update_job_times(self, job_id: int) -> None:
        session = SqlServerSessionLocal()
        try:
            repo = SqlServerRepository(session)
            aps_job = self.scheduler.get_job(f"service_job_{job_id}")
            next_run_time = aps_job.next_run_time.replace(tzinfo=None) if aps_job and aps_job.next_run_time else None
            repo.update_job_execution(
                job_id=job_id,
                ultima_ejecucion=datetime.utcnow(),
                proxima_ejecucion=next_run_time,
            )
        finally:
            session.close()

    @staticmethod
    def _parse_cron(expression: str) -> CronTrigger | None:
        parts = expression.split()
        if len(parts) != 5:
            return None
        minute, hour, day, month, day_of_week = parts
        return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week)
