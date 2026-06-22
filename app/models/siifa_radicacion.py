"""Modelos de dominio para la integración SIIFA → ERP (radicación)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class EstadoProcesoFactura(StrEnum):
    PENDIENTE = "PENDIENTE"
    NO_ENCONTRADA_ERP = "NO_ENCONTRADA_ERP"
    NO_RADICADA_ERP = "NO_RADICADA_ERP"
    RADICADA = "RADICADA"
    ERROR = "ERROR"
    OMITIDA = "OMITIDA"


class ResultadoTraza(StrEnum):
    OK = "OK"
    ERROR = "ERROR"
    NO_ENCONTRADA = "NO_ENCONTRADA"
    NO_RADICADA_ERP = "NO_RADICADA_ERP"
    OMITIDA = "OMITIDA"


@dataclass(frozen=True)
class FacturaSiifaItem:
    id_factura: int
    numero_factura: str
    nit_emisor: str

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> FacturaSiifaItem:
        emisor = item.get("emisor") or {}
        nit = str(emisor.get("nitEmisor") or "").strip()
        return cls(
            id_factura=int(item["idFactura"]),
            numero_factura=str(item.get("numeroFactura") or "").strip(),
            nit_emisor=nit,
        )


@dataclass
class RipsAfMatch:
    consecutivo_rips_af: int
    consecutivo_rips: int
    numero_factura: str
    numero_identificacion: str
    radicado_siifa: int | None = None
    idfactura_siifa: str | None = None


@dataclass
class RipsResumenRadicado:
    consecutivo_rips: int
    estado: int
    radica_rips: str
    fecha_radica: datetime | None = None


@dataclass
class RadicadoSiifaRequest:
    id_factura: int
    radicado: str
    fecha_radicado: str


@dataclass
class RadicadoSiifaResponse:
    id_factura_radicado: int
    id_factura: int
    numero_factura: str
    nit_adquiriente: str
    nit_emisor: str
    radicado: str
    fecha_radicado: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> RadicadoSiifaResponse:
        return cls(
            id_factura_radicado=int(data["idFacturaRadicado"]),
            id_factura=int(data["idFactura"]),
            numero_factura=str(data.get("numeroFactura") or ""),
            nit_adquiriente=str(data.get("nitAdquiriente") or ""),
            nit_emisor=str(data.get("nitEmisor") or ""),
            radicado=str(data.get("radicado") or ""),
            fecha_radicado=str(data.get("fechaRadicado") or ""),
        )


@dataclass
class ProcesoFacturaResultado:
    id_factura_siifa: int
    numero_factura: str
    nit_emisor: str
    estado: EstadoProcesoFactura
    mensaje: str = ""
    id_factura_radicado_siifa: int | None = None
    consecutivo_rips_af: int | None = None


@dataclass
class MetricasEjecucion:
    procesadas: int = 0
    radicadas: int = 0
    no_encontradas_erp: int = 0
    no_radicadas_erp: int = 0
    errores: int = 0
    omitidas: int = 0
    advertencias: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "procesadas": self.procesadas,
            "radicadas": self.radicadas,
            "no_encontradas_erp": self.no_encontradas_erp,
            "no_radicadas_erp": self.no_radicadas_erp,
            "errores": self.errores,
            "omitidas": self.omitidas,
            "advertencias": self.advertencias[:200],
        }
