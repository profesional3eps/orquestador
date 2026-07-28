import base64
import binascii
import json
from datetime import date, datetime, time as _time
from decimal import Decimal
from pathlib import Path
import re
from typing import Any
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.core.soporte_orden_medica import validar_soporte_orden_medica_pdf
from app.core.afiliado_estado import (
    ESTADOS_PERMITIDOS_AUTORIZACION_IPS,
    MENSAJE_ESTADO_NO_PERMITIDO_AUTORIZACION_IPS,
    nombre_estado_afiliado,
    estado_afiliado_permite_autorizacion_ips,
)

from app.core.database import get_postgres_session, get_sqlserver_session
from app.core.tipo_identificacion import resolve_tipo_identificacion
from app.core.permissions import (
    ACC_EJECUTAR,
    MOD_FACTURAS_DECIMALES,
    MOD_UPDATE_SALDO_VALOR,
)
from app.api.form_openapi import form_entero, form_fecha, validar_fechas_solicitud_no_futuras
from app.openapi_docs import (
    merge_openapi_responses,
    RESP_401_BEARER,
    RESP_401_LOGIN,
    RESP_403_PERMISOS,
    RESP_422_VALIDATION,
    RESP_500_INTERNO,
    RESP_503_SERVICIO,
)
from app.core.security import (
    create_access_token,
    enforce_user_ip_whitelist,
    get_current_username,
    require_autoriza_med,
    require_permission,
    resolve_client_ip,
    verify_password_hash,
)
from app.models.dto import (
    AccessLogDTO,
    AgendamientoEstadoUpdateRequest,
    AgendamientoRequest,
    AgendamientoResponse,
    ActualizacionDatosMicrositioResponse,
    ActivacionOrdenMedicaIpsResponse,
    AutorizacionOrdenMedicaIpsResponse,
    ApiExecutionResponse,
    CertificadoAfiliacionRequest,
    CertificadoAfiliacionResponse,
    ConsultaAfiliadoItem,
    ConsultaAfiliadoRequest,
    ConsultaAfiliadoResponse,
    ConsumptionLogDTO,
    DireccionamientoCobro,
    DireccionamientoIpsAutorizada,
    DireccionamientoMedicamentoResultado,
    HistoriaClinicaRequest,
    HistoriaClinicaResponse,
    LoginRequest,
    MeResponse,
    PortabilidadConsultaRequest,
    PortabilidadConsultaResponse,
    PqrAfiliadoListaResponse,
    PqrAfiliadoConsultaItem,
    PqrAfiliadoConsultaResponse,
    PqrPorAfiliadoRequest,
    PortabilidadAfiliadoItem,
    PortabilidadAfiliadoResponse,
    MedicoSolicitanteResumen,
    PrestadorIpsResuelto,
    PrestadorNitResumen,
    TokenResponse,
)
from app.repositories.postgres_repository import PostgresRepository
from app.repositories.sqlserver_repository import SqlServerRepository
from app.services.orchestrator_service import OrchestratorService
from app.services.pqr_consulta_service import fila_encabezado_a_resumen
from app.services.certificado_afiliacion_service import generar_certificado_pdf_base64
from app.services.portabilidad_service import (
    construir_respuesta_portabilidad,
    estado_afiliado_es_activo_para_portabilidad,
)
from app.services.fhir_historia_clinica_service import (
    FhirHistoriaClinicaError,
    map_fhir_bundle_to_historia_request,
)
from app.services.autorizacion_ips_validacion import (
    NOMBRE_ORIGEN_ATENCION_ENFERMEDAD_GENERAL,
    ORIGEN_ATENCION_ENFERMEDAD_GENERAL,
    ORIGEN_SOLICITUD_ORDEN_MEDICA_IPS,
    UBICACION_PACIENTE_ORDEN_MEDICA_IPS,
    validar_telefono_y_celular_obligatorios,
)
from app.services.autorizacion_medicamentos_ips_service import (
    activar_autorizacion_orden_medica_ips,
    procesar_autorizacion_medicamentos_orden_medica_ips,
)
from fastapi import Body

# servicio de contabilización/fin de autorización añadido aditivo
from app.services.authorization_contabilizacion_service import (
    build_authorization_handlers,
    ValidationError as AuthorizationValidationError,
)
from app.services.messiah_autorizacion_pdf_service import adjuntar_pdf_respuesta

router = APIRouter()
service = OrchestratorService()


def _map_medicamentos_resultado(items: list[dict[str, Any]]) -> list[DireccionamientoMedicamentoResultado]:
    return [DireccionamientoMedicamentoResultado(**m) for m in items]

SERVICIO_CONSULTA_PORTABILIDAD = "consulta_portabilidad"
SERVICIO_CONSULTA_PQR_AFILIADO = "consulta_pqr_por_afiliado"
SERVICIO_CERTIFICADO_AFILIACION = "generacion_certificado_afiliacion"
SERVICIO_PORTABILIDAD_RESUMEN_AFILIADO = "consulta_portabilidad_resumen_afiliado"
SERVICIO_PQR_AFILIADO_OPCIONAL = "consulta_pqr_afiliado_opcional_consecutivo"
SERVICIO_ACTUALIZACION_DATOS_MICROSITIO = "actualizacion_datos_micrositio"
SERVICIO_CONSULTA_AFILIADO = "consulta_afiliado"
SERVICIO_AUTORIZACION_ORDEN_MEDICA_IPS = "autorizacion_orden_medica_ips"
SERVICIO_AGENDAMIENTO_CREAR = "crear_agendamiento"
SERVICIO_AGENDAMIENTO_EDITAR = "editar_agendamiento"
SERVICIO_HISTORIA_CLINICA_CREAR = "registrar_historia_clinica"
SERVICIO_HISTORIA_CLINICA_FHIR_CREAR = "registrar_historia_clinica_hl7_fhir"
SERVICIO_ACTIVACION_ORDEN_MEDICA_IPS = "activacion_autorizacion_orden_medica_ips"
ESTADOS_AGENDAMIENTO_LABELS = {
    0: "Pendiente / Agendada",
    1: "Confirmada",
    2: "Cancelada",
    3: "Atendida",
    4: "No asistio (No Show)",
    5: "Reprogramada",
}
ESTADOS_CONTRATO_LABELS = {
    1: "En Trámite",
    2: "Por Legalizar",
    3: "Activo",
    4: "Inactivo",
    5: "Suspendido",
    6: "Legalizado",
    7: "En Liquidación",
    8: "Liquidado",
    9: "Anulado",
    10: "Terminado",
}
SOPORTES_DOC_IDENTIDAD_VALIDOS = {"CN", "RC", "TI", "CC", "CE", "PA", "CD", "SC", "PE", "MS", "CERTIFICADO_REGISTRADURIA"}
SOPORTES_EXT_VALIDAS = {".pdf", ".png", ".jpg", ".jpeg"}
SOPORTE_ORDEN_MEDICAMENTO = "10000019"
SOPORTE_HISTORIA_CLINICA_MEDICAMENTO = "15"
SOPORTE_ADICIONAL_AUTORIZACION_MEDICAMENTO = "10000056"

def _descripcion_soporte_confirmacion() -> str:
    max_mb = get_settings().orden_medica_soporte_max_mb
    return (
        f"Archivo PDF de soporte de confirmación de prestación (solo .pdf, contenido PDF válido). "
        f"Tamaño máximo: {max_mb} MB. Obligatorio en el paso 2 (activar)."
    )


def _descripcion_soporte_orden_medica(*, obligatorio_en_endpoint: bool) -> str:
    max_mb = get_settings().orden_medica_soporte_max_mb
    base = (
        f"Archivo PDF de la orden médica (solo .pdf, contenido PDF válido). "
        f"Tamaño máximo: {max_mb} MB (configurar ORDEN_MEDICA_SOPORTE_MAX_MB en el servidor)."
    )
    if obligatorio_en_endpoint:
        return base + " Obligatorio."
    return base + " Obligatorio salvo DIRECCIONAMIENTO_SOPORTE_OBLIGATORIO=false."


def _normalize_openapi_path(path: str) -> str:
    p = path.strip().lower()
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/") if len(p) > 1 else p


def _swagger_hidden_path_set() -> frozenset[str]:
    raw = (get_settings().swagger_hidden_paths or "").strip()
    if not raw:
        return frozenset()
    return frozenset(_normalize_openapi_path(part) for part in raw.split(",") if part.strip())


def _include_in_openapi(route_path: str) -> bool:
    return _normalize_openapi_path(route_path) not in _swagger_hidden_path_set()


def filter_openapi_hidden_paths(openapi_schema: dict) -> dict:
    """Quita de paths las rutas listadas en SWAGGER_HIDDEN_PATHS (OpenAPI 3.x)."""
    hidden = _swagger_hidden_path_set()
    if not hidden:
        return openapi_schema
    paths = openapi_schema.get("paths")
    if not paths:
        return openapi_schema
    for path in list(paths.keys()):
        if _normalize_openapi_path(path) in hidden:
            del paths[path]
    return openapi_schema


def _resolver_documento_afiliado(tipo_raw: str, numero_raw: str) -> tuple[str, str, str]:
    """Resuelve tipo a código BD + descripción catálogo; número sin espacios laterales."""
    try:
        codigo, desc = resolve_tipo_identificacion(tipo_raw)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    num = numero_raw.strip()
    return codigo, desc, num


def _parse_hora_cita_optional(value: str | None) -> _time | None:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    parts = s.split(":")
    if len(parts) < 2:
        raise ValueError("hora_cita debe tener formato HH:MM o HH:MM:SS")
    hh = int(parts[0])
    mm = int(parts[1])
    ss = int(parts[2]) if len(parts) > 2 else 0
    return _time(hh, mm, ss)


def _validar_fecha_hora_agendamiento(fecha_cita, hora_cita_raw: str | None) -> _time:
    hora_cita = _parse_hora_cita_optional(hora_cita_raw)
    if hora_cita is None:
        raise ValueError("hora_cita es obligatoria y debe tener formato HH:MM o HH:MM:SS")
    fecha_hora_cita = datetime.combine(fecha_cita, hora_cita)
    if fecha_hora_cita < datetime.now():
        raise ValueError("fecha_cita y hora_cita deben ser mayor o igual a la fecha y hora actual.")
    return hora_cita


def _decode_pdf_base64(pdf_base64: str) -> bytes:
    txt = (pdf_base64 or "").strip()
    if not txt:
        raise ValueError("historia_clinica_pdf_base64 no puede estar vacío.")
    if txt.lower().startswith("data:application/pdf;base64,"):
        txt = txt.split(",", 1)[1].strip()
    try:
        raw = base64.b64decode(txt, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("historia_clinica_pdf_base64 no es un Base64 válido.") from exc
    if not raw.startswith(b"%PDF-"):
        raise ValueError("historia_clinica_pdf_base64 no corresponde a un archivo PDF válido.")
    return raw


def _sanitizar_para_log(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _sanitizar_para_log(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitizar_para_log(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return f"<bytes len={len(value)}>"
    return value


def _detalle_traza_autorizacion_ips(
    *,
    out: dict[str, Any] | None = None,
    request_ctx: dict[str, Any] | None = None,
    error: Any = None,
    motivo: str | None = None,
) -> dict[str, Any]:
    detalle: dict[str, Any] = {}
    if motivo:
        detalle["motivo"] = motivo
    if request_ctx:
        detalle["request"] = _sanitizar_para_log(request_ctx)
    if out is not None:
        detalle["resultado"] = _sanitizar_para_log(out)
    if error is not None:
        detalle["error"] = _sanitizar_para_log(error)
    return detalle


def _registrar_consumo_api(
    sql_repo: SqlServerRepository,
    *,
    servicio: str,
    username: str,
    tipo_id: str,
    numero_id: str,
    resultado: str,
    http_status: int,
    client_ip: str | None,
    detalle: dict | None,
) -> None:
    detalle_safe = _sanitizar_para_log(detalle) if detalle is not None else None
    sql_repo.create_api_consumption_log(
        ConsumptionLogDTO(
            servicio=servicio,
            username=username,
            tipo_identificacion=tipo_id,
            numero_identificacion=numero_id,
            resultado=resultado,
            ip_origen=client_ip,
            http_status=http_status,
            detalle=json.dumps(detalle_safe, ensure_ascii=True) if detalle_safe is not None else None,
        )
    )


def _parse_json_array_field(raw_value: str | None, field_name: str) -> list[dict]:
    txt = (raw_value or "").strip()
    if not txt:
        return []
    try:
        data = json.loads(txt)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} debe ser un JSON válido.",
        ) from exc
    if not isinstance(data, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} debe ser un arreglo JSON.",
        )
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field_name} debe contener objetos JSON.",
            )
        out.append(item)
    return out


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["Auth"],
    summary="Inicio de sesión",
    description=(
        "Permite a un usuario autorizado identificarse en el sistema y obtener las credenciales "
        "necesarias para consumir el resto de servicios."
    ),
    responses=merge_openapi_responses(
        {
            200: {
                "description": "Autenticación correcta. Use `access_token` como Bearer en rutas protegidas.",
            },
        },
        RESP_401_LOGIN,
        RESP_422_VALIDATION,
    ),
)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_sqlserver_session),
) -> TokenResponse:
    settings = get_settings()
    repo = SqlServerRepository(db)
    client_ip = resolve_client_ip(request)
    user = repo.get_active_user_by_username(payload.username)

    if user is None:
        repo.create_access_log(
            AccessLogDTO(
                username=payload.username,
                resultado="ERROR",
                ip_origen=client_ip,
                mensaje="Usuario no existe o esta inactivo",
            )
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales invalidas.")

    if not verify_password_hash(payload.password, user.password_hash):
        repo.create_access_log(
            AccessLogDTO(
                username=payload.username,
                resultado="ERROR",
                ip_origen=client_ip,
                mensaje="Password invalido",
            )
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales invalidas.")

    try:
        enforce_user_ip_whitelist(
            client_ip=client_ip,
            ip_permitida=user.ip_permitida,
            username=user.username,
        )
    except HTTPException:
        repo.create_access_log(
            AccessLogDTO(
                username=payload.username,
                resultado="ERROR",
                ip_origen=client_ip,
                mensaje="IP no autorizada para el usuario",
            )
        )
        raise

    repo.update_user_last_login(user.id)
    repo.create_access_log(
        AccessLogDTO(
            username=user.username,
            resultado="SUCCESS",
            ip_origen=client_ip,
            mensaje="Login exitoso",
        )
    )
    roles = repo.get_user_role_names(user)
    token = create_access_token(user=user, roles=roles)
    return TokenResponse(access_token=token, expires_in=settings.jwt_expire_minutes * 60)


@router.get(
    "/auth/me",
    response_model=MeResponse,
    include_in_schema=_include_in_openapi("/auth/me"),
    tags=["Auth"],
    summary="Perfil y permisos del usuario",
    description=(
        "Consulta la información del usuario conectado y los permisos de operación "
        "que tiene asignados en el sistema."
    ),
    responses=merge_openapi_responses(
        {
            200: {"description": "Perfil resuelto (`MeResponse`)."},
            503: {
                "description": "No se pudieron leer módulos/permisos en base de datos (p. ej. falta SELECT en esquema seg).",
            },
        },
        RESP_401_BEARER,
    ),
)
def auth_me(username: str = Depends(get_current_username), db: Session = Depends(get_sqlserver_session)) -> MeResponse:
    repo = SqlServerRepository(db)
    me = repo.get_me(username)
    if me is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado o inactivo.")
    return me


@router.get(
    "/health",
    include_in_schema=_include_in_openapi("/health"),
    tags=["Health"],
    summary="Estado del servicio",
    description="Verifica que el servicio esté disponible y respondiendo correctamente.",
    responses=merge_openapi_responses(
        {200: {"description": "Servicio en ejecución y token válido."}},
        RESP_401_BEARER,
    ),
)
def health(_: str = Depends(get_current_username)) -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    "/consultas/portabilidad",
    response_model=PortabilidadConsultaResponse,
    tags=["Procesos Afiliado"],
    summary="Historial de portabilidad del afiliado",
    description=(
        "Consulta los movimientos de portabilidad registrados para un afiliado con afiliación vigente. "
        "Entrega el historial completo de traslados entre municipios o entidades."
    ),
    responses=merge_openapi_responses(
        {
            200: {"description": "Afiliado activo; cuerpo `PortabilidadConsultaResponse`."},
            400: {"description": "Afiliado existe pero no está en estado activo (no aplica la consulta CA1)."},
            404: {"description": "No existe afiliado para el tipo y número de documento."},
        },
        RESP_401_BEARER,
        RESP_422_VALIDATION,
        RESP_500_INTERNO,
    ),
)
def consulta_portabilidad(
    payload: PortabilidadConsultaRequest,
    request: Request,
    username: str = Depends(get_current_username),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> PortabilidadConsultaResponse:
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    pg_repo = PostgresRepository(pg)
    codigo_tipo, tipo_desc, numero_id = _resolver_documento_afiliado(
        payload.tipo_identificacion, payload.numero_identificacion
    )
    tipo_id = codigo_tipo

    try:
        base = pg_repo.fetch_afiliado_resumen_por_documento(codigo_tipo, numero_id)
        if base is None:
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_CONSULTA_PORTABILIDAD,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "afiliado_no_encontrado"},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Afiliado no encontrado para el tipo y numero de documento indicados.",
            )
        if not estado_afiliado_es_activo_para_portabilidad(base.get("estado_afiliado")):
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_CONSULTA_PORTABILIDAD,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=400,
                client_ip=client_ip,
                detalle={
                    "motivo": "afiliado_no_activo",
                    "estado_afiliado": base.get("estado_afiliado"),
                    "afiliado": base.get("afiliado"),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El afiliado no esta en estado de afiliacion activo; no se consulta la portabilidad.",
            )
        mov_rows = pg_repo.fetch_todas_portabilidades_por_afiliado(base.get("afiliado"))
        out = construir_respuesta_portabilidad(
            base,
            mov_rows,
            tipo_identificacion_codigo=codigo_tipo,
            tipo_identificacion_descripcion=tipo_desc,
            numero_identificacion=numero_id,
        )
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_CONSULTA_PORTABILIDAD,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=200,
            client_ip=client_ip,
            detalle={
                "afiliado": base.get("afiliado"),
                "total_portabilidades": len(out.portabilidades),
            },
        )
        return out
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_CONSULTA_PORTABILIDAD,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "error_interno", "error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post(
    "/consultas/pqr/por-afiliado",
    response_model=PqrAfiliadoListaResponse,
    tags=["Procesos Afiliado"],
    summary="Consulta de PQR del afiliado",
    description=(
        "Permite consultar las peticiones, quejas y reclamos asociados a un afiliado. "
        "Puede filtrarse por número de radicado o listar todas las solicitudes registradas."
    ),
    responses=merge_openapi_responses(
        {
            200: {"description": "Listado (`PqrAfiliadoListaResponse`)."},
            404: {"description": "Afiliado no encontrado para el documento indicado."},
        },
        RESP_401_BEARER,
        RESP_422_VALIDATION,
        RESP_500_INTERNO,
    ),
)
def consulta_pqr_por_afiliado(
    payload: PqrPorAfiliadoRequest,
    request: Request,
    username: str = Depends(get_current_username),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> PqrAfiliadoListaResponse:
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    pg_repo = PostgresRepository(pg)
    codigo_tipo, tipo_desc, numero_id = _resolver_documento_afiliado(
        payload.tipo_identificacion, payload.numero_identificacion
    )
    tipo_id = codigo_tipo

    consec = payload.consecutivo_peticion

    try:
        if pg_repo.fetch_afiliado_resumen_por_documento(codigo_tipo, numero_id) is None:
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_CONSULTA_PQR_AFILIADO,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "afiliado_no_encontrado"},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Afiliado no encontrado para el tipo y numero de documento indicados.",
            )
        if consec is None:
            raw = pg_repo.fetch_pqrs_resumen_por_documento_afiliado(codigo_tipo, numero_id)
        else:
            one = pg_repo.fetch_pqr_resumen_por_consecutivo_y_documento(codigo_tipo, numero_id, consec)
            raw = [one] if one is not None else []
        out = PqrAfiliadoListaResponse(
            tipo_identificacion_codigo=codigo_tipo,
            tipo_identificacion_descripcion=tipo_desc,
            numero_identificacion=numero_id,
            pqrs=[fila_encabezado_a_resumen(r) for r in raw],
        )
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_CONSULTA_PQR_AFILIADO,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=200,
            client_ip=client_ip,
            detalle={"total_pqr": len(out.pqrs), "consecutivo_peticion": consec},
        )
        return out
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_CONSULTA_PQR_AFILIADO,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "error_interno", "error": str(exc), "consecutivo_peticion": consec},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get(
    "/consultas/afiliado/portabilidad",
    response_model=PortabilidadAfiliadoResponse,
    include_in_schema=_include_in_openapi("/consultas/afiliado/portabilidad"),
    tags=["Procesos Afiliado"],
    summary="Detalle de portabilidad del afiliado",
    description=(
        "Consulta la situación de portabilidad de un afiliado: municipio de atención, fechas, "
        "IPS asignada y estado actual de la afiliación."
    ),
    responses=merge_openapi_responses(
        {
            200: {"description": "Detalle de portabilidad del afiliado."},
            404: {"description": "Afiliado no encontrado para el documento indicado."},
        },
        RESP_401_BEARER,
        RESP_500_INTERNO,
    ),
)
def consulta_portabilidad_afiliado_detalle(
    tipo_identificacion: str,
    numero_identificacion: str,
    request: Request,
    username: str = Depends(get_current_username),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> PortabilidadAfiliadoResponse:
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    pg_repo = PostgresRepository(pg)
    codigo_tipo, tipo_desc, numero_id = _resolver_documento_afiliado(tipo_identificacion, numero_identificacion)
    tipo_id = codigo_tipo

    try:
        base = pg_repo.fetch_afiliado_resumen_por_documento(codigo_tipo, numero_id)
        if base is None:
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_PORTABILIDAD_RESUMEN_AFILIADO,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "afiliado_no_encontrado"},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Afiliado no encontrado para el tipo y numero de documento indicados.",
            )

        raw = pg_repo.fetch_portabilidad_detalle_por_documento(codigo_tipo, numero_id)
        out = PortabilidadAfiliadoResponse(
            tipo_identificacion_codigo=codigo_tipo,
            tipo_identificacion_descripcion=tipo_desc,
            numero_identificacion=numero_id,
            portabilidades=[PortabilidadAfiliadoItem(**r) for r in raw],
        )
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_PORTABILIDAD_RESUMEN_AFILIADO,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=200,
            client_ip=client_ip,
            detalle={"total_portabilidades": len(out.portabilidades)},
        )
        return out
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_PORTABILIDAD_RESUMEN_AFILIADO,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "error_interno", "error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get(
    "/consultas/afiliado/pqr",
    response_model=PqrAfiliadoConsultaResponse,
    include_in_schema=_include_in_openapi("/consultas/afiliado/pqr"),
    tags=["Procesos Afiliado"],
    summary="Consulta de PQR del afiliado",
    description=(
        "Permite consultar las peticiones, quejas y reclamos de un afiliado. "
        "Opcionalmente se puede filtrar por un radicado específico."
    ),
    responses=merge_openapi_responses(
        {
            200: {"description": "Listado de PQR (o una sola si se filtró por consecutivo)."},
            404: {"description": "Afiliado no encontrado para el documento indicado."},
        },
        RESP_401_BEARER,
        RESP_500_INTERNO,
    ),
)
def consulta_pqr_afiliado_opcional_consecutivo(
    tipo_identificacion: str,
    numero_identificacion: str,
    request: Request,
    consecutivo_pqr: int | None = None,
    username: str = Depends(get_current_username),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> PqrAfiliadoConsultaResponse:
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    pg_repo = PostgresRepository(pg)
    codigo_tipo, tipo_desc, numero_id = _resolver_documento_afiliado(tipo_identificacion, numero_identificacion)
    tipo_id = codigo_tipo

    try:
        base = pg_repo.fetch_afiliado_resumen_por_documento(codigo_tipo, numero_id)
        if base is None:
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_PQR_AFILIADO_OPCIONAL,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "afiliado_no_encontrado", "consecutivo_pqr": consecutivo_pqr},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Afiliado no encontrado para el tipo y numero de documento indicados.",
            )

        rows = pg_repo.fetch_pqr_por_documento_con_opcional_consecutivo(codigo_tipo, numero_id, consecutivo_pqr)
        out = PqrAfiliadoConsultaResponse(
            tipo_identificacion_codigo=codigo_tipo,
            tipo_identificacion_descripcion=tipo_desc,
            numero_identificacion=numero_id,
            pqrs=[PqrAfiliadoConsultaItem(**r) for r in rows],
        )
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_PQR_AFILIADO_OPCIONAL,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=200,
            client_ip=client_ip,
            detalle={"consecutivo_pqr": consecutivo_pqr, "total_pqr": len(out.pqrs)},
        )
        return out
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_PQR_AFILIADO_OPCIONAL,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "error_interno", "error": str(exc), "consecutivo_pqr": consecutivo_pqr},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post(
    "/consultas/afiliado",
    response_model=ConsultaAfiliadoResponse,
    tags=["Procesos Afiliado"],
    summary="Consulta de datos del afiliado",
    description=(
        "Entrega la información general del afiliado: identificación, ubicación, estado de afiliación, "
        "régimen, IPS y movilidad."
    ),
    responses=merge_openapi_responses(
        {
            200: {"description": "Consulta realizada correctamente."},
            404: {"description": "No existe afiliado para el documento indicado."},
        },
        RESP_401_BEARER,
        RESP_422_VALIDATION,
        RESP_500_INTERNO,
    ),
)
def consulta_afiliado(
    payload: ConsultaAfiliadoRequest,
    request: Request,
    username: str = Depends(get_current_username),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> ConsultaAfiliadoResponse:
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    pg_repo = PostgresRepository(pg)
    codigo_tipo, tipo_desc, numero_id = _resolver_documento_afiliado(
        payload.tipo_identificacion,
        payload.numero_identificacion,
    )
    tipo_id = codigo_tipo

    try:
        rows = pg_repo.fetch_consulta_afiliado_por_documento(codigo_tipo, numero_id)
        if not rows:
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_CONSULTA_AFILIADO,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "afiliado_no_encontrado"},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Afiliado no encontrado para el tipo y numero de documento indicados.",
            )

        nit_ips = payload.nit_ips
        if nit_ips:
            ips_row = pg_repo.fetch_ips_por_nit(nit_ips)
            if ips_row is None:
                _registrar_consumo_api(
                    sql_repo,
                    servicio=SERVICIO_CONSULTA_AFILIADO,
                    username=username,
                    tipo_id=tipo_id,
                    numero_id=numero_id,
                    resultado="ERROR",
                    http_status=404,
                    client_ip=client_ip,
                    detalle={"motivo": "nit_ips_no_encontrado", "nit_ips": nit_ips},
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="El nit_ips no existe en administrativo.ct_ips.",
                )

            # El contrato se valida contra el departamento del primer registro afiliado.
            depto_raw = rows[0].get("departamento_codigo")
            if depto_raw is None:
                _registrar_consumo_api(
                    sql_repo,
                    servicio=SERVICIO_CONSULTA_AFILIADO,
                    username=username,
                    tipo_id=tipo_id,
                    numero_id=numero_id,
                    resultado="ERROR",
                    http_status=400,
                    client_ip=client_ip,
                    detalle={"motivo": "afiliado_sin_departamento_codigo", "nit_ips": nit_ips},
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="El afiliado no tiene departamento_codigo para validar contrato IPS.",
                )

            depto_codigo = str(depto_raw).strip().zfill(2)[:2]
            contrato = pg_repo.fetch_contrato_ips_por_departamento(int(ips_row["ips"]), depto_codigo)
            if contrato is None:
                _registrar_consumo_api(
                    sql_repo,
                    servicio=SERVICIO_CONSULTA_AFILIADO,
                    username=username,
                    tipo_id=tipo_id,
                    numero_id=numero_id,
                    resultado="ERROR",
                    http_status=404,
                    client_ip=client_ip,
                    detalle={
                        "motivo": "contrato_ips_no_encontrado",
                        "nit_ips": nit_ips,
                        "ips": ips_row.get("ips"),
                        "departamento_codigo": depto_codigo,
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No existe contrato IPS para el departamento del afiliado.",
                )

            estado_contrato = int(contrato.get("estado_contrato")) if contrato.get("estado_contrato") is not None else None
            for r in rows:
                r["numero_contrato"] = str(contrato.get("numero_contrato")) if contrato.get("numero_contrato") is not None else None
                r["estado_contrato"] = estado_contrato
                r["nombre_estado_contrato"] = ESTADOS_CONTRATO_LABELS.get(estado_contrato)

        out = ConsultaAfiliadoResponse(
            tipo_identificacion_codigo=codigo_tipo,
            tipo_identificacion_descripcion=tipo_desc,
            numero_identificacion=numero_id,
            afiliados=[ConsultaAfiliadoItem(**r) for r in rows],
        )
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_CONSULTA_AFILIADO,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=200,
            client_ip=client_ip,
            detalle={"total_registros": len(out.afiliados), "nit_ips": nit_ips},
        )
        return out
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_CONSULTA_AFILIADO,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "error_interno", "error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post(
    "/agendamientos",
    response_model=AgendamientoResponse,
    tags=["Procesos IPS"],
    summary="Registrar cita médica",
    description=(
        "Agenda una cita para un afiliado activo en la sede, especialidad y fecha indicadas."
    ),
    responses=merge_openapi_responses(
        {
            201: {"description": "Agendamiento creado exitosamente."},
            400: {"description": "Afiliado no activo o datos inválidos."},
        },
        RESP_401_BEARER,
        RESP_500_INTERNO,
    ),
)
def crear_agendamiento(
    payload: AgendamientoRequest,
    request: Request,
    username: str = Depends(get_current_username),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> AgendamientoResponse:
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    pg_repo = PostgresRepository(pg)
    codigo_tipo, _, numero_id = _resolver_documento_afiliado(
        str(payload.tipoDoc), str(payload.numDoc)
    )
    tipo_id = str(payload.tipoDoc).strip()

    try:
        hora_cita_val = _validar_fecha_hora_agendamiento(payload.fecha_cita, payload.hora_cita)

        if not pg_repo.afiliado_activo_por_documento(codigo_tipo, numero_id):
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_AGENDAMIENTO_CREAR,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=400,
                client_ip=client_ip,
                detalle={"motivo": "afiliado_no_activo_o_no_existe"},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se puede crear el agendamiento porque el afiliado no está activo o no existe.",
            )

        if sql_repo.exists_agendamiento_conflicto(
            sede=payload.sede.strip(),
            tipo_doc_prof=str(payload.tipoDoc_Prof).strip(),
            num_doc_prof=str(payload.numDoc_Prof).strip(),
            fecha_cita=payload.fecha_cita,
            hora_cita=hora_cita_val,
            especialidad=payload.especialidad.strip(),
            estado=payload.estado,
        ):
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_AGENDAMIENTO_CREAR,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=409,
                client_ip=client_ip,
                detalle={
                    "motivo": "cita_duplicada",
                    "sede": payload.sede.strip(),
                    "tipo_doc_prof": str(payload.tipoDoc_Prof).strip(),
                    "num_doc_prof": str(payload.numDoc_Prof).strip(),
                    "fecha_cita": str(payload.fecha_cita),
                    "hora_cita": str(hora_cita_val),
                    "especialidad": payload.especialidad.strip(),
                    "estado": payload.estado,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Ya existe una cita agendada con la misma fecha, hora, sede, profesional, "
                    "especialidad y estado."
                ),
            )

        new_id = sql_repo.create_agendamiento(
            sede=payload.sede.strip(),
            tipo_doc=tipo_id,
            num_doc=numero_id,
            tipo_doc_prof=str(payload.tipoDoc_Prof).strip(),
            num_doc_prof=str(payload.numDoc_Prof).strip(),
            fecha_cita=payload.fecha_cita,
            hora_cita=hora_cita_val,
            usuario_asignacion=(payload.usuario_asignacion.strip() if payload.usuario_asignacion else None),
            especialidad=payload.especialidad.strip(),
            programa=(payload.programa.strip() if payload.programa else None),
            estado=payload.estado,
            username=username,
        )
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_AGENDAMIENTO_CREAR,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=201,
            client_ip=client_ip,
            detalle={
                "id_agendamiento": new_id,
                "estado": payload.estado,
                "estado_nombre": ESTADOS_AGENDAMIENTO_LABELS.get(payload.estado),
            },
        )
        return AgendamientoResponse(id_agendamiento=new_id, mensaje="Agendamiento creado con éxito")
    except HTTPException:
        raise
    except ValueError as exc:
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_AGENDAMIENTO_CREAR,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=400,
            client_ip=client_ip,
            detalle={"motivo": "validacion", "error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_AGENDAMIENTO_CREAR,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "error_interno", "error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.put(
    "/agendamientos/{id_agendamiento}",
    response_model=AgendamientoResponse,
    tags=["Procesos IPS"],
    summary="Actualizar estado de la cita",
    description=(
        "Modifica el estado de una cita previamente registrada "
        "(confirmada, cancelada, atendida, no asistió, reprogramada, etc.)."
    ),
    responses=merge_openapi_responses(
        {
            200: {"description": "Agendamiento actualizado."},
            404: {"description": "Agendamiento no encontrado."},
        },
        RESP_401_BEARER,
        RESP_500_INTERNO,
    ),
)
def editar_agendamiento(
    id_agendamiento: int,
    payload: AgendamientoEstadoUpdateRequest,
    request: Request,
    username: str = Depends(get_current_username),
    sql: Session = Depends(get_sqlserver_session),
) -> AgendamientoResponse:
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    tipo_id = "AGENDAMIENTO"
    numero_id = str(id_agendamiento)

    try:
        ok = sql_repo.update_agendamiento_estado(
            agendamiento_id=id_agendamiento,
            estado=payload.estado,
            username=username,
        )
        if not ok:
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_AGENDAMIENTO_EDITAR,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "agendamiento_no_encontrado", "id_agendamiento": id_agendamiento},
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agendamiento no encontrado.")

        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_AGENDAMIENTO_EDITAR,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=200,
            client_ip=client_ip,
            detalle={
                "id_agendamiento": id_agendamiento,
                "estado": payload.estado,
                "estado_nombre": ESTADOS_AGENDAMIENTO_LABELS.get(payload.estado),
            },
        )
        return AgendamientoResponse(id_agendamiento=id_agendamiento, mensaje="Agendamiento editado con éxito")
    except HTTPException:
        raise
    except ValueError as exc:
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_AGENDAMIENTO_EDITAR,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=400,
            client_ip=client_ip,
            detalle={"motivo": "validacion", "error": str(exc), "id_agendamiento": id_agendamiento},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_AGENDAMIENTO_EDITAR,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "error_interno", "error": str(exc), "id_agendamiento": id_agendamiento},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post(
    "/historia_clinica",
    response_model=HistoriaClinicaResponse,
    tags=["Procesos IPS"],
    summary="Registrar historia clínica",
    description=(
        "Registra una atención ambulatoria del afiliado, incluyendo diagnóstico, procedimiento "
        "realizado y datos clínicos de la consulta."
    ),
    responses=merge_openapi_responses(
        {
            201: {"description": "Historia clínica creada."},
            400: {"description": "Error de validación."},
            404: {"description": "Afiliado / IPS / CIE10 / CUPS no encontrado."},
        },
        RESP_401_BEARER,
        RESP_500_INTERNO,
    ),
)
def registrar_historia_clinica(
    payload: HistoriaClinicaRequest,
    request: Request,
    username: str = Depends(get_current_username),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> HistoriaClinicaResponse:
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    pg_repo = PostgresRepository(pg)
    codigo_tipo, _, numero_id = _resolver_documento_afiliado(
        str(payload.Usuario.TipoIdentificacion), str(payload.Usuario.Identificacion)
    )
    tipo_id = str(payload.Usuario.TipoIdentificacion).strip()

    try:
        if not pg_repo.afiliado_activo_por_documento(codigo_tipo, numero_id):
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_HISTORIA_CLINICA_CREAR,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "afiliado_no_activo_o_no_existe"},
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El afiliado no está activo o no existe.")

        nit_prestador = str(payload.Prestador.Identificacion).strip()
        if not pg_repo.existe_ips_por_nit(nit_prestador):
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_HISTORIA_CLINICA_CREAR,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "ips_no_encontrada", "nit_prestador": nit_prestador},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El prestador con identificación proporcionada no existe.",
            )

        codigo_entidad = str(payload.EntidadResponsable.Codigo).strip().upper()
        if codigo_entidad not in {"CCF033", "CCFC33"}:
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_HISTORIA_CLINICA_CREAR,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=400,
                client_ip=client_ip,
                detalle={"motivo": "codigo_entidad_responsable_invalido", "codigo": codigo_entidad},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='El Código de la Entidad Responsable no es válido. Debe ser "CCF033" o "CCFC33".',
            )

        if not pg_repo.existe_cie10(payload.Cita.CodigoDiagnosticoPrincipal):
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_HISTORIA_CLINICA_CREAR,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "cie10_no_encontrado", "codigo": payload.Cita.CodigoDiagnosticoPrincipal},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El Código Diagnóstico Principal no existe en la base de datos.",
            )

        if not pg_repo.existe_cup(payload.Cita.CodigoCups):
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_HISTORIA_CLINICA_CREAR,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "cup_no_encontrado", "codigo": payload.Cita.CodigoCups},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="El Código CUPS no existe en la base de datos.",
            )

        actividades = [
            {
                "identificacion_profesional": a.Profesional.Identificacion,
                "tipo_identificacion_profesional": a.Profesional.TipoIdentificacion,
                "valor_consulta_procedimiento": a.ValorConsultaProcedimiento,
            }
            for a in payload.Actividades
        ]

        historia_id = sql_repo.create_historia_clinica(
            identificacion_prestador=nit_prestador,
            codigo_entidad_responsable=codigo_entidad,
            plan_beneficios=payload.EntidadResponsable.PlanBeneficios,
            valor_copago_cuota_moderadora=payload.EntidadResponsable.ValorCopagoCuotaModeradora,
            tipo_identificacion_usuario=tipo_id,
            identificacion_usuario=numero_id,
            fecha_asignacion_cita=payload.Cita.FechaAsignacion,
            fecha_atencion=payload.Cita.FechaAtencion,
            numero_autorizacion=payload.Cita.NumeroAutorizacion,
            codigo_cups=payload.Cita.CodigoCups,
            codigo_causa_externa=payload.Cita.CodigoCausaExterna,
            codigo_diagnostico_principal=payload.Cita.CodigoDiagnosticoPrincipal,
            peso=payload.Mediciones.Peso,
            talla=payload.Mediciones.Talla,
            perimetro_abdominal=payload.Mediciones.PerimetroAbdominal,
            ta_sistolica=payload.Mediciones.Tasistolica,
            ta_diastolica=payload.Mediciones.Tadiastolica,
            edad_menarquia=payload.Mediciones.EdadDeLaMenarquia,
            edad_menopausia_pnal=payload.Mediciones.EdadMenopausiaPnal,
            imc=payload.Mediciones.IMC,
            actividades=actividades,
            username=username,
        )
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_HISTORIA_CLINICA_CREAR,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=201,
            client_ip=client_ip,
            detalle={"nueva_historia_id": historia_id, "total_actividades": len(actividades)},
        )
        return HistoriaClinicaResponse(nueva_historia_id=historia_id, mensaje="Historia clínica creada con éxito")
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_HISTORIA_CLINICA_CREAR,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "error_interno", "error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post(
    "/historia_clinica/hl7",
    response_model=HistoriaClinicaResponse,
    tags=["Procesos IPS"],
    summary="Registrar historia clínica (intercambio FHIR)",
    description=(
        "Registra una atención clínica a partir de un mensaje estándar de salud (HL7 FHIR), "
        "incluyendo el documento PDF de soporte de la atención."
    ),
    responses=merge_openapi_responses(
        {
            201: {"description": "Historia clínica creada desde FHIR."},
            400: {"description": "Error de mapeo/validación del payload FHIR."},
            404: {"description": "Afiliado / IPS / CIE10 / CUPS no encontrado."},
        },
        RESP_401_BEARER,
        RESP_500_INTERNO,
    ),
)
def registrar_historia_clinica_hl7_fhir(
    payload: dict[str, Any],
    request: Request,
    username: str = Depends(get_current_username),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> HistoriaClinicaResponse:
    sql_repo = SqlServerRepository(sql)
    client_ip = request.client.host if request.client else None

    try:
        bundle_raw = payload.get("bundle")
        if not isinstance(bundle_raw, dict):
            raise FhirHistoriaClinicaError(
                "FHIR inválido: el payload debe incluir `bundle` con un Bundle FHIR."
            )
        pdf_b64 = payload.get("historia_clinica_pdf_base64") or payload.get("historiaClinicaPdfBase64")
        if pdf_b64 is None:
            raise FhirHistoriaClinicaError(
                "El campo historia_clinica_pdf_base64 es obligatorio."
            )
        pdf_len = len(_decode_pdf_base64(str(pdf_b64)))

        bundle = bundle_raw
        historia_payload = map_fhir_bundle_to_historia_request(bundle)
    except FhirHistoriaClinicaError as exc:
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_HISTORIA_CLINICA_FHIR_CREAR,
            username=username,
            tipo_id="FHIR",
            numero_id="BUNDLE",
            resultado="ERROR",
            http_status=400,
            client_ip=client_ip,
            detalle={"motivo": "fhir_mapping_error", "error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_HISTORIA_CLINICA_FHIR_CREAR,
            username=username,
            tipo_id="FHIR",
            numero_id="BUNDLE",
            resultado="ERROR",
            http_status=400,
            client_ip=client_ip,
            detalle={"motivo": "pdf_base64_invalido", "error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_HISTORIA_CLINICA_FHIR_CREAR,
            username=username,
            tipo_id="FHIR",
            numero_id="BUNDLE",
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "fhir_mapping_error_interno", "error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    try:
        out = registrar_historia_clinica(
            payload=historia_payload,
            request=request,
            username=username,
            pg=pg,
            sql=sql,
        )
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_HISTORIA_CLINICA_FHIR_CREAR,
            username=username,
            tipo_id=str(historia_payload.Usuario.TipoIdentificacion),
            numero_id=str(historia_payload.Usuario.Identificacion),
            resultado="SUCCESS",
            http_status=201,
            client_ip=client_ip,
            detalle={"historia_clinica_pdf_recibido": True, "pdf_bytes": pdf_len},
        )
        return out
    except HTTPException:
        raise


@router.post(
    "/consultas/certificado-afiliacion",
    response_model=CertificadoAfiliacionResponse,
    tags=["Procesos Afiliado"],
    summary="Certificado de afiliación",
    description=(
        "Genera el certificado de afiliación en formato PDF para afiliados con vigencia activa."
    ),
    responses=merge_openapi_responses(
        {
            200: {"description": "PDF generado; cuerpo `CertificadoAfiliacionResponse` (`archivo_pdf_base64`)."},
            400: {"description": "Afiliado no activo: no procede expedir certificado."},
            404: {"description": "No existe afiliado para el documento indicado."},
        },
        RESP_401_BEARER,
        RESP_422_VALIDATION,
        RESP_500_INTERNO,
        RESP_503_SERVICIO,
    ),
)
def consulta_certificado_afiliacion(
    payload: CertificadoAfiliacionRequest,
    request: Request,
    username: str = Depends(get_current_username),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> CertificadoAfiliacionResponse:
    settings = get_settings()
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    pg_repo = PostgresRepository(pg)
    codigo_tipo, tipo_desc, numero_id = _resolver_documento_afiliado(
        payload.tipo_identificacion,
        payload.numero_identificacion,
    )
    tipo_id = codigo_tipo

    try:
        row = pg_repo.fetch_afiliado_datos_certificado_por_documento(codigo_tipo, numero_id)
        if row is None:
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_CERTIFICADO_AFILIACION,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "afiliado_no_encontrado"},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Afiliado no encontrado para el tipo y numero de documento indicados.",
            )
        relaciones_laborales = []
        if str(row.get("des_tipo_reg") or "").strip().lower() == "contributivo" and row.get("afiliado") is not None:
            relaciones_laborales = pg_repo.fetch_relaciones_laborales_certificado(int(row["afiliado"]))
        b64 = generar_certificado_pdf_base64(
            settings,
            row,
            tipo_desc,
            relaciones_laborales=relaciones_laborales,
        )
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_CERTIFICADO_AFILIACION,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=200,
            client_ip=client_ip,
            detalle={"pdf_base64_chars": len(b64)},
        )
        return CertificadoAfiliacionResponse(
            tipo_identificacion_codigo=codigo_tipo,
            tipo_identificacion_descripcion=tipo_desc,
            numero_identificacion=numero_id,
            archivo_pdf_base64=b64,
        )
    except HTTPException:
        raise
    except FileNotFoundError as fnf:
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_CERTIFICADO_AFILIACION,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "plantilla_no_encontrada", "error": str(fnf)},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(fnf)) from fnf
    except RuntimeError as rerr:
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_CERTIFICADO_AFILIACION,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=503,
            client_ip=client_ip,
            detalle={"motivo": "pdf_o_libreoffice", "error": str(rerr)},
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(rerr)) from rerr
    except Exception as exc:  # pragma: no cover
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_CERTIFICADO_AFILIACION,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "error_interno", "error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post(
    "/afiliados/actualizacion-datos-micrositio",
    response_model=ActualizacionDatosMicrositioResponse,
    tags=["Procesos Afiliado"],
    summary="Actualización de datos del afiliado (micrositio)",
    description=(
        "Registra la solicitud de actualización de datos de contacto y ubicación del afiliado "
        "realizada desde el micrositio, con soporte documental opcional."
    ),
    responses=merge_openapi_responses(
        {
            200: {"description": "Ticket registrado correctamente."},
            400: {"description": "Afiliado menor de edad o dato de contacto con formato inválido."},
            404: {"description": "Afiliado no encontrado."},
        },
        RESP_401_BEARER,
        RESP_422_VALIDATION,
        RESP_500_INTERNO,
    ),
)
def registrar_actualizacion_datos_micrositio(
    request: Request,
    tipo_identificacion: str = Form(...),
    numero_identificacion: str = Form(...),
    barrio: str = Form(...),
    direccion: str = Form(...),
    telefono: str | None = Form(None),
    celular: str = Form(...),
    correo_electronico: str = Form(...),
    observacion: str = Form(...),
    tipo_soporte_documento: str | None = Form(
        None,
        description="Tipo de soporte: CN, RC, TI, CC, CE, PA, CD, SC, PE, MS o CERTIFICADO_REGISTRADURIA.",
    ),
    soporte_documento: UploadFile | None = File(
        None,
        description="Archivo soporte opcional del documento de identificación.",
    ),
    username: str = Depends(get_current_username),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> ActualizacionDatosMicrositioResponse:
    settings = get_settings()
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    pg_repo = PostgresRepository(pg)
    codigo_tipo, tipo_desc, numero_id = _resolver_documento_afiliado(
        tipo_identificacion,
        numero_identificacion,
    )
    tipo_id = codigo_tipo

    try:
        barrio_norm = barrio.strip()
        direccion_norm = direccion.strip()
        telefono_norm = (telefono or "").strip() or None
        celular_norm = celular.strip()
        correo_norm = correo_electronico.strip()
        observacion_norm = observacion.strip()
        if not all([barrio_norm, direccion_norm, celular_norm, correo_norm, observacion_norm]):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="barrio, direccion, celular, correo_electronico y observacion son obligatorios.",
            )

        soporte_filename: str | None = None
        tipo_soporte_norm: str | None = None
        if soporte_documento is not None:
            if not soporte_documento.filename:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El soporte no contiene nombre de archivo.")
            tipo_soporte_norm = (tipo_soporte_documento or "").strip().upper()
            tipo_soporte_norm = re.sub(r"\s+", "_", tipo_soporte_norm)
            if tipo_soporte_norm == "CERTIFICADO_DE_LA_REGISTRADURIA":
                tipo_soporte_norm = "CERTIFICADO_REGISTRADURIA"
            if tipo_soporte_norm not in SOPORTES_DOC_IDENTIDAD_VALIDOS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "tipo_soporte_documento inválido. Use uno de: CN, RC, TI, CC, CE, PA, CD, SC, PE, MS, "
                        "CERTIFICADO_REGISTRADURIA."
                    ),
                )
            ext = Path(soporte_documento.filename).suffix.lower()
            if ext not in SOPORTES_EXT_VALIDAS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Extensión de soporte no permitida. Solo PDF, PNG o JPG/JPEG.",
                )
            data = soporte_documento.file.read()
            max_bytes = int(settings.ticket_support_max_mb) * 1024 * 1024
            if len(data) == 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo soporte está vacío.")
            if len(data) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"El soporte supera el tamaño máximo permitido de {settings.ticket_support_max_mb} MB.",
                )
            settings.ticket_supports_dir.mkdir(parents=True, exist_ok=True)
            safe_doc = re.sub(r"[^A-Za-z0-9_-]+", "_", numero_id)
            support_name = f"ticket_doc_{safe_doc}_{uuid.uuid4().hex[:10]}_{tipo_soporte_norm}{ext}"
            support_path = settings.ticket_supports_dir / support_name
            with support_path.open("wb") as f:
                f.write(data)
            soporte_filename = support_name
        elif tipo_soporte_documento:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Si envía tipo_soporte_documento también debe adjuntar soporte_documento.",
            )

        afiliado = pg_repo.fetch_afiliado_para_actualizacion_datos(codigo_tipo, numero_id)
        if afiliado is None:
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_ACTUALIZACION_DATOS_MICROSITIO,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=404,
                client_ip=client_ip,
                detalle={"motivo": "afiliado_no_encontrado"},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Afiliado no encontrado para el tipo y numero de documento indicados.",
            )

        edad = afiliado.get("edad_anios")
        if edad is None or int(edad) < 18:
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_ACTUALIZACION_DATOS_MICROSITIO,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=400,
                client_ip=client_ip,
                detalle={"motivo": "afiliado_menor_edad", "edad_anios": edad},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El afiliado debe ser mayor de edad para registrar actualización de datos por micrositio.",
            )

        if not pg_repo.validar_dato_contacto(telefono_norm, "telefono"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Formato inválido para telefono. Debe usar 9999999999.",
            )
        if not pg_repo.validar_dato_contacto(celular_norm, "celular"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Formato inválido para celular. Debe usar 9999999999.",
            )
        if not pg_repo.validar_dato_contacto(correo_norm, "correo"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Formato inválido para correo_electronico.",
            )

        consecutivo_ticket = pg_repo.create_ticket_actualizacion_datos_micrositio(
            afiliado_data=afiliado,
            usuario_grabado=username,
            observacion=observacion_norm,
            telefono=telefono_norm,
            celular=celular_norm,
            direccion=direccion_norm,
            correo_electronico=correo_norm,
            barrio=barrio_norm,
            soporte_url=soporte_filename,
        )

        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_ACTUALIZACION_DATOS_MICROSITIO,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=200,
            client_ip=client_ip,
            detalle={
                "consecutivo_ticket": consecutivo_ticket,
                "afiliado": afiliado.get("afiliado"),
                "tipo_proceso": 1,
                "origen_solicitud": 2,
                "soporte_adjuntado": bool(soporte_filename),
                "tipo_soporte_documento": tipo_soporte_norm,
            },
        )
        return ActualizacionDatosMicrositioResponse(
            consecutivo_ticket=consecutivo_ticket,
            tipo_identificacion_codigo=codigo_tipo,
            tipo_identificacion_descripcion=tipo_desc,
            numero_identificacion=numero_id,
            afiliado=int(afiliado["afiliado"]),
            mensaje=f"Solicitud registrada con numero {consecutivo_ticket}.",
        )
    except HTTPException as exc:
        if exc.status_code != 404 and exc.status_code != 500:
            _registrar_consumo_api(
                sql_repo,
                servicio=SERVICIO_ACTUALIZACION_DATOS_MICROSITIO,
                username=username,
                tipo_id=tipo_id,
                numero_id=numero_id,
                resultado="ERROR",
                http_status=exc.status_code,
                client_ip=client_ip,
                detalle={"motivo": "validacion", "error": exc.detail},
            )
        raise
    except Exception as exc:  # pragma: no cover
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_ACTUALIZACION_DATOS_MICROSITIO,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle={"motivo": "error_interno", "error": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post(
    "/afiliados/autorizacion-orden-medica-ips",
    response_model=AutorizacionOrdenMedicaIpsResponse,
    include_in_schema=_include_in_openapi("/afiliados/autorizacion-orden-medica-ips"),
    tags=["Procesos IPS"],
    summary="Solicitud de medicamentos IPS por orden médica (paso 1)",
    description=(
        "Paso 1 — Solicitud de medicamentos IPS (Messiah authorization_request_ips). "
        "Crea ss_solicitud, evalúa medicamentos y registra ct_ips_ss_solicitud (estado 1). "
        "No genera ss_autorizacion; use el paso 2 con consecutivo_solicitud para emitir, activar y confirmar. "
        "Origen Enfermedad General, modalidad Ambulatorios. CUM en tarifarios de contratos activos con cobertura del municipio del afiliado."
    ),
    responses=merge_openapi_responses(
        {
            200: {"description": "Solicitud registrada. Pendiente de autorización en paso 2."},
            400: {
                "description": (
                    "Datos incompletos, afiliado no elegible, medicamento o diagnóstico no válido, "
                    "validación de medicamentos no cumplida (ningún medicamento autorizado; no se crea solicitud), "
                    "o soporte_orden_medica inválido (solo PDF, tamaño máximo configurado)."
                ),
            },
            403: {"description": "El usuario no tiene permiso para autorizar medicamentos."},
            404: {"description": "Afiliado, IPS, médico o medicamento no encontrado."},
        },
        RESP_401_BEARER,
        RESP_422_VALIDATION,
        RESP_500_INTERNO,
    ),
)
def registrar_autorizacion_orden_medica_ips(
    request: Request,
    tipo_identificacion: str = Form(...),
    numero_identificacion: str = Form(...),
    observacion: str = Form(...),
    email: str | None = Form(None, description="Correo de contacto. Si no se envía, se toma del afiliado o de la IPS."),
    telefono: str = Form(..., description="Teléfono de contacto (obligatorio). Formato: 9999999999."),
    celular: str = Form(..., description="Celular de contacto (obligatorio). Formato: 9999999999."),
    prioridad_atencion: int = form_entero("Prioridad de atención (smallint).", default=1),
    justificacion_clinica: str | None = Form(None),
    diagnostico_principal: str = Form(..., description="Diagnóstico principal (CIE-10, validación TB_CIE10)."),
    diagnostico_relacionado_1: str | None = Form(
        None, description="Diagnóstico relacionado 1 (CIE-10). Opcional; misma validación que el principal."
    ),
    diagnostico_relacionado_2: str | None = Form(
        None, description="Diagnóstico relacionado 2 (CIE-10). Opcional; misma validación que el principal."
    ),
    nit_ips_prestador: str = Form(..., description="NIT del prestador solicitante (CT_IPS)."),
    nit_ips_direccionamiento: str = Form(
        ...,
        description="NIT del prestador donde se direcciona/autoriza. Debe tener el CUM en tarifario vigente para el municipio del afiliado.",
    ),
    consecutivo_sede_ips: int | None = form_entero(
        "Sede del prestador (integer, ct_ips_sede.consecutivo_sede_ips). Vacío = primera sede."
    ),
    telefono_institucional_extension: str | None = Form(None, description="Extensión teléfono institucional (opcional)."),
    prestador_solicitante: str | None = Form(None, description="Nombre IPS solicitante. Opcional si envía NIT."),
    municipio_prestador: str | None = Form(None, description="Municipio prestador. Opcional; se completa desde sede/IPS."),
    fecha_solicitud_proceso: date = form_fecha(
        "Fecha de registro de la solicitud (date, ss_solicitud.fecha_solicitud_proceso). ISO YYYY-MM-DD."
    ),
    fecha_solicitud_medico: date = form_fecha(
        "Fecha de la orden médica (date, ss_solicitud.fecha_solicitud_medico). ISO YYYY-MM-DD."
    ),
    registro_profesional: str = Form(
        ...,
        description="Número de registro profesional del médico que ordena los medicamentos.",
    ),
    medicamentos_json: str = Form(
        ...,
        description=(
            "Listado de medicamentos solicitados. Por cada uno: código CUM, cantidad, días de tratamiento "
            "y observación opcional."
        ),
    ),
    soporte_orden_medica: UploadFile | None = File(
        None,
        description=_descripcion_soporte_orden_medica(obligatorio_en_endpoint=True),
    ),
    username: str = Depends(require_autoriza_med),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> AutorizacionOrdenMedicaIpsResponse:
    settings = get_settings()
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    codigo_tipo, tipo_desc, numero_id = _resolver_documento_afiliado(tipo_identificacion, numero_identificacion)
    tipo_id = codigo_tipo
    pg_repo = PostgresRepository(pg)
    tipo_doc_abrev = pg_repo._tipo_documento_abreviatura(codigo_tipo)

    request_ctx: dict[str, Any] = {
        "origen_solicitud": ORIGEN_SOLICITUD_ORDEN_MEDICA_IPS,
        "origen_atencion": NOMBRE_ORIGEN_ATENCION_ENFERMEDAD_GENERAL,
        "ubicacion_paciente": UBICACION_PACIENTE_ORDEN_MEDICA_IPS,
        "nit_ips_prestador": (nit_ips_prestador or "").strip() or None,
        "nit_ips_direccionamiento": (nit_ips_direccionamiento or "").strip(),
        "diagnostico_principal": diagnostico_principal.strip(),
        "registro_profesional": registro_profesional.strip(),
    }



    try:
        medicamentos = _parse_json_array_field(medicamentos_json, "medicamentos_json")
        request_ctx["total_medicamentos"] = len(medicamentos)
        request_ctx["cums"] = [str(m.get("cum") or "").strip() for m in medicamentos]
        validar_fechas_solicitud_no_futuras(fecha_solicitud_proceso, fecha_solicitud_medico)

        soporte_data: bytes | None = None
        soporte_filename: str | None = None
        if soporte_orden_medica is not None and soporte_orden_medica.filename:
            soporte_filename = soporte_orden_medica.filename
            soporte_data = soporte_orden_medica.file.read()

        out = procesar_autorizacion_medicamentos_orden_medica_ips(
            pg=pg,
            settings=settings,
            username=username,
            codigo_tipo=codigo_tipo,
            numero_id=numero_id,
            tipo_doc_abrev=tipo_doc_abrev,
            origen_solicitud=ORIGEN_SOLICITUD_ORDEN_MEDICA_IPS,
            observacion=observacion,
            email=email,
            telefono=telefono,
            celular=celular,
            prioridad_atencion=prioridad_atencion,
            ubicacion_paciente=UBICACION_PACIENTE_ORDEN_MEDICA_IPS,
            servicio_hospitalario=None,
            numero_cama=None,
            justificacion_clinica=justificacion_clinica,
            diagnostico_principal=diagnostico_principal,
            diagnostico_relacionado_1=diagnostico_relacionado_1,
            diagnostico_relacionado_2=diagnostico_relacionado_2,
            prestador_solicitante=prestador_solicitante,
            nit_ips_prestador=nit_ips_prestador,
            nit_ips_direccionamiento=nit_ips_direccionamiento,
            consecutivo_sede_ips=consecutivo_sede_ips,
            telefono_institucional_extension=telefono_institucional_extension,
            municipio_prestador=municipio_prestador,
            fecha_solicitud_proceso=fecha_solicitud_proceso,
            fecha_solicitud_medico=fecha_solicitud_medico,
            registro_profesional=registro_profesional,
            medicamentos=medicamentos,
            insumos=[],
            consecutivo_ips=None,
            soporte_orden_medica_filename=soporte_filename,
            soporte_orden_medica_data=soporte_data,
            soporte_obligatorio=True,
            soporte_subdir="autorizacion_orden_medica_ips",
        )

        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_AUTORIZACION_ORDEN_MEDICA_IPS,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=200,
            client_ip=client_ip,
            detalle=_detalle_traza_autorizacion_ips(
                out=out,
                request_ctx={
                    **request_ctx,
                    "consecutivo_solicitud_ips": out.get("consecutivo_solicitud_ips"),
                    "tipo_resultado": out.get("tipo_resultado"),
                },
                motivo="success",
            ),
        )

        ips_auth = out.get("ips_autorizada")
        prestador = out.get("prestador_resuelto")

        # Validar antes de convertir
        if not out.get("consecutivo_solicitud"):
            raise ValueError("consecutivo_solicitud no puede ser None")
        
        #inserta datos del soporte en la db sqlserver
        sql_repo.guardar_soporte_orden_medica_ips(
            consecutivo_solicitud=int(out["consecutivo_solicitud"]) or "",
            consecutivo_solicitud_ips=int(out["consecutivo_solicitud_ips"]) or "",
            archivo_info={
                "nombre_archivo": soporte_filename or f"{out['consecutivo_solicitud']}.pdf",
                "ruta_archivo": f"sie_descargas/soporte_ips_solicitud_autorizacion/{out['consecutivo_solicitud_ips']}.pdf",
                "extension": ".pdf",
                "tipo_mime": soporte_orden_medica.content_type if soporte_orden_medica else "application/pdf",
                "tamano_bytes": len(soporte_data) if soporte_data else 0,
            },
            usuario=username,
            ip=client_ip,
        )

        pg_repo.create_soporte_orden_medica(
            consecutivo_solicitud=int(out["consecutivo_solicitud"]) or "",
                consecutivo_soporte="10000019",
                archivo_info={
                    "nombre_archivo": soporte_filename or f"{out['consecutivo_solicitud']}.pdf",
                    "ruta_archivo": f"sie_descargas/soporte_ips_solicitud_autorizacion/{out['consecutivo_solicitud_ips']}.pdf",
                    "extension": ".pdf",
                    "tipo_mime": soporte_orden_medica.content_type if soporte_orden_medica else "application/pdf",
                    "tamano_bytes": len(soporte_data) if soporte_data else 0,
                }
        )

        return AutorizacionOrdenMedicaIpsResponse(
            consecutivo_solicitud=int(out["consecutivo_solicitud"]),
            consecutivo_solicitud_ips=int(out["consecutivo_solicitud_ips"]),
            solicitud_usuario=int(out["solicitud_usuario"]),
            numero_solicitud=out.get("numero_solicitud"),
            consecutivo_autorizacion=out.get("consecutivo_autorizacion"),
            consecutivo_interno=out.get("consecutivo_interno"),
            pin_activacion=out.get("pin_activacion"),
            autorizacion_activa=bool(out.get("autorizacion_activa")),
            pendiente_activacion=bool(out.get("pendiente_activacion")),
            estado_trazabilidad=out.get("estado_trazabilidad"),
            tipo_resultado=str(out.get("tipo_resultado") or "NINGUNA"),
            total_solicitados=int(out.get("total_solicitados") or 0),
            total_autorizados=int(out.get("total_autorizados") or 0),
            total_no_autorizados=int(out.get("total_no_autorizados") or 0),
            valor_autorizacion=out.get("valor_autorizacion"),
            fecha_fin_vigencia=out.get("fecha_fin_vigencia"),
            tipo_identificacion_codigo=codigo_tipo,
            tipo_identificacion_descripcion=tipo_desc,
            numero_identificacion=numero_id,
            afiliado=int(out["afiliado_id"]),
            nombre_afiliado=str(out["nombre_afiliado"]),
            tipo_servicio=1,
            estado=2 if out.get("consecutivo_autorizacion") else 1,
            origen=str(out.get("origen") or NOMBRE_ORIGEN_ATENCION_ENFERMEDAD_GENERAL),
            origen_solicitud=str(out.get("origen_solicitud") or "Orden Médica"),
            origen_atencion=NOMBRE_ORIGEN_ATENCION_ENFERMEDAD_GENERAL,
            modalidad_servicio=str(out.get("modalidad_servicio") or "Ambulatorios"),
            prestador_resuelto=PrestadorIpsResuelto(**prestador) if prestador else None,
            prestador_solicitante=(
                PrestadorNitResumen(**out["prestador_solicitante"])
                if out.get("prestador_solicitante")
                else None
            ),
            prestador_direccionamiento=(
                PrestadorNitResumen(**out["prestador_direccionamiento"])
                if out.get("prestador_direccionamiento")
                else None
            ),
            medico_solicitante=(
                MedicoSolicitanteResumen(**out["medico_solicitante"])
                if out.get("medico_solicitante")
                else None
            ),
            ips_autorizada=(
                DireccionamientoIpsAutorizada(**ips_auth) if ips_auth else None
            ),
            cobro=DireccionamientoCobro(**out["cobro"]),
            medicamentos_solicitados=_map_medicamentos_resultado(out.get("medicamentos_solicitados") or []),
            medicamentos_autorizados=_map_medicamentos_resultado(out["medicamentos_autorizados"]),
            medicamentos_no_autorizados=_map_medicamentos_resultado(out["medicamentos_no_autorizados"]),
            mensaje=str(out["mensaje"]),
            soporte_registrado_messiah=bool(out.get("soporte_registrado_messiah")),
            soporte_messiah_aviso=out.get("soporte_messiah_aviso"),
            autorizacion_pdf_base64=out.get("autorizacion_pdf_base64"),
            autorizacion_pdf_nombre=out.get("autorizacion_pdf_nombre"),
            pdf_generado=bool(out.get("pdf_generado")),
            pdf_aviso=out.get("pdf_aviso"),
        )
    except HTTPException as exc:
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_AUTORIZACION_ORDEN_MEDICA_IPS,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=exc.status_code,
            client_ip=client_ip,
            detalle=_detalle_traza_autorizacion_ips(
                request_ctx={**request_ctx, "http_status": exc.status_code},
                error=exc.detail,
                motivo="validacion" if exc.status_code < 500 else "error_http",
            ),
        )
        raise
    except Exception as exc:  # pragma: no cover
        pg.rollback()
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_AUTORIZACION_ORDEN_MEDICA_IPS,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle=_detalle_traza_autorizacion_ips(
                request_ctx=request_ctx,
                error=str(exc),
                motivo="error_interno",
            ),
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post(
    "/afiliados/autorizacion-orden-medica-ips/activar",
    response_model=ActivacionOrdenMedicaIpsResponse,
    include_in_schema=_include_in_openapi("/afiliados/autorizacion-orden-medica-ips/activar"),
    tags=["Procesos IPS"],
    summary="Activación y confirmación de autorización IPS por orden médica (paso 2)",
    description=(
        "Paso 2 — Emisión, activación y confirmación Messiah (SsAutorizacionActivacion). "
        "Requiere consecutivo_solicitud del paso 1 (o pin_activacion si ya fue emitida). "
        "Emite ss_autorizacion, activa (ACTIVADA), confirma prestación (COMPLETADA) y contabiliza "
        "si tb_preferencia tipo 288 (confirmación) o 390 (activación) está activa. "
        "Requiere soporte_confirmacion (PDF obligatorio). "
        "Devuelve PDF de autorización prestada (reAutorizacion.jasper, PRESTADO=1) en autorizacion_pdf_base64."
    ),
    responses=merge_openapi_responses(
        {
            200: {"description": "Autorización emitida/activada y prestación confirmada correctamente."},
            400: {
                "description": (
                    "soporte_confirmacion ausente o inválido (PDF obligatorio), "
                    "o validación de fechas/documento."
                ),
            },
            403: {"description": "El usuario no tiene permiso para autorizar medicamentos."},
            404: {
                "description": (
                    "No existe solicitud o autorización para el documento, consecutivo_solicitud/PIN e IPS indicados."
                ),
            },
            409: {"description": "La autorización ya fue activada o no está disponible."},
        },
        RESP_401_BEARER,
        RESP_422_VALIDATION,
        RESP_500_INTERNO,
    ),
)
def activar_autorizacion_orden_medica_ips_endpoint(
    request: Request,
    tipo_identificacion: str = Form(...),
    numero_identificacion: str = Form(...),
    pin_activacion: str | None = Form(
        None,
        description="PIN devuelto tras emitir la autorización en este paso. Opcional si envía consecutivo_solicitud.",
    ),
    consecutivo_solicitud: int | None = form_entero(
        "Consecutivo ss_solicitud devuelto en el paso 1. Obligatorio si no envía pin_activacion.",
    ),
    nit_ips_direccionamiento: str = Form(
        ...,
        description="NIT de la IPS donde se autorizó (mismo valor del paso 1).",
    ),
    fecha_programacion: date | None = form_fecha(
        "Fecha de programación de medicamentos (ss_autorizacion_medicamento.fecha_programacion). "
        "Por defecto: hoy. ISO YYYY-MM-DD.",
        requerido=False,
    ),
    fecha_prestacion: date | None = form_fecha(
        "Fecha de prestación del servicio (ss_autorizacion.fecha_real_prestacion_servicio). "
        "Por defecto: hoy. ISO YYYY-MM-DD.",
        requerido=False,
    ),
    confirmar_prestacion: bool = Form(
        True,
        description="Si true (default), confirma la prestación y devuelve PDF con PRESTADO=1.",
    ),
    soporte_confirmacion: UploadFile = File(
        ...,
        description=_descripcion_soporte_confirmacion(),
    ),
    username: str = Depends(require_autoriza_med),
    pg: Session = Depends(get_postgres_session),
    sql: Session = Depends(get_sqlserver_session),
) -> ActivacionOrdenMedicaIpsResponse:
    settings = get_settings()
    client_ip = request.client.host if request.client else None
    sql_repo = SqlServerRepository(sql)
    codigo_tipo, tipo_desc, numero_id = _resolver_documento_afiliado(tipo_identificacion, numero_identificacion)
    tipo_id = codigo_tipo
    pg_repo = PostgresRepository(pg)
    tipo_doc_abrev = pg_repo._tipo_documento_abreviatura(codigo_tipo)

    soporte_confirmacion_filename: str | None = None
    soporte_confirmacion_data: bytes | None = None
    if not soporte_confirmacion.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe adjuntar soporte_confirmacion (archivo PDF).",
        )
    soporte_confirmacion_filename = soporte_confirmacion.filename
    soporte_confirmacion_data = soporte_confirmacion.file.read()

    request_ctx: dict[str, Any] = {
        "nit_ips_direccionamiento": (nit_ips_direccionamiento or "").strip(),
        "consecutivo_solicitud": consecutivo_solicitud,
        "pin_activacion": "***" if pin_activacion else None,
        "confirmar_prestacion": confirmar_prestacion,
    }

    try:
        out = activar_autorizacion_orden_medica_ips(
            pg=pg,
            settings=settings,
            username=username,
            codigo_tipo=codigo_tipo,
            numero_id=numero_id,
            tipo_doc_abrev=tipo_doc_abrev,
            pin_activacion=pin_activacion,
            consecutivo_solicitud=consecutivo_solicitud,
            nit_ips_direccionamiento=nit_ips_direccionamiento,
            fecha_programacion=fecha_programacion,
            fecha_prestacion=fecha_prestacion,
            confirmar_prestacion=confirmar_prestacion,
            soporte_confirmacion_filename=soporte_confirmacion_filename,
            soporte_confirmacion_data=soporte_confirmacion_data,
        )
        pg.commit()

        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_ACTIVACION_ORDEN_MEDICA_IPS,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="SUCCESS",
            http_status=200,
            client_ip=client_ip,
            detalle=_detalle_traza_autorizacion_ips(
                out=out,
                request_ctx=request_ctx,
                motivo="success",
            ),
        )

        prestador = out.get("prestador_direccionamiento")

        # Validar antes de convertir
        if not out.get("consecutivo_solicitud"):
            raise ValueError("consecutivo_solicitud no puede ser None")
        
        #inserta datos del soporte en la db
        sql_repo.guardar_soporte_orden_medica_ips(
            consecutivo_solicitud=int(out["consecutivo_solicitud"]) or "",
            consecutivo_solicitud_ips=int(out["numero_solicitud"]) or "",
            archivo_info={
                "nombre_archivo": soporte_confirmacion_filename or f"{out['consecutivo_autorizacion']}.pdf",
                "ruta_archivo": f"sie_descargas/autorizacion_confirmada/{out['consecutivo_autorizacion']}-{soporte_confirmacion_filename}",
                "extension": ".pdf",
                "tipo_mime": soporte_confirmacion.content_type or "application/pdf",
                "tamano_bytes": len(soporte_confirmacion_data) if soporte_confirmacion_data else 0,
            },
            usuario=username,
            ip=client_ip,
        )

        return ActivacionOrdenMedicaIpsResponse(
            consecutivo_autorizacion=int(out["consecutivo_autorizacion"]),
            consecutivo_interno=str(out["consecutivo_interno"]),
            consecutivo_solicitud=int(out["consecutivo_solicitud"]),
            numero_solicitud=out.get("numero_solicitud"),
            pin_activacion=str(out["pin_activacion"]),
            autorizacion_activa=bool(out.get("autorizacion_activa")),
            pendiente_activacion=bool(out.get("pendiente_activacion")),
            prestacion_confirmada=bool(out.get("prestacion_confirmada")),
            estado_trazabilidad=str(out.get("estado_trazabilidad") or "ACTIVADA"),
            valor_autorizacion=out.get("valor_autorizacion"),
            fecha_fin_vigencia=out.get("fecha_fin_vigencia"),
            fecha_programacion=out.get("fecha_programacion"),
            fecha_prestacion=out.get("fecha_prestacion"),
            fecha_real_prestacion_servicio=out.get("fecha_real_prestacion_servicio"),
            tipo_identificacion_codigo=codigo_tipo,
            tipo_identificacion_descripcion=tipo_desc,
            numero_identificacion=numero_id,
            nombre_afiliado=str(out.get("nombre_afiliado") or ""),
            prestador_direccionamiento=(
                PrestadorNitResumen(**prestador) if prestador else None
            ),
            mensaje=str(out["mensaje"]),
            ya_activada=bool(out.get("ya_activada")),
            ya_confirmada=bool(out.get("ya_confirmada")),
            soporte_confirmacion_registrado_messiah=bool(
                out.get("soporte_confirmacion_registrado_messiah")
            ),
            soporte_messiah_aviso=out.get("soporte_messiah_aviso"),
            autorizacion_pdf_base64=out.get("autorizacion_pdf_base64"),
            autorizacion_pdf_nombre=out.get("autorizacion_pdf_nombre"),
            pdf_generado=bool(out.get("pdf_generado")),
            pdf_aviso=out.get("pdf_aviso"),
            consecutivo_saldo=out.get("consecutivo_saldo"),
            autorizacion_emitida=bool(out.get("autorizacion_emitida")),
        )
    except HTTPException as exc:
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_ACTIVACION_ORDEN_MEDICA_IPS,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=exc.status_code,
            client_ip=client_ip,
            detalle=_detalle_traza_autorizacion_ips(
                request_ctx={**request_ctx, "http_status": exc.status_code},
                error=exc.detail,
                motivo="validacion" if exc.status_code < 500 else "error_http",
            ),
        )
        raise
    except Exception as exc:  # pragma: no cover
        pg.rollback()
        _registrar_consumo_api(
            sql_repo,
            servicio=SERVICIO_ACTIVACION_ORDEN_MEDICA_IPS,
            username=username,
            tipo_id=tipo_id,
            numero_id=numero_id,
            resultado="ERROR",
            http_status=500,
            client_ip=client_ip,
            detalle=_detalle_traza_autorizacion_ips(
                request_ctx=request_ctx,
                error=str(exc),
                motivo="error_interno",
            ),
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc




## Endpoints añadidos: finalizar y contabilizar autorizaciones (módulo aditivo)
#finalizar_handler, contabilizar_handler = build_authorization_handlers()


# @router.post(
#     "/autorizaciones/{autorizacion_id}/finalizar",
#     tags=["Procesos IPS"],
#     summary="Cerrar/Finalizar una autorización (aditivo)",
#     include_in_schema=_include_in_openapi("/autorizaciones/{autorizacion_id}/finalizar"),
# )
# def finalizar_autorizacion_endpoint(
#     autorizacion_id: int,
#     payload: dict[str, Any] = Body(...),
#     username: str = Depends(require_autoriza_med),
#     pg: Session = Depends(get_postgres_session),
# ) -> dict:
#     try:
#         # delega en el servicio sin tocar lógicas existentes
#         out =   finalizar_handler(autorizacion_id, payload)

#         # intentar generar PDF usando los reportes/Messiah si está disponible
#         settings = get_settings()
#         try:
#             adjuntar_pdf_respuesta(out, pg, settings, consecutivo_autorizacion=autorizacion_id, usuario=username, etapa="activada")
#         except Exception:
#             out.setdefault("pdf_aviso", "Error al generar PDF de autorización (no crítico).")

#         return out
#     except AuthorizationValidationError as ve:
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve)) from ve
#     except Exception as exc:
#         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


# @router.post(
#     "/autorizaciones/{autorizacion_id}/contabilizar",
#     tags=["Procesos IPS"],
#     summary="Contabilizar una autorización (aditivo)",
#     include_in_schema=_include_in_openapi("/autorizaciones/{autorizacion_id}/contabilizar"),
# )
# def contabilizar_autorizacion_endpoint(
#     autorizacion_id: int,
#     payload: dict[str, Any] = Body(...),
#     username: str = Depends(require_autoriza_med),
#     pg: Session = Depends(get_postgres_session),
# ):
#     try:
#         # contabiliza la autorización y, si es posible, agrega el PDF final de autorización
#         out = contabilizar_handler(autorizacion_id, payload)
#         settings = get_settings()
#         try:
#             adjuntar_pdf_respuesta(
#                 out,
#                 pg,
#                 settings,
#                 consecutivo_autorizacion=autorizacion_id,
#                 usuario=username,
#                 etapa="activada",
#             )
#         except Exception:
#             out.setdefault("pdf_aviso", "Error al generar PDF de autorización tras contabilizar (no crítico).")
#         return out
#     except HTTPException as ve:
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve)) from ve
#     except Exception as exc:
#         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


# @router.post(
#     "/execute/facturas_decimales",
#     response_model=ApiExecutionResponse,
#     include_in_schema=_include_in_openapi("/execute/facturas_decimales"),
#     tags=["Procesos Financieros"],
#     summary="Ajuste decimal de facturas",
#     description=(
#         "Ejecuta el proceso de ajuste decimal de facturas según la parametrización financiera del sistema."
#     ),
#     responses=merge_openapi_responses(
#         {
#             200: {"description": "Lote procesado; estadísticas en `ApiExecutionResponse`."},
#             503: {
#                 "description": "No se pudieron leer permisos en base de datos (p. ej. falta SELECT en tablas del esquema seg).",
#             },
#         },
#         RESP_401_BEARER,
#         RESP_403_PERMISOS,
#         RESP_500_INTERNO,
#     ),
# )
# def execute_facturas_decimales(
#     username: str = Depends(require_permission(MOD_FACTURAS_DECIMALES, ACC_EJECUTAR)),
# ) -> ApiExecutionResponse:
#     try:
#         return service.run_service1(executed_by=username)
#     except Exception as exc:  # pragma: no cover
#         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


# @router.post(
#     "/execute/update_saldo_y_valor_factura",
#     response_model=ApiExecutionResponse,
#     include_in_schema=_include_in_openapi("/execute/update_saldo_y_valor_factura"),
#     tags=["Procesos Financieros"],
#     summary="Actualización de saldo y valor de facturas",
#     description=(
#         "Ejecuta el proceso de actualización del saldo y valor de las facturas pendientes en el sistema."
#     ),
#     responses=merge_openapi_responses(
#         {
#             200: {"description": "Flujo completado; estadísticas en `ApiExecutionResponse`."},
#             503: {
#                 "description": "No se pudieron leer permisos en base de datos (p. ej. falta SELECT en tablas del esquema seg).",
#             },
#         },
#         RESP_401_BEARER,
#         RESP_403_PERMISOS,
#         RESP_500_INTERNO,
#     ),
# )
# def execute_update_saldo_y_valor(
#     username: str = Depends(require_permission(MOD_UPDATE_SALDO_VALOR, ACC_EJECUTAR)),
# ) -> ApiExecutionResponse:
#     try:
#         return service.run_service2_and_service3(executed_by=username)
#     except Exception as exc:  # pragma: no cover
#         raise HTTPException(status_code=500, detail=str(exc)) from exc


# @router.post("/execute/service1", include_in_schema=False, tags=["Ejecución"])
# def execute_service1_legacy(
#     username: str = Depends(require_permission(MOD_FACTURAS_DECIMALES, ACC_EJECUTAR)),
# ) -> ApiExecutionResponse:
#     return service.run_service1(executed_by=username)
