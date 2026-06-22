"""Catálogo y resolución de tipo de identificación (nomenclatura → código en BD)."""

from __future__ import annotations

import re
import unicodedata

# Código homologación (BD) → (descripción catálogo, nomenclatura de entrada en API).
_CATALOGO: dict[str, tuple[str, str]] = {
    "1": ("NIT", "NI"),
    "2": ("RUT", "RUT"),
    "3": ("Cédula de Ciudadania", "CC"),
    "4": ("Menor sin Identificacion", "MS"),
    "5": ("Registro Civil", "RC"),
    "6": ("Tarjeta de Identidad", "TI"),
    "7": ("Cedula de Extranjeria", "CE"),
    "8": ("Pasaporte", "PA"),
    "9": ("Adulto sin Identificacion", "AS"),
    "10": ("Carnet Diplomatico", "CD"),
    "11": ("Certificado Nacido Vivo", "CN"),
    "12": ("Salvo Conducto", "SC"),
    "13": ("Permiso Especial de Permanencia", "PE"),
    "14": ("Permiso protección temporal", "PT"),
    "15": ("DE", "DE"),
}

# Códigos tal como se almacenan en administrativo.af_afiliado.tipo_identificacion (texto/cast).
TIPOS_DOCUMENTO_NOMBRES: dict[str, str] = {codigo: nombre for codigo, (nombre, _) in _CATALOGO.items()}

# Nomenclatura de homologación (entrada API) → código en BD. Claves en mayúsculas.
NOMENCLATURA_A_CODIGO: dict[str, str] = {
    nomenclatura.upper(): codigo for codigo, (_, nomenclatura) in _CATALOGO.items()
}


def _strip_accents(s: str) -> str:
    nk = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in nk if unicodedata.category(ch) != "Mn")


def _norm_key(s: str) -> str:
    t = _strip_accents(s.strip().lower())
    t = re.sub(r"\s+", " ", t)
    return t


def nombre_tipo_por_codigo(codigo: str | int | None) -> str:
    if codigo is None:
        return ""
    key = str(codigo).strip()
    return TIPOS_DOCUMENTO_NOMBRES.get(key, key)


def nomenclatura_por_codigo(codigo: str | int | None) -> str:
    if codigo is None:
        return ""
    key = str(codigo).strip()
    entry = _CATALOGO.get(key)
    return entry[1] if entry else key


def resolve_tipo_identificacion(raw: str | int) -> tuple[str, str]:
    """
    Devuelve (codigo_para_consulta_bd, descripcion_catalogo).
    Acepta la nomenclatura de homologación (p. ej. CC, ti, RUT), sin distinguir mayúsculas/minúsculas.
    También acepta la descripción del catálogo (con tolerancia a acentos y espacios).
    """
    s = str(raw).strip()
    if not s:
        raise ValueError("tipo_identificacion no puede estar vacío.")

    codigo = NOMENCLATURA_A_CODIGO.get(s.upper())
    if codigo is not None:
        return codigo, TIPOS_DOCUMENTO_NOMBRES[codigo]

    raw_n = _norm_key(s)
    for code, name in TIPOS_DOCUMENTO_NOMBRES.items():
        if _norm_key(name) == raw_n:
            return code, name

    for code, name in TIPOS_DOCUMENTO_NOMBRES.items():
        if raw_n in _norm_key(name) or _norm_key(name) in raw_n:
            return code, name

    raise ValueError(
        "tipo_identificacion no reconocido; indique la nomenclatura del catálogo "
        "(p. ej. CC, TI, RC, CE). No se acepta el código numérico de homologación."
    )
