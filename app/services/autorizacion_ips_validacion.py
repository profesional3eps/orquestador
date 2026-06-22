"""Validaciones alineadas con Messiah authorization_request_ips (orden médica, medicamentos)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.repositories.messiah_direccionamiento_repository import MessiahDireccionamientoRepository
from app.repositories.postgres_repository import PostgresRepository

# Fase 1: orden médica ambulatoria (valores fijos, no expuestos en el formulario del endpoint IPS).
ORIGEN_SOLICITUD_ORDEN_MEDICA_IPS = "ORDEN_MEDICA"
UBICACION_PACIENTE_ORDEN_MEDICA_IPS = "AMBULATORIO"
# Messiah EOriginAttention.GENERAL_DISEASE → ss_solicitud_atencion.atencion
ORIGEN_ATENCION_ENFERMEDAD_GENERAL = 1
NOMBRE_ORIGEN_ATENCION_ENFERMEDAD_GENERAL = "Enfermedad General"
NOMBRE_MODALIDAD_AMBULATORIOS = "Ambulatorios"

CAMPOS_MEDICAMENTO_IPS = frozenset({"cum", "cantidad", "dias", "observacion"})


def validar_telefono_y_celular_obligatorios(
    pg_repo: PostgresRepository,
    telefono: str | None,
    celular: str | None,
) -> tuple[str, str]:
    """Teléfono y celular son obligatorios en autorización de medicamentos (formato 9999999999)."""
    telefono_norm = (telefono or "").strip()
    celular_norm = (celular or "").strip()
    if not telefono_norm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="telefono es obligatorio.",
        )
    if not celular_norm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="celular es obligatorio.",
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
    return telefono_norm, celular_norm


def normalizar_medicamentos_json_ips(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Acepta cum, cantidad, dias y observacion por ítem; posología se resuelve del catálogo tb_medicamento."""
    if not items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe enviar al menos un medicamento en medicamentos_json.",
        )

    normalizados: list[dict[str, Any]] = []
    for idx, raw in enumerate(items, start=1):
        if not isinstance(raw, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"medicamentos_json[{idx - 1}] debe ser un objeto JSON.",
            )
        extra = set(raw.keys()) - CAMPOS_MEDICAMENTO_IPS
        if extra:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"medicamentos_json[{idx - 1}] contiene campos no permitidos: {sorted(extra)}. "
                    f"Solo: {sorted(CAMPOS_MEDICAMENTO_IPS)}."
                ),
            )

        cum = str(raw.get("cum") or "").strip()
        if not cum:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"medicamentos_json[{idx - 1}].cum es obligatorio.",
            )

        try:
            cantidad = int(raw.get("cantidad"))
        except (TypeError, ValueError):
            cantidad = 0
        if cantidad <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"medicamentos_json[{idx - 1}].cantidad debe ser mayor a cero.",
            )

        dias_raw = raw.get("dias")
        if dias_raw is None or str(dias_raw).strip() == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"medicamentos_json[{idx - 1}].dias es obligatorio.",
            )
        try:
            dias = int(dias_raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"medicamentos_json[{idx - 1}].dias debe ser numérico.",
            ) from None
        if dias <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"medicamentos_json[{idx - 1}].dias debe ser mayor a cero.",
            )

        observacion = str(raw.get("observacion") or raw.get("Observación") or raw.get("Observacion") or "").strip()

        normalizados.append(
            {
                "cum": cum,
                "cantidad": cantidad,
                "dias": dias,
                "observacion": observacion,
            }
        )
    return normalizados


def validar_diagnostico_principal_messiah(
    repo: MessiahDireccionamientoRepository,
    simbolo: str,
) -> dict[str, Any]:
    """Equivalente a autocomplete CIE-10 con forceSelection (diagnóstico principal obligatorio)."""
    clave = simbolo.strip()
    if not clave:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="diagnostico_principal es obligatorio.",
        )
    cie = repo.fetch_cie10(clave)
    if cie is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"diagnostico_principal '{clave}' no existe en TB_CIE10.",
        )
    return cie


def validar_diagnostico_cie10_opcional(
    repo: MessiahDireccionamientoRepository,
    simbolo: str | None,
    *,
    campo: str,
) -> int | None:
    """Valida CIE-10 opcional (diagnósticos relacionados) con la misma regla que el principal."""
    clave = (simbolo or "").strip()
    if not clave:
        return None
    cie = repo.fetch_cie10(clave)
    if cie is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{campo} '{clave}' no existe en TB_CIE10.",
        )
    return int(cie["consecutivo_cie10"])


def validar_registro_profesional_messiah(
    repo: MessiahDireccionamientoRepository,
    registro: str,
) -> dict[str, Any]:
    """Equivalente a loadMedicalRecord: registro obligatorio y debe existir en tb_medico_solicitante."""
    registro_norm = registro.strip()
    if not registro_norm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="registro_profesional es obligatorio.",
        )
    medico = repo.fetch_medico_solicitante(registro_norm)
    if medico is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"registro_profesional '{registro_norm}' no existe en TB_MEDICO_SOLICITANTE.",
        )
    if not str(medico.get("nombre") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El médico solicitante no tiene nombre parametrizado en TB_MEDICO_SOLICITANTE.",
        )
    if not str(medico.get("cargo") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El médico solicitante no tiene cargo parametrizado en TB_MEDICO_SOLICITANTE.",
        )
    if medico.get("consecutivo_especialidad") is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El médico solicitante no tiene especialidad parametrizada en TB_MEDICO_SOLICITANTE.",
        )
    return medico


def validar_medicamentos_sin_repetir(
    repo: MessiahDireccionamientoRepository,
    items: list[dict[str, Any]],
) -> None:
    """Evita CUM/medicamento duplicado en la misma solicitud (Messiah validationsEdit)."""
    vistos: set[int] = set()
    for item in items:
        cum = item["cum"]
        med = repo.fetch_medicamento_por_cum(cum)
        if med is None:
            continue
        med_id = int(med["medicamento"])
        if med_id in vistos:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Medicamento CUM '{cum}' está repetido en medicamentos_json.",
            )
        vistos.add(med_id)
