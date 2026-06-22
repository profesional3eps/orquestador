"""Cliente HTTP hacia las APIs SIIFA (seguridad y factura) con JWT y reintentos."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config.settings import Settings
from app.models.siifa_radicacion import RadicadoSiifaRequest, RadicadoSiifaResponse

logger = logging.getLogger(__name__)

_RETRIABLE_HTTP = frozenset({429, 500, 502, 503, 504})


def extract_siifa_token_from_login(payload: dict[str, Any]) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("token", "accessToken", "access_token", "jwt", "bearerToken"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        return extract_siifa_token_from_login(data)
    return None


class SIIFAClient:
    """Cliente resiliente con renovación automática de JWT y retry exponencial."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._token_obtained_at: datetime | None = None
        self._timeout = httpx.Timeout(settings.siifa_http_timeout_seconds)

    def _ensure_credentials(self) -> tuple[str, str]:
        user = (self._settings.siifa_username or "").strip()
        pwd = (self._settings.siifa_password or "").strip()
        if not user or not pwd:
            raise ValueError("Configure SIIFA_USERNAME y SIIFA_PASSWORD en el entorno (.env).")
        return user, pwd

    def _token_needs_renewal(self) -> bool:
        if not self._token or not self._token_obtained_at:
            return True
        renew_minutes = max(1, int(self._settings.siifa_jwt_renew_minutes))
        elapsed = datetime.now(timezone.utc) - self._token_obtained_at
        return elapsed >= timedelta(minutes=renew_minutes)

    def login(self, *, force: bool = False) -> str:
        if not force and self._token and not self._token_needs_renewal():
            return self._token

        user, pwd = self._ensure_credentials()
        url = f"{self._settings.siifa_seguridad_base_url.rstrip('/')}/api/Auth/login"
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/plain",
        }
        if self._settings.siifa_login_bearer_token:
            headers["Authorization"] = f"Bearer {self._settings.siifa_login_bearer_token.strip()}"
        body = {"userName": user, "password": pwd}

        data = self._request("POST", url, json_body=body, headers=headers, auth_required=False)
        token = extract_siifa_token_from_login(data)
        if not token:
            raise ValueError(
                "Login SIIFA no devolvió un token reconocido (revise claves token/accessToken en la respuesta)."
            )
        self._token = token
        self._token_obtained_at = datetime.now(timezone.utc)
        logger.info("siifa_login_ok token_renovado=%s", force or self._token_needs_renewal())
        return token

    def get_bearer_token(self) -> str:
        return self.login()

    def get_facturas_sin_radicar(
        self,
        *,
        pagina_actual: int = 1,
        registros_por_pagina: int | None = None,
    ) -> dict[str, Any]:
        token = self.get_bearer_token()
        url = f"{self._settings.siifa_factura_base_url.rstrip('/')}/api/Factura"
        params: dict[str, Any] = {
            "tieneRadicado": "false",
            "numeroPagina": pagina_actual,
        }
        reg = registros_por_pagina or int(self._settings.siifa_registros_por_pagina)
        params["registrosPorPagina"] = reg
        nit = (self._settings.siifa_nit_adquiriente or "").strip()
        if nit:
            params["nitAdquiriente"] = nit
        headers = {
            "Accept": "text/plain",
            "Authorization": f"Bearer {token}",
        }
        return self._request("GET", url, params=params, headers=headers)

    def radicar_factura(self, request: RadicadoSiifaRequest) -> RadicadoSiifaResponse:
        token = self.get_bearer_token()
        url = f"{self._settings.siifa_factura_base_url.rstrip('/')}/api/FacturaRadicado"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "Authorization": f"Bearer {token}",
        }
        body = {
            "idFactura": request.id_factura,
            "radicado": request.radicado,
            "fechaRadicado": request.fecha_radicado,
        }
        data = self._request("POST", url, json_body=body, headers=headers)
        return RadicadoSiifaResponse.from_api(data)

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth_required: bool = True,
    ) -> dict[str, Any]:
        max_attempts = max(1, int(self._settings.siifa_retry_max_attempts))
        base_delay = float(self._settings.siifa_retry_base_delay_seconds)
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
                    resp = client.request(method, url, params=params, json=json_body, headers=headers)

                if resp.status_code == 401 and auth_required and attempt < max_attempts:
                    logger.warning("siifa_http_401 reintentando con nuevo token intento=%s", attempt)
                    self.login(force=True)
                    if headers and "Authorization" in headers:
                        headers["Authorization"] = f"Bearer {self._token}"
                    time.sleep(base_delay * (2 ** (attempt - 1)))
                    continue

                if resp.status_code >= 400:
                    retriable = resp.status_code in _RETRIABLE_HTTP
                    attempts_for_error = max(max_attempts, 8) if retriable else max_attempts
                    logger.warning(
                        "siifa_http_%s url=%s body=%s intento=%s/%s",
                        resp.status_code,
                        url,
                        resp.text[:500],
                        attempt,
                        attempts_for_error,
                    )
                    if retriable and attempt < attempts_for_error:
                        delay = min(60.0, base_delay * (2 ** (attempt - 1)))
                        time.sleep(delay)
                        continue
                    resp.raise_for_status()

                try:
                    data = resp.json()
                except Exception as exc:
                    raise ValueError(f"Respuesta SIIFA no es JSON: {resp.text[:200]}") from exc
                if not isinstance(data, dict):
                    raise ValueError("Respuesta SIIFA inesperada (no es objeto JSON).")
                return data

            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < max_attempts:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning("siifa_request_fallo intento=%s delay=%.1fs error=%s", attempt, delay, exc)
                    time.sleep(delay)
                    continue
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError("SIIFA request falló sin excepción capturada")


# ── Compatibilidad con sincronización legada (dbo.factura) ──

def siifa_login(settings: Settings, user_name: str, password: str) -> dict[str, Any]:
    client = SIIFAClient(settings)
    client._settings = settings
    url = f"{settings.siifa_seguridad_base_url.rstrip('/')}/api/Auth/login"
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "text/plain"}
    if settings.siifa_login_bearer_token:
        headers["Authorization"] = f"Bearer {settings.siifa_login_bearer_token.strip()}"
    return client._request(
        "POST",
        url,
        json_body={"userName": user_name, "password": password},
        headers=headers,
        auth_required=False,
    )


def siifa_get_facturas_page(
    settings: Settings,
    *,
    bearer_token: str,
    nit_adquiriente: str,
    pagina_actual: int = 1,
    registros_por_pagina: int | None = None,
) -> dict[str, Any]:
    client = SIIFAClient(settings)
    client._token = bearer_token
    client._token_obtained_at = datetime.now(timezone.utc)
    url = f"{settings.siifa_factura_base_url.rstrip('/')}/api/Factura"
    params: dict[str, Any] = {
        "nitAdquiriente": nit_adquiriente.strip(),
        "numeroPagina": pagina_actual,
    }
    if registros_por_pagina is not None:
        params["registrosPorPagina"] = registros_por_pagina
    headers = {"Accept": "text/plain", "Authorization": f"Bearer {bearer_token.strip()}"}
    return client._request("GET", url, params=params, headers=headers)
