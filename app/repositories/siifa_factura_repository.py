"""Persistencia de facturas SIIFA en dbo.factura, dbo.terceros y dbo.factura_tercero."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.sqlserver_models import Factura, FacturaTercero, Tercero

_ROL_EMISOR = "EMISOR"
_ROL_ADQUIRIENTE = "ADQUIRIENTE"


def _parse_date(val: Any) -> date:
    if val is None:
        return date.today()
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    s = str(val).strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    y, m, d = (int(x) for x in s[:10].split("-"))
    return date(y, m, d)


def _parse_date_optional(val: Any) -> date | None:
    if val is None or val == "":
        return None
    return _parse_date(val)


def _parse_time_optional(val: Any) -> time | None:
    if val is None or val == "":
        return None
    if isinstance(val, time):
        return val
    s = str(val).strip()
    parts = s.split(":")
    h = int(parts[0])
    mi = int(parts[1]) if len(parts) > 1 else 0
    se = int(parts[2]) if len(parts) > 2 else 0
    return time(h, mi, se)


def _dec(val: Any) -> Decimal | None:
    if val is None:
        return None
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))


def _int_opt(val: Any) -> int | None:
    if val is None:
        return None
    return int(val)


def _str_opt(val: Any, max_len: int) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    return s[:max_len]


def _get_or_create_tercero(
    db: Session,
    *,
    tipo_persona: str | None,
    razon_social: str,
    nombre_comercial: str | None,
    nit: str,
) -> int:
    nit_key = nit.strip()[:20]
    row = db.scalars(select(Tercero).where(Tercero.nit == nit_key)).first()
    if row is not None:
        changed = False
        if tipo_persona is not None and row.tipo_persona != tipo_persona[:2]:
            row.tipo_persona = tipo_persona[:2]
            changed = True
        if razon_social and row.razon_social != razon_social[:255]:
            row.razon_social = razon_social[:255]
            changed = True
        if nombre_comercial:
            nm = nombre_comercial[:255]
            if row.nombre_comercial != nm:
                row.nombre_comercial = nm
                changed = True
        if changed:
            db.add(row)
        return int(row.id_tercero)

    t = Tercero(
        tipo_persona=tipo_persona[:2] if tipo_persona else None,
        razon_social=razon_social[:255],
        nombre_comercial=nombre_comercial[:255] if nombre_comercial else None,
        nit=nit_key,
        fecha_creacion=datetime.now(),
    )
    db.add(t)
    db.flush()
    return int(t.id_tercero)


def _ensure_factura_tercero(db: Session, id_factura: int, id_tercero: int, rol: str) -> bool:
    q = select(FacturaTercero.id).where(
        and_(
            FacturaTercero.id_factura == id_factura,
            FacturaTercero.id_tercero == id_tercero,
            FacturaTercero.rol == rol,
        )
    )
    if db.scalars(q).first() is not None:
        return False
    db.add(FacturaTercero(id_factura=id_factura, id_tercero=id_tercero, rol=rol))
    return True


def upsert_factura_from_siifa_item(db: Session, item: dict[str, Any]) -> tuple[str, int]:
    """
    Inserta o actualiza factura + vínculos terceros.
    Returns: ("inserted" | "updated" | "unchanged", cantidad_filas_nuevas_en_factura_tercero)
    """
    emisor = item.get("emisor") or {}
    adq = item.get("adquiriente") or {}
    id_factura = int(item["idFactura"])
    cufe = str(item.get("cufe") or "").strip()[:100]
    if not cufe:
        raise ValueError("Factura sin CUFE")

    id_em = _get_or_create_tercero(
        db,
        tipo_persona=_str_opt(emisor.get("tipoPersona"), 2),
        razon_social=str(emisor.get("razonSocial") or "SIN RAZON SOCIAL")[:255],
        nombre_comercial=_str_opt(emisor.get("nombreComercial"), 255),
        nit=str(emisor.get("nitEmisor") or "").strip(),
    )
    id_adq = _get_or_create_tercero(
        db,
        tipo_persona=_str_opt(adq.get("tipoPersona"), 2),
        razon_social=str(adq.get("razonSocial") or "SIN RAZON SOCIAL")[:255],
        nombre_comercial=_str_opt(adq.get("nombreComercial"), 255),
        nit=str(adq.get("nitAdquiriente") or "").strip(),
    )

    fecha_em = _parse_date(item.get("fechaEmision"))
    hora_em = _parse_time_optional(item.get("horaEmision"))
    fv = _parse_date_optional(item.get("fechaVencimiento"))

    payload_core = {
        "id_factura_emisor": _int_opt(item.get("idFacturaEmisor")),
        "id_factura_adquiriente": _int_opt(item.get("idFacturaAdquiriente")),
        "indicador_tipo_operacion": _str_opt(item.get("indicadorTipoOperacion"), 50),
        "profile_execution_id": _int_opt(item.get("profileexecutionid2")),
        "numero_factura": str(item.get("numeroFactura") or "")[:50],
        "cufe": cufe,
        "fecha_emision": fecha_em,
        "hora_emision": hora_em,
        "fecha_vencimiento": fv,
        "tipo_factura": _str_opt(item.get("tipoFactura"), 5),
        "divisa_factura": _str_opt(item.get("divisaFactura"), 5),
        "numero_elementos": _int_opt(item.get("numeroElementos")),
        "total_valor_bruto": _dec(item.get("totalValorBruto")),
        "total_valor_base_imponible": _dec(item.get("totalValorBaseImponible")),
        "total_valor_bruto_atributos": _dec(item.get("totalValorBrutoAtributos")),
        "descuento_total": _dec(item.get("descuentoTotal")),
        "cargo_total": _dec(item.get("cargoTotal")),
        "anticipo_total": _dec(item.get("anticipoTotal")),
        "valor_factura": _dec(item.get("valorFactura")),
    }

    existing = db.get(Factura, id_factura)
    if existing is None:
        f = Factura(
            id_factura=id_factura,
            fecha_creacion=datetime.now(),
            radicado_siifa=False,
            fecha_rad_siifa=None,
            id_factura_siifa=str(id_factura)[:100],
            **payload_core,
        )
        db.add(f)
        db.flush()
        status = "inserted"
    else:
        changed = False
        for key, val in payload_core.items():
            if getattr(existing, key) != val:
                setattr(existing, key, val)
                changed = True
        if changed:
            status = "updated"
        else:
            status = "unchanged"
        db.add(existing)

    added_e = _ensure_factura_tercero(db, id_factura, id_em, _ROL_EMISOR)
    added_a = _ensure_factura_tercero(db, id_factura, id_adq, _ROL_ADQUIRIENTE)
    return status, int(added_e) + int(added_a)
