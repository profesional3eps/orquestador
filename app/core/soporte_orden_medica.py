"""Validación del archivo soporte_orden_medica (solo PDF, tamaño máximo)."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, status

EXTENSION_PDF = ".pdf"
PDF_MAGIC = b"%PDF-"


def bytes_maximo_soporte_orden_medica(max_mb: int) -> int:
    return int(max_mb) * 1024 * 1024


def validar_soporte_orden_medica_pdf(
    filename: str | None,
    data: bytes | None,
    *,
    max_mb: int,
    obligatorio: bool = True,
    etiqueta: str = "soporte_orden_medica",
) -> tuple[str, bytes]:
    """
    Valida el cargue de un soporte PDF (orden médica o confirmación).

    - Extensión .pdf (insensible a mayúsculas en el nombre).
    - Contenido con firma PDF (%PDF-).
    - Tamaño > 0 y <= max_mb.
    """
    if not filename or not str(filename).strip():
        if obligatorio:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Debe adjuntar {etiqueta} (archivo PDF).",
            )
        return "", b""

    ext = Path(filename).suffix.lower()
    if ext != EXTENSION_PDF:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El {etiqueta} debe ser un archivo PDF (.pdf).",
        )

    if data is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se pudo leer el archivo {etiqueta}.",
        )

    if len(data) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El archivo {etiqueta} está vacío.",
        )

    max_bytes = bytes_maximo_soporte_orden_medica(max_mb)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"El {etiqueta} supera el tamaño máximo permitido de {max_mb} MB."
            ),
        )

    if not data.startswith(PDF_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El contenido del {etiqueta} no corresponde a un archivo PDF válido.",
        )

    return EXTENSION_PDF, data
