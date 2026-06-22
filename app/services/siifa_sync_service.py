"""Sincronización paginada de facturas SIIFA hacia SQL Server."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.repositories.siifa_factura_repository import upsert_factura_from_siifa_item
from app.services.siifa_client import extract_siifa_token_from_login, siifa_get_facturas_page, siifa_login

logger = logging.getLogger(__name__)


def sync_facturas_siifa_a_sqlserver(
    db: Session,
    settings: Settings,
    *,
    nit_adquiriente: str,
) -> dict[str, Any]:
    user = (settings.siifa_username or "").strip()
    pwd = (settings.siifa_password or "").strip()
    if not user or not pwd:
        raise ValueError("Configure SIIFA_USERNAME y SIIFA_PASSWORD en el entorno (.env).")

    login_json = siifa_login(settings, user, pwd)
    token = extract_siifa_token_from_login(login_json)
    if not token:
        raise ValueError(
            "Login SIIFA no devolvió un token reconocido (revise claves token/accessToken en la respuesta)."
        )

    nit = nit_adquiriente.strip()
    reg = int(settings.siifa_registros_por_pagina)
    max_pages = int(settings.siifa_max_paginas)

    inserted = updated = unchanged = links = 0
    advertencias: list[str] = []
    pages_done = 0

    def _process_items(payload: dict[str, Any], page_label: int) -> None:
        nonlocal inserted, updated, unchanged, links, pages_done
        items = payload.get("resultado") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                st, links_added = upsert_factura_from_siifa_item(db, item)
                db.commit()
                if st == "inserted":
                    inserted += 1
                elif st == "updated":
                    updated += 1
                else:
                    unchanged += 1
                links += links_added
            except Exception as exc:  # pragma: no cover
                db.rollback()
                fid = item.get("idFactura")
                advertencias.append(f"Página {page_label}, idFactura={fid}: {exc}")
                logger.exception("siifa_sync_item_fallo idFactura=%s", fid)
        pages_done += 1

    first = siifa_get_facturas_page(
        settings,
        bearer_token=token,
        nit_adquiriente=nit,
        pagina_actual=1,
        registros_por_pagina=reg,
    )
    total_paginas = max(1, int(first.get("totalPaginas") or 1))
    _process_items(first, 1)

    limit_pages = total_paginas if max_pages <= 0 else min(total_paginas, max_pages)
    for p in range(2, limit_pages + 1):
        payload = siifa_get_facturas_page(
            settings,
            bearer_token=token,
            nit_adquiriente=nit,
            pagina_actual=p,
            registros_por_pagina=reg,
        )
        _process_items(payload, p)

    return {
        "nit_adquiriente": nit,
        "paginas_procesadas": pages_done,
        "total_paginas_reportadas_siifa": total_paginas,
        "paginas_limite_aplicado": max_pages > 0,
        "facturas_insertadas": inserted,
        "facturas_actualizadas": updated,
        "facturas_sin_cambio": unchanged,
        "detalles_rol_creados": links,
        "advertencias": advertencias[:200],
    }
