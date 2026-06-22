"""
Documentación OpenAPI (Swagger UI / ReDoc): descripción global, etiquetas y respuestas reutilizables.
"""

from __future__ import annotations

from typing import Any

# --- Metadatos de la aplicación (FastAPI(...)) ---

API_TITLE = "EPS Familiar de Colombia API"

API_VERSION = "1.1.0"

API_DESCRIPTION = """
## Alcance

Servicios de **EPS Familiar de Colombia** para consultas y trámites de afiliados, operaciones de prestadores de salud y procesos administrativos autorizados según el perfil del usuario conectado.
""".strip()

OPENAPI_CONTACT: dict[str, str | Any] = {
    "name": "EPS Familiar de Colombia",
}

OPENAPI_LICENSE: dict[str, str] = {
    "name": "Uso interno",
}

OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": "Auth", "description": "Inicio de sesión y perfil del usuario."},
    {"name": "Health", "description": "Disponibilidad del servicio."},
    {
        "name": "Procesos Afiliado",
        "description": (
            "Consultas y trámites del afiliado: portabilidad, peticiones PQR, certificado de afiliación "
            "y actualización de datos de contacto."
        ),
    },
    {
        "name": "Procesos IPS",
        "description": (
            "Operaciones de prestadores de salud: agendamiento de citas, registro de atenciones, "
            "dispensación y autorización de medicamentos por orden médica."
        ),
    },
    {
        "name": "Procesos SIIFA",
        "description": "Consulta e incorporación de facturas radicadas en SIIFA.",
    },
    {
        "name": "Procesos Financieros",
        "description": "Procesos de ajuste y actualización de información financiera de facturas.",
    },
]


def _json_detail_example(summary: str, detail: str) -> dict[str, Any]:
    return {
        "summary": summary,
        "value": {"detail": detail},
    }


CONTENT_DETAIL = {
    "application/json": {
        "schema": {
            "type": "object",
            "properties": {
                "detail": {
                    "description": "Mensaje de error o lista de errores de validación.",
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "object"}},
                    ],
                }
            },
            "required": ["detail"],
        }
    },
}


def detail_response(
    status_code: int,
    description: str,
    *,
    example_summary: str,
    example_detail: str,
) -> dict[int, Any]:
    return {
        status_code: {
            "description": description,
            "content": {
                "application/json": {
                    "schema": CONTENT_DETAIL["application/json"]["schema"],
                    "examples": {
                        "default": _json_detail_example(example_summary, example_detail),
                    },
                }
            },
        }
    }


# Respuestas comunes (combinar con `responses={**..., **RESP_X}`)

RESP_401_LOGIN = detail_response(
    401,
    "Credenciales incorrectas, usuario inexistente o usuario inactivo.",
    example_summary="Login rechazado",
    example_detail="Credenciales inválidas.",
)

RESP_401_BEARER = detail_response(
    401,
    "No autenticado: falta el token, el esquema no es Bearer, o el JWT es inválido o expiró.",
    example_summary="Token ausente o inválido",
    example_detail="Not authenticated",
)

RESP_403_PERMISOS = detail_response(
    403,
    "Autenticado pero sin permiso para ejecutar la acción solicitada en el módulo indicado.",
    example_summary="Sin permiso de ejecución",
    example_detail="Permiso denegado para este módulo o acción.",
)

RESP_422_VALIDATION = detail_response(
    422,
    "Cuerpo de la petición inválido o tipo de identificación no reconocido en consultas por documento.",
    example_summary="Validación o tipo de documento",
    example_detail="tipo_identificacion no reconocido; indique la nomenclatura del catálogo (p. ej. CC, TI, RC).",
)

RESP_500_INTERNO = detail_response(
    500,
    "Error interno no controlado o fallo de infraestructura durante el procesamiento.",
    example_summary="Error interno",
    example_detail="Mensaje técnico devuelto por el servidor.",
)

RESP_503_SERVICIO = detail_response(
    503,
    "Servicio temporalmente no disponible (por ejemplo, generación de documentos en este momento).",
    example_summary="Servicio no disponible",
    example_detail="El servicio de generación de documentos no está disponible temporalmente.",
)


def merge_openapi_responses(*blocks: dict[int, Any]) -> dict[int, Any]:
    merged: dict[int, Any] = {}
    for b in blocks:
        merged.update(b)
    return merged
