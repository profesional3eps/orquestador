from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import logging

from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.core.exceptions import PermissionLookupFailed
from app.models.dto import (
    AccessLogDTO,
    AccionEnModuloDTO,
    ConsumptionLogDTO,
    MeResponse,
    ModuloConAccionesDTO,
    ProcessLogDTO,
    Service1ResultDTO,
)
from app.models.sqlserver_models import (
    Accion,
    LogAcceso,
    LogProceso,
    Modulo,
    Perfil,
    Permiso,
    ResultadoProceso,
    SchedulerJob,
    Agendamiento,
    Dispensacion,
    DispensacionDiagnostico,
    DispensacionPaciente,
    DispensacionPrestador,
    DispensacionPrescripcion,
    DispensacionProducto,
    HistoriaClinica,
    HistoriaClinicaActividad,
    Usuario,
    UsuarioEndpoint,
    UsuarioPerfil,
)

logger = logging.getLogger(__name__)

_PERM_LOOKUP_USER_MESSAGE = (
    "No se pudieron validar los permisos: la cuenta de servicio de la aplicación no tiene permiso de lectura "
    "(SELECT) sobre las tablas del esquema de seguridad necesarias (por ejemplo seg.acciones, seg.modulos, "
    "seg.permisos, seg.perfiles, seg.usuario_perfil). Solicite al administrador de bases de datos ajustar los permisos."
)


class SqlServerRepository:
    def _seg_table_exists(self, table_name: str) -> bool:
        row = self.db.execute(
            text(
                """
                SELECT 1
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = 'seg' AND TABLE_NAME = :table_name
                """
            ),
            {"table_name": table_name},
        ).first()
        return row is not None

    def _table_columns(self, table_name: str) -> set[str]:
        rows = self.db.execute(
            text(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = 'orq' AND TABLE_NAME = :table_name
                """
            ),
            {"table_name": table_name},
        ).all()
        return {str(r[0]).lower() for r in rows}

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _to_sqlserver_decimal(value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _append_json_event(existing_json: str | None, event: dict) -> str:
        payload: dict
        if existing_json:
            try:
                payload = json.loads(existing_json)
            except json.JSONDecodeError:
                payload = {"events": [{"event": "legacy_text", "detail": existing_json}]}
        else:
            payload = {"events": []}
        payload.setdefault("events", []).append(event)
        return json.dumps(payload, ensure_ascii=True)

    def commit(self) -> None:
        self.db.commit()

    def store_service1_results(self, rows: list[Service1ResultDTO], username: str) -> tuple[int, int]:
        if not rows:
            return 0, 0

        referencias = list({row.consecutivo_factura for row in rows})
        existing_stmt = (
            select(ResultadoProceso)
            .where(ResultadoProceso.tipo_proceso == "facturas_decimales")
            .where(ResultadoProceso.referencia.in_(referencias))
        )
        existing_refs = {str(item.referencia) for item in self.db.scalars(existing_stmt).all() if item.referencia}

        inserts: list[ResultadoProceso] = []
        duplicates = 0
        seen_refs = set(existing_refs)
        for row in rows:
            now = datetime.now()
            ref = row.consecutivo_factura
            if ref in seen_refs:
                duplicates += 1
                continue
            seen_refs.add(ref)

            inserts.append(
                ResultadoProceso(
                    tipo_proceso="facturas_decimales",
                    referencia=row.consecutivo_factura,
                    documento_soporte=row.documento_soporte,
                    valor=self._to_sqlserver_decimal(row.valor),
                    saldo_factura=self._to_sqlserver_decimal(row.saldo_factura),
                    delta=self._to_sqlserver_decimal(row.delta),
                    observacion=json.dumps(
                        {
                            "events": [
                                {
                                    "timestamp": now.isoformat(),
                                    "event": "created",
                                    "detail": "Registro insertado desde facturas_decimales",
                                }
                            ]
                        },
                        ensure_ascii=True,
                    ),
                    fecha_proceso=now,
                    usuario_creacion=username,
                    fecha_creacion=now,
                    usuario_modificacion=username,
                    fecha_modificacion=now,
                )
            )

        if inserts:
            self.db.add_all(inserts)
        self.db.commit()
        return len(inserts), duplicates

    def fetch_latest_service1_results(self) -> list[ResultadoProceso]:
        stmt = (
            select(ResultadoProceso)
            .where(ResultadoProceso.tipo_proceso.in_(["facturas_decimales", "service1"]))
            .order_by(ResultadoProceso.id.asc())
        )
        rows = list(self.db.scalars(stmt).all())
        by_referencia: dict[str, ResultadoProceso] = {}
        for item in rows:
            if item.referencia is None:
                by_referencia[f"__id_{item.id}"] = item
            else:
                by_referencia[str(item.referencia)] = item
        return list(by_referencia.values())

    def create_log(self, data: ProcessLogDTO, autocommit: bool = True) -> None:
        event = {
            "timestamp": datetime.now().isoformat(),
            "estado": data.estado,
            "mensaje": data.mensaje,
            "intentos": data.intentos,
            "fecha_inicio": data.fecha_inicio.isoformat(),
            "fecha_fin": data.fecha_fin.isoformat() if data.fecha_fin else None,
            "usuario": data.usuario_creacion,
        }
        stmt = (
            select(LogProceso)
            .where(LogProceso.servicio == data.servicio)
            .where(LogProceso.referencia == data.referencia)
            .order_by(LogProceso.id.desc())
        )
        log = self.db.scalars(stmt).first()
        now = datetime.now()
        if log is None:
            log = LogProceso(
                servicio=data.servicio,
                referencia=data.referencia,
                fecha_inicio=data.fecha_inicio,
                fecha_fin=data.fecha_fin,
                estado=data.estado,
                mensaje=json.dumps({"events": [event]}, ensure_ascii=True),
                intentos=data.intentos,
                usuario_creacion=data.usuario_creacion,
                fecha_creacion=now,
            )
        else:
            log.estado = data.estado
            log.fecha_fin = data.fecha_fin
            log.intentos = (log.intentos or 0) + 1
            log.mensaje = self._append_json_event(log.mensaje, event)
        self.db.add(log)
        if autocommit:
            self.db.commit()

    def mark_result_action(
        self,
        referencia: str | None,
        service_name: str,
        success: bool,
        detail: str,
        username: str,
        service1_next_run: datetime | None,
        autocommit: bool = True,
    ) -> None:
        if referencia is None:
            return
        stmt = (
            select(ResultadoProceso)
            .where(ResultadoProceso.tipo_proceso == "facturas_decimales")
            .where(ResultadoProceso.referencia == str(referencia))
            .order_by(ResultadoProceso.id.desc())
        )
        record = self.db.scalars(stmt).first()
        if record is None:
            return

        now = datetime.now()
        event = {
            "timestamp": now.isoformat(),
            "service": service_name,
            "status": "SUCCESS" if success else "ERROR",
            "detail": detail,
            "service1_next_run": service1_next_run.isoformat() if service1_next_run else None,
        }
        record.observacion = self._append_json_event(record.observacion, event)
        record.usuario_modificacion = username
        record.fecha_modificacion = now
        self.db.add(record)
        if autocommit:
            self.db.commit()

    def get_service_next_execution(self, service_name: str) -> datetime | None:
        stmt = (
            select(SchedulerJob)
            .where(SchedulerJob.nombre_servicio == service_name)
            .order_by(SchedulerJob.id.desc())
        )
        job = self.db.scalars(stmt).first()
        if job is None:
            return None
        return job.proxima_ejecucion

    def create_access_log(self, data: AccessLogDTO) -> None:
        access_log = LogAcceso(
            username=data.username,
            fecha_intento=datetime.now(),
            resultado=data.resultado,
            ip_origen=data.ip_origen,
            mensaje=data.mensaje,
        )
        self.db.add(access_log)
        self.db.commit()

    def create_api_consumption_log(self, data: ConsumptionLogDTO) -> None:
        """Persiste un consumo de endpoint en `orq.log_procesos` (una fila por llamada).

        Convencion:
        - `servicio`: prefijo `api:` + nombre logico del endpoint (max 100).
        - `referencia`: tipo|numero de documento (max 100) cuando aplica.
        - `estado`: SUCCESS / ERROR (mismo criterio que antes).
        - `intentos`: codigo HTTP (reutiliza columna para consultas rapidas).
        - `mensaje`: JSON con todo el detalle (usuario, IP, detalle parseado, etc.).
        """
        now = datetime.now()
        servicio = f"api:{data.servicio}"[:100]
        ref_parts = [p for p in (data.tipo_identificacion, data.numero_identificacion) if p]
        referencia = ("|".join(ref_parts)[:100] if ref_parts else None)

        detalle_obj: object = None
        if data.detalle:
            try:
                detalle_obj = json.loads(data.detalle)
            except json.JSONDecodeError:
                detalle_obj = data.detalle

        payload = {
            "kind": "api_endpoint_consumption",
            "servicio_logico": data.servicio,
            "username": data.username,
            "tipo_identificacion": data.tipo_identificacion,
            "numero_identificacion": data.numero_identificacion,
            "resultado": data.resultado,
            "http_status": data.http_status,
            "ip_origen": data.ip_origen,
            "detalle": detalle_obj,
            "timestamp": now.isoformat(),
        }
        mensaje = json.dumps(payload, ensure_ascii=True)

        estado = (data.resultado or "ERROR")[:20]
        row = LogProceso(
            servicio=servicio,
            referencia=referencia,
            fecha_inicio=now,
            fecha_fin=now,
            estado=estado,
            mensaje=mensaje,
            intentos=data.http_status,
            usuario_creacion=(data.username[:100] if data.username else None),
            fecha_creacion=now,
        )
        self.db.add(row)
        try:
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            logger.warning(
                "No se pudo escribir consumo de API en orq.log_procesos (servicio=%s). Error: %s",
                servicio,
                exc,
            )

    def get_active_user_by_username(self, username: str) -> Usuario | None:
        stmt = select(Usuario).where(Usuario.username == username).where(Usuario.activo == True)  # noqa: E712
        return self.db.scalars(stmt).first()

    def get_user_role_names(self, user: Usuario) -> list[str]:
        roles: list[str] = []
        if user.tipo:
            roles.append("ADMINISTRADOR")
        stmt = (
            select(Perfil.nombre)
            .select_from(UsuarioPerfil)
            .join(Perfil, Perfil.id_perfil == UsuarioPerfil.id_perfil)
            .where(UsuarioPerfil.id_usuario == user.id)
            .where(Perfil.activo == True)  # noqa: E712
        )
        try:
            rows = self.db.execute(stmt).scalars().all()
        except ProgrammingError as exc:
            self.db.rollback()
            logger.exception("permisos_seg_lectura_denegada: no se pudieron leer roles (seg.perfiles).")
            raise PermissionLookupFailed(_PERM_LOOKUP_USER_MESSAGE) from exc
        seen: set[str] = set()
        for nombre in rows:
            role = str(nombre).strip()
            if not role:
                continue
            key = role.casefold()
            if key in seen:
                continue
            seen.add(key)
            roles.append(role)
        if not roles:
            roles.append("USUARIO_API")
        return roles

    def user_can_autoriza_med(self, username: str) -> bool:
        user = self.get_active_user_by_username(username)
        if user is None:
            return False
        return bool(user.autoriza_med)

    def create_agendamiento(
        self,
        *,
        sede: str,
        tipo_doc: str,
        num_doc: str,
        tipo_doc_prof: str,
        num_doc_prof: str,
        fecha_cita,
        hora_cita,
        usuario_asignacion: str | None,
        especialidad: str,
        programa: str | None,
        estado: int,
        username: str,
    ) -> int:
        now = datetime.now()
        row = Agendamiento(
            sede=sede,
            tipo_doc=tipo_doc,
            num_doc=num_doc,
            tipo_doc_prof=tipo_doc_prof,
            num_doc_prof=num_doc_prof,
            fecha_cita=fecha_cita,
            hora_cita=hora_cita,
            usuario_asignacion=usuario_asignacion,
            especialidad=especialidad,
            programa=programa,
            estado=estado,
            usuario_creacion=username,
            fecha_creacion=now,
            usuario_modificacion=username,
            fecha_modificacion=now,
        )
        self.db.add(row)
        self.db.flush()
        self.db.commit()
        return int(row.id)

    def exists_agendamiento_conflicto(
        self,
        *,
        sede: str,
        tipo_doc_prof: str,
        num_doc_prof: str,
        fecha_cita,
        hora_cita,
        especialidad: str,
        estado: int,
    ) -> bool:
        stmt = (
            select(Agendamiento.id)
            .where(Agendamiento.sede == sede)
            .where(Agendamiento.tipo_doc_prof == tipo_doc_prof)
            .where(Agendamiento.num_doc_prof == num_doc_prof)
            .where(Agendamiento.fecha_cita == fecha_cita)
            .where(Agendamiento.hora_cita == hora_cita)
            .where(Agendamiento.especialidad == especialidad)
            .where(Agendamiento.estado == estado)
        )
        return self.db.execute(stmt).first() is not None

    def update_agendamiento(
        self,
        *,
        agendamiento_id: int,
        sede: str,
        tipo_doc: str,
        num_doc: str,
        tipo_doc_prof: str,
        num_doc_prof: str,
        fecha_cita,
        hora_cita,
        usuario_asignacion: str | None,
        especialidad: str,
        programa: str | None,
        estado: int,
        username: str,
    ) -> bool:
        row = self.db.get(Agendamiento, agendamiento_id)
        if row is None:
            return False
        row.sede = sede
        row.tipo_doc = tipo_doc
        row.num_doc = num_doc
        row.tipo_doc_prof = tipo_doc_prof
        row.num_doc_prof = num_doc_prof
        row.fecha_cita = fecha_cita
        row.hora_cita = hora_cita
        row.usuario_asignacion = usuario_asignacion
        row.especialidad = especialidad
        row.programa = programa
        row.estado = estado
        row.usuario_modificacion = username
        row.fecha_modificacion = datetime.now()
        self.db.add(row)
        self.db.commit()
        return True

    def update_agendamiento_estado(
        self,
        *,
        agendamiento_id: int,
        estado: int,
        username: str,
    ) -> bool:
        row = self.db.get(Agendamiento, agendamiento_id)
        if row is None:
            return False
        row.estado = estado
        row.usuario_modificacion = username
        row.fecha_modificacion = datetime.now()
        self.db.add(row)
        self.db.commit()
        return True

    def create_historia_clinica(
        self,
        *,
        identificacion_prestador: str,
        codigo_entidad_responsable: str,
        plan_beneficios: str | None,
        valor_copago_cuota_moderadora,
        tipo_identificacion_usuario: str,
        identificacion_usuario: str,
        fecha_asignacion_cita,
        fecha_atencion,
        numero_autorizacion: str | None,
        codigo_cups: str,
        codigo_causa_externa: int,
        codigo_diagnostico_principal: str,
        peso: int,
        talla: int,
        perimetro_abdominal: int,
        ta_sistolica: int,
        ta_diastolica: int,
        edad_menarquia: int,
        edad_menopausia_pnal: int,
        imc,
        actividades: list[dict],
        username: str,
    ) -> int:
        now = datetime.now()
        row = HistoriaClinica(
            identificacion_prestador=identificacion_prestador,
            codigo_entidad_responsable=codigo_entidad_responsable,
            plan_beneficios=plan_beneficios,
            valor_copago_cuota_moderadora=valor_copago_cuota_moderadora,
            tipo_identificacion_usuario=tipo_identificacion_usuario,
            identificacion_usuario=identificacion_usuario,
            fecha_asignacion_cita=fecha_asignacion_cita,
            fecha_atencion=fecha_atencion,
            numero_autorizacion=numero_autorizacion,
            codigo_cups=codigo_cups,
            codigo_causa_externa=codigo_causa_externa,
            codigo_diagnostico_principal=codigo_diagnostico_principal,
            peso=peso,
            talla=talla,
            perimetro_abdominal=perimetro_abdominal,
            ta_sistolica=ta_sistolica,
            ta_diastolica=ta_diastolica,
            edad_menarquia=edad_menarquia,
            edad_menopausia_pnal=edad_menopausia_pnal,
            imc=imc,
            usuario_creacion=username,
            fecha_creacion=now,
        )
        self.db.add(row)
        self.db.flush()
        for actividad in actividades:
            self.db.add(
                HistoriaClinicaActividad(
                    historia_id=int(row.id),
                    identificacion_profesional=str(actividad["identificacion_profesional"]),
                    tipo_identificacion_profesional=str(actividad["tipo_identificacion_profesional"]),
                    valor_consulta_procedimiento=actividad["valor_consulta_procedimiento"],
                )
            )
        self.db.commit()
        return int(row.id)

    def create_dispensacion(
        self,
        *,
        identificacion_prestador: str,
        codigo_entidad_responsable: str,
        punto_atencion: str,
        fecha,
        numero: int,
        paciente: dict,
        diagnostico: dict,
        prestador: dict,
        prescripcion: dict,
        productos: list[dict],
        username: str,
    ) -> int:
        now = datetime.now()
        row = Dispensacion(
            identificacion_prestador=identificacion_prestador,
            codigo_entidad_responsable=codigo_entidad_responsable,
            punto_atencion=punto_atencion,
            fecha=fecha,
            numero=numero,
            usuario_creacion=username,
            fecha_creacion=now,
        )
        self.db.add(row)
        self.db.flush()

        self.db.add(
            DispensacionPaciente(
                dispensacion_id=int(row.id),
                fecha_nacimiento=paciente["fecha_nacimiento"],
                tipo_identificacion_usuario=str(paciente["tipo_identificacion_usuario"]),
                identificacion_usuario=str(paciente["identificacion_usuario"]),
                movil_usuario=paciente.get("movil_usuario"),
            )
        )
        self.db.add(
            DispensacionDiagnostico(
                dispensacion_id=int(row.id),
                id_dx=diagnostico["id_dx"],
                tipo_serv=diagnostico["tipo_serv"],
                servicio=diagnostico["servicio"],
                causa_externa=diagnostico["causa_externa"],
            )
        )
        self.db.add(
            DispensacionPrestador(
                dispensacion_id=int(row.id),
                id_remitente=prestador["id_remitente"],
                id_usuario_autorizacion=prestador["id_usuario_autorizacion"],
                id_prestador=prestador["id_prestador"],
                pyp=bool(prestador["pyp"]),
                servicio_ag1=prestador.get("servicio_ag1"),
            )
        )
        self.db.add(
            DispensacionPrescripcion(
                dispensacion_id=int(row.id),
                prof_prescripcion=prescripcion["prof_prescripcion"],
                esp_profesional=prescripcion["esp_profesional"],
            )
        )
        for p in productos:
            self.db.add(
                DispensacionProducto(
                    dispensacion_id=int(row.id),
                    cod_med_insumo=p["cod_med_insumo"],
                    posologia=p["posologia"],
                    cantidad=int(p["cantidad"]),
                    valor=p["valor"],
                )
            )
        self.db.commit()
        return int(row.id)

    def update_user_last_login(self, user_id: int) -> None:
        user = self.db.get(Usuario, user_id)
        if user is None:
            return
        user.ultimo_login = datetime.now()
        self.db.add(user)
        self.db.commit()

    def _resolve_scheduler_source(self) -> str | None:
        """Determina la tabla de scheduler disponible en orq.*."""
        checks = (
            ("tareas_programadas", "tareas_programadas"),
            ("scheduler_jobs", "scheduler_jobs"),
        )
        for table_name, source in checks:
            exists = self.db.execute(
                text(
                    """
                    SELECT 1
                    FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_SCHEMA = 'orq' AND TABLE_NAME = :table_name
                    """
                ),
                {"table_name": table_name},
            ).first()
            if exists:
                return source
        return None

    def fetch_active_scheduler_jobs(self) -> list[dict]:
        source = self._resolve_scheduler_source()
        if source == "tareas_programadas":
            cols = self._table_columns("tareas_programadas")
            servicio_col = "nombre_servicio" if "nombre_servicio" in cols else "servicio"
            cron_col = "cron_expression" if "cron_expression" in cols else "expresion_cron"
            params_col = "parametros" if "parametros" in cols else None
            rows = self.db.execute(
                text(
                    f"""
                    SELECT
                        tp.id,
                        tp.{servicio_col} AS nombre_servicio,
                        tp.{cron_col} AS cron_expression,
                        tp.activo,
                        {"tp." + params_col if params_col else "NULL"} AS parametros
                    FROM orq.tareas_programadas tp
                    WHERE tp.activo = 1
                    """
                )
            ).mappings().all()
            return [dict(r) for r in rows]
        if source == "scheduler_jobs":
            stmt = select(SchedulerJob).where(SchedulerJob.activo == True)  # noqa: E712
            rows = list(self.db.scalars(stmt).all())
            return [
                {
                    "id": r.id,
                    "nombre_servicio": r.nombre_servicio,
                    "cron_expression": r.cron_expression,
                    "activo": r.activo,
                    "parametros": r.parametros,
                }
                for r in rows
            ]
        logger.warning("No se encontró tabla de tareas programadas (orq.tareas_programadas / orq.scheduler_jobs).")
        return []

    def update_job_execution(self, job_id: int, ultima_ejecucion: datetime, proxima_ejecucion: datetime | None) -> None:
        source = self._resolve_scheduler_source()
        if source == "tareas_programadas":
            self.db.execute(
                text(
                    """
                    UPDATE orq.tareas_programadas
                    SET ultima_ejecucion = :ultima_ejecucion,
                        proxima_ejecucion = :proxima_ejecucion
                    WHERE id = :job_id
                    """
                ),
                {
                    "job_id": job_id,
                    "ultima_ejecucion": ultima_ejecucion,
                    "proxima_ejecucion": proxima_ejecucion,
                },
            )
            self.db.commit()
            return
        if source == "scheduler_jobs":
            job = self.db.get(SchedulerJob, job_id)
            if job is None:
                return
            job.ultima_ejecucion = ultima_ejecucion
            job.proxima_ejecucion = proxima_ejecucion
            self.db.add(job)
            self.db.commit()

    def _permission_pairs_for_user_id(self, user_id: int) -> set[tuple[str, str]]:
        stmt = (
            select(Modulo.nombre, Accion.nombre)
            .select_from(UsuarioPerfil)
            .join(Perfil, Perfil.id_perfil == UsuarioPerfil.id_perfil)
            .join(Permiso, Permiso.id_perfil == Perfil.id_perfil)
            .join(Modulo, Modulo.id_modulo == Permiso.id_modulo)
            .join(Accion, Accion.id_accion == Permiso.id_accion)
            .where(UsuarioPerfil.id_usuario == user_id)
            .where(Perfil.activo == True)  # noqa: E712
            .where(Modulo.activo == True)  # noqa: E712
            .where(Permiso.permitido == True)  # noqa: E712
        )
        pairs: set[tuple[str, str]] = set()
        try:
            rows = self.db.execute(stmt).all()
        except ProgrammingError as exc:
            self.db.rollback()
            logger.exception(
                "permisos_seg_lectura_denegada: no se pudo resolver usuario_perfil/permisos (revise GRANT SELECT en seg.*)."
            )
            raise PermissionLookupFailed(_PERM_LOOKUP_USER_MESSAGE) from exc
        for modulo_nombre, accion_nombre in rows:
            pairs.add((str(modulo_nombre).strip().casefold(), str(accion_nombre).strip().casefold()))
        return pairs

    def user_has_permission(self, username: str, modulo: str, accion: str) -> bool:
        user = self.get_active_user_by_username(username)
        if user is None:
            return False
        if user.tipo:
            return True
        key = (modulo.strip().casefold(), accion.strip().casefold())
        return key in self._permission_pairs_for_user_id(user.id)

    def user_has_endpoint_access(self, username: str, method: str, endpoint_path: str) -> bool:
        user = self.get_active_user_by_username(username)
        if user is None:
            return False
        if user.tipo:
            return True

        # Backward compatibility: if no endpoint ACL table exists, keep current behavior.
        if not self._seg_table_exists("usuario_endpoints"):
            return True

        total_rules_stmt = (
            select(UsuarioEndpoint.id_usuario_endpoint)
            .where(UsuarioEndpoint.id_usuario == user.id)
            .where(UsuarioEndpoint.activo == True)  # noqa: E712
        )
        has_rules = self.db.execute(total_rules_stmt).first() is not None
        if not has_rules:
            # If user has no endpoint rules, do not block legacy users.
            return True

        normalized_method = method.strip().upper()
        normalized_path = endpoint_path.strip()
        match_stmt = (
            select(UsuarioEndpoint.id_usuario_endpoint)
            .where(UsuarioEndpoint.id_usuario == user.id)
            .where(UsuarioEndpoint.activo == True)  # noqa: E712
            .where(UsuarioEndpoint.permitido == True)  # noqa: E712
            .where(UsuarioEndpoint.metodo_http == normalized_method)
            .where(UsuarioEndpoint.endpoint == normalized_path)
        )
        return self.db.execute(match_stmt).first() is not None

    def get_me(self, username: str) -> MeResponse | None:
        user = self.get_active_user_by_username(username)
        if user is None:
            return None

        if user.tipo:
            try:
                modulos = list(self.db.scalars(select(Modulo).where(Modulo.activo == True)).all())  # noqa: E712
                acciones = list(self.db.scalars(select(Accion)).all())
            except ProgrammingError as exc:
                self.db.rollback()
                logger.exception("permisos_seg_lectura_denegada: get_me administrador (seg.modulos / seg.acciones).")
                raise PermissionLookupFailed(_PERM_LOOKUP_USER_MESSAGE) from exc
            return MeResponse(
                username=user.username,
                es_administrador=True,
                modulos=[
                    ModuloConAccionesDTO(
                        id_modulo=m.id_modulo,
                        nombre=m.nombre,
                        descripcion=m.descripcion,
                        activo=m.activo,
                        acciones=[AccionEnModuloDTO(nombre=a.nombre, permitido=True) for a in acciones],
                    )
                    for m in modulos
                ],
            )

        stmt = (
            select(Modulo.id_modulo, Modulo.nombre, Modulo.descripcion, Modulo.activo, Accion.nombre)
            .select_from(UsuarioPerfil)
            .join(Perfil, Perfil.id_perfil == UsuarioPerfil.id_perfil)
            .join(Permiso, Permiso.id_perfil == Perfil.id_perfil)
            .join(Modulo, Modulo.id_modulo == Permiso.id_modulo)
            .join(Accion, Accion.id_accion == Permiso.id_accion)
            .join(Usuario, Usuario.id == UsuarioPerfil.id_usuario)
            .where(Usuario.username == username)
            .where(Usuario.activo == True)  # noqa: E712
            .where(Perfil.activo == True)  # noqa: E712
            .where(Modulo.activo == True)  # noqa: E712
            .where(Permiso.permitido == True)  # noqa: E712
        )
        try:
            rows = self.db.execute(stmt).all()
        except ProgrammingError as exc:
            self.db.rollback()
            logger.exception("permisos_seg_lectura_denegada: get_me usuario con perfiles.")
            raise PermissionLookupFailed(_PERM_LOOKUP_USER_MESSAGE) from exc

        by_modulo: dict[int, dict] = {}
        seen_accion: dict[int, set[str]] = {}
        for id_m, nom_m, desc_m, act_m, nom_a in rows:
            if id_m not in by_modulo:
                by_modulo[id_m] = {
                    "nombre": nom_m,
                    "descripcion": desc_m,
                    "activo": act_m,
                    "acciones": [],
                }
                seen_accion[id_m] = set()
            k = str(nom_a).strip().casefold()
            if k in seen_accion[id_m]:
                continue
            seen_accion[id_m].add(k)
            by_modulo[id_m]["acciones"].append(AccionEnModuloDTO(nombre=str(nom_a), permitido=True))

        modulos_dto = [
            ModuloConAccionesDTO(
                id_modulo=mid,
                nombre=meta["nombre"],
                descripcion=meta["descripcion"],
                activo=meta["activo"],
                acciones=meta["acciones"],
            )
            for mid, meta in sorted(by_modulo.items(), key=lambda x: x[0])
        ]

        return MeResponse(
            username=user.username,
            es_administrador=False,
            modulos=modulos_dto,
        )

    def guardar_soporte_orden_medica_ips(
        self,
        *,
        consecutivo_solicitud: int,
        consecutivo_solicitud_ips: int | None,
        archivo_info: dict,
        usuario: str,
        ip: str | None,
    ) -> None:
        query = text("""
            INSERT INTO orq.soporte_orden_medica_ips (
                consecutivo_solicitud,
                consecutivo_solicitud_ips,
                nombre_archivo,
                ruta_archivo,
                extension,
                tipo_mime,
                tamano_bytes,
                usuario_carga,
                ip_carga
            )
            VALUES (
                :consecutivo_solicitud,
                :consecutivo_solicitud_ips,
                :nombre_archivo,
                :ruta_archivo,
                :extension,
                :tipo_mime,
                :tamano_bytes,
                :usuario_carga,
                :ip_carga
            )
        """)
    
        self.db.execute(query, {
            "consecutivo_solicitud": consecutivo_solicitud,
            "consecutivo_solicitud_ips": consecutivo_solicitud_ips,
            "nombre_archivo": archivo_info.get("nombre_archivo", "")[:255],
            "ruta_archivo": archivo_info.get("ruta_archivo", "")[:500],
            "extension": archivo_info.get("extension", "")[:10],
            "tipo_mime": archivo_info.get("tipo_mime", "")[:100],
            "tamano_bytes": archivo_info.get("tamano_bytes", 0),
            "usuario_carga": (usuario[:100] if usuario else None),
            "ip_carga": (ip[:45] if ip else None),  # IPv6 max
        })
        self.db.commit()

    