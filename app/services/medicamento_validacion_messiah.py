"""
Validaciones de medicamentos al generar autorización en Messiah.

Réplica de CostImplService.validacionesAutorizacionMedicamentos (líneas ~10365-10556),
invocada desde updateAuthorizationsNewCore después de armar ListSsAutorizacionMedicamento.

No incluye edad ni tiempo_limite_dias: no están en ese método Java.
Las reglas de contrato/tarifario/IPS/direccionamiento equivalen a serviciosIncluidosNoSeleccionadoContrato
y se evalúan antes en direccionamiento_service.evaluar_medicamentos.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, TYPE_CHECKING

from app.core.zona_horaria import hoy_bogota

if TYPE_CHECKING:
    from app.repositories.messiah_direccionamiento_repository import MessiahDireccionamientoRepository
    from app.services.direccionamiento_service import MedicamentoEvaluado

MSG_MAXIMO_VECES_DIA = (
    "El medicamento {codigo} - {descripcion} supera la cantidad de veces permitidas diarias para autorizar."
)
MSG_MAXIMO_VECES_MES = (
    "El medicamento {codigo} - {descripcion} supera la cantidad de veces permitidas mensual para autorizar."
)
MSG_MAXIMO_VECES_ANO = (
    "El medicamento {codigo} - {descripcion} supera la cantidad de veces permitidas anual para autorizar."
)
MSG_MAXIMO_VECES_VIDA = (
    "El medicamento {codigo} - {descripcion} supera la cantidad de veces permitidas en la vida para autorizar."
)


def _inicio_dia(fecha: date) -> datetime:
    return datetime.combine(fecha, time.min)


def _fin_dia(fecha: date) -> datetime:
    return datetime.combine(fecha, time(23, 59, 59, 999000))


def _inicio_mes(fecha: date) -> datetime:
    return datetime.combine(fecha.replace(day=1), time.min)


def _fin_mes(fecha: date) -> datetime:
    from calendar import monthrange

    ultimo_dia = monthrange(fecha.year, fecha.month)[1]
    return datetime.combine(fecha.replace(day=ultimo_dia), time(23, 59, 59, 999000))


def _inicio_ano(fecha: date) -> datetime:
    return datetime.combine(date(fecha.year, 1, 1), time.min)


def _fin_ano(fecha: date) -> datetime:
    return datetime.combine(date(fecha.year, 12, 31), time(23, 59, 59, 999000))


def validaciones_autorizacion_medicamentos_messiah(
    repo: MessiahDireccionamientoRepository,
    afiliado_id: int,
    cantidad_por_medicamento: dict[int, float],
    catalogo_por_medicamento: dict[int, dict[str, Any]],
    *,
    fecha_referencia: date | None = None,
) -> list[str]:
    """
    Réplica fiel de validacionesAutorizacionMedicamentos.

    - cantidad_por_medicamento: equivalente a medicamentosCantidadAutorizar (último valor por id, como HashMap.put).
    - catalogo_por_medicamento: equivalente a mapaMedicamentoAutorizacion (tb_medicamento).
    """
    if not cantidad_por_medicamento:
        return []

    medicamento_ids = list(cantidad_por_medicamento.keys())
    hoy = fecha_referencia or hoy_bogota()
    mensajes: list[str] = []

    def _validar_periodo(
        *,
        maximo_attr: str,
        historico_map: dict[int, float],
        plantilla: str,
        requiere_filas_historicas: bool,
    ) -> None:
        if requiere_filas_historicas and not historico_map:
            return
        for med_id, med in catalogo_por_medicamento.items():
            maximo = int(med.get(maximo_attr) or 0)
            if maximo == 0:
                continue
            codigo = str(med.get("codigo_interno") or "").strip()
            descripcion = str(med.get("descripcion") or "").strip()
            cantidad_autorizar = float(cantidad_por_medicamento.get(med_id) or 0)
            historico = float(historico_map.get(med_id) or 0)
            total = historico + cantidad_autorizar
            if total > maximo:
                mensajes.append(
                    plantilla.format(codigo=codigo, descripcion=descripcion)
                )

    historicos_dia = repo.sum_cantidades_autorizacion_medicamento(
        afiliado_id, medicamento_ids, fecha_inicio=_inicio_dia(hoy), fecha_fin=_fin_dia(hoy)
    )
    _validar_periodo(
        maximo_attr="maximo_veces_dias",
        historico_map=historicos_dia,
        plantilla=MSG_MAXIMO_VECES_DIA,
        requiere_filas_historicas=True,
    )

    historicos_mes = repo.sum_cantidades_autorizacion_medicamento(
        afiliado_id, medicamento_ids, fecha_inicio=_inicio_mes(hoy), fecha_fin=_fin_mes(hoy)
    )
    _validar_periodo(
        maximo_attr="maximo_veces_mes",
        historico_map=historicos_mes,
        plantilla=MSG_MAXIMO_VECES_MES,
        requiere_filas_historicas=True,
    )

    historicos_ano = repo.sum_cantidades_autorizacion_medicamento(
        afiliado_id, medicamento_ids, fecha_inicio=_inicio_ano(hoy), fecha_fin=_fin_ano(hoy)
    )
    _validar_periodo(
        maximo_attr="maximo_veces_ano",
        historico_map=historicos_ano,
        plantilla=MSG_MAXIMO_VECES_ANO,
        requiere_filas_historicas=True,
    )

    historicos_vida = repo.sum_cantidades_autorizacion_medicamento(afiliado_id, medicamento_ids)
    _validar_periodo(
        maximo_attr="maximo_veces_vida",
        historico_map=historicos_vida,
        plantilla=MSG_MAXIMO_VECES_VIDA,
        requiere_filas_historicas=True,
    )

    return mensajes


def _mensaje_aplica_a_evaluado(mensaje: str, ev: MedicamentoEvaluado) -> bool:
    codigo = str(ev.codigo_interno or ev.cum or "").strip()
    if not codigo:
        return False
    return codigo in mensaje


def aplicar_validaciones_autorizacion_messiah(
    evaluados: list[MedicamentoEvaluado],
    *,
    afiliado: dict[str, Any],
    repo: MessiahDireccionamientoRepository,
) -> None:
    """
    Aplica validacionesAutorizacionMedicamentos solo sobre ítems que ya pasarían
    a ListSsAutorizacionMedicamento (autorizado=True antes de esta llamada).
    """
    candidatos = [e for e in evaluados if e.autorizado and e.med_row.get("medicamento")]
    if not candidatos:
        return

    afiliado_id = int(afiliado["afiliado"])
    cantidad_por_med: dict[int, float] = {}
    catalogo_por_med: dict[int, dict[str, Any]] = {}
    for ev in candidatos:
        med_id = int(ev.med_row["medicamento"])
        # Messiah: HashMap.put por cada línea (último valor gana si hubiera duplicado de id).
        cantidad_por_med[med_id] = float(ev.cantidad)
        catalogo_por_med[med_id] = ev.med_row

    mensajes = validaciones_autorizacion_medicamentos_messiah(
        repo,
        afiliado_id,
        cantidad_por_med,
        catalogo_por_med,
    )
    if not mensajes:
        return

    for ev in evaluados:
        if not ev.autorizado:
            continue
        aplicables = [m for m in mensajes if _mensaje_aplica_a_evaluado(m, ev)]
        if not aplicables:
            continue
        extra = "; ".join(aplicables)
        ev.motivo = f"{ev.motivo}; {extra}" if ev.motivo else extra
        ev.autorizado = False
