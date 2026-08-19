"""Solicitud y autorización de medicamentos (flujo portal IPS / Orden médica)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.core.zona_horaria import hoy_bogota

from app.repositories.messiah_direccionamiento_repository import MessiahDireccionamientoRepository
from app.services.medicamento_validacion_messiah import aplicar_validaciones_autorizacion_messiah


@dataclass
class MedicamentoEvaluado:
    secuencia: int
    cum: str
    codigo_interno: str
    descripcion: str
    cantidad: int
    dias: int
    posologia: str
    observacion: str
    concentracion: str | None
    forma_farmaceutica: str | None
    unidad_medida: str | None
    med_row: dict[str, Any]
    autorizado: bool
    motivo: str | None = None


class SolicitudMedicamentosRechazadaError(ValueError):
    """Ningún medicamento pasó validación; no debe persistirse la solicitud."""

    def __init__(self, evaluados: list[MedicamentoEvaluado]) -> None:
        self.evaluados = evaluados
        self.rechazados = [e for e in evaluados if not e.autorizado]
        total = len(evaluados)
        super().__init__(
            f"No se creó la solicitud: ninguno de los {total} medicamento(s) cumple las validaciones."
        )

    def detalle_api(self) -> dict[str, Any]:
        return {
            "mensaje": str(self),
            "total_solicitados": len(self.evaluados),
            "total_autorizados": 0,
            "total_no_autorizados": len(self.rechazados),
            "tipo_resultado": "NINGUNA",
            "medicamentos_no_autorizados": [
                {
                    "secuencia": e.secuencia,
                    "cum": e.cum,
                    "descripcion": e.descripcion,
                    "motivo": e.motivo or "No cumple reglas de autorización automática.",
                }
                for e in self.rechazados
            ],
        }


class DireccionamientoService:
    """Evaluación de medicamentos alineada a Messiah (ETipoAutorizaIps / tarifario)."""

    # Messiah ETipoAutorizaIps.AUTORIZACION_AUTOMATICA
    TIPO_AUTORIZA_AUTOMATICA = 2

    ORIGEN_ORDEN_MEDICA_VALORES = {
        "ORDEN_MEDICA",
        "ORDEN MEDICA",
        "ORDEN-MEDICA",
        "1",
    }

    UBICACION_MAP = {
        "AMBULATORIO": 1,
        "URGENCIAS": 2,
        "HOSPITALIZACION": 3,
        "HOSPITALIZACIÓN": 3,
    }

    def __init__(self, repo: MessiahDireccionamientoRepository) -> None:
        self.repo = repo

    @staticmethod
    def _normalizar_origen(valor: str) -> str:
        return valor.strip().upper().replace("_", " ").replace("-", " ")

    def validar_origen_orden_medica(self, origen_atencion: str, origen_solicitud: str | None) -> None:
        candidatos = {self._normalizar_origen(origen_atencion)}
        if origen_solicitud:
            candidatos.add(self._normalizar_origen(origen_solicitud))
        permitido = {self._normalizar_origen(x.replace("_", " ")) for x in self.ORIGEN_ORDEN_MEDICA_VALORES}
        if not candidatos.intersection(permitido) and "ORDEN MEDICA" not in " ".join(candidatos):
            raise ValueError(
                "El origen debe ser Orden médica. Indique origen_solicitud=ORDEN_MEDICA u origen_atencion equivalente."
            )

    def resolver_ubicacion_codigo(self, ubicacion_paciente: str) -> int:
        clave = ubicacion_paciente.strip().upper()
        if clave.isdigit():
            return int(clave)
        return self.UBICACION_MAP.get(clave, 3)

    # @staticmethod
    # def _edad_afiliado_anios(fecha_nacimiento: Any, *, referencia: date | None = None) -> int | None:
    #     """Edad en años completos a partir de la fecha de nacimiento (Messiah af_afiliado)."""
    #     if fecha_nacimiento is None or str(fecha_nacimiento).strip() == "":
    #         return None
    #     if isinstance(fecha_nacimiento, date):
    #         nacimiento = fecha_nacimiento
    #     else:
    #         try:
    #             nacimiento = date.fromisoformat(str(fecha_nacimiento)[:10])
    #         except (TypeError, ValueError):
    #             return None
    #     ref = referencia or hoy_bogota()
    #     anios = ref.year - nacimiento.year
    #     if (ref.month, ref.day) < (nacimiento.month, nacimiento.day):
    #         anios -= 1
    #     return max(anios, 0)

    @staticmethod
    def ips_permite_autorizacion_automatica_medicamentos(ips: dict[str, Any]) -> bool:
        """
        Messiah: ct_ips.tipo_autoriza = AUTORIZACION_AUTOMATICA (2) o
        ct_ips.tipo_medicamento = AUTORIZACION_AUTOMATICA (2) para medicamentos.
        No usa sw_autorizacion_masiva.
        """
        tipo_autoriza = int(ips.get("tipo_autoriza") or 0)
        tipo_medicamento = int(ips.get("tipo_medicamento") or 0)
        auto = DireccionamientoService.TIPO_AUTORIZA_AUTOMATICA
        return tipo_autoriza == auto or tipo_medicamento == auto

    @staticmethod
    def exigir_al_menos_un_medicamento_autorizado(evaluados: list[MedicamentoEvaluado]) -> None:
        """Persistir solicitud solo si al menos un medicamento fue autorizado."""
        if not any(e.autorizado for e in evaluados):
            raise SolicitudMedicamentosRechazadaError(evaluados)

    def evaluar_medicamentos(
        self,
        *,
        ips: dict[str, Any],
        afiliado: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> list[MedicamentoEvaluado]:
        requiere_dir = self.repo.ips_requiere_direccionamiento(int(ips["ips"]))
        habilitada = int(ips.get("sw_habilitada") or 0) == 1
        auto_ips = self.ips_permite_autorizacion_automatica_medicamentos(ips)
        municipio_afiliado = str(afiliado.get("municipio_codigo") or "").strip()
        ips_id = int(ips["ips"])
        tiene_contrato_municipio = self.repo.ips_tiene_contrato_medicamento_municipio(
            ips_id, municipio_afiliado
        )

##nuevo
        med_rows_cache: dict[str, dict[str, Any] | None] = {}
        tarifa_cache: dict[tuple[int, str, str], dict[str, Any] | None] = {}
        direccionamiento_cache: dict[int, bool] = {}
#######

        evaluados: list[MedicamentoEvaluado] = []
        for idx, item in enumerate(items, start=1):
            cum = str(item.get("cum") or item.get("codigo_interno") or "").strip()
            codigo = cum
            try:
                cantidad = int(item.get("cantidad"))
            except (TypeError, ValueError):
                cantidad = 0
            try:
                dias = int(item.get("dias") or 1)
            except (TypeError, ValueError):
                dias = 1
            observacion = str(item.get("observacion") or "").strip()

            motivos: list[str] = []
            ##med_row = self.repo.fetch_medicamento_por_cum(cum) if cum else None
#######

            med_row = None
            if cum:
                if cum not in med_rows_cache:
                    med_rows_cache[cum] = self.repo.fetch_medicamento_por_cum(cum)
                med_row = med_rows_cache[cum]

########

            if med_row is None:
                descripcion = ""
            else:
                descripcion = str(med_row.get("descripcion") or "").strip()
                codigo = str(med_row.get("codigo_interno") or cum).strip()

            posologia_raw = item.get("posologia")
            if posologia_raw is not None and str(posologia_raw).strip():
                posologia = str(posologia_raw).strip()
            elif med_row and med_row.get("posologia_catalogo"):
                posologia = str(med_row.get("posologia_catalogo")).strip()
            else:
                posologia = "NA"
            concentracion = str((med_row or {}).get("concentracion") or "").strip() or None
            forma_farmaceutica = str((med_row or {}).get("forma_farmaceutica") or "").strip() or None
            unidad_medida = str((med_row or {}).get("unidad_medida") or "").strip() or None

            if not cum or cantidad <= 0:
                motivos.append("cum y cantidad > 0 son obligatorios por medicamento.")
            if dias <= 0:
                motivos.append("dias debe ser mayor a cero por medicamento.")
            if med_row is None:
                motivos.append(f"Medicamento CUM '{cum}' no existe en tb_medicamento.")
            elif int(med_row.get("sw_activo") or 0) != 1:
                motivos.append(f"Medicamento CUM '{cum}' no está activo en catálogo.")

            if med_row is not None:
                if not tiene_contrato_municipio:
                    nit_dir = str(ips.get("nit") or "").strip()
                    motivos.append(
                        f"El prestador de direccionamiento (NIT {nit_dir or 'N/A'}) no tiene contrato vigente "
                        "con tarifario de medicamentos y cobertura para el municipio del afiliado."
                    )
                else:
                    ##tarifa = self.repo.fetch_tarifario_medicamento_contratos_municipio(
                    ##    ips_id, municipio_afiliado, codigo
                    ##)
                    tarifa_key = (ips_id, municipio_afiliado, codigo)
                    if tarifa_key not in tarifa_cache:
                        tarifa_cache[tarifa_key] = self.repo.fetch_tarifario_medicamento_contratos_municipio(
                            ips_id, municipio_afiliado, codigo
                        )
                    tarifa = tarifa_cache[tarifa_key]

                    if tarifa is None:
                        motivos.append(
                            f"Medicamento CUM '{cum}' no está en ningún tarifario de medicamentos de los "
                            f"contratos activos del prestador de direccionamiento (NIT {ips.get('nit')}) "
                            f"con cobertura en el municipio del afiliado."
                        )
                    else:
                        if int(tarifa.get("sw_activo") or 0) != 1:
                            motivos.append(
                                f"Medicamento CUM '{cum}' no está activo en el tarifario del contrato "
                                f"{tarifa.get('numero_contrato')}."
                            )
                        if int(tarifa.get("sw_automatico") or 0) != 1:
                            motivos.append(
                                f"Medicamento CUM '{cum}' no está marcado para autorización automática en tarifario "
                                f"(contrato {tarifa.get('numero_contrato')})."
                            )
                        med_row = dict(med_row)
                        med_row["valor"] = tarifa.get("valor_servicio") or med_row.get("valor") or 0
                        if not descripcion:
                            descripcion = str(tarifa.get("descripcion") or "").strip()

                # edad_min = int(med_row.get("edad_minima") or 0)
                # edad_max = int(med_row.get("edad_maxima") or 0)
                # if edad_min > 0 or edad_max > 0:
                #     edad = self._edad_afiliado_anios(afiliado.get("fecha_nacimiento"))
                #     if edad is None:
                #         motivos.append(
                #             f"Medicamento CUM '{cum}' no puede autorizarse: no se pudo determinar la edad del afiliado."
                #         )
                #     else:
                #         if edad_min > 0 and edad < edad_min:
                #             motivos.append(
                #                 f"El afiliado no tiene edad apta para recibir el medicamento CUM '{cum}' "
                #                 f"(edad mínima requerida {edad_min} años)."
                #             )
                #         if edad_max > 0 and edad > edad_max:
                #             motivos.append(
                #                 f"El afiliado no tiene edad apta para recibir el medicamento CUM '{cum}' "
                #                 f"(edad máxima permitida {edad_max} años)."
                #             )

            if not habilitada:
                motivos.append("La IPS prestadora no está habilitada (sw_habilitada=0).")
            if not auto_ips:
                motivos.append(
                    "La IPS no está habilitada para autorización automatizada de medicamentos "
                    "(ct_ips.tipo_autoriza≠2 y ct_ips.tipo_medicamento≠2)."
                )
                
            # if med_row and requiere_dir and not self.repo.medicamento_en_direccionamiento(
            #     int(ips["ips"]), int(med_row["medicamento"])
            # ):
            #     motivos.append(
            #         f"Medicamento CUM '{cum}' no está parametrizado en direccionamiento para esta IPS."
            #     )

            medicamento_id = int(med_row["medicamento"]) if med_row and med_row.get("medicamento") is not None else None
            if (
                medicamento_id is not None
                and requiere_dir
            ):
                if medicamento_id not in direccionamiento_cache:
                    direccionamiento_cache[medicamento_id] = self.repo.medicamento_en_direccionamiento(
                        int(ips["ips"]), medicamento_id
                    )
                if not direccionamiento_cache[medicamento_id]:
                    motivos.append(
                        f"Medicamento CUM '{cum}' no está parametrizado en direccionamiento para esta IPS."
                    )

            autorizado = not motivos and med_row is not None
            evaluados.append(
                MedicamentoEvaluado(
                    secuencia=idx,
                    cum=cum,
                    codigo_interno=codigo,
                    descripcion=descripcion,
                    cantidad=cantidad,
                    dias=dias,
                    posologia=posologia,
                    observacion=observacion,
                    concentracion=concentracion,
                    forma_farmaceutica=forma_farmaceutica,
                    unidad_medida=unidad_medida,
                    med_row=med_row or {},
                    autorizado=autorizado,
                    motivo="; ".join(motivos) if motivos else None,
                )
            )

        # Messiah updateAuthorizationsNewCore → validacionesAutorizacionMedicamentos
        aplicar_validaciones_autorizacion_messiah(evaluados, afiliado=afiliado, repo=self.repo)
        return evaluados

    def _item_resultado(
        self,
        e: MedicamentoEvaluado,
        *,
        pin: str | None,
        consecutivo_autorizacion: int | None,
        consecutivo_interno: str | None,
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "secuencia": e.secuencia,
            "cum": e.cum,
            "codigo_interno": e.codigo_interno,
            "descripcion": e.descripcion,
            "cantidad": e.cantidad,
            "dias": e.dias,
            "posologia": e.posologia,
            "concentracion": e.concentracion,
            "forma_farmaceutica": e.forma_farmaceutica,
            "unidad_medida": e.unidad_medida,
            "observacion": e.observacion or None,
            "autorizado": e.autorizado,
        }
        if e.autorizado and pin and consecutivo_autorizacion is not None:
            interno = consecutivo_interno or str(consecutivo_autorizacion)
            valor_linea = float(Decimal(str(e.med_row.get("valor") or 0)) * e.cantidad)
            base.update(
                {
                    "pin_activacion": pin,
                    "consecutivo_autorizacion": consecutivo_autorizacion,
                    "consecutivo_interno": interno,
                    "pin_codigo": f"{pin} - {interno}",
                    "informacion_gestion": "Autorización automática",
                    "informacion_gestion_si": "Autorización automática generada",
                    "valor_autorizado": valor_linea,
                }
            )
        elif e.autorizado:
            motivo = (
                "Aprobado para direccionamiento. Emitir autorización en el paso 2 "
                "(POST /afiliados/autorizacion-orden-medica-ips/activar)."
            )
            base.update(
                {
                    "motivo": motivo,
                    "informacion_gestion": motivo,
                    "informacion_gestion_si": motivo,
                }
            )
        else:
            motivo = e.motivo or "No cumple reglas de autorización automática."
            base.update(
                {
                    "motivo": motivo,
                    "informacion_gestion": motivo,
                    "informacion_gestion_no": motivo,
                }
            )
        return base

    @staticmethod
    def _tipo_resultado(total: int, autorizados: int) -> str:
        if autorizados <= 0:
            return "NINGUNA"
        if autorizados >= total:
            return "TOTAL"
        return "PARCIAL"

    def procesar(
        self,
        *,
        afiliado: dict[str, Any],
        ips_solicitante: dict[str, Any],
        ips_direccionamiento: dict[str, Any],
        ips_ss_consecutivo: int,
        form: dict[str, Any],
        medicamentos_json: list[dict[str, Any]],
        username: str,
        tipo_doc_abrev: str,
        diagnostico_relacionado1: int | None = None,
        diagnostico_relacionado2: int | None = None,
        origenes_atencion: list[int] | None = None,
        sede: dict[str, Any] | None = None,
        evaluados: list[MedicamentoEvaluado] | None = None,
    ) -> dict[str, Any]:
        cie = self.repo.fetch_cie10(str(form["diagnostico_principal"]))
        if cie is None:
            raise ValueError(
                f"diagnostico_principal '{form['diagnostico_principal']}' no existe en TB_CIE10."
            )
        consecutivo_especialidad = form.get("consecutivo_especialidad")
        if consecutivo_especialidad is None and form.get("especialidad"):
            consecutivo_especialidad = self.repo.fetch_especialidad(str(form["especialidad"]))
            if consecutivo_especialidad is None:
                raise ValueError("especialidad no existe en TB_ESPECIALIDAD.")
        if consecutivo_especialidad is None:
            raise ValueError("consecutivo_especialidad es obligatorio (médico solicitante).")

        form_payload = dict(form)
        form_payload["ubicacion_paciente_codigo"] = self.resolver_ubicacion_codigo(str(form["ubicacion_paciente"]))
        form_payload["consecutivo_nivel"] = form.get("consecutivo_nivel") or 2
        form_payload["consecutivo_ambito"] = form.get("consecutivo_ambito") or 23
        form_payload["sw_internacion"] = 1 if "hospital" in str(form["ubicacion_paciente"]).lower() else 0

        evaluados = evaluados or self.evaluar_medicamentos(
            ips=ips_direccionamiento,
            afiliado=afiliado,
            items=medicamentos_json,
        )
        if not evaluados:
            raise ValueError("Debe enviar al menos un medicamento en medicamentos_json.")

        self.exigir_al_menos_un_medicamento_autorizado(evaluados)

        meds_payload = []
        for e in evaluados:
            med_id = e.med_row.get("medicamento") if e.med_row else None
            if med_id is None:
                continue
            meds_payload.append(
                {
                    "secuencia": e.secuencia,
                    "medicamento": med_id,
                    "cantidad": e.cantidad,
                    "dias": e.dias,
                    "posologia": e.posologia,
                    "observacion": e.observacion,
                }
            )
        autorizaciones = [
            {
                "secuencia": e.secuencia,
                "cantidad": e.cantidad,
                "med_row": e.med_row,
                "valor_total": Decimal(str(e.med_row.get("valor") or 0)) * e.cantidad,
            }
            for e in evaluados
            if e.autorizado
        ]

        resultado = self.repo.crear_solicitud_y_autorizar(
            afiliado=afiliado,
            ips_solicitante=ips_solicitante,
            ips_direccionamiento=ips_direccionamiento,
            sede=sede,
            ips_ss_consecutivo=ips_ss_consecutivo,
            diagnostico_cie10=int(cie["consecutivo_cie10"]),
            diagnostico_relacionado1=diagnostico_relacionado1,
            diagnostico_relacionado2=diagnostico_relacionado2,
            consecutivo_especialidad=consecutivo_especialidad,
            origenes_atencion=origenes_atencion or [],
            form=form_payload,
            medicamentos=meds_payload,
            autorizaciones=autorizaciones,
            username=username,
            tipo_doc_abrev=tipo_doc_abrev,
        )

        pin = resultado.get("pin")
        consecutivo_autorizacion = resultado.get("consecutivo_autorizacion")
        consecutivo_interno = resultado.get("consecutivo_interno")

        autorizados = [
            self._item_resultado(
                e,
                pin=pin,
                consecutivo_autorizacion=consecutivo_autorizacion,
                consecutivo_interno=consecutivo_interno,
            )
            for e in evaluados
            if e.autorizado
        ]
        no_autorizados = [
            self._item_resultado(
                e,
                pin=None,
                consecutivo_autorizacion=None,
                consecutivo_interno=None,
            )
            for e in evaluados
            if not e.autorizado
        ]
        medicamentos_solicitados = sorted(
            autorizados + no_autorizados,
            key=lambda x: int(x["secuencia"]),
        )
        total_solicitados = len(evaluados)
        total_autorizados = len(autorizados)
        total_no_autorizados = len(no_autorizados)
        tipo_resultado = self._tipo_resultado(total_solicitados, total_autorizados)

        municipio_ips = str(ips_direccionamiento.get("municipio") or "")
        direccion_ips = str(ips_direccionamiento.get("direccion") or "")
        nombre_ips = str(ips_direccionamiento.get("razon_social") or "")
        if ips_direccionamiento.get("sigla"):
            nombre_ips = f"{nombre_ips} / {ips_direccionamiento['sigla']}"

        mensaje = (
            f"Solicitud {resultado.get('numero_solicitud')} registrada "
            f"(consecutivo {resultado['consecutivo_solicitud']}). "
            f"Autorizados: {total_autorizados} de {total_solicitados}."
        )
        if total_no_autorizados:
            mensaje += f" No autorizados: {total_no_autorizados}."
        mensaje += (
            " Pendiente de autorización. "
            "Use POST /afiliados/autorizacion-orden-medica-ips/activar con el mismo "
            f"consecutivo_solicitud {resultado['consecutivo_solicitud']}."
        )

        return {
            **resultado,
            "solicitud_usuario": resultado["consecutivo_solicitud"],
            "afiliado_id": int(afiliado["afiliado"]),
            "tipo_identificacion": tipo_doc_abrev,
            "numero_identificacion": str(afiliado["numero_identificacion"]),
            "nombre_afiliado": " ".join(
                p
                for p in [
                    afiliado.get("primer_nombre"),
                    afiliado.get("segundo_nombre"),
                    afiliado.get("primer_apellido"),
                    afiliado.get("segundo_apellido"),
                ]
                if p
            ).strip(),
            "origen": form.get("nombre_origen_atencion") or "Orden Médica",
            "origen_solicitud": "Orden Médica",
            "modalidad_servicio": form.get("nombre_modalidad") or "Ambulatorios",
            "pin_activacion": None,
            "consecutivo_interno": None,
            "autorizacion_activa": False,
            "pendiente_activacion": False,
            "estado_trazabilidad": "SOLICITUD",
            "tipo_resultado": tipo_resultado,
            "total_solicitados": total_solicitados,
            "total_autorizados": total_autorizados,
            "total_no_autorizados": total_no_autorizados,
            "valor_autorizacion": resultado.get("valor_autorizacion"),
            "fecha_fin_vigencia": resultado.get("fecha_fin_vigencia"),
            "ips_autorizada": None,
            "prestador_solicitante": {
                "nit": str(ips_solicitante.get("nit") or ""),
                "razon_social": str(ips_solicitante.get("razon_social") or ""),
            },
            "prestador_direccionamiento": {
                "nit": str(ips_direccionamiento.get("nit") or ""),
                "razon_social": str(ips_direccionamiento.get("razon_social") or ""),
            },
            "cobro": {
                "tipo_cobro": resultado.get("tipo_cobro"),
                "descripcion": resultado.get("tipo_cobro_descripcion"),
                "valor_aplicar": float(resultado.get("valor_cobro") or 0),
            },
            "medicamentos_solicitados": medicamentos_solicitados,
            "medicamentos_autorizados": autorizados,
            "medicamentos_no_autorizados": no_autorizados,
            "mensaje": mensaje,
        }
