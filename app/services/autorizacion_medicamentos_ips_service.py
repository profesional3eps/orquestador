"""Flujo Messiah authorization_request_ips (Orden médica → medicamentos → activación)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.core.zona_horaria import hoy_bogota
from app.core.messiah_soporte_storage import (
    guardar_soporte_confirmacion_autorizacion_messiah,
    guardar_soporte_orden_medica_messiah,
    mensaje_soporte_no_disponible,
)
from app.core.soporte_orden_medica import validar_soporte_orden_medica_pdf
from app.core.afiliado_estado import (
    ESTADOS_PERMITIDOS_AUTORIZACION_IPS,
    MENSAJE_ESTADO_NO_PERMITIDO_AUTORIZACION_IPS,
    nombre_estado_afiliado,
    estado_afiliado_permite_autorizacion_ips,
)
from app.repositories.messiah_contabilizacion_repository import (
    MessiahContabilizacionError,
    contabilizar_autorizacion_medicamentos,
)
from app.repositories.messiah_direccionamiento_repository import MessiahDireccionamientoRepository
from app.repositories.postgres_repository import PostgresRepository
from app.services.direccionamiento_service import (
    DireccionamientoService,
    SolicitudMedicamentosRechazadaError,
)
from app.services.messiah_preferencia_service import (
    debe_contabilizar_en_activacion,
    debe_contabilizar_en_confirmacion,
)
from app.services.autorizacion_ips_validacion import (
    NOMBRE_MODALIDAD_AMBULATORIOS,
    NOMBRE_ORIGEN_ATENCION_ENFERMEDAD_GENERAL,
    ORIGEN_ATENCION_ENFERMEDAD_GENERAL,
    ORIGEN_SOLICITUD_ORDEN_MEDICA_IPS,
    normalizar_medicamentos_json_ips,
    validar_diagnostico_cie10_opcional,
    validar_diagnostico_principal_messiah,
    validar_medicamentos_sin_repetir,
    validar_registro_profesional_messiah,
    validar_telefono_y_celular_obligatorios,
)
from app.services.messiah_autorizacion_pdf_service import adjuntar_pdf_respuesta


def resolver_ips_prestador(
    messiah_repo: MessiahDireccionamientoRepository,
    *,
    nit_ips_prestador: str | None,
    consecutivo_ips: int | None,
    prestador_solicitante: str | None,
    etiqueta: str = "IPS",
) -> dict[str, Any]:
    """Resuelve IPS desde NIT (autocompletar Messiah) o consecutivo/nombre opcional."""
    ips_row: dict[str, Any] | None = None
    if nit_ips_prestador and nit_ips_prestador.strip():
        ips_row = messiah_repo.fetch_ips_por_nit(nit_ips_prestador.strip())
    if ips_row is None and consecutivo_ips is not None:
        ips_row = messiah_repo.fetch_ips_por_consecutivo(int(consecutivo_ips))
    if ips_row is None and prestador_solicitante and prestador_solicitante.strip():
        nombre = prestador_solicitante.strip()
        ips_row = messiah_repo.fetch_ips_por_nombre(nombre)
        if ips_row is None and nombre.isdigit():
            ips_row = messiah_repo.fetch_ips_por_nit(nombre)
    if ips_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{etiqueta} no encontrada para el NIT indicado.",
        )
    return ips_row


def _resolver_email_solicitud(
    *,
    email: str | None,
    afiliado: dict[str, Any],
    ips_row: dict[str, Any],
    sede: dict[str, Any] | None,
) -> str:
    sede_row = sede or {}
    email_norm = (
        (email or "").strip()
        or str(sede_row.get("correo_electronico") or "")
        or str(afiliado.get("correo_electronico") or ips_row.get("correo_electronico") or "").strip()
    )
    return email_norm


def _registrar_soporte_en_messiah(
    pg: Session,
    settings: Settings,
    messiah_repo: MessiahDireccionamientoRepository,
    *,
    consecutivo_ips_ss: int,
    consecutivo_ss_solicitud: int | None,
    soporte_bytes: bytes,
) -> bool:
    """Persiste PDF en disco Messiah y enlaza ct_ips_ss_solicitud + ss_solicitud_soporte."""
    if consecutivo_ss_solicitud is None:
        return False
    nombres = guardar_soporte_orden_medica_messiah(
        settings,
        consecutivo_ips_ss=int(consecutivo_ips_ss),
        consecutivo_ss_solicitud=int(consecutivo_ss_solicitud),
        pdf_bytes=soporte_bytes,
    )
    if nombres is None:
        return False
    nombre_ips, nombre_ss = nombres
    messiah_repo.actualizar_url_archivo_ct_ips_ss(consecutivo_ips_ss, nombre_ips)
    messiah_repo.registrar_soporte_ss_solicitud(
        consecutivo_solicitud=int(consecutivo_ss_solicitud),
        url=nombre_ss,
    )
    return True


def procesar_autorizacion_medicamentos_orden_medica_ips(
    *,
    pg: Session,
    settings: Settings,
    username: str,
    codigo_tipo: str,
    numero_id: str,
    tipo_doc_abrev: str,
    origen_solicitud: str,
    observacion: str,
    email: str | None,
    telefono: str | None,
    celular: str | None,
    prioridad_atencion: int,
    ubicacion_paciente: str,
    servicio_hospitalario: str | None,
    numero_cama: str | None,
    justificacion_clinica: str | None,
    diagnostico_principal: str,
    diagnostico_relacionado_1: str | None,
    diagnostico_relacionado_2: str | None,
    prestador_solicitante: str | None,
    nit_ips_prestador: str | None,
    nit_ips_direccionamiento: str,
    consecutivo_sede_ips: int | None,
    telefono_institucional_extension: str | None,
    municipio_prestador: str | None,
    fecha_solicitud_proceso: date,
    fecha_solicitud_medico: date,
    registro_profesional: str,
    medicamentos: list[dict[str, Any]],
    insumos: list[dict[str, Any]],
    consecutivo_ips: int | None,
    soporte_orden_medica_filename: str | None,
    soporte_orden_medica_data: bytes | None,
    soporte_obligatorio: bool = True,
    soporte_subdir: str = "autorizacion_orden_medica_ips",
) -> dict[str, Any]:
    """
    Replica authorization_request_ips (Origen Orden médica) hasta gestión/autorización.
    La autorización automática valida tarifario y direccionamiento del NIT de direccionamiento.
    """
    messiah_repo = MessiahDireccionamientoRepository(pg)
    pg_repo = PostgresRepository(pg)
    direccionamiento = DireccionamientoService(messiah_repo)

    direccionamiento.validar_origen_orden_medica(ORIGEN_SOLICITUD_ORDEN_MEDICA_IPS, origen_solicitud)

    observacion_norm = observacion.strip()
    if not observacion_norm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="observacion es obligatoria.",
        )

    if insumos:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo medicamentos en este flujo (insumos_json debe ir vacío).",
        )

    medicamentos = normalizar_medicamentos_json_ips(medicamentos)
    validar_diagnostico_principal_messiah(messiah_repo, diagnostico_principal)
    dx_rel_1 = validar_diagnostico_cie10_opcional(
        messiah_repo, diagnostico_relacionado_1, campo="diagnostico_relacionado_1"
    )
    dx_rel_2 = validar_diagnostico_cie10_opcional(
        messiah_repo, diagnostico_relacionado_2, campo="diagnostico_relacionado_2"
    )
    medico = validar_registro_profesional_messiah(messiah_repo, registro_profesional)
    validar_medicamentos_sin_repetir(messiah_repo, medicamentos)

    hoy = hoy_bogota()
    if fecha_solicitud_proceso > hoy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fecha_solicitud_proceso no puede ser mayor a la fecha actual.",
        )
    if fecha_solicitud_medico > hoy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fecha_solicitud_medico no puede ser mayor a la fecha actual.",
        )

    if "hospital" in ubicacion_paciente.lower():
        if not (servicio_hospitalario or "").strip() or not (numero_cama or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Para hospitalización, servicio_hospitalario y numero_cama son obligatorios.",
            )

    ips_solicitante = resolver_ips_prestador(
        messiah_repo,
        nit_ips_prestador=nit_ips_prestador,
        consecutivo_ips=consecutivo_ips,
        prestador_solicitante=prestador_solicitante,
        etiqueta="IPS solicitante",
    )
    ips_direccionamiento = resolver_ips_prestador(
        messiah_repo,
        nit_ips_prestador=nit_ips_direccionamiento.strip(),
        consecutivo_ips=None,
        prestador_solicitante=None,
        etiqueta="IPS de direccionamiento",
    )

    sede_solicitante = messiah_repo.fetch_ips_sede(
        int(ips_solicitante["ips"]),
        consecutivo_sede_ips,
    )

    afiliado = messiah_repo.fetch_afiliado(codigo_tipo, numero_id)
    if afiliado is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Afiliado no encontrado para el tipo y número de documento indicados.",
        )
    if not estado_afiliado_permite_autorizacion_ips(afiliado.get("estado_afiliado")):
        estado_code = afiliado.get("estado_afiliado")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "mensaje": MENSAJE_ESTADO_NO_PERMITIDO_AUTORIZACION_IPS,
                "estado_afiliado": estado_code,
                "nombre_estado_afiliado": nombre_estado_afiliado(estado_code),
                "estados_permitidos": sorted(ESTADOS_PERMITIDOS_AUTORIZACION_IPS),
                "nota": "Messiah authorization_request_ips solo permite afiliado ACTIVO (estado=1).",
            },
        )

    email_norm = _resolver_email_solicitud(
        email=email,
        afiliado=afiliado,
        ips_row=ips_solicitante,
        sede=sede_solicitante,
    )
    if not email_norm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="email es obligatorio (o debe existir en sede/afiliado/IPS).",
        )
    if not pg_repo.validar_dato_contacto(email_norm, "correo"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Formato inválido para email.")
    telefono_norm, celular_norm = validar_telefono_y_celular_obligatorios(
        pg_repo, telefono, celular
    )

    prestador_nombre = str(ips_solicitante.get("razon_social") or prestador_solicitante or "").strip()
    municipio_prest = (
        (municipio_prestador or "").strip()
        or str((sede_solicitante or {}).get("municipio_descripcion") or "")
        or str(ips_solicitante.get("municipio_descripcion") or ips_solicitante.get("municipio") or "").strip()
    )
    modalidad_row = messiah_repo.fetch_modalidad_ambulatoria()
    consecutivo_modalidad = (
        int(modalidad_row["consecutivo_modalidad"]) if modalidad_row else 1
    )
    nombre_modalidad = (
        str(modalidad_row.get("descripcion") or "").strip()
        if modalidad_row
        else NOMBRE_MODALIDAD_AMBULATORIOS
    ) or NOMBRE_MODALIDAD_AMBULATORIOS

    ext, soporte_bytes = validar_soporte_orden_medica_pdf(
        soporte_orden_medica_filename,
        soporte_orden_medica_data,
        max_mb=settings.orden_medica_soporte_max_mb,
        obligatorio=soporte_obligatorio,
    )
    ips_consecutivo = int(consecutivo_ips) if consecutivo_ips else int(ips_solicitante["ips"])

    form = {
        "observacion": observacion_norm,
        "email": email_norm,
        "email_institucional": email_norm,
        "prioridad_atencion": prioridad_atencion,
        "ubicacion_paciente": ubicacion_paciente,
        "justificacion_clinica": justificacion_clinica or observacion_norm,
        "diagnostico_principal": diagnostico_principal.strip(),
        "prestador_solicitante": prestador_nombre,
        "municipio_prestador": municipio_prest,
        "fecha_solicitud_proceso": fecha_solicitud_proceso,
        "fecha_solicitud_medico": fecha_solicitud_medico,
        "registro_profesional": registro_profesional.strip(),
        "nombre_profesional_solicitante": str(medico.get("nombre") or "").strip(),
        "cargo_actividad": str(medico.get("cargo") or "").strip(),
        "consecutivo_especialidad": int(medico["consecutivo_especialidad"]),
        "consecutivo_medico": int(medico["consecutivo_medico"]),
        "medico_tipo_identificacion": medico.get("tipo_identificacion"),
        "medico_numero_identificacion": medico.get("numero_identificacion"),
        "especialidad": str(medico.get("especialidad_descripcion") or "").strip(),
        "consecutivo_modalidad": consecutivo_modalidad,
        "nombre_modalidad": nombre_modalidad,
        "nombre_origen_atencion": NOMBRE_ORIGEN_ATENCION_ENFERMEDAD_GENERAL,
        "telefono_institucional_indicativo": "57",
        "telefono_institucional_extension": (telefono_institucional_extension or "").strip() or None,
    }

    evaluados = direccionamiento.evaluar_medicamentos(
        ips=ips_direccionamiento,
        afiliado=afiliado,
        items=medicamentos,
    )
    try:
        DireccionamientoService.exigir_al_menos_un_medicamento_autorizado(evaluados)
    except SolicitudMedicamentosRechazadaError as exc:
        pg.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.detalle_api(),
        ) from exc

    consecutivo_ips_ss = pg_repo.create_solicitud_autorizacion_orden_medica(
        afiliado_data={
            "afiliado": afiliado["afiliado"],
            "tipo_identificacion": codigo_tipo,
            "numero_identificacion": numero_id,
        },
        usuario_grabado=username,
        observacion=observacion_norm,
        email=email_norm,
        telefono=telefono_norm,
        celular=celular_norm,
        soporte_url="",
        consecutivo_ips=ips_consecutivo,
        tipo_servicio=1,
    )

    try:
        out = direccionamiento.procesar(
            afiliado=afiliado,
            ips_solicitante=ips_solicitante,
            ips_direccionamiento=ips_direccionamiento,
            ips_ss_consecutivo=consecutivo_ips_ss,
            form=form,
            medicamentos_json=medicamentos,
            username=username,
            tipo_doc_abrev=tipo_doc_abrev,
            diagnostico_relacionado1=dx_rel_1,
            diagnostico_relacionado2=dx_rel_2,
            origenes_atencion=[ORIGEN_ATENCION_ENFERMEDAD_GENERAL],
            sede=sede_solicitante,
            evaluados=evaluados,
        )
    except SolicitudMedicamentosRechazadaError as exc:
        pg.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.detalle_api(),
        ) from exc
    except ValueError as exc:
        pg.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    soporte_registrado_messiah = False
    if soporte_bytes and out.get("consecutivo_solicitud"):
        soporte_registrado_messiah = _registrar_soporte_en_messiah(
            pg,
            settings,
            messiah_repo,
            consecutivo_ips_ss=consecutivo_ips_ss,
            consecutivo_ss_solicitud=int(out["consecutivo_solicitud"]),
            soporte_bytes=soporte_bytes,
        )
        if soporte_registrado_messiah:
            pg.commit()

    sede_info = sede_solicitante or {}
    out["consecutivo_solicitud_ips"] = consecutivo_ips_ss
    out["prestador_resuelto"] = {
        "nit": str(ips_solicitante.get("nit") or ""),
        "razon_social": prestador_nombre,
        "telefono": str(sede_info.get("telefono") or ips_solicitante.get("telefono") or ""),
        "email": str(sede_info.get("correo_electronico") or email_norm),
        "municipio": municipio_prest,
        "direccion": str(sede_info.get("direccion") or ips_solicitante.get("direccion") or ""),
        "sede": str(sede_info.get("nombre_sede") or ""),
        "consecutivo_sede_ips": sede_info.get("consecutivo_sede_ips"),
    }
    out["medico_solicitante"] = {
        "registro_profesional": registro_profesional.strip(),
        "nombre": form["nombre_profesional_solicitante"],
        "tipo_identificacion": medico.get("tipo_identificacion"),
        "numero_identificacion": medico.get("numero_identificacion"),
        "cargo": form["cargo_actividad"],
        "especialidad": form["especialidad"],
    }
    out["soporte_registrado_messiah"] = soporte_registrado_messiah
    if soporte_bytes and not soporte_registrado_messiah:
        out["soporte_messiah_aviso"] = mensaje_soporte_no_disponible(settings)

    return out


def _as_date(value: date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


def _validar_fecha_programacion(
    fecha_programacion: date,
    *,
    fecha_grabado: date | None,
    fecha_fin_vigencia: date | None,
) -> None:
    if fecha_grabado is not None and fecha_programacion < fecha_grabado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "fecha_programacion no puede ser anterior a la fecha de autorización "
                f"({fecha_grabado.isoformat()})."
            ),
        )
    if fecha_fin_vigencia is not None and fecha_programacion >= fecha_fin_vigencia:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "fecha_programacion debe ser anterior a la fecha fin de vigencia "
                f"({fecha_fin_vigencia.isoformat()})."
            ),
        )


def _validar_fecha_prestacion(
    fecha_prestacion: date,
    *,
    fecha_grabado: date | None,
    fecha_programacion: date,
) -> None:
    hoy = hoy_bogota()
    if fecha_prestacion > hoy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fecha_prestacion no puede ser mayor a la fecha actual.",
        )
    if fecha_grabado is not None and fecha_prestacion < fecha_grabado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "fecha_prestacion no puede ser anterior a la fecha de autorización "
                f"({fecha_grabado.isoformat()})."
            ),
        )
    if fecha_prestacion < fecha_programacion:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "fecha_prestacion no puede ser anterior a fecha_programacion "
                f"({fecha_programacion.isoformat()})."
            ),
        )


def _nombre_afiliado_desde_row(afiliado: dict[str, Any] | None) -> str:
    if not afiliado:
        return ""
    return " ".join(
        p
        for p in [
            afiliado.get("primer_nombre"),
            afiliado.get("segundo_nombre"),
            afiliado.get("primer_apellido"),
            afiliado.get("segundo_apellido"),
        ]
        if p
    ).strip()


def _contabilizar_si_aplica(
    pg: Session,
    *,
    consecutivo_autorizacion: int,
    username: str,
    en_activacion: bool,
) -> int | None:
    solo_medicamento = True
    debe = (
        debe_contabilizar_en_activacion(pg, solo_medicamento=solo_medicamento)
        if en_activacion
        else debe_contabilizar_en_confirmacion(pg, solo_medicamento=solo_medicamento)
    )
    if not debe:
        return None
    try:
        return contabilizar_autorizacion_medicamentos(
            pg,
            consecutivo_autorizacion=consecutivo_autorizacion,
            username=username,
        )
    except MessiahContabilizacionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Contabilización de autorización: {exc.mensaje}",
        ) from exc


def _emitir_autorizacion_desde_solicitud(
    *,
    messiah_repo: MessiahDireccionamientoRepository,
    direccionamiento: DireccionamientoService,
    consecutivo_solicitud: int,
    tipo_doc_abrev: str,
    numero_id: str,
    codigo_tipo: str,
    ips_direccionamiento: dict[str, Any],
    username: str,
) -> tuple[dict[str, Any], bool]:
    """Emite ss_autorizacion si la solicitud aún no tiene. Retorna (auth_row, recien_emitida)."""
    existente = messiah_repo.fetch_autorizacion_por_solicitud(consecutivo_solicitud)
    if existente is not None:
        return existente, False

    solicitud = messiah_repo.fetch_solicitud_pendiente_autorizacion(
        consecutivo_solicitud=consecutivo_solicitud,
        tipo_doc_abrev=tipo_doc_abrev,
        numero_identificacion=numero_id,
    )
    if solicitud is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No se encontró solicitud pendiente de autorización para el consecutivo y documento indicados."
            ),
        )

    afiliado = messiah_repo.fetch_afiliado(codigo_tipo, numero_id)
    if afiliado is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Afiliado no encontrado.")

    ips_solicitante = messiah_repo.fetch_ips_por_consecutivo(int(solicitud["ips_solicitante"]))
    if ips_solicitante is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="IPS solicitante de la solicitud no encontrada.",
        )

    filas = messiah_repo.fetch_medicamentos_solicitud_para_evaluacion(consecutivo_solicitud)
    medicamentos_json = [
        {
            "cum": str(r["cum"]),
            "cantidad": int(r["cantidad"]),
            "dias": int(r.get("dias") or 1),
            "posologia": r.get("posologia") or "NA",
            "observacion": r.get("observacion") or "",
        }
        for r in filas
    ]
    evaluados = direccionamiento.evaluar_medicamentos(
        ips=ips_direccionamiento,
        afiliado=afiliado,
        items=medicamentos_json,
    )
    direccionamiento.exigir_al_menos_un_medicamento_autorizado(evaluados)

    autorizaciones = [
        {
            "secuencia": e.secuencia,
            "cantidad": e.cantidad,
            "med_row": e.med_row,
            "valor_total": Decimal(str(e.med_row.get("valor") or 0)) * e.cantidad,
        }
        for e in evaluados
        if e.autorizado
    ]
    form = {
        "consecutivo_ambito": 23,
        "consecutivo_nivel": 2,
    }
    emitida = messiah_repo.crear_autorizacion_desde_solicitud(
        consecutivo_solicitud=consecutivo_solicitud,
        afiliado=afiliado,
        ips_direccionamiento=ips_direccionamiento,
        ips_ss_consecutivo=int(solicitud["consecutivo_ips_ss"]),
        diagnostico_cie10=int(solicitud["diagnostico_principal"]),
        form=form,
        autorizaciones=autorizaciones,
        username=username,
        tipo_doc_abrev=tipo_doc_abrev,
    )
    auth = messiah_repo.fetch_autorizacion_por_solicitud(consecutivo_solicitud)
    if auth is None:
        auth = {
            "consecutivo_autorizacion": emitida["consecutivo_autorizacion"],
            "consecutivo_interno": emitida["consecutivo_interno"],
            "consecutivo_solicitud": consecutivo_solicitud,
            "pin": emitida["pin"],
            "valor_autorizacion": emitida["valor_autorizacion"],
            "fecha_fin_vigencia": emitida["fecha_fin_vigencia"],
            "fecha_grabado": None,
            "numero_solicitud": solicitud.get("numero_solicitud"),
        }
    return auth, True


def activar_autorizacion_orden_medica_ips(
    *,
    pg: Session,
    settings: Settings,
    username: str,
    codigo_tipo: str,
    numero_id: str,
    tipo_doc_abrev: str,
    nit_ips_direccionamiento: str,
    consecutivo_solicitud: int | None = None,
    pin_activacion: str | None = None,
    fecha_programacion: date | None = None,
    fecha_prestacion: date | None = None,
    confirmar_prestacion: bool = True,
    soporte_confirmacion_filename: str | None = None,
    soporte_confirmacion_data: bytes | None = None,
) -> dict[str, Any]:
    """
    Emite autorización (si aplica), activa y confirma prestación al estilo Messiah.
    Requiere consecutivo_solicitud del paso 1 o pin_activacion si ya fue emitida.
    """
    messiah_repo = MessiahDireccionamientoRepository(pg)
    direccionamiento = DireccionamientoService(messiah_repo)

    pin_norm = (pin_activacion or "").strip().upper()
    if consecutivo_solicitud is None and not pin_norm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="consecutivo_solicitud o pin_activacion es obligatorio.",
        )

    ips_row = resolver_ips_prestador(
        messiah_repo,
        nit_ips_prestador=nit_ips_direccionamiento.strip(),
        consecutivo_ips=None,
        prestador_solicitante=None,
        etiqueta="IPS de direccionamiento",
    )
    consecutivo_ips = int(ips_row["ips"])
    hoy = hoy_bogota()
    fecha_prog = fecha_programacion or hoy
    fecha_prest = fecha_prestacion or hoy

    ya_activada = False
    ya_confirmada = False
    prestacion_confirmada = False
    soporte_registrado_messiah = False
    recien_emitida = False
    consecutivo_saldo: int | None = None

    auth: dict[str, Any] | None = None
    if consecutivo_solicitud is not None:
        auth, recien_emitida = _emitir_autorizacion_desde_solicitud(
            messiah_repo=messiah_repo,
            direccionamiento=direccionamiento,
            consecutivo_solicitud=int(consecutivo_solicitud),
            tipo_doc_abrev=tipo_doc_abrev,
            numero_id=numero_id,
            codigo_tipo=codigo_tipo,
            ips_direccionamiento=ips_row,
            username=username,
        )
        pin_norm = str(auth.get("pin") or pin_norm).strip().upper()

    if auth is None and pin_norm:
        auth = messiah_repo.fetch_autorizacion_completada_por_pin(
            tipo_doc_abrev=tipo_doc_abrev,
            numero_identificacion=numero_id,
            pin=pin_norm,
            consecutivo_ips=consecutivo_ips,
        )
        if auth is not None:
            ya_activada = True
            ya_confirmada = True
            prestacion_confirmada = confirmar_prestacion

    if auth is None and pin_norm:
        auth = messiah_repo.fetch_autorizacion_pendiente_activacion(
            tipo_doc_abrev=tipo_doc_abrev,
            numero_identificacion=numero_id,
            pin=pin_norm,
            consecutivo_ips=consecutivo_ips,
        )

    if auth is None and pin_norm:
        auth = messiah_repo.fetch_autorizacion_activada_sin_confirmar(
            tipo_doc_abrev=tipo_doc_abrev,
            numero_identificacion=numero_id,
            pin=pin_norm,
            consecutivo_ips=consecutivo_ips,
        )
        if auth is not None:
            ya_activada = True

    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No se encontró solicitud/autorización para autorizar y activar. "
                "Verifique consecutivo_solicitud, documento, NIT de direccionamiento y PIN."
            ),
        )

    consecutivo_auth = int(auth["consecutivo_autorizacion"])

    if not ya_activada and auth.get("fecha_activacion") is None:
        fecha_grabado = _as_date(auth.get("fecha_grabado"))
        fecha_fin_vigencia = _as_date(auth.get("fecha_fin_vigencia"))
        _validar_fecha_programacion(
            fecha_prog,
            fecha_grabado=fecha_grabado,
            fecha_fin_vigencia=fecha_fin_vigencia,
        )
        messiah_repo.programar_medicamentos_autorizacion(consecutivo_auth, fecha_prog)
        if not messiah_repo.activar_autorizacion(
            consecutivo_auth,
            username,
            fecha_real_autorizacion=fecha_prog,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La autorización no pudo activarse (ya activada o no disponible).",
            )
        consecutivo_saldo = _contabilizar_si_aplica(
            pg,
            consecutivo_autorizacion=consecutivo_auth,
            username=username,
            en_activacion=True,
        )
        auth = (
            messiah_repo.fetch_autorizacion_activada_sin_confirmar(
                tipo_doc_abrev=tipo_doc_abrev,
                numero_identificacion=numero_id,
                pin=pin_norm,
                consecutivo_ips=consecutivo_ips,
            )
            or auth
        )
    elif auth.get("fecha_activacion") is not None:
        ya_activada = True

    if confirmar_prestacion and not ya_confirmada:
        fecha_grabado = _as_date(auth.get("fecha_grabado"))
        _validar_fecha_prestacion(
            fecha_prest,
            fecha_grabado=fecha_grabado,
            fecha_programacion=fecha_prog,
        )
        _, soporte_bytes = validar_soporte_orden_medica_pdf(
            soporte_confirmacion_filename,
            soporte_confirmacion_data,
            max_mb=settings.orden_medica_soporte_max_mb,
            obligatorio=True,
            etiqueta="soporte_confirmacion",
        )
        url_activacion: str | None = None
        if soporte_bytes:
            url_activacion = guardar_soporte_confirmacion_autorizacion_messiah(
                settings,
                consecutivo_autorizacion=consecutivo_auth,
                pdf_bytes=soporte_bytes,
                nombre_original=soporte_confirmacion_filename or "soporte_confirmacion.pdf",
            )
            soporte_registrado_messiah = url_activacion is not None

        if auth.get("fecha_real_prestacion_servicio") is None:
            if not messiah_repo.confirmar_prestacion_autorizacion(
                consecutivo_auth,
                fecha_real_prestacion=fecha_prest,
                fecha_prestacion_lineas=fecha_prest,
                username=username,
                url_activacion=url_activacion,
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="La prestación no pudo confirmarse (ya confirmada o no disponible).",
                )
            saldo_conf = _contabilizar_si_aplica(
                pg,
                consecutivo_autorizacion=consecutivo_auth,
                username=username,
                en_activacion=False,
            )
            if saldo_conf is not None:
                consecutivo_saldo = saldo_conf
        prestacion_confirmada = True
        auth = (
            messiah_repo.fetch_autorizacion_completada_por_pin(
                tipo_doc_abrev=tipo_doc_abrev,
                numero_identificacion=numero_id,
                pin=pin_norm,
                consecutivo_ips=consecutivo_ips,
            )
            or messiah_repo.fetch_autorizacion_por_solicitud(int(auth["consecutivo_solicitud"]))
            or auth
        )
    elif auth.get("fecha_real_prestacion_servicio") is not None:
        ya_confirmada = True
        prestacion_confirmada = confirmar_prestacion

    afiliado = messiah_repo.fetch_afiliado(codigo_tipo, numero_id)
    nombre_afiliado = _nombre_afiliado_desde_row(afiliado)
    interno = str(auth.get("consecutivo_interno") or consecutivo_auth)

    if consecutivo_saldo is None and auth.get("consecutivo_saldo") is not None:
        consecutivo_saldo = int(auth["consecutivo_saldo"])

    if ya_confirmada and not confirmar_prestacion:
        estado = "COMPLETADA"
        mensaje = f"Autorización {interno} ya estaba confirmada. Se devuelve el estado actual."
    elif prestacion_confirmada:
        estado = "COMPLETADA"
        if ya_confirmada:
            mensaje = f"Autorización {interno} ya estaba confirmada. Se devuelve el estado actual."
        elif ya_activada:
            mensaje = f"Autorización {interno} confirmada correctamente (ya estaba activada)."
        elif recien_emitida:
            mensaje = (
                f"Autorización {interno} emitida, activada y prestación confirmada correctamente."
            )
        else:
            mensaje = f"Autorización {interno} activada y prestación confirmada correctamente."
    elif ya_activada:
        estado = "ACTIVADA"
        mensaje = f"Autorización {interno} ya estaba activada. Prestación no confirmada en esta llamada."
    else:
        estado = "ACTIVADA"
        mensaje = f"Autorización {interno} activada correctamente."

    out: dict[str, Any] = {
        "consecutivo_autorizacion": consecutivo_auth,
        "consecutivo_interno": interno,
        "consecutivo_solicitud": int(auth["consecutivo_solicitud"]),
        "numero_solicitud": auth.get("numero_solicitud"),
        "pin_activacion": pin_norm,
        "autorizacion_activa": True,
        "pendiente_activacion": False,
        "prestacion_confirmada": prestacion_confirmada,
        "estado_trazabilidad": estado,
        "valor_autorizacion": float(auth["valor_autorizacion"]) if auth.get("valor_autorizacion") is not None else None,
        "fecha_fin_vigencia": auth.get("fecha_fin_vigencia"),
        "fecha_programacion": fecha_prog,
        "fecha_prestacion": fecha_prest if confirmar_prestacion else None,
        "fecha_real_prestacion_servicio": _as_date(auth.get("fecha_real_prestacion_servicio")),
        "tipo_identificacion_codigo": codigo_tipo,
        "numero_identificacion": numero_id,
        "nombre_afiliado": nombre_afiliado,
        "prestador_direccionamiento": {
            "nit": str(ips_row.get("nit") or ""),
            "razon_social": str(ips_row.get("razon_social") or ""),
        },
        "mensaje": mensaje,
        "ya_activada": ya_activada,
        "ya_confirmada": ya_confirmada,
        "soporte_confirmacion_registrado_messiah": soporte_registrado_messiah,
        "consecutivo_saldo": consecutivo_saldo,
        "autorizacion_emitida": recien_emitida,
    }
    if soporte_confirmacion_data and not soporte_registrado_messiah and confirmar_prestacion:
        out["soporte_messiah_aviso"] = mensaje_soporte_no_disponible(settings)

    etapa_pdf: str = "confirmada" if prestacion_confirmada else "activada"
    adjuntar_pdf_respuesta(
        out,
        pg,
        settings,
        consecutivo_autorizacion=consecutivo_auth,
        usuario=username,
        etapa=etapa_pdf,  # type: ignore[arg-type]
    )
    return out
