from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol
from uuid import uuid4


class ValidationError(ValueError):
    """Excepción usada para validar reglas de negocio."""


@dataclass
class AuthorizationLine:
    id: int
    codigo: str
    descripcion: str
    cantidad: int
    valor_unitario: float
    valor_total: float

    def validate(self) -> None:
        if self.id <= 0:
            raise ValidationError("La línea debe tener un id positivo")
        if not self.codigo or not self.codigo.strip():
            raise ValidationError("El código de la línea es obligatorio")
        if not self.descripcion or not self.descripcion.strip():
            raise ValidationError("La descripción de la línea es obligatoria")
        if self.cantidad <= 0:
            raise ValidationError("La cantidad debe ser mayor que cero")
        if self.valor_unitario <= 0:
            raise ValidationError("El valor unitario debe ser mayor que cero")
        if self.valor_total <= 0:
            raise ValidationError("El valor total de la línea debe ser mayor que cero")


@dataclass
class AuthorizationRecord:
    id: int
    afiliado_id: int
    regimen: str
    estado: str = "CREADA"
    valor_base_cobro: float = 0.0
    valor_total_autorizado: float = 0.0
    valor_copago: float = 0.0
    lineas: List[AuthorizationLine] = field(default_factory=list)
    contabilizada: bool = False
    estado_contable: Optional[str] = None
    usuario_cierre: Optional[str] = None
    observaciones_cierre: Optional[str] = None
    contabilizacion_id: Optional[str] = None
    fecha_cierre: Optional[datetime] = None
    fecha_contabilizacion: Optional[datetime] = None

    def validate(self) -> None:
        if self.id <= 0:
            raise ValidationError("El id de la autorización debe ser mayor que cero")
        if self.afiliado_id <= 0:
            raise ValidationError("El afiliado debe ser válido")
        if not self.regimen or not self.regimen.strip():
            raise ValidationError("El régimen del afiliado es obligatorio")
        if self.valor_total_autorizado <= 0:
            raise ValidationError("El valor total autorizado debe ser mayor que cero")
        if not self.lineas:
            raise ValidationError("La autorización debe tener al menos una línea")
        for line in self.lineas:
            line.validate()


@dataclass
class FinalizarAutorizacionPayload:
    usuario: str
    observaciones: Optional[str] = None

    def validate(self) -> None:
        if not self.usuario or not self.usuario.strip():
            raise ValidationError("El usuario que ejecuta el cierre es obligatorio")
        self.usuario = self.usuario.strip()
        if self.observaciones is not None:
            self.observaciones = self.observaciones.strip()
            if len(self.observaciones) > 1000:
                raise ValidationError("Las observaciones no pueden superar los 1000 caracteres")


@dataclass
class ContabilizarAutorizacionPayload:
    usuario: str
    observaciones: Optional[str] = None

    def validate(self) -> None:
        if not self.usuario or not self.usuario.strip():
            raise ValidationError("El usuario que ejecuta la contabilización es obligatorio")
        self.usuario = self.usuario.strip()
        if self.observaciones is not None:
            self.observaciones = self.observaciones.strip()
            if len(self.observaciones) > 1000:
                raise ValidationError("Las observaciones no pueden superar los 1000 caracteres")


@dataclass
class FinalizarAutorizacionResponse:
    autorizacion_id: int
    estado: str
    valor_cobro: float
    valor_copago: float
    mensaje: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "autorizacion_id": self.autorizacion_id,
            "estado": self.estado,
            "valor_cobro": self.valor_cobro,
            "valor_copago": self.valor_copago,
            "mensaje": self.mensaje,
        }


@dataclass
class ContabilizarAutorizacionResponse:
    autorizacion_id: int
    asiento_id: str
    estado_contable: str
    valor_debito: float
    valor_credito: float
    mensaje: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "autorizacion_id": self.autorizacion_id,
            "asiento_id": self.asiento_id,
            "estado_contable": self.estado_contable,
            "valor_debito": self.valor_debito,
            "valor_credito": self.valor_credito,
            "mensaje": self.mensaje,
        }


class AuthorizationRepository(Protocol):
    def get(self, authorization_id: int) -> Optional[AuthorizationRecord]:
        ...

    def save(self, record: AuthorizationRecord) -> AuthorizationRecord:
        ...


class InMemoryAuthorizationRepository:
    """Repositorio simple en memoria para estudiar el flujo sin dependencias."""

    def __init__(self) -> None:
        self._store: Dict[int, AuthorizationRecord] = {}

    def get(self, authorization_id: int) -> Optional[AuthorizationRecord]:
        return self._store.get(authorization_id)

    def save(self, record: AuthorizationRecord) -> AuthorizationRecord:
        self._store[record.id] = record
        return record


class AuthorizationService:
    """Servicio que encapsula el cierre y la contabilización de una autorización."""

    def __init__(self, repository: AuthorizationRepository) -> None:
        self.repository = repository

    def finalizar_autorizacion(self, authorization_id: int, payload: Any) -> Dict[str, Any]:
        if authorization_id <= 0:
            raise ValidationError("El id de la autorización debe ser mayor que cero")

        payload_obj = self._coerce_finalize_payload(payload)
        authorization = self.repository.get(authorization_id)
        if authorization is None:
            raise ValidationError("La autorización no existe")

        self._validate_finalize(authorization)

        valor_copago = self._calculate_copago(
            authorization.regimen,
            authorization.valor_total_autorizado,
        )

        authorization.estado = "CERRADA"
        authorization.valor_cobro = valor_copago
        authorization.valor_copago = valor_copago
        authorization.usuario_cierre = payload_obj.usuario
        authorization.observaciones_cierre = payload_obj.observaciones
        authorization.fecha_cierre = datetime.now(timezone.utc)

        self.repository.save(authorization)

        response = FinalizarAutorizacionResponse(
            autorizacion_id=authorization.id,
            estado=authorization.estado,
            valor_cobro=authorization.valor_cobro,
            valor_copago=authorization.valor_copago,
            mensaje="La autorización fue cerrada correctamente y el copago fue calculado.",
        )
        return response.to_dict()

    def contabilizar_autorizacion(self, authorization_id: int, payload: Any) -> Dict[str, Any]:
        if authorization_id <= 0:
            raise ValidationError("El id de la autorización debe ser mayor que cero")

        payload_obj = self._coerce_accounting_payload(payload)
        authorization = self.repository.get(authorization_id)
        if authorization is None:
            raise ValidationError("La autorización no existe")

        self._validate_accounting(authorization)

        asiento_id = f"AUT-{authorization.id}-{uuid4().hex[:8].upper()}"
        authorization.contabilizacion_id = asiento_id
        authorization.estado_contable = "REGISTRADO"
        authorization.contabilizada = True
        authorization.fecha_contabilizacion = datetime.now(timezone.utc)

        self.repository.save(authorization)

        response = ContabilizarAutorizacionResponse(
            autorizacion_id=authorization.id,
            asiento_id=asiento_id,
            estado_contable=authorization.estado_contable,
            valor_debito=authorization.valor_base_cobro,
            valor_credito=authorization.valor_base_cobro,
            mensaje="La contabilización fue registrada correctamente.",
        )
        return response.to_dict()

    def _coerce_finalize_payload(self, payload: Any) -> FinalizarAutorizacionPayload:
        if isinstance(payload, FinalizarAutorizacionPayload):
            payload_obj = payload
        elif isinstance(payload, dict):
            payload_obj = FinalizarAutorizacionPayload(
                usuario=str(payload.get("usuario", "")).strip(),
                observaciones=payload.get("observaciones"),
            )
        else:
            raise ValidationError(
                "El payload debe ser un diccionario o una instancia de FinalizarAutorizacionPayload"
            )

        payload_obj.validate()
        return payload_obj

    def _coerce_accounting_payload(self, payload: Any) -> ContabilizarAutorizacionPayload:
        if isinstance(payload, ContabilizarAutorizacionPayload):
            payload_obj = payload
        elif isinstance(payload, dict):
            payload_obj = ContabilizarAutorizacionPayload(
                usuario=str(payload.get("usuario", "")).strip(),
                observaciones=payload.get("observaciones"),
            )
        else:
            raise ValidationError(
                "El payload debe ser un diccionario o una instancia de ContabilizarAutorizacionPayload"
            )

        payload_obj.validate()
        return payload_obj

    def _validate_finalize(self, authorization: AuthorizationRecord) -> None:
        authorization.validate()

        if authorization.estado in {"CERRADA", "ANULADA"}:
            raise ValidationError("La autorización ya fue cerrada o anulada")

        regimen = authorization.regimen.lower()
        if regimen not in {"subsidiado", "contributivo"}:
            raise ValidationError("El régimen no tiene una regla de copago soportada")

    def _validate_accounting(self, authorization: AuthorizationRecord) -> None:
        if authorization.estado != "CERRADA":
            raise ValidationError("La autorización debe estar cerrada antes de contabilizar")

        if authorization.contabilizada:
            raise ValidationError("La autorización ya fue contabilizada")

        if authorization.valor_base_cobro <= 0:
            raise ValidationError("El valor base de cobro debe ser mayor que cero")

    def _calculate_copago(self, regimen: str, valor_total: float) -> float:
        if valor_total <= 0:
            raise ValidationError("El valor total autorizado debe ser mayor que cero")

        regimen = regimen.lower()
        if regimen == "subsidiado":
            return round(valor_total * 0.2, 2)
        if regimen == "contributivo":
            return round(valor_total * 0.1, 2)

        raise ValidationError("El régimen no tiene regla de copago soportada")


repository = InMemoryAuthorizationRepository()
service = AuthorizationService(repository)


def build_authorization_handlers() -> tuple[Any, Any]:
    """Devuelve dos funciones listas para enganchar en tu API."""

    def finalizar_handler(authorization_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        return service.finalizar_autorizacion(authorization_id, payload)

    def contabilizar_handler(authorization_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        return service.contabilizar_autorizacion(authorization_id, payload)

    return finalizar_handler, contabilizar_handler
