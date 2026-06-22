"""Helpers OpenAPI y validación para Form() alineados a columnas Messiah."""

from __future__ import annotations

from datetime import date

from fastapi import Form, HTTPException, status

from app.core.zona_horaria import hoy_bogota

# Esquema OpenAPI para campos date/integer en multipart/form-data.
OPENAPI_FORMAT_DATE = {"format": "date", "examples": ["2026-06-03"]}
OPENAPI_TYPE_INTEGER = {"type": "integer"}


def form_fecha(description: str, *, requerido: bool = True):
    """Factory de Form() con format: date para Swagger/Postman (import OpenAPI)."""
    if requerido:
        return Form(..., description=description, json_schema_extra=OPENAPI_FORMAT_DATE)
    return Form(None, description=description, json_schema_extra=OPENAPI_FORMAT_DATE)


def form_entero(description: str, *, default: int | None = None):
    """Factory de Form() con type: integer."""
    if default is None:
        return Form(None, description=description, json_schema_extra=OPENAPI_TYPE_INTEGER)
    return Form(default, description=description, json_schema_extra=OPENAPI_TYPE_INTEGER)


def validar_fechas_solicitud_no_futuras(
    fecha_solicitud_proceso: date,
    fecha_solicitud_medico: date,
) -> None:
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
