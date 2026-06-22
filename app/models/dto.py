from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class Service1ResultDTO(BaseModel):
    consecutivo_factura: str
    documento_soporte: str | None
    valor: Decimal
    saldo_factura: Decimal
    delta: Decimal

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "consecutivo_factura": "1058842",
                "documento_soporte": "DS-2026-00018",
                "valor": "150000.00",
                "saldo_factura": "120000.00",
                "delta": "30000.00",
            }
        },
    )


class ProcessLogDTO(BaseModel):
    servicio: str
    referencia: str | None
    fecha_inicio: datetime
    fecha_fin: datetime | None
    estado: str
    mensaje: str | None
    intentos: int
    usuario_creacion: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "servicio": "update_saldo_factura_factura_encabezado",
                "referencia": "1058842",
                "fecha_inicio": "2026-04-15T16:20:10",
                "fecha_fin": "2026-04-15T16:20:11",
                "estado": "SUCCESS",
                "mensaje": "Actualizacion saldo_factura completada",
                "intentos": 1,
            }
        }
    )


class ApiExecutionResponse(BaseModel):
    service_name: str
    total: int
    success: int
    errors: int
    detail: str

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "service_name": "facturas_decimales",
                    "total": 15,
                    "success": 15,
                    "errors": 0,
                    "detail": "Consulta ejecutada y resultados almacenados en SQL Server",
                },
                {
                    "service_name": "update_saldo_y_valor_factura",
                    "total": 15,
                    "success": 15,
                    "errors": 0,
                    "detail": "Ejecutado en orden: saldo encabezado, luego valor detalle",
                },
            ]
        }
    )


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AccessLogDTO(BaseModel):
    username: str | None
    resultado: str
    ip_origen: str | None = None
    mensaje: str | None = None


class AccionEnModuloDTO(BaseModel):
    nombre: str
    permitido: bool


class ModuloConAccionesDTO(BaseModel):
    id_modulo: int
    nombre: str
    descripcion: str | None = None
    activo: bool
    acciones: list[AccionEnModuloDTO]


class MeResponse(BaseModel):
    username: str
    es_administrador: bool
    modulos: list[ModuloConAccionesDTO]


class DocumentoAfiliadoRequestBase(BaseModel):
    """Criterio de documento homologado para consultas por afiliado."""

    tipo_identificacion: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description=(
            "Tipo de documento: nomenclatura de homologación (p. ej. CC, TI, RC). "
            "No distingue mayúsculas/minúsculas. También se acepta la descripción del catálogo."
        ),
    )
    numero_identificacion: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Número de identificación del afiliado.",
    )

    @field_validator("tipo_identificacion", mode="before")
    @classmethod
    def _coerce_tipo_identificacion(cls, v: Any) -> str:
        if v is None:
            raise ValueError("tipo_identificacion es obligatorio")
        s = str(v).strip()
        if not s:
            raise ValueError("tipo_identificacion no puede estar vacío")
        return s

    @field_validator("numero_identificacion", mode="before")
    @classmethod
    def _coerce_numero_identificacion(cls, v: Any) -> str:
        if v is None:
            raise ValueError("numero_identificacion es obligatorio")
        s = str(v).strip()
        if not s:
            raise ValueError("numero_identificacion no puede estar vacío")
        return s


class PortabilidadConsultaRequest(DocumentoAfiliadoRequestBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"tipo_identificacion": "CC", "numero_identificacion": "1234567890"},
                {"tipo_identificacion": "cc", "numero_identificacion": "1234567890"},
            ]
        }
    )


class PortabilidadHistorialItem(BaseModel):
    """Un registro de portabilidad / movilidad (cualquier estado)."""

    consecutivoMovilidad: int | None = Field(
        default=None,
        description="Consecutivo en af_afiliado_movilidad, si aplica.",
    )
    estadoPortabilidad: str | None = Field(
        default=None,
        description="Descripcion legible del estado de esta portabilidad.",
    )
    estadoPortabilidadCodigo: int | None = Field(
        default=None,
        description="Codigo en af_afiliado_movilidad.estado.",
    )
    ciudadOrigen: str | None = Field(default=None, description="Municipio origen (descripcion o codigo).")
    ciudadDestino: str | None = Field(default=None, description="Municipio destino (descripcion o codigo).")
    fechaInicio: date | None = None
    fechaFin: date | None = None


class PortabilidadConsultaResponse(BaseModel):
    """Respuesta CA1-CA3: afiliado activo; historial completo de portabilidades en arreglo."""

    tipo_identificacion_codigo: str = Field(
        ...,
        description="Código de tipo de identificación según administrativo.af_afiliado.tipo_identificacion.",
    )
    tipo_identificacion_descripcion: str = Field(
        ...,
        description="Descripción legible del tipo de documento según catálogo institucional.",
    )
    numero_identificacion: str = Field(..., description="Número de documento consultado.")
    nombreCompleto: str = Field(..., description="Nombres y apellidos concatenados del afiliado.")
    portabilidades: list[PortabilidadHistorialItem] = Field(
        default_factory=list,
        description="Todas las portabilidades registradas para el afiliado, sin filtrar por estado de cada una.",
    )


class PortabilidadAfiliadoItem(BaseModel):
    consecutivo_movilidad: int | None = None
    estado_portabilidad: int | None = None
    nombre_estado_portabilidad: str | None = None
    estadoPortabilidad: str | None = None
    municipio_actual: str | None = None
    municipio_receptor: str | None = None
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    estado_afiliado: int | None = None
    nombre_estado_afiliado: str | None = None
    ips_primaria: str | None = None
    ips_odontologica: str | None = None


class PortabilidadAfiliadoResponse(BaseModel):
    tipo_identificacion_codigo: str
    tipo_identificacion_descripcion: str
    numero_identificacion: str
    portabilidades: list[PortabilidadAfiliadoItem] = Field(default_factory=list)


class ConsultaAfiliadoRequest(DocumentoAfiliadoRequestBase):
    nit_ips: str | None = Field(
        default=None,
        min_length=1,
        max_length=30,
        description="Opcional. NIT de IPS para validar contrato por departamento del afiliado.",
    )

    @field_validator("nit_ips", mode="before")
    @classmethod
    def _coerce_nit_ips(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"tipo_identificacion": "CC", "numero_identificacion": "1234567890"},
                {"tipo_identificacion": "cc", "numero_identificacion": "1234567890"},
                {"tipo_identificacion": "CC", "numero_identificacion": "1234567890", "nit_ips": "900123456"},
            ]
        }
    )


class ConsultaAfiliadoItem(BaseModel):
    afiliado: int | None = None
    nombre_tipo_identificacion: str | None = None
    tipo_identificacion: int | None = None
    numero_identificacion: str | None = None
    primer_nombre: str | None = None
    segundo_nombre: str | None = None
    primer_apellido: str | None = None
    segundo_apellido: str | None = None
    fecha_nacimiento: date | None = None
    nombre_sexo: str | None = None
    sexo: int | None = None
    telefono_1: str | None = None
    celular: str | None = None
    correo_electronico: str | None = None
    direccion: str | None = None
    departamento_codigo: int | None = None
    departamento: str | None = None
    municipio_codigo: int | None = None
    municipio: str | None = None
    zona_afiliacion: int | None = None
    estrato: int | None = None
    nombre_tipo_afiliado: str | None = None
    tipo_afiliado: int | None = None
    estado_afiliado: int | None = None
    nombre_estado_afiliado: str | None = None
    nombre_tipo_regimen: str | None = None
    tipo_regimen: int | None = None
    ips_primaria: str | None = None
    ips_prim_dir: str | None = None
    ips_prim_telf: str | None = None
    ips_prim_email: str | None = None
    ips_prim_nit: str | None = None
    consecutivo_movilidad: int | None = None
    municipio_actual: str | None = None
    municipio_receptor: str | None = None
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    estado_portabilidad: str | None = None
    nombre_estado_portabilidad: str | None = None
    ips_odontologica: str | None = None
    ips_odontologica_dir: str | None = None
    ips_odontologica_telf: str | None = None
    ips_odontologica_email: str | None = None
    numero_contrato: str | None = None
    estado_contrato: int | None = None
    nombre_estado_contrato: str | None = None


class ConsultaAfiliadoResponse(BaseModel):
    tipo_identificacion_codigo: str
    tipo_identificacion_descripcion: str
    numero_identificacion: str
    afiliados: list[ConsultaAfiliadoItem] = Field(default_factory=list)


class PqrPorAfiliadoRequest(DocumentoAfiliadoRequestBase):
    consecutivo_peticion: int | None = Field(
        default=None,
        ge=1,
        description="Opcional. Si se envía, filtra una sola PQR por consecutivo.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"tipo_identificacion": "CC", "numero_identificacion": "1234567890"},
                {
                    "tipo_identificacion": "TI",
                    "numero_identificacion": "1234567890",
                    "consecutivo_peticion": 1001,
                },
                {"tipo_identificacion": "Tarjeta de Identidad", "numero_identificacion": "987654321"},
            ]
        }
    )


class PqrPorConsecutivoRequest(DocumentoAfiliadoRequestBase):
    consecutivo_peticion: int = Field(..., ge=1, description="Consecutivo de pqr_peticion_encabezado.")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "tipo_identificacion": "CC",
                    "numero_identificacion": "1234567890",
                    "consecutivo_peticion": 1001,
                }
            ]
        }
    )


class PqrResumenItem(BaseModel):
    consecutivoPeticion: int
    tipo_identificacion_codigo: str | None = Field(
        default=None,
        description="Solo en respuesta de detalle por consecutivo: código de tipo de documento del afiliado.",
    )
    tipo_identificacion_descripcion: str | None = Field(
        default=None,
        description="Solo en detalle por consecutivo: descripción del tipo de documento.",
    )
    numero_identificacion: str | None = Field(
        default=None,
        description="Solo en detalle por consecutivo: número de documento del afiliado consultado.",
    )
    tipoSolicitud: str | None = Field(default=None, description="Descripcion desde pqr_tipo_solicitud.")
    fechaRadicado: datetime | date | None = Field(
        default=None,
        description="Fecha de recepcion o radicacion (date o datetime segun el tipo de columna en BD).",
    )
    estadopqr: str | None = Field(default=None, description="Estado legible de la PQR.")
    areaResponsable: str | None = Field(
        default=None,
        description="Descripcion de `prb_dependencia` segun `consecutivo_dependencia` del encabezado.",
    )
    respuestaResumen: str | None = Field(
        default=None,
        description="Resumen de respuesta o relato resumido segun datos del encabezado.",
    )


class PqrAfiliadoListaResponse(BaseModel):
    tipo_identificacion_codigo: str = Field(
        ...,
        description="Código de tipo de identificación resuelto a partir de la petición.",
    )
    tipo_identificacion_descripcion: str = Field(..., description="Descripción del tipo de documento.")
    numero_identificacion: str = Field(..., description="Número de documento consultado.")
    pqrs: list[PqrResumenItem] = Field(default_factory=list)


class PqrAfiliadoConsultaItem(BaseModel):
    consecutivo_pqr: int
    estado_pqr: int | None = None
    nombre_estado_pqr: str | None = None
    fecha_grabado_fecha_recepcion: datetime | date | None = None
    respuesta: str | None = None
    arearesponsable: str | None = None


class PqrAfiliadoConsultaResponse(BaseModel):
    tipo_identificacion_codigo: str
    tipo_identificacion_descripcion: str
    numero_identificacion: str
    pqrs: list[PqrAfiliadoConsultaItem] = Field(default_factory=list)


class CertificadoAfiliacionRequest(DocumentoAfiliadoRequestBase):
    """Misma semántica de documento que portabilidad / PQR."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"tipo_identificacion": "CC", "numero_identificacion": "1234567890"},
                {"tipo_identificacion": "cc", "numero_identificacion": "1234567890"},
            ]
        }
    )


class CertificadoAfiliacionResponse(BaseModel):
    tipo_identificacion_codigo: str = Field(..., description="Código de tipo de documento en BD.")
    tipo_identificacion_descripcion: str = Field(..., description="Descripción del catálogo de tipos de documento.")
    numero_identificacion: str = Field(..., description="Número de documento del afiliado.")
    archivo_pdf_base64: str = Field(
        ...,
        description="Contenido del PDF en Base64 (estándar RFC 4648, ASCII en el JSON).",
    )
    nombre_archivo: str = Field(
        default="certificado_afiliacion.pdf",
        description="Nombre sugerido al guardar el binario decodificado.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tipo_identificacion_codigo": "3",
                "tipo_identificacion_descripcion": "Cédula de Ciudadania",
                "numero_identificacion": "1234567890",
                "archivo_pdf_base64": "JVBERi0xLjQKJeLjz9MKMSAwIG9iago8PC9UeXBlL0NhdGFsb2cvUGFnZXMgMiAwIFI+PmVuZG9iagolLi4uIHRyYW5jYWRvCiUlRU9G",
                "nombre_archivo": "certificado_afiliacion.pdf",
            }
        }
    )


class ActualizacionDatosMicrositioRequest(DocumentoAfiliadoRequestBase):
    barrio: str = Field(..., min_length=1, max_length=250, description="Barrio del afiliado (obligatorio).")
    direccion: str = Field(..., min_length=1, max_length=250, description="Direccion de residencia (obligatorio).")
    telefono: str | None = Field(
        default=None,
        max_length=25,
        description="Telefono fijo (opcional). Formato esperado: 9999999999.",
    )
    celular: str = Field(..., min_length=1, max_length=25, description="Celular (obligatorio). Formato esperado: 9999999999.")
    correo_electronico: str = Field(..., min_length=1, max_length=200, description="Correo electronico (obligatorio).")
    observacion: str = Field(..., min_length=1, max_length=2000, description="Observacion de la solicitud (obligatorio).")

    @field_validator("barrio", "direccion", "celular", "correo_electronico", "observacion", mode="before")
    @classmethod
    def _coerce_required_text_fields(cls, v: Any) -> str:
        if v is None:
            raise ValueError("Campo obligatorio")
        s = str(v).strip()
        if not s:
            raise ValueError("Campo obligatorio")
        return s

    @field_validator("telefono", mode="before")
    @classmethod
    def _coerce_optional_telefono(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "tipo_identificacion": "CC",
                    "numero_identificacion": "1113817562",
                    "barrio": "BARRIO CENTRO",
                    "direccion": "CL 10 # 8 - 15",
                    "telefono": "6051234567",
                    "celular": "3023413324",
                    "correo_electronico": "usuario@email.com",
                    "observacion": "Solicitud desde micrositio para actualizacion de datos.",
                }
            ]
        }
    )


class ActualizacionDatosMicrositioResponse(BaseModel):
    consecutivo_ticket: int
    tipo_identificacion_codigo: str
    tipo_identificacion_descripcion: str
    numero_identificacion: str
    afiliado: int
    mensaje: str


class AutorizacionPdfMessiah(BaseModel):
    """PDF generado con reportes Jasper de Messiah (si JasperStarter/Java están disponibles)."""

    autorizacion_pdf_base64: str | None = Field(
        default=None,
        description="PDF en Base64 (código de activación tras paso 1, o autorización completa tras paso 2).",
    )
    autorizacion_pdf_nombre: str | None = Field(
        default=None,
        description="Nombre sugerido del archivo PDF.",
    )
    pdf_generado: bool = Field(
        default=False,
        description="true si se generó y adjuntó el PDF en esta respuesta.",
    )
    pdf_aviso: str | None = Field(
        default=None,
        description="Motivo si pdf_generado=false (Jasper/Java no disponible o datos incompletos).",
    )


class AutorizacionOrdenMedicaIpsResponse(AutorizacionPdfMessiah):
    """Respuesta paso 1: solicitud registrada (sin autorización; use paso 2)."""

    consecutivo_solicitud: int
    consecutivo_solicitud_ips: int
    solicitud_usuario: int
    numero_solicitud: str | None = None
    consecutivo_autorizacion: int | None = None
    consecutivo_interno: str | None = None
    pin_activacion: str | None = None
    autorizacion_activa: bool = False
    pendiente_activacion: bool = False
    estado_trazabilidad: str | None = None
    tipo_resultado: str = "NINGUNA"
    total_solicitados: int = 0
    total_autorizados: int = 0
    total_no_autorizados: int = 0
    valor_autorizacion: float | None = None
    fecha_fin_vigencia: date | None = None
    tipo_identificacion_codigo: str
    tipo_identificacion_descripcion: str
    numero_identificacion: str
    afiliado: int
    nombre_afiliado: str
    tipo_servicio: int = 1
    estado: int = 1
    origen: str = "Orden Médica"
    origen_solicitud: str = "Orden Médica"
    origen_atencion: str = "Enfermedad General"
    modalidad_servicio: str = "Ambulatorios"
    prestador_resuelto: PrestadorIpsResuelto | None = None
    prestador_solicitante: PrestadorNitResumen | None = None
    prestador_direccionamiento: PrestadorNitResumen | None = None
    medico_solicitante: MedicoSolicitanteResumen | None = None
    ips_autorizada: DireccionamientoIpsAutorizada | None = None
    cobro: DireccionamientoCobro
    medicamentos_solicitados: list[DireccionamientoMedicamentoResultado] = Field(default_factory=list)
    medicamentos_autorizados: list[DireccionamientoMedicamentoResultado] = Field(default_factory=list)
    medicamentos_no_autorizados: list[DireccionamientoMedicamentoResultado] = Field(default_factory=list)
    mensaje: str
    soporte_registrado_messiah: bool = False
    soporte_messiah_aviso: str | None = Field(
        default=None,
        description="Aviso si el PDF no se copió a la ruta de descargas de Messiah (MESSIAH_DESCARGAS_RUTA).",
    )


class ActivacionOrdenMedicaIpsResponse(AutorizacionPdfMessiah):
    """Respuesta tras emitir, activar y confirmar autorización (paso 2)."""

    consecutivo_autorizacion: int
    consecutivo_interno: str
    consecutivo_solicitud: int
    numero_solicitud: str | None = None
    pin_activacion: str
    autorizacion_activa: bool = True
    pendiente_activacion: bool = False
    prestacion_confirmada: bool = False
    estado_trazabilidad: str = "ACTIVADA"
    valor_autorizacion: float | None = None
    fecha_fin_vigencia: date | None = None
    fecha_programacion: date | None = None
    fecha_prestacion: date | None = None
    fecha_real_prestacion_servicio: date | None = None
    tipo_identificacion_codigo: str
    tipo_identificacion_descripcion: str = ""
    numero_identificacion: str
    nombre_afiliado: str = ""
    prestador_direccionamiento: PrestadorNitResumen | None = None
    mensaje: str
    ya_activada: bool = False
    ya_confirmada: bool = False
    autorizacion_emitida: bool = Field(
        default=False,
        description="true si en esta llamada se creó ss_autorizacion desde la solicitud del paso 1.",
    )
    consecutivo_saldo: int | None = Field(
        default=None,
        description="Consecutivo sc_saldo_encabezado si se contabilizó según preferencias 288/390.",
    )
    soporte_confirmacion_registrado_messiah: bool = False
    soporte_messiah_aviso: str | None = Field(
        default=None,
        description="Aviso si el soporte de confirmación no se copió al repositorio Messiah.",
    )


class ConsumptionLogDTO(BaseModel):
    """Se persiste en `orq.log_procesos` via create_api_consumption_log (no tabla aparte)."""

    servicio: str
    username: str | None
    tipo_identificacion: str | None
    numero_identificacion: str | None
    resultado: str
    ip_origen: str | None = None
    http_status: int | None = None
    detalle: str | None = None


class AgendamientoRequest(BaseModel):
    sede: str = Field(..., min_length=1, max_length=50)
    tipoDoc: str = Field(..., min_length=1, max_length=10)
    numDoc: str = Field(..., min_length=1, max_length=20)
    tipoDoc_Prof: str = Field(..., min_length=1, max_length=10)
    numDoc_Prof: str = Field(..., min_length=1, max_length=20)
    fecha_cita: date
    hora_cita: str | None = Field(default=None, max_length=8)
    usuario_asignacion: str | None = Field(default=None, max_length=50)
    especialidad: str = Field(..., min_length=1, max_length=50)
    programa: str | None = Field(default=None, max_length=50)
    estado: int = Field(
        ...,
        ge=0,
        le=5,
        description=(
            "Estado del agendamiento: 0=Pendiente/Agendada, 1=Confirmada, 2=Cancelada, "
            "3=Atendida, 4=No asistio, 5=Reprogramada."
        ),
    )

    @field_validator("tipoDoc", "numDoc", "tipoDoc_Prof", "numDoc_Prof", mode="before")
    @classmethod
    def _coerce_string_ids(cls, v: Any) -> str:
        if v is None:
            raise ValueError("Campo obligatorio")
        s = str(v).strip()
        if not s:
            raise ValueError("Campo obligatorio")
        return s


class AgendamientoResponse(BaseModel):
    id_agendamiento: int
    mensaje: str


class AgendamientoEstadoUpdateRequest(BaseModel):
    estado: int = Field(
        ...,
        ge=0,
        le=5,
        description=(
            "Estado del agendamiento: 0=Pendiente/Agendada, 1=Confirmada, 2=Cancelada, "
            "3=Atendida, 4=No asistio, 5=Reprogramada."
        ),
    )


class HistoriaClinicaProfesionalItem(BaseModel):
    Identificacion: str = Field(..., min_length=1, max_length=20)
    TipoIdentificacion: str = Field(..., min_length=1, max_length=10)


class HistoriaClinicaActividadItem(BaseModel):
    Profesional: HistoriaClinicaProfesionalItem
    ValorConsultaProcedimiento: Decimal


class HistoriaClinicaPrestador(BaseModel):
    Identificacion: str = Field(..., min_length=1, max_length=20)


class HistoriaClinicaEntidadResponsable(BaseModel):
    Codigo: str = Field(..., min_length=1, max_length=15)
    PlanBeneficios: str | None = Field(default=None, max_length=25)
    ValorCopagoCuotaModeradora: Decimal


class HistoriaClinicaUsuario(BaseModel):
    TipoIdentificacion: str = Field(..., min_length=1, max_length=10)
    Identificacion: str = Field(..., min_length=1, max_length=20)


class HistoriaClinicaCita(BaseModel):
    FechaAsignacion: date | None = None
    FechaAtencion: date
    NumeroAutorizacion: str | None = Field(default=None, max_length=20)
    CodigoCups: str = Field(..., min_length=1, max_length=20)
    CodigoCausaExterna: int
    CodigoDiagnosticoPrincipal: str = Field(..., min_length=1, max_length=10)


class HistoriaClinicaMediciones(BaseModel):
    Peso: int
    Talla: int
    PerimetroAbdominal: int
    Tasistolica: int
    Tadiastolica: int
    EdadDeLaMenarquia: int
    EdadMenopausiaPnal: int
    IMC: Decimal


class HistoriaClinicaRequest(BaseModel):
    Prestador: HistoriaClinicaPrestador
    EntidadResponsable: HistoriaClinicaEntidadResponsable
    Usuario: HistoriaClinicaUsuario
    Cita: HistoriaClinicaCita
    Mediciones: HistoriaClinicaMediciones
    Actividades: list[HistoriaClinicaActividadItem] = Field(default_factory=list)


class HistoriaClinicaResponse(BaseModel):
    nueva_historia_id: int
    mensaje: str


class DispensacionEncabezadoPayload(BaseModel):
    IdentificacionPrestador: str = Field(..., min_length=1, max_length=20)
    CodigoEntidadResponsable: str = Field(..., min_length=1, max_length=15)
    PuntoAtencion: str = Field(..., min_length=1, max_length=50)
    Fecha: date
    Numero: int


class DispensacionPacientePayload(BaseModel):
    FechaNacimiento: date
    TipoIdentificacionUsuario: str = Field(..., min_length=1, max_length=10)
    IdentificacionUsuario: str = Field(..., min_length=1, max_length=20)
    MovilUsuario: str | None = Field(default=None, max_length=20)


class DispensacionDiagnosticoPayload(BaseModel):
    IdDx: str = Field(..., min_length=1, max_length=15)
    TipoServ: str = Field(..., min_length=1, max_length=50)
    Servicio: str = Field(..., min_length=1, max_length=50)
    CausaExterna: str = Field(..., min_length=1, max_length=50)


class DispensacionPrestadorPayload(BaseModel):
    IDRemitente: str = Field(..., min_length=1, max_length=50)
    IdUsuarioAutorizacion: str = Field(..., min_length=1, max_length=50)
    IdPrestador: str = Field(..., min_length=1, max_length=50)
    PYP: bool
    ServicioAg1: str | None = Field(default=None, max_length=50)


class DispensacionPrescripcionPayload(BaseModel):
    ProfPrescripcion: str = Field(..., min_length=1, max_length=50)
    EspProfesional: str = Field(..., min_length=1, max_length=50)


class DispensacionProductoPayload(BaseModel):
    Cod_Med_Insumo: str = Field(..., min_length=1, max_length=50)
    Posologia: str = Field(..., min_length=1, max_length=50)
    Cantidad: int
    Valor: Decimal


class DispensacionRequest(BaseModel):
    Dispensacion: DispensacionEncabezadoPayload
    Paciente: DispensacionPacientePayload
    Diagnostico: DispensacionDiagnosticoPayload
    Prestador: DispensacionPrestadorPayload
    Prescripcion: DispensacionPrescripcionPayload
    Productos: list[DispensacionProductoPayload] = Field(default_factory=list)


class DispensacionResponse(BaseModel):
    dispensacion_id: int
    mensaje: str


class DireccionamientoMedicamentoResultado(BaseModel):
    secuencia: int
    cum: str
    codigo_interno: str
    descripcion: str
    cantidad: int
    dias: int | None = None
    posologia: str | None = None
    concentracion: str | None = None
    forma_farmaceutica: str | None = None
    unidad_medida: str | None = None
    observacion: str | None = None
    autorizado: bool
    motivo: str | None = None
    pin_activacion: str | None = None
    consecutivo_autorizacion: int | None = None
    consecutivo_interno: str | None = None
    pin_codigo: str | None = None
    informacion_gestion: str | None = None
    informacion_gestion_si: str | None = None
    informacion_gestion_no: str | None = None
    valor_autorizado: float | None = None


class PrestadorIpsResuelto(BaseModel):
    nit: str
    razon_social: str
    telefono: str = ""
    email: str = ""
    municipio: str = ""
    direccion: str = ""
    sede: str = ""
    consecutivo_sede_ips: int | None = None


class MedicoSolicitanteResumen(BaseModel):
    registro_profesional: str
    nombre: str
    tipo_identificacion: int | None = None
    numero_identificacion: str | None = None
    cargo: str = ""
    especialidad: str = ""


class PrestadorNitResumen(BaseModel):
    nit: str
    razon_social: str


class DireccionamientoIpsAutorizada(BaseModel):
    razon_social: str
    direccion: str
    municipio: str
    nit: str


class DireccionamientoCobro(BaseModel):
    tipo_cobro: int | None = None
    descripcion: str
    valor_aplicar: float = 0


class DireccionamientoResponse(BaseModel):
    """Respuesta alineada con general_requests_ips (PIN, IPS autorizada, cobro)."""

    consecutivo_solicitud: int
    consecutivo_solicitud_ips: int
    solicitud_usuario: int
    numero_solicitud: str | None = None
    consecutivo_autorizacion: int | None = None
    consecutivo_interno: str | None = None
    pin_activacion: str | None = None
    autorizacion_activa: bool = False
    tipo_resultado: str = "NINGUNA"
    total_solicitados: int = 0
    total_autorizados: int = 0
    total_no_autorizados: int = 0
    valor_autorizacion: float | None = None
    fecha_fin_vigencia: date | None = None
    origen: str = "Orden Médica"
    afiliado_id: int
    tipo_identificacion: str
    numero_identificacion: str
    nombre_afiliado: str
    ips_autorizada: DireccionamientoIpsAutorizada | None = None
    cobro: DireccionamientoCobro
    medicamentos_solicitados: list[DireccionamientoMedicamentoResultado] = Field(default_factory=list)
    medicamentos_autorizados: list[DireccionamientoMedicamentoResultado] = Field(default_factory=list)
    medicamentos_no_autorizados: list[DireccionamientoMedicamentoResultado] = Field(default_factory=list)
    mensaje: str
