from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Text, Time
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SqlServerBase(DeclarativeBase):
    pass


class ResultadoProceso(SqlServerBase):
    __tablename__ = "resultados_procesos"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tipo_proceso: Mapped[str] = mapped_column(String(100), nullable=False)
    referencia: Mapped[str | None] = mapped_column(String(100), nullable=True)
    documento_soporte: Mapped[str | None] = mapped_column(String(255), nullable=True)
    valor: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    saldo_factura: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    delta: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    observacion: Mapped[str | None] = mapped_column(Text, nullable=True)
    fecha_proceso: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    usuario_creacion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    usuario_modificacion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_modificacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class LogProceso(SqlServerBase):
    __tablename__ = "log_procesos"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    servicio: Mapped[str] = mapped_column(String(100), nullable=False)
    referencia: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    fecha_fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    estado: Mapped[str] = mapped_column(String(20), nullable=False)
    mensaje: Mapped[str | None] = mapped_column(Text, nullable=True)
    intentos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usuario_creacion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class LogAcceso(SqlServerBase):
    __tablename__ = "log_accesos"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_intento: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    resultado: Mapped[str] = mapped_column(String(20), nullable=False)
    ip_origen: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mensaje: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Usuario(SqlServerBase):
    __tablename__ = "usuarios"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    ultimo_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    tipo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    autoriza_med: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tipoentidad: Mapped[str] = mapped_column(String(50), nullable=False, default="PROVEEDOR_TECNOLOGICO")
    nitentidad: Mapped[str] = mapped_column(String(20), nullable=False, default="SIN_NIT")
    nombreentidad: Mapped[str] = mapped_column(String(200), nullable=False, default="NO DEFINIDO")
    contactotecnico: Mapped[str | None] = mapped_column(String(100), nullable=True)
    correocontacto: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telefonocontacto: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ip_permitida: Mapped[str | None] = mapped_column("IpPermitida", String(500), nullable=True)


class Modulo(SqlServerBase):
    __tablename__ = "modulos"
    __table_args__ = {"schema": "seg"}

    id_modulo: Mapped[int] = mapped_column("id_modulo", Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False)


class Accion(SqlServerBase):
    __tablename__ = "acciones"
    __table_args__ = {"schema": "seg"}

    id_accion: Mapped[int] = mapped_column("id_accion", Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(50), nullable=False)


class Perfil(SqlServerBase):
    __tablename__ = "perfiles"
    __table_args__ = {"schema": "seg"}

    id_perfil: Mapped[int] = mapped_column("id_perfil", Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class Permiso(SqlServerBase):
    __tablename__ = "permisos"
    __table_args__ = {"schema": "seg"}

    id_permiso: Mapped[int] = mapped_column("id_permiso", Integer, primary_key=True, autoincrement=True)
    id_perfil: Mapped[int] = mapped_column(Integer, nullable=False)
    id_modulo: Mapped[int] = mapped_column(Integer, nullable=False)
    id_accion: Mapped[int] = mapped_column(Integer, nullable=False)
    permitido: Mapped[bool] = mapped_column(Boolean, nullable=False)


class UsuarioPerfil(SqlServerBase):
    __tablename__ = "usuario_perfil"
    __table_args__ = {"schema": "seg"}

    id_usuario_perfil: Mapped[int] = mapped_column("id_usuario_perfil", Integer, primary_key=True, autoincrement=True)
    id_usuario: Mapped[int] = mapped_column(Integer, nullable=False)
    id_perfil: Mapped[int] = mapped_column(Integer, nullable=False)


class UsuarioEndpoint(SqlServerBase):
    __tablename__ = "usuario_endpoints"
    __table_args__ = {"schema": "seg"}

    id_usuario_endpoint: Mapped[int] = mapped_column("id_usuario_endpoint", Integer, primary_key=True, autoincrement=True)
    id_usuario: Mapped[int] = mapped_column(Integer, nullable=False)
    metodo_http: Mapped[str] = mapped_column(String(10), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    permitido: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class Tercero(SqlServerBase):
    __tablename__ = "terceros"
    __table_args__ = {"schema": "dbo"}

    id_tercero: Mapped[int] = mapped_column("id_tercero", Integer, primary_key=True, autoincrement=True)
    tipo_persona: Mapped[str | None] = mapped_column(String(2), nullable=True)
    razon_social: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre_comercial: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nit: Mapped[str] = mapped_column(String(20), nullable=False)
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class Factura(SqlServerBase):
    __tablename__ = "factura"
    __table_args__ = {"schema": "dbo"}

    # PK = id SIIFA (valor explícito). Sin autoincrement para no emitir SET IDENTITY_INSERT en SQL Server.
    id_factura: Mapped[int] = mapped_column(
        "id_factura", BigInteger, primary_key=True, autoincrement=False
    )
    id_factura_emisor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    id_factura_adquiriente: Mapped[int | None] = mapped_column(Integer, nullable=True)
    indicador_tipo_operacion: Mapped[str | None] = mapped_column(String(50), nullable=True)
    profile_execution_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    numero_factura: Mapped[str] = mapped_column(String(50), nullable=False)
    cufe: Mapped[str] = mapped_column(String(100), nullable=False)
    fecha_emision: Mapped[date] = mapped_column(Date, nullable=False)
    hora_emision: Mapped[time | None] = mapped_column(Time, nullable=True)
    fecha_vencimiento: Mapped[date | None] = mapped_column(Date, nullable=True)
    tipo_factura: Mapped[str | None] = mapped_column(String(5), nullable=True)
    divisa_factura: Mapped[str | None] = mapped_column(String(5), nullable=True)
    numero_elementos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_valor_bruto: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_valor_base_imponible: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_valor_bruto_atributos: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    descuento_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    cargo_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    anticipo_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    valor_factura: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    radicado_siifa: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fecha_rad_siifa: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    id_factura_siifa: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_radica_erp: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class FacturaTercero(SqlServerBase):
    __tablename__ = "factura_tercero"
    __table_args__ = {"schema": "dbo"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_factura: Mapped[int] = mapped_column(BigInteger, nullable=False)
    id_tercero: Mapped[int] = mapped_column(Integer, nullable=False)
    rol: Mapped[str] = mapped_column(String(30), nullable=False)


class SchedulerJob(SqlServerBase):
    __tablename__ = "scheduler_jobs"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre_servicio: Mapped[str] = mapped_column(String(100), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(50), nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    parametros: Mapped[str | None] = mapped_column(Text, nullable=True)
    ultima_ejecucion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    proxima_ejecucion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    max_reintentos: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Agendamiento(SqlServerBase):
    __tablename__ = "agendamiento"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sede: Mapped[str] = mapped_column(String(50), nullable=False)
    tipo_doc: Mapped[str] = mapped_column(String(10), nullable=False)
    num_doc: Mapped[str] = mapped_column(String(20), nullable=False)
    tipo_doc_prof: Mapped[str] = mapped_column(String(10), nullable=False)
    num_doc_prof: Mapped[str] = mapped_column(String(20), nullable=False)
    fecha_cita: Mapped[date] = mapped_column(Date, nullable=False)
    hora_cita: Mapped[time | None] = mapped_column(Time, nullable=True)
    usuario_asignacion: Mapped[str | None] = mapped_column(String(50), nullable=True)
    especialidad: Mapped[str] = mapped_column(String(50), nullable=False)
    programa: Mapped[str | None] = mapped_column(String(50), nullable=True)
    estado: Mapped[int] = mapped_column(Integer, nullable=False)
    usuario_creacion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    usuario_modificacion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_modificacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class HistoriaClinica(SqlServerBase):
    __tablename__ = "historia_clinica"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identificacion_prestador: Mapped[str] = mapped_column(String(20), nullable=False)
    codigo_entidad_responsable: Mapped[str] = mapped_column(String(15), nullable=False)
    plan_beneficios: Mapped[str | None] = mapped_column(String(25), nullable=True)
    valor_copago_cuota_moderadora: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tipo_identificacion_usuario: Mapped[str] = mapped_column(String(10), nullable=False)
    identificacion_usuario: Mapped[str] = mapped_column(String(20), nullable=False)
    fecha_asignacion_cita: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_atencion: Mapped[date] = mapped_column(Date, nullable=False)
    numero_autorizacion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    codigo_cups: Mapped[str] = mapped_column(String(20), nullable=False)
    codigo_causa_externa: Mapped[int] = mapped_column(Integer, nullable=False)
    codigo_diagnostico_principal: Mapped[str] = mapped_column(String(10), nullable=False)
    peso: Mapped[int] = mapped_column(Integer, nullable=False)
    talla: Mapped[int] = mapped_column(Integer, nullable=False)
    perimetro_abdominal: Mapped[int] = mapped_column(Integer, nullable=False)
    ta_sistolica: Mapped[int] = mapped_column(Integer, nullable=False)
    ta_diastolica: Mapped[int] = mapped_column(Integer, nullable=False)
    edad_menarquia: Mapped[int] = mapped_column(Integer, nullable=False)
    edad_menopausia_pnal: Mapped[int] = mapped_column(Integer, nullable=False)
    imc: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    usuario_creacion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class HistoriaClinicaActividad(SqlServerBase):
    __tablename__ = "historia_clinica_actividad"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    historia_id: Mapped[int] = mapped_column(Integer, nullable=False)
    identificacion_profesional: Mapped[str] = mapped_column(String(20), nullable=False)
    tipo_identificacion_profesional: Mapped[str] = mapped_column(String(10), nullable=False)
    valor_consulta_procedimiento: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)


class Dispensacion(SqlServerBase):
    __tablename__ = "dispensacion"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identificacion_prestador: Mapped[str] = mapped_column(String(20), nullable=False)
    codigo_entidad_responsable: Mapped[str] = mapped_column(String(15), nullable=False)
    punto_atencion: Mapped[str] = mapped_column(String(50), nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    numero: Mapped[int] = mapped_column(Integer, nullable=False)
    usuario_creacion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class DispensacionPaciente(SqlServerBase):
    __tablename__ = "dispensacion_paciente"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dispensacion_id: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_nacimiento: Mapped[date] = mapped_column(Date, nullable=False)
    tipo_identificacion_usuario: Mapped[str] = mapped_column(String(10), nullable=False)
    identificacion_usuario: Mapped[str] = mapped_column(String(20), nullable=False)
    movil_usuario: Mapped[str | None] = mapped_column(String(20), nullable=True)


class DispensacionDiagnostico(SqlServerBase):
    __tablename__ = "dispensacion_diagnostico"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dispensacion_id: Mapped[int] = mapped_column(Integer, nullable=False)
    id_dx: Mapped[str] = mapped_column(String(15), nullable=False)
    tipo_serv: Mapped[str] = mapped_column(String(50), nullable=False)
    servicio: Mapped[str] = mapped_column(String(50), nullable=False)
    causa_externa: Mapped[str] = mapped_column(String(50), nullable=False)


class DispensacionPrestador(SqlServerBase):
    __tablename__ = "dispensacion_prestador"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dispensacion_id: Mapped[int] = mapped_column(Integer, nullable=False)
    id_remitente: Mapped[str] = mapped_column(String(50), nullable=False)
    id_usuario_autorizacion: Mapped[str] = mapped_column(String(50), nullable=False)
    id_prestador: Mapped[str] = mapped_column(String(50), nullable=False)
    pyp: Mapped[bool] = mapped_column(Boolean, nullable=False)
    servicio_ag1: Mapped[str | None] = mapped_column(String(50), nullable=True)


class DispensacionPrescripcion(SqlServerBase):
    __tablename__ = "dispensacion_prescripcion"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dispensacion_id: Mapped[int] = mapped_column(Integer, nullable=False)
    prof_prescripcion: Mapped[str] = mapped_column(String(50), nullable=False)
    esp_profesional: Mapped[str] = mapped_column(String(50), nullable=False)


class DispensacionProducto(SqlServerBase):
    __tablename__ = "dispensacion_producto"
    __table_args__ = {"schema": "orq"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dispensacion_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cod_med_insumo: Mapped[str] = mapped_column(String(50), nullable=False)
    posologia: Mapped[str] = mapped_column(String(50), nullable=False)
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
