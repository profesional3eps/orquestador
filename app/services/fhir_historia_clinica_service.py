from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.models.dto import HistoriaClinicaRequest


class FhirHistoriaClinicaError(ValueError):
    pass


FHIR_EXT = {
    "codigo_entidad": "http://orquestacionbd.local/fhir/StructureDefinition/codigo-entidad-responsable",
    "plan_beneficios": "http://orquestacionbd.local/fhir/StructureDefinition/plan-beneficios",
    "valor_copago": "http://orquestacionbd.local/fhir/StructureDefinition/valor-copago-cuota-moderadora",
    "codigo_causa_externa": "http://orquestacionbd.local/fhir/StructureDefinition/codigo-causa-externa",
    "fecha_asignacion": "http://orquestacionbd.local/fhir/StructureDefinition/fecha-asignacion-cita",
    "numero_autorizacion": "http://orquestacionbd.local/fhir/StructureDefinition/numero-autorizacion",
}

OBS_CODES = {
    "PESO": "Peso",
    "TALLA": "Talla",
    "PERIMETRO_ABDOMINAL": "PerimetroAbdominal",
    "TA_SISTOLICA": "Tasistolica",
    "TA_DIASTOLICA": "Tadiastolica",
    "EDAD_MENARQUIA": "EdadDeLaMenarquia",
    "EDAD_MENOPAUSIA_PNAL": "EdadMenopausiaPnal",
    "IMC": "IMC",
}


def _normalize_code(raw: str) -> str:
    return raw.strip().upper().replace(" ", "_").replace("-", "_")


def _entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    if str(bundle.get("resourceType")) != "Bundle":
        raise FhirHistoriaClinicaError("FHIR inválido: se espera resourceType=Bundle.")
    entries = bundle.get("entry")
    if not isinstance(entries, list):
        raise FhirHistoriaClinicaError("FHIR inválido: Bundle.entry debe ser arreglo.")
    resources = []
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("resource"), dict):
            resources.append(e["resource"])
    return resources


def _first_resource(resources: list[dict[str, Any]], resource_type: str) -> dict[str, Any]:
    for r in resources:
        if str(r.get("resourceType")) == resource_type:
            return r
    raise FhirHistoriaClinicaError(f"FHIR inválido: no se encontró recurso {resource_type}.")


def _resources(resources: list[dict[str, Any]], resource_type: str) -> list[dict[str, Any]]:
    return [r for r in resources if str(r.get("resourceType")) == resource_type]


def _identifier_value(resource: dict[str, Any]) -> str | None:
    ids = resource.get("identifier")
    if not isinstance(ids, list):
        return None
    for i in ids:
        if isinstance(i, dict):
            val = i.get("value")
            if val is not None and str(val).strip():
                return str(val).strip()
    return None


def _extension_value(resource: dict[str, Any], url: str) -> Any:
    exts = resource.get("extension")
    if not isinstance(exts, list):
        return None
    for ext in exts:
        if not isinstance(ext, dict):
            continue
        if str(ext.get("url")) != url:
            continue
        for k in ("valueString", "valueCode", "valueInteger", "valueDecimal", "valueDate", "valueDateTime"):
            if k in ext:
                return ext[k]
    return None


def _to_date(value: Any, field_name: str) -> date:
    if value is None:
        raise FhirHistoriaClinicaError(f"FHIR inválido: falta {field_name}.")
    s = str(value).strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise FhirHistoriaClinicaError(f"FHIR inválido: {field_name} debe tener formato fecha ISO.") from exc


def _obs_values(resources: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for obs in _resources(resources, "Observation"):
        code_obj = obs.get("code") or {}
        code = None
        coding = code_obj.get("coding")
        if isinstance(coding, list):
            for c in coding:
                if isinstance(c, dict) and c.get("code"):
                    code = str(c["code"])
                    break
        if not code and code_obj.get("text"):
            code = str(code_obj["text"])
        if not code:
            continue
        mapped = OBS_CODES.get(_normalize_code(code))
        if not mapped:
            continue
        val = None
        if isinstance(obs.get("valueQuantity"), dict):
            val = obs["valueQuantity"].get("value")
        if val is None:
            val = obs.get("valueInteger", obs.get("valueDecimal"))
        if val is None:
            continue
        out[mapped] = val
    return out


def _activities_from_procedure(proc: dict[str, Any]) -> list[dict[str, Any]]:
    performers = proc.get("performer")
    if not isinstance(performers, list):
        return []
    out: list[dict[str, Any]] = []
    for p in performers:
        if not isinstance(p, dict):
            continue
        actor = p.get("actor") if isinstance(p.get("actor"), dict) else {}
        actor_ident = None
        if isinstance(actor.get("identifier"), dict):
            actor_ident = actor["identifier"].get("value")
        tipo = "CC"
        exts = p.get("extension")
        if isinstance(exts, list):
            for ext in exts:
                if not isinstance(ext, dict):
                    continue
                if str(ext.get("url")).endswith("/tipo-identificacion-profesional"):
                    tipo = str(ext.get("valueString") or tipo)
        valor = None
        if isinstance(exts, list):
            for ext in exts:
                if not isinstance(ext, dict):
                    continue
                if str(ext.get("url")).endswith("/valor-consulta-procedimiento"):
                    valor = ext.get("valueDecimal", ext.get("valueInteger"))
        if actor_ident is None or valor is None:
            continue
        out.append(
            {
                "Profesional": {"Identificacion": str(actor_ident), "TipoIdentificacion": str(tipo)},
                "ValorConsultaProcedimiento": valor,
            }
        )
    return out


def map_fhir_bundle_to_historia_request(bundle: dict[str, Any]) -> HistoriaClinicaRequest:
    resources = _entries(bundle)
    patient = _first_resource(resources, "Patient")
    encounter = _first_resource(resources, "Encounter")
    organization = _first_resource(resources, "Organization")
    condition = _first_resource(resources, "Condition")
    procedure = _first_resource(resources, "Procedure")

    tipo_doc = None
    ids = patient.get("identifier")
    if isinstance(ids, list):
        for i in ids:
            if not isinstance(i, dict):
                continue
            if isinstance(i.get("type"), dict):
                coding = i["type"].get("coding")
                if isinstance(coding, list) and coding and isinstance(coding[0], dict) and coding[0].get("code"):
                    tipo_doc = str(coding[0]["code"]).strip()
            if not tipo_doc and isinstance(i.get("type"), dict) and i["type"].get("text"):
                tipo_doc = str(i["type"]["text"]).strip()
            if tipo_doc:
                break
    if not tipo_doc:
        raise FhirHistoriaClinicaError("FHIR inválido: Patient.identifier.type es obligatorio para TipoIdentificacion.")
    ident_usuario = _identifier_value(patient)
    if not ident_usuario:
        raise FhirHistoriaClinicaError("FHIR inválido: Patient.identifier.value es obligatorio.")

    nit_prestador = _identifier_value(organization)
    if not nit_prestador:
        raise FhirHistoriaClinicaError("FHIR inválido: Organization.identifier.value (NIT prestador) es obligatorio.")

    codigo_entidad = _extension_value(encounter, FHIR_EXT["codigo_entidad"]) or "CCF033"
    plan_beneficios = _extension_value(encounter, FHIR_EXT["plan_beneficios"])
    valor_copago = _extension_value(encounter, FHIR_EXT["valor_copago"])
    codigo_causa_externa = _extension_value(encounter, FHIR_EXT["codigo_causa_externa"])
    if valor_copago is None:
        valor_copago = 0
    if codigo_causa_externa is None:
        raise FhirHistoriaClinicaError("FHIR inválido: falta extension codigo-causa-externa en Encounter.")

    period = encounter.get("period") if isinstance(encounter.get("period"), dict) else {}
    fecha_atencion = _to_date(period.get("start"), "Encounter.period.start")
    fecha_asignacion_raw = _extension_value(encounter, FHIR_EXT["fecha_asignacion"]) or period.get("start")
    fecha_asignacion = _to_date(fecha_asignacion_raw, "fecha_asignacion")
    numero_aut = _extension_value(encounter, FHIR_EXT["numero_autorizacion"])
    if not numero_aut:
        numero_aut = _identifier_value(encounter)

    diag = None
    cond_code = condition.get("code") if isinstance(condition.get("code"), dict) else {}
    coding = cond_code.get("coding")
    if isinstance(coding, list):
        for c in coding:
            if isinstance(c, dict) and c.get("code"):
                diag = str(c["code"]).strip()
                break
    if not diag:
        raise FhirHistoriaClinicaError("FHIR inválido: Condition.code.coding[0].code es obligatorio (diagnóstico).")

    cups = None
    proc_code = procedure.get("code") if isinstance(procedure.get("code"), dict) else {}
    proc_coding = proc_code.get("coding")
    if isinstance(proc_coding, list):
        for c in proc_coding:
            if isinstance(c, dict) and c.get("code"):
                cups = str(c["code"]).strip()
                break
    if not cups:
        raise FhirHistoriaClinicaError("FHIR inválido: Procedure.code.coding[0].code es obligatorio (CUPS).")

    obs = _obs_values(resources)
    for required in (
        "Peso",
        "Talla",
        "PerimetroAbdominal",
        "Tasistolica",
        "Tadiastolica",
        "EdadDeLaMenarquia",
        "EdadMenopausiaPnal",
        "IMC",
    ):
        if required not in obs:
            raise FhirHistoriaClinicaError(f"FHIR inválido: falta Observation para {required}.")

    actividades = _activities_from_procedure(procedure)

    payload = {
        "Prestador": {"Identificacion": nit_prestador},
        "EntidadResponsable": {
            "Codigo": str(codigo_entidad),
            "PlanBeneficios": (str(plan_beneficios) if plan_beneficios is not None else None),
            "ValorCopagoCuotaModeradora": Decimal(str(valor_copago)),
        },
        "Usuario": {
            "TipoIdentificacion": tipo_doc,
            "Identificacion": ident_usuario,
        },
        "Cita": {
            "FechaAsignacion": fecha_asignacion,
            "FechaAtencion": fecha_atencion,
            "NumeroAutorizacion": (str(numero_aut) if numero_aut else None),
            "CodigoCups": cups,
            "CodigoCausaExterna": int(codigo_causa_externa),
            "CodigoDiagnosticoPrincipal": diag,
        },
        "Mediciones": {
            "Peso": int(obs["Peso"]),
            "Talla": int(obs["Talla"]),
            "PerimetroAbdominal": int(obs["PerimetroAbdominal"]),
            "Tasistolica": int(obs["Tasistolica"]),
            "Tadiastolica": int(obs["Tadiastolica"]),
            "EdadDeLaMenarquia": int(obs["EdadDeLaMenarquia"]),
            "EdadMenopausiaPnal": int(obs["EdadMenopausiaPnal"]),
            "IMC": Decimal(str(obs["IMC"])),
        },
        "Actividades": actividades,
    }
    return HistoriaClinicaRequest(**payload)

