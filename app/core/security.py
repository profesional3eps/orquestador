from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import re
import uuid

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.core.database import get_sqlserver_session
from app.models.sqlserver_models import Usuario
from app.repositories.sqlserver_repository import SqlServerRepository

bearer_scheme = HTTPBearer(auto_error=True)

CLAIM_NAMEIDENTIFIER = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier"
CLAIM_NAME = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"
CLAIM_GIVENNAME = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname"
CLAIM_SURNAME = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname"
CLAIM_ROLE = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"

_DOC_USERNAME_RE = re.compile(r"^([A-Z]{2,4})(\d+)$", re.IGNORECASE)


def _stable_subject_uid(user_id: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"orq.usuarios:{user_id}"))


def _split_nombre_contacto(contacto: str | None) -> dict[str, str]:
    parts = (contacto or "").strip().split()
    return {
        "primer_nombre": parts[0] if len(parts) > 0 else "",
        "segundo_nombre": parts[1] if len(parts) > 1 else "",
        "primer_apellido": parts[2] if len(parts) > 2 else "",
        "segundo_apellido": parts[3] if len(parts) > 3 else "",
    }


def _parse_documento_username(username: str) -> tuple[str, str]:
    match = _DOC_USERNAME_RE.match(username.strip())
    if not match:
        return "", ""
    return match.group(1).upper(), match.group(2)


def resolve_client_ip(request: Request) -> str | None:
    """IP del consumidor; prioriza X-Forwarded-For cuando la API está detrás de proxy/balanceador."""
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    if request.client:
        return request.client.host
    return None


def parse_ip_whitelist(ip_permitida: str | None) -> set[str]:
    raw = (ip_permitida or "").strip()
    if not raw:
        return set()
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


def client_ip_is_allowed(client_ip: str | None, ip_permitida: str | None) -> bool:
    allowed = parse_ip_whitelist(ip_permitida)
    if not allowed:
        return True
    if not client_ip:
        return False
    return client_ip.strip() in allowed


def enforce_user_ip_whitelist(
    *,
    client_ip: str | None,
    ip_permitida: str | None,
    username: str,
) -> None:
    if client_ip_is_allowed(client_ip, ip_permitida):
        return
    allowed = parse_ip_whitelist(ip_permitida)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"Acceso denegado: la IP de origen no esta autorizada para el usuario {username}. "
            f"IPs permitidas: {', '.join(sorted(allowed))}."
        ),
    )


def build_access_token_claims(user: Usuario, roles: list[str]) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.jwt_expire_minutes)
    subject_uid = _stable_subject_uid(user.id)
    nombres = _split_nombre_contacto(user.contactotecnico)
    tipo_documento, numero_documento = _parse_documento_username(user.username)
    unique_name = user.username.strip()
    email = (user.correocontacto or "").strip()
    phone = (user.telefonocontacto or "").strip()
    role_list = [r for r in roles if r]

    payload: dict = {
        "sub": subject_uid,
        "unique_name": unique_name,
        "email": email,
        "jti": str(uuid.uuid4()),
        CLAIM_NAMEIDENTIFIER: subject_uid,
        CLAIM_NAME: unique_name,
        CLAIM_GIVENNAME: nombres["primer_nombre"],
        CLAIM_SURNAME: nombres["primer_apellido"],
        "PrimerNombre": nombres["primer_nombre"],
        "SegundoNombre": nombres["segundo_nombre"],
        "PrimerApellido": nombres["primer_apellido"],
        "SegundoApellido": nombres["segundo_apellido"],
        "TipoDocumento": tipo_documento,
        "NumeroDocumento": numero_documento,
        "PhoneNumber": phone,
        "TwoFactorEnabled": "False",
        "TipoEntidad": (user.tipoentidad or "").strip(),
        "NombreEntidad": (user.nombreentidad or "").strip(),
        "NitEntidad": (user.nitentidad or "").strip(),
        "iat": now,
        "nbf": now,
        "exp": expires_at,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    if role_list:
        payload[CLAIM_ROLE] = role_list
    return payload


def create_access_token(user: Usuario, roles: list[str]) -> str:
    settings = get_settings()
    payload = build_access_token_claims(user, roles)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_password_hash(plain_password: str, stored_hash: str) -> bool:
    # Supports SHA256 hex hashes and a temporary plain-text fallback.
    if len(stored_hash) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in stored_hash):
        computed_hash = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(computed_hash, stored_hash.lower())
    return hmac.compare_digest(plain_password, stored_hash)


def username_from_token_payload(payload: dict) -> str | None:
    for key in ("unique_name", CLAIM_NAME, "username"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    sub = payload.get("sub")
    if isinstance(sub, str) and sub.strip():
        return sub.strip()
    return None


def require_jwt_token(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    settings = get_settings()
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "sub", "jti", "iss", "aud"]},
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido o expirado.",
        ) from exc

    if not username_from_token_payload(payload):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido: usuario no encontrado en los claims.",
        )
    return payload


def get_current_username(
    request: Request,
    payload: dict = Depends(require_jwt_token),
    db: Session = Depends(get_sqlserver_session),
) -> str:
    username = username_from_token_payload(payload)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido: usuario no encontrado en los claims.",
        )
    repo = SqlServerRepository(db)
    user = repo.get_active_user_by_username(username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo.",
        )
    enforce_user_ip_whitelist(
        client_ip=resolve_client_ip(request),
        ip_permitida=user.ip_permitida,
        username=username,
    )
    route = request.scope.get("route")
    endpoint_path = getattr(route, "path", request.url.path)
    method = request.method
    if not repo.user_has_endpoint_access(username, method, endpoint_path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"El usuario {username} no tiene permiso para consumir {method} {endpoint_path}. "
                "Parametrize el acceso en seg.usuario_endpoints."
            ),
        )
    return username


def require_permission(module: str, action: str):
    """Dependencia: exige permiso según seg.permisos (o usuario administrador en orq.usuarios.tipo)."""

    def _dep(
        username: str = Depends(get_current_username),
        db: Session = Depends(get_sqlserver_session),
    ) -> str:
        repo = SqlServerRepository(db)
        if not repo.user_has_permission(username, module, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"No tiene permiso para esta operación: se requiere la acción «{action}» sobre el módulo «{module}». "
                    "Compruebe en base de datos que exista la fila en seg.permisos, que el perfil esté activo y que "
                    f"su usuario ({username}) esté asociado a ese perfil en seg.usuario_perfil; "
                    "como alternativa, un administrador puede marcar orq.usuarios.tipo = 1 para su usuario."
                ),
            )
        return username

    return _dep


def require_autoriza_med(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_sqlserver_session),
) -> str:
    """Solo usuarios con orq.usuarios.autoriza_med = true pueden solicitar/autorizar medicamentos."""
    repo = SqlServerRepository(db)
    if not repo.user_can_autoriza_med(username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"El usuario {username} no está habilitado para direccionamiento de medicamentos "
                "(orq.usuarios.autoriza_med debe ser true)."
            ),
        )
    return username
